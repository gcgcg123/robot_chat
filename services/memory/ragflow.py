"""Optional RAGFlow adapter modeled after xiaozhi's retrieval plugin.

It is opt-in: no endpoint or token is assumed, and retrieved chunks remain
untrusted context. The local SQLite retriever remains the P1 default.
"""
from __future__ import annotations

import os
import httpx


class RAGFlowProvider:
    def __init__(self, base_url: str | None = None, api_key: str | None = None, dataset_ids: list[str] | None = None, timeout: float = 10):
        self.base_url = (base_url or os.getenv("RAGFLOW_BASE_URL", "")).rstrip("/")
        self.api_key = api_key or os.getenv("RAGFLOW_API_KEY", "")
        self.dataset_ids = dataset_ids or [x for x in os.getenv("RAGFLOW_DATASET_IDS", "").split(",") if x]
        self.timeout = timeout

    def search(self, question: str, limit: int = 5) -> list[dict]:
        if not self.base_url or not self.api_key or not question.strip():
            return []
        payload = {"question": question, "dataset_ids": self.dataset_ids}
        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.post(f"{self.base_url}/api/v1/retrieval", headers={"Authorization": f"Bearer {self.api_key}"}, json=payload)
                response.raise_for_status(); body = response.json()
            chunks = body.get("data", {}).get("chunks", body.get("chunks", []))
            return [{"text": str(c.get("content", c.get("text", ""))), "source_id": str(c.get("id", c.get("document_id", "ragflow")))} for c in chunks[: min(5, max(1, limit))]]
        except Exception:
            return []
