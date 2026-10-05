"""Spoken control of the conversation itself.

Two things a person expects to be able to say to a speaker that is talking at
them, and which this project previously could not honour:

* **"停"** -- shut up, but stay available (the device keeps listening).
* **"退下"** -- end the session and go back to 待机.

Both are matched *exactly* against the whole utterance, and both are length
capped.  A substring rule ("结束" anywhere in the sentence) would fire on
"我想结束这段关系，怎么办？" -- a real thing a user says to an emotional-support
assistant -- and silently hang up on them.  Losing a session to a false positive
is far worse than making the user repeat a short command word.
"""
from __future__ import annotations

#: "stop talking, keep listening"
_STOP_WORDS = (
    "停", "停止", "停一下", "停下", "停停", "别说了", "別說了", "不要说了", "不用说了",
    "别讲了", "別講了", "安静", "安靜", "闭嘴", "閉嘴", "打断", "打斷", "别念了",
    "stop", "shutup",
)

#: "end the conversation"
_END_WORDS = (
    "退下", "退下吧", "结束", "結束", "结束对话", "結束對話", "结束聊天", "結束聊天",
    "退出对话", "退出對話", "不聊了", "不聊啦", "就这样", "就這樣", "就这样吧",
    "拜拜", "拜拜啦", "再见", "再見", "不用了", "没事了", "沒事了", "先这样",
    "byebye", "goodbye", "good bye",
)

#: Punctuation and filler the ASR may append around a two-character command.
_TRIM = "。！？!?.,，、；;：: 　\t~～…\"'“”‘’()（）[]【】"

#: Anything longer than this is a sentence, not a command.
MAX_COMMAND_CHARS = 12

_ACKNOWLEDGEMENTS = {
    "stop": {
        "zh-CN": "好，我不说了。",
        "yue-HK": "好，我唔講喇。",
        "en-US": "Okay, I'll stop.",
    },
    "end": {
        "zh-CN": "好，那我先退下，需要我的時候再叫我一聲。",
        "yue-HK": "好，咁我先退下，需要我嘅時候再嗌我。",
        "en-US": "Alright, I'll step back. Call me whenever you need me.",
    },
}


def normalize(text: str) -> str:
    """Collapse whitespace and strip the decoration ASR likes to add."""
    return "".join((text or "").split()).strip(_TRIM)


def classify(text: str, *, max_chars: int = MAX_COMMAND_CHARS) -> str | None:
    """Return ``"stop"`` / ``"end"`` / ``None`` for one utterance."""
    normalised = normalize(text)
    if not normalised or len(normalised) > max_chars:
        return None
    if normalised in _STOP_WORDS:
        return "stop"
    if normalised in _END_WORDS:
        return "end"
    return None


def acknowledgement(kind: str, language: str = "zh-CN") -> str:
    """Short spoken reply confirming a ``"stop"`` or ``"end"`` command."""
    table = _ACKNOWLEDGEMENTS.get(kind) or {}
    return table.get(language) or table.get("zh-CN", "")


def end_words() -> list[str]:
    """The end-of-conversation vocabulary, for docs and configuration help."""
    return list(_END_WORDS)


def stop_words() -> list[str]:
    """The stop-speaking vocabulary, for docs and configuration help."""
    return list(_STOP_WORDS)
