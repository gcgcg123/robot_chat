"""Streaming speech playback for the xiaozhi ESP transport.

The old path was strictly serial: wait for the *whole* answer, split it into
sentences, then for each sentence synthesise it completely, encode it, and push
every packet.  Three consequences, all of them reported from the real device:

* **Slow to start.** The first audio could not begin until the model had
  finished writing and the first sentence had been fully synthesised.
* **Stuck on long sentences.** One unpunctuated run became one huge synthesis
  request; when it hit the provider timeout the sentence was dropped and the
  reply was never finished.
* **Uninterruptible.** Every step was awaited inline, so nothing could cut the
  playback short.

This player fixes the shape of the problem rather than the symptoms.  Sentences
arrive on a queue as the model writes them, and two workers run in parallel:

``[_synth]``  text -> PCM, in a worker thread (edge-tts + ffmpeg are blocking)
``[_play]``   PCM -> Opus -> paced frames on the socket

Synthesis of sentence *n+1* therefore overlaps playback of sentence *n*, a
failure to synthesise one sentence only costs that sentence, and because both
stages poll :func:`aborted` the whole thing can be torn down mid-word.

Queue depth is unbounded on purpose: the producer is a language model whose
output is bounded by the answer itself, so back-pressure would only add a
deadlock surface (a worker thread cannot ``await`` a full ``asyncio.Queue``).
"""
from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable

#: Refuse to buffer more than this many sentences.  Not a throughput limit --
#: simply a guard so a pathological model cannot grow the queue without end.
_MAX_PENDING = 64


class TtsPlayer:
    """Speak a stream of sentences to one device session.

    Every collaborator is injected, which keeps the whole thing testable without
    a socket, an Opus codec, or a real TTS provider.
    """

    def __init__(
        self,
        *,
        turn_id: str,
        session_id: str,
        provider: Any,
        encoder: Any,
        resample: Callable[[bytes, int, int], bytes],
        send_json: Callable[[dict], Awaitable[None]],
        send_bytes: Callable[[bytes], Awaitable[None]],
        publish: Callable[[str, dict, str], Awaitable[None]],
        set_state: Callable[[str, str], None],
        is_cancelled: Callable[[], bool],
        language: Callable[[], str],
        sample_rate: int,
        frame_duration_ms: int,
        prebuffer_frames: int = 5,
        result_payload: dict | None = None,
        segment_extra: dict | None = None,
        speaking_state: str = "speaking",
        audio_enabled: bool = True,
    ) -> None:
        self.turn_id = turn_id
        self.session_id = session_id
        self._provider = provider
        self._encoder = encoder
        self._resample = resample
        self._send_json = send_json
        self._send_bytes = send_bytes
        self._publish = publish
        self._set_state = set_state
        self._is_cancelled = is_cancelled
        self._language = language
        self.sample_rate = sample_rate
        self._interval = max(0.0, frame_duration_ms / 1000.0)
        self._prebuffer = max(0, prebuffer_frames)
        self._payload = result_payload or {}
        self._segment_extra = dict(segment_extra or {})
        self._speaking_state = speaking_state
        self._audio_enabled = audio_enabled

        self._texts: asyncio.Queue[str | None] = asyncio.Queue()
        self._audios: asyncio.Queue[tuple[str, bytes] | None] = asyncio.Queue()
        self._synth_task: asyncio.Task | None = None
        self._play_task: asyncio.Task | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

        self._aborted = False
        self._closed = False
        self._started = False
        self._offered = 0
        self._played = 0
        self._errors: list[str] = []

    # ------------------------------------------------------------------ probes

    @property
    def offered(self) -> int:
        return self._offered

    @property
    def played(self) -> int:
        return self._played

    @property
    def errors(self) -> list[str]:
        return list(self._errors)

    @property
    def aborted(self) -> bool:
        return self._aborted

    # ------------------------------------------------------------------ producer

    def start(self) -> "TtsPlayer":
        """Bind to the running loop and spin up both workers."""
        self._loop = asyncio.get_running_loop()
        self._synth_task = asyncio.create_task(self._synthesize_loop())
        self._play_task = asyncio.create_task(self._playback_loop())
        return self

    def offer(self, text: str) -> None:
        """Queue one sentence.  Safe to call from a worker thread."""
        phrase = (text or "").strip()
        if not phrase or self._aborted or self._closed:
            return
        if self._offered >= _MAX_PENDING:
            return
        self._offered += 1
        self._push(self._texts, phrase)

    def finish(self) -> None:
        """No more sentences will arrive."""
        if self._closed:
            return
        self._closed = True
        self._push(self._texts, None)

    # ------------------------------------------------------------------ consumer

    async def wait(self) -> None:
        """Run until every offered sentence has been spoken (or tear-down)."""
        tasks = [task for task in (self._synth_task, self._play_task) if task is not None]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def abort(self) -> None:
        """Stop immediately, discarding anything not yet spoken.

        The caller is responsible for the ``tts:stop`` that actually moves the
        device out of its speaking state -- that message is the interrupt lever,
        and sending it is part of the interrupt, not of the playback.
        """
        self._aborted = True
        for task in (self._synth_task, self._play_task):
            if task is not None and not task.done():
                task.cancel()
        tasks = [task for task in (self._synth_task, self._play_task) if task is not None]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._drain(self._texts)
        self._drain(self._audios)

    # ------------------------------------------------------------------ workers

    async def _synthesize_loop(self) -> None:
        try:
            while True:
                text = await self._texts.get()
                if text is None or self._aborted:
                    break
                if self._is_cancelled():
                    break
                try:
                    pcm = await self._synthesize(text)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001 - one bad sentence is not the turn
                    reason = f"{type(exc).__name__}:{exc}"
                    self._errors.append(reason)
                    await self._publish(
                        "display.state",
                        {"state": self._speaking_state, "caption": text, "audio_error": reason},
                        self.turn_id,
                    )
                    continue
                if self._aborted:
                    break
                await self._audios.put((text, pcm))
        finally:
            # Always hand the player its sentinel, cancelled or not, so it can
            # never block forever on an empty queue.
            self._push_sentinel()

    async def _playback_loop(self) -> None:
        while True:
            item = await self._audios.get()
            if item is None or self._aborted:
                break
            text, pcm = item
            if self._is_cancelled():
                break
            await self._emit(text, pcm)
        # Deliberately no ``tts:stop`` here: the session owns that message so it
        # can be ordered *after* the turn's own bookkeeping.  The device answers
        # ``tts:stop`` by leaving its speaking state, which makes it send
        # ``listen start`` straight back -- if that arrived while the turn was
        # still winding down it would look like a barge-in and cancel it.

    async def _synthesize(self, text: str) -> bytes:
        if not self._audio_enabled:
            return b""
        artifact = await asyncio.to_thread(self._provider.synthesize, text)
        return self._resample(artifact.pcm16, artifact.sample_rate, self.sample_rate)

    async def _emit(self, text: str, pcm: bytes) -> None:
        state = "start" if not self._started else "sentence_start"
        self._started = True
        await self._send_json(_tts_message(state, self.session_id, text))
        self._set_state(self._speaking_state, text)

        if pcm and self._audio_enabled and self._encoder is not None:
            frames = self._encoder.encode(pcm)
            for index, frame in enumerate(frames):
                if self._aborted or self._is_cancelled():
                    return
                await self._send_bytes(frame)
                # Pre-buffer a few frames so the device's jitter buffer starts
                # ahead of playback, then pace the rest at the frame interval
                # instead of flooding the socket in one burst.
                if index >= self._prebuffer:
                    await asyncio.sleep(self._interval)

        self._played += 1
        await self._publish(
            "tts.segment",
            {
                "index": self._played - 1,
                "sequence": self._played - 1,
                "segment_id": f"{self.turn_id}-{self._played - 1}",
                "text": text,
                "language": self._language(),
                "audio": bool(pcm) and self._audio_enabled,
                "final": self._closed and self._texts.empty(),
                "result": self._payload,
                **self._segment_extra,
            },
            self.turn_id,
        )

    # ------------------------------------------------------------------ plumbing

    def _push(self, queue: asyncio.Queue, item: Any) -> None:
        """Put from any thread: marshal onto the loop when we are not on it."""
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        loop = self._loop
        if running is not None and running is loop:
            queue.put_nowait(item)
            return
        if loop is not None and loop.is_running():
            try:
                loop.call_soon_threadsafe(queue.put_nowait, item)
                return
            except RuntimeError:
                pass
        queue.put_nowait(item)

    def _push_sentinel(self) -> None:
        try:
            self._audios.put_nowait(None)
        except Exception:  # noqa: BLE001 - unbounded queue, but never fatal
            pass

    @staticmethod
    def _drain(queue: asyncio.Queue) -> None:
        while True:
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:
                return


def _tts_message(state: str, session_id: str, text: str | None = None) -> dict:
    message: dict[str, Any] = {"type": "tts", "state": state, "session_id": session_id}
    if text is not None:
        message["text"] = text
    return message
