from __future__ import annotations

import json
import os
import re
import time
from typing import Any

import httpx

_EMOTIONS = {"positive", "negative", "neutral"}
_RISK_LEVELS = {"none", "attention", "urgent"}
_FENCED_JSON = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)
_BARE_JSON = re.compile(r"(\{.*?\})", re.DOTALL)

# 模型可能回傳中文／近義詞，統一歸一到標準枚舉。
_EMOTION_ALIASES = {
    "negative": {"負面", "负面", "負向", "愤怒", "憤怒", "生气", "生氣", "難過", "难过", "傷心", "伤心", "悲傷", "悲伤", "焦慮", "焦虑", "消極", "消极", "angry", "mad", "sad", "upset", "negative"},
    "positive": {"正面", "正向", "積極", "积极", "開心", "开心", "高興", "高兴", "快樂", "快乐", "喜悅", "喜悦", "happy", "glad", "positive"},
    "neutral": {"中性", "平靜", "平静", "平穩", "平稳", "平淡", "正常", "neutral", "calm"},
}
_RISK_ALIASES = {
    "none": {"無", "无", "無風險", "无风险", "正常", "低", "none"},
    "attention": {"關注", "关注", "需關注", "需关注", "需留意", "注意", "中等", "attention"},
    "urgent": {"緊急", "紧急", "嚴重", "严重", "危急", "高危", "危险", "危險", "urgent"},
}


def _normalize(value: Any, aliases: dict[str, set[str]]) -> str | None:
    if value is None:
        return None
    key = str(value).strip()
    if key in aliases:
        return key
    for canonical, names in aliases.items():
        if key in names:
            return canonical
    return None


def extract_structured(content: str) -> dict[str, Any]:
    """Split a trailing ``{emotion, risk, risk_evidence, remember}`` block from model output.

    Returns ``{"text", "emotion", "risk_level", "risk_evidence", "remember"}``.
    Only values in the fixed sets are accepted; anything else is dropped so
    callers can fall back to their own keyword baselines. The matched block is
    removed from ``text``.

    ``remember`` is passed through **raw**: validating and approving memory
    proposals belongs to ``services.memory.candidates``, which knows the storage
    contract. Riding along in this block is what makes memory extraction free --
    it shares the one model call the turn already makes.
    """
    text = (content or "").strip()
    for pattern in (_FENCED_JSON, _BARE_JSON):
        for match in reversed(list(pattern.finditer(text))):
            try:
                obj = json.loads(match.group(1))
            except (json.JSONDecodeError, TypeError):
                continue
            if not isinstance(obj, dict):
                continue
            emotion = _normalize(obj.get("emotion"), _EMOTION_ALIASES)
            risk = _normalize(obj.get("risk", obj.get("risk_level")), _RISK_ALIASES)
            remember = _remember_items(obj.get("remember", obj.get("memories")))
            if emotion is None and risk is None and not remember:
                continue
            evidence = obj.get("risk_evidence", obj.get("evidence"))
            risk_evidence = [str(item) for item in evidence] if isinstance(evidence, list) else []
            return {
                "text": (text[: match.start()] + text[match.end():]).strip(),
                "emotion": emotion,
                "risk_level": risk,
                "risk_evidence": risk_evidence,
                "remember": remember,
            }
    return {"text": text, "emotion": None, "risk_level": None, "risk_evidence": [], "remember": []}


def _remember_items(value: Any) -> list[dict[str, Any]]:
    """Accept either a list of proposals or a single one."""

    if isinstance(value, dict):
        return [value]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    return []


def _tool_calls(message: dict[str, Any]) -> list[dict[str, Any]]:
    """Normalise an OpenAI-style ``tool_calls`` block into plain dicts.

    ``arguments`` arrives as a JSON *string* (occasionally already an object),
    and the pipeline is the layer that decides how to run a tool, so both shapes
    are passed through untouched instead of being guessed at here.
    """
    calls: list[dict[str, Any]] = []
    for item in message.get("tool_calls") or []:
        if not isinstance(item, dict):
            continue
        function = item.get("function") if isinstance(item.get("function"), dict) else {}
        name = str(function.get("name") or item.get("name") or "").strip()
        if not name:
            continue
        raw = function.get("arguments", item.get("arguments"))
        calls.append(
            {
                "id": str(item.get("id") or f"call_{len(calls) + 1}"),
                "name": name,
                "arguments": raw if isinstance(raw, (dict, list)) else str(raw or "{}"),
            }
        )
    return calls


def _parse_sse_event(line: str, served: str) -> tuple[str | None, Any, str]:
    """Return ``(delta_text, tool_call_deltas, served_model)`` for one SSE line.

    ``delta_text`` is ``None`` for everything that is not a content chunk --
    keep-alive comments, the role-only opener, ``[DONE]``, malformed JSON --
    which is what lets the caller treat "no delta" and "empty delta" alike.

    ``tool_call_deltas`` is the raw ``delta.tool_calls`` array when present.
    Function calling *is* a stream: the model either writes text or asks for a
    tool, and both arrive as deltas on the same response, so a client that only
    reads ``content`` cannot both speak early and act on the device.
    """
    if not line:
        return None, None, served
    text = line.strip()
    if not text or text.startswith(":"):
        return None, None, served
    if text.startswith("data:"):
        text = text[5:].strip()
    if not text or text == "[DONE]":
        return None, None, served
    try:
        body = json.loads(text)
    except (TypeError, ValueError):
        return None, None, served
    if not isinstance(body, dict):
        return None, None, served
    if isinstance(body.get("model"), str) and body["model"]:
        served = body["model"]
    choices = body.get("choices") or []
    if not choices or not isinstance(choices[0], dict):
        return None, None, served
    delta = choices[0].get("delta") or {}
    if not isinstance(delta, dict):
        return None, None, served
    calls = delta.get("tool_calls")
    content = delta.get("content")
    return (str(content) if content else None), calls, served


def _parse_sse_line(line: str, served: str) -> tuple[str | None, str]:
    """Content-only view of :func:`_parse_sse_event`, kept for existing callers."""
    content, _calls, served = _parse_sse_event(line, served)
    return content, served


def _merge_tool_call_deltas(accumulator: dict[int, dict[str, Any]], raw: Any) -> None:
    """Fold one ``delta.tool_calls`` array into the accumulator.

    OpenAI-compatible streams split a single call across chunks: the first
    carries ``id`` and ``function.name``, later ones append ``function.arguments``
    fragments.  ``index`` is the key that ties them together, and it is the only
    thing that survives all the way to a runnable call.
    """
    if not isinstance(raw, list):
        return
    for position, item in enumerate(raw):
        if not isinstance(item, dict):
            continue
        index = item.get("index")
        if not isinstance(index, int):
            index = position
        entry = accumulator.setdefault(index, {"id": "", "name": "", "arguments": ""})
        if isinstance(item.get("id"), str) and item["id"]:
            entry["id"] = entry["id"] or item["id"]
        function = item.get("function") if isinstance(item.get("function"), dict) else {}
        name = function.get("name") or item.get("name")
        if isinstance(name, str) and name:
            entry["name"] = entry["name"] or name
        fragment = function.get("arguments", item.get("arguments"))
        if isinstance(fragment, str):
            entry["arguments"] += fragment
        elif isinstance(fragment, (dict, list)):
            entry["arguments"] = json.dumps(fragment, ensure_ascii=False)


def _finalise_tool_calls(accumulator: dict[int, dict[str, Any]]) -> list[dict[str, Any]]:
    """Turn the accumulator into the same shape ``reply`` returns."""
    calls: list[dict[str, Any]] = []
    for index in sorted(accumulator):
        entry = accumulator[index]
        name = str(entry.get("name") or "").strip()
        if not name:
            # A fragment with no name is not runnable; dropping it keeps a
            # truncated stream from executing a half-parsed call.
            continue
        calls.append(
            {
                "id": str(entry.get("id") or f"call_{len(calls) + 1}"),
                "name": name,
                "arguments": entry.get("arguments") or "{}",
            }
        )
    return calls



class DeepSeekClient:
    def __init__(self, api_key: str | None = None, base_url: str | None = None, model: str | None = None, timeout: float = 30, transport=None, attempts: int = 2):
        self.api_key = (api_key if api_key is not None else os.getenv("DEEPSEEK_API_KEY", "")).strip()
        self.base_url = (base_url or os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")).rstrip("/")
        self.model = model or os.getenv("DEEPSEEK_MODEL", "deepseek-flash")
        self.timeout = timeout
        self.transport = transport
        # Transport failures (a hanging relay, a reset connection) are transient and
        # cost the whole turn: the reply degrades to the canned fallback and, worse,
        # the turn carries no JSON block, so nothing is remembered. One retry turns
        # most of those into a normal answer.
        self.attempts = max(1, int(attempts))

    # Statuses worth a second attempt. 401/429/5xx are answered by the server and
    # will answer the same way again, so retrying them only wastes the user's time.
    _RETRYABLE = {"timeout", "network_error", "error"}

    def _attempt(self, payload: dict, request_id: str) -> dict:
        started = time.perf_counter()
        try:
            with httpx.Client(timeout=self.timeout, transport=self.transport) as client:
                response = client.post(f"{self.base_url}/v1/chat/completions", headers={"Authorization": f"Bearer {self.api_key}"}, json=payload)
                if response.status_code == 401: return {"text": "", "model": self.model, "usage": {}, "status": "unauthorized", "request_id": request_id}
                if response.status_code == 429: return {"text": "", "model": self.model, "usage": {}, "status": "rate_limited", "request_id": request_id}
                if response.status_code >= 500: return {"text": "", "model": self.model, "usage": {}, "status": "upstream_error", "request_id": request_id}
                response.raise_for_status()
                body = response.json()
                message = body.get("choices", [{}])[0].get("message", {}) or {}
                parsed = extract_structured(str(message.get("content") or ""))
                # ``model`` is always the name we asked for, on success and failure
                # alike, so the history stays comparable.  ``served_model`` is what
                # the endpoint reports it actually ran -- the two differ when a relay
                # normalises the request (this project's endpoint answers
                # "deepseek-flash" whatever name it is sent).
                return {"text": parsed["text"], "emotion": parsed["emotion"], "risk_level": parsed["risk_level"], "risk_evidence": parsed["risk_evidence"], "remember": parsed["remember"], "tool_calls": _tool_calls(message), "model": self.model, "served_model": str(body.get("model") or self.model), "usage": body.get("usage", {}), "status": "ok", "latency_ms": int((time.perf_counter() - started) * 1000), "request_id": request_id}
        except httpx.TimeoutException:
            return {"text": "", "model": self.model, "usage": {}, "status": "timeout", "latency_ms": int((time.perf_counter() - started) * 1000), "request_id": request_id}
        except httpx.NetworkError:
            return {"text": "", "model": self.model, "usage": {}, "status": "network_error", "latency_ms": int((time.perf_counter() - started) * 1000), "request_id": request_id}
        except Exception:
            return {"text": "", "model": self.model, "usage": {}, "status": "error", "latency_ms": int((time.perf_counter() - started) * 1000), "request_id": request_id}

    def reply_stream(
        self,
        messages: list[dict],
        request_id: str = "",
        on_delta: Any = None,
        *,
        temperature: float = 0.6,
        tools: list[dict] | None = None,
    ) -> dict:
        """Stream the answer, handing each text delta to ``on_delta`` as it lands.

        Latency, not throughput, is the reason this exists: a device that waits
        for the whole completion before synthesising anything stays silent for
        several seconds, while a device fed sentence-by-sentence starts speaking
        after the first one.

        ``tools`` is streamed together with the text rather than instead of it.
        Treating them as alternatives is what made streaming invisible on a real
        device: the firmware always declares tools, the tool loop took over every
        ordinary question, and the device went back to waiting for whole
        completions.  A call request arrives as ``tool_calls`` deltas and is
        returned in the same shape :meth:`reply` produces, so the caller can run
        it and ask again -- while the text of earlier rounds has already been
        spoken.

        The return value has the same shape as :meth:`reply`, so the caller's
        bookkeeping (memory, risk, captions) is unchanged.  Any failure -- a
        relay that ignores ``stream``, a truncated connection -- degrades to the
        plain non-streaming call, but only when nothing has been emitted yet:
        once deltas are out, the audio has already started and re-asking would
        make the device repeat itself.
        """
        if not self.api_key:
            return {"text": "", "model": self.model, "usage": {}, "status": "unavailable", "request_id": request_id}
        if on_delta is None:
            return self.reply(messages, request_id, tools=tools)

        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "stream": True,
        }
        if tools:
            payload["tools"] = list(tools)
            payload["tool_choice"] = "auto"
        started = time.perf_counter()
        parts: list[str] = []
        pending: dict[int, dict[str, Any]] = {}
        served = self.model
        try:
            with httpx.Client(timeout=httpx.Timeout(self.timeout, read=self.timeout), transport=self.transport) as client:
                with client.stream(
                    "POST",
                    f"{self.base_url}/v1/chat/completions",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json=payload,
                ) as response:
                    if response.status_code != 200:
                        response.read()
                        # 401/429/5xx are the plain call's business; let it decide.
                        # ``tools`` rides along so a degraded stream does not also
                        # cost the ability to act on the device.
                        return self.reply(messages, request_id, tools=tools)
                    for line in response.iter_lines():
                        delta, call_deltas, served = _parse_sse_event(line, served)
                        if call_deltas is not None:
                            _merge_tool_call_deltas(pending, call_deltas)
                        if delta is None:
                            continue
                        parts.append(delta)
                        try:
                            on_delta(delta)
                        except Exception:  # noqa: BLE001 - a consumer bug must not kill the stream
                            pass
        except Exception as exc:  # noqa: BLE001 - degradation is the contract here
            if not parts:
                print(f"[deepseek] stream failed ({type(exc).__name__}: {exc}); "
                      f"falling back to non-streaming request_id={request_id}", flush=True)
                return self.reply(messages, request_id, tools=tools)
            print(f"[deepseek] stream interrupted after {len(parts)} chunk(s): "
                  f"{type(exc).__name__}: {exc}", flush=True)

        parsed = extract_structured("".join(parts))
        return {
            "text": parsed["text"],
            "emotion": parsed["emotion"],
            "risk_level": parsed["risk_level"],
            "risk_evidence": parsed["risk_evidence"],
            "remember": parsed["remember"],
            "tool_calls": _finalise_tool_calls(pending),
            "model": self.model,
            "served_model": served,
            "usage": {},
            "status": "ok",
            # Only claim streaming when something actually streamed, so the flag
            # stays a usable diagnostic instead of a constant.
            "streamed": bool(parts),
            "latency_ms": int((time.perf_counter() - started) * 1000),
            "request_id": request_id,
        }

    def reply(self, messages: list[dict], request_id: str = "", tools: list[dict] | None = None) -> dict:
        if not self.api_key:
            return {"text": "", "model": self.model, "usage": {}, "status": "unavailable", "request_id": request_id}
        payload: dict[str, Any] = {"model": self.model, "messages": messages, "temperature": 0.6}
        if tools:
            # Function calling is what lets the model act on the device instead of
            # only talking about it.  An endpoint that ignores ``tools`` simply
            # answers as before -- the rule-based path in the ESP session keeps
            # the common controls working either way.
            payload["tools"] = list(tools)
            payload["tool_choice"] = "auto"
        result: dict = {}
        for attempt in range(1, self.attempts + 1):
            result = self._attempt(payload, request_id)
            if result.get("status") == "ok" or result.get("status") not in self._RETRYABLE:
                break
            if attempt < self.attempts:
                # Without this line a degraded turn leaves no trace anywhere: the
                # user just sees a canned reply and the memory never gets updated.
                print(f"[deepseek] {result.get('status')} after {result.get('latency_ms')} ms "
                      f"(attempt {attempt}/{self.attempts}), retrying request_id={request_id}", flush=True)
        else:
            print(f"[deepseek] giving up after {self.attempts} attempts: status={result.get('status')} "
                  f"request_id={request_id}", flush=True)
        return result
