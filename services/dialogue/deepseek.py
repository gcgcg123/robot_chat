from __future__ import annotations

import os
import time
import httpx


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
                return {"text": str(choice).strip(), "model": body.get("model", self.model), "usage": body.get("usage", {}), "status": "ok", "latency_ms": int((time.perf_counter() - started) * 1000), "request_id": request_id}
        except httpx.TimeoutException:
            return {"text": "", "model": self.model, "usage": {}, "status": "timeout", "request_id": request_id}
        except httpx.NetworkError:
            return {"text": "", "model": self.model, "usage": {}, "status": "network_error", "request_id": request_id}
        except Exception:
            return {"text": "", "model": self.model, "usage": {}, "status": "error", "request_id": request_id}
