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


class DeepSeekClient:
    def __init__(self, api_key: str | None = None, base_url: str | None = None, model: str | None = None, timeout: float = 30, transport=None):
        self.api_key = (api_key if api_key is not None else os.getenv("DEEPSEEK_API_KEY", "")).strip()
        self.base_url = (base_url or os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")).rstrip("/")
        self.model = model or os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
        self.timeout = timeout
        self.transport = transport

    def reply(self, messages: list[dict], request_id: str = "") -> dict:
        if not self.api_key:
            return {"text": "", "model": self.model, "usage": {}, "status": "unavailable", "request_id": request_id}
        payload = {"model": self.model, "messages": messages, "temperature": 0.6}
        started = time.perf_counter()
        try:
            with httpx.Client(timeout=self.timeout, transport=self.transport) as client:
                response = client.post(f"{self.base_url}/v1/chat/completions", headers={"Authorization": f"Bearer {self.api_key}"}, json=payload)
                if response.status_code == 401: return {"text": "", "model": self.model, "usage": {}, "status": "unauthorized", "request_id": request_id}
                if response.status_code == 429: return {"text": "", "model": self.model, "usage": {}, "status": "rate_limited", "request_id": request_id}
                if response.status_code >= 500: return {"text": "", "model": self.model, "usage": {}, "status": "upstream_error", "request_id": request_id}
                response.raise_for_status()
                body = response.json(); choice = body.get("choices", [{}])[0].get("message", {}).get("content", "")
                parsed = extract_structured(str(choice))
                return {"text": parsed["text"], "emotion": parsed["emotion"], "risk_level": parsed["risk_level"], "risk_evidence": parsed["risk_evidence"], "remember": parsed["remember"], "model": body.get("model", self.model), "usage": body.get("usage", {}), "status": "ok", "latency_ms": int((time.perf_counter() - started) * 1000), "request_id": request_id}
        except httpx.TimeoutException:
            return {"text": "", "model": self.model, "usage": {}, "status": "timeout", "request_id": request_id}
        except httpx.NetworkError:
            return {"text": "", "model": self.model, "usage": {}, "status": "network_error", "request_id": request_id}
        except Exception:
            return {"text": "", "model": self.model, "usage": {}, "status": "error", "request_id": request_id}
