from __future__ import annotations
import os

RANK = {"none": 0, "attention": 1, "urgent": 2}


class _TransformersDetector:
    def __init__(self, model_name: str, task: str):
        self.model_name, self.task = model_name, task
        self._pipeline = None
        self._error = None

    def status(self) -> dict:
        return {"provider": "transformers", "model": self.model_name, "status": "error" if self._error else ("ready" if self._pipeline else "lazy")}

    def _load(self):
        if self._pipeline is None and self._error is None:
            try:
                from transformers import pipeline
                self._pipeline = pipeline(self.task, model=self.model_name)
            except Exception as exc:
                self._error = str(exc)
        return self._pipeline

    def predict(self, text: str):
        pipe = self._load()
        if pipe is None:
            return None
        try:
            result = pipe(text, truncation=True)[0]
            return str(result.get("label", "")).lower()
        except Exception as exc:
            self._error = str(exc)
            return None


_risk_detector = None


def risk_detector_status() -> dict:
    global _risk_detector
    model = os.getenv("IOT_RISK_MODEL", "").strip()
    if not model:
        return {"provider": "rules", "status": "ready", "model": None}
    if _risk_detector is None or _risk_detector.model_name != model:
        _risk_detector = _TransformersDetector(model, "text-classification")
    return _risk_detector.status()


def analyze_local(text: str) -> dict:
    lowered = text.lower()
    urgent = ("自殺", "自傷", "不想活", "傷害自己", "suicide", "kill myself")
    attention = ("很痛苦", "崩潰", "絕望", "撐不下去")
    level = "urgent" if any(x in lowered for x in urgent) else "attention" if any(x in lowered for x in attention) else "none"
    detector = risk_detector_status()
    if detector["provider"] == "transformers":
        label = _risk_detector.predict(text)
        mapped = {"urgent": "urgent", "critical": "urgent", "high": "urgent", "attention": "attention", "medium": "attention", "low": "none", "none": "none"}
        if label in mapped:
            level = max(level, mapped[label], key=RANK.__getitem__)
    return {"risk_level": level, "evidence": [x for x in urgent + attention if x in lowered], "detector_version": f"{detector['provider']}-risk-1", "status": "ok" if detector["status"] != "error" else "fallback", "provider": detector}


def merge_risk(local_level: str, llm_level: str | None, llm_status: str) -> dict:
    if local_level not in RANK or (llm_level is not None and llm_level not in RANK):
        raise ValueError("invalid_risk_level")
    level = max((local_level, llm_level or "none"), key=RANK.__getitem__)
    return {"risk_level": level, "analysis_status": "ok" if llm_status == "ok" else "partial"}
