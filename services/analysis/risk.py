from __future__ import annotations

RANK = {"none": 0, "attention": 1, "urgent": 2}

# Two vocabularies, and they are separate tuples on purpose: `services/memory/safety.py` stores a
# *different* long-term memory for a self-harm disclosure than for a threat to someone else, and it
# classifies by asking which tuple matched. Keeping one flat list would leave that module guessing.
#
# The local detector is the conservative half of the risk path (`merge_risk` takes the higher
# level), so it errs toward flagging: a false positive costs one line of human review, a false
# negative costs a missed disclosure. 2026-10-03: the first version did not contain 跳樓 at all --
# the exact phrase this feature was requested for -- nor 想死 / 活不下去 / 上吊 / 割腕 / 吞藥, so
# "我要跳樓" was scored risk=none and nothing was remembered.
SELF_HARM = (
    "自殺", "自杀", "自傷", "自伤", "自殘", "自残",
    "不想活", "活不下去", "想死", "尋死", "寻死", "一了百了",
    "傷害自己", "伤害自己", "結束生命", "结束生命", "輕生", "轻生",
    "跳樓", "跳楼", "跳海", "上吊", "割腕", "割脈", "服毒", "吞藥", "吞药",
    "suicide", "kill myself", "kill herself", "kill himself", "end my life", "want to die",
    "jump off a building", "jump off the roof", "throw myself off", "hang myself", "cut myself",
    "self-harm", "self harm",
)
VIOLENCE = (
    "殺人", "杀人", "殺了他", "杀了他", "殺死他", "杀死他", "捅死", "砍死",
    "傷人", "伤人", "傷害別人", "伤害别人", "打人", "揍人",
    "hurt someone", "hurt people", "kill him", "kill her", "kill them",
)
URGENT = SELF_HARM + VIOLENCE
# Deliberately narrow. Every attention hit becomes a `risk_events` row for human review, so a wide
# list here would bury the real disclosures under ordinary bad days.
ATTENTION = (
    "很痛苦", "崩潰", "崩溃", "絕望", "绝望", "撐不下去", "撑不下去",
    "打人", "揍", "傷人", "伤人", "傷害別人", "伤害别人",
)


def analyze_local(text: str) -> dict:
    lowered = text.lower()
    level = "urgent" if any(x in lowered for x in URGENT) else "attention" if any(x in lowered for x in ATTENTION) else "none"
    return {"risk_level": level, "evidence": [x for x in URGENT + ATTENTION if x in lowered], "detector_version": "local-rules-2", "status": "ok"}


def merge_risk(local_level: str, llm_level: str | None, llm_status: str) -> dict:
    if local_level not in RANK or (llm_level is not None and llm_level not in RANK):
        raise ValueError("invalid_risk_level")
    level = max((local_level, llm_level or "none"), key=RANK.__getitem__)
    return {"risk_level": level, "analysis_status": "ok" if llm_status == "ok" else "partial"}
