from __future__ import annotations

RANK = {"none": 0, "attention": 1, "urgent": 2}


def analyze_local(text: str) -> dict:
    lowered = text.lower()
    urgent = ("自殺", "自傷", "不想活", "傷害自己", "suicide", "kill myself")
    attention = ("很痛苦", "崩潰", "絕望", "撐不下去")
    level = "urgent" if any(x in lowered for x in urgent) else "attention" if any(x in lowered for x in attention) else "none"
    return {"risk_level": level, "evidence": [x for x in urgent + attention if x in lowered], "detector_version": "local-rules-1", "status": "ok"}


def merge_risk(local_level: str, llm_level: str | None, llm_status: str) -> dict:
    if local_level not in RANK or (llm_level is not None and llm_level not in RANK):
        raise ValueError("invalid_risk_level")
    level = max((local_level, llm_level or "none"), key=RANK.__getitem__)
    return {"risk_level": level, "analysis_status": "ok" if llm_status == "ok" else "partial"}
