from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import queue


class QueueFullError(RuntimeError): pass


class AsrWorker:
    def __init__(self, service, max_queue: int = 4):
        # max_queue is queued work; one additional slot is reserved for the active task.
        self.service = service; self._slots = queue.Queue(maxsize=max_queue + 1); self._pool = ThreadPoolExecutor(max_workers=1)
    def submit(self, audio, language=None):
        try: self._slots.put_nowait(1)
        except queue.Full: raise QueueFullError("asr_queue_full")
        def run():
            try: return self.service.transcribe(audio, language)
            finally: self._slots.get_nowait(); self._slots.task_done()
        return self._pool.submit(run)
    def shutdown(self): self._pool.shutdown(wait=True)
