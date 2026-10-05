"""Rule-based parsing of "change a device setting" utterances.

The LLM path (function calling) is the general solution, but it depends on a
model that honours ``tools`` -- and a relay that silently drops them would leave
the most common request of all ("把音量调到 60") answered only in words.  These
rules are the deterministic floor: when the user says something unambiguous about
volume or brightness, the device is driven directly and the spoken confirmation
is generated locally, with no model call at all.

Deliberately narrow.  Anything fuzzy ("聲音好像有點大") returns ``None`` and is
left to the model, so the rules can never hijack a normal conversation: a target
word *and* an explicit value/step marker are both required.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Iterable

#: Phrases that look like numbers but are not ("大一點", "想一下").
_FILLERS = ("一點", "一点", "一些", "一下", "一會兒", "一会儿", "一点儿")

#: target -> keywords found in the user's sentence.
#: Order matters: "屏幕调成夜间模式" contains both a theme phrase and the word
#: 屏幕, and the more specific one has to win.
_TARGETS: dict[str, tuple[str, ...]] = {
    "volume": ("音量", "聲音", "声音", "喇叭", "volume", "聲量", "声量"),
    "theme": ("主題", "主题", "夜間模式", "夜间模式", "深色", "淺色", "浅色", "theme"),
    "brightness": ("亮度", "屏幕亮", "螢幕亮", "屏幕暗", "螢幕暗", "brightness", "屏幕", "螢幕"),
}

#: target -> fragments a *tool* name/description must contain to serve it.
_TOOL_HINTS: dict[str, tuple[str, ...]] = {
    "volume": ("volume", "音量", "speaker", "audio_speaker"),
    "brightness": ("brightness", "亮度", "screen", "display"),
    "theme": ("theme", "主題", "主题", "screen", "display"),
}

#: Preferred argument names, in order, when a tool exposes several properties.
_ARGUMENT_NAMES: dict[str, tuple[str, ...]] = {
    "volume": ("volume", "level", "value", "percent", "value_percent"),
    "brightness": ("brightness", "level", "value", "percent"),
    "theme": ("theme", "mode", "value", "name"),
}

# "大/小" here is always relative to the *named* target, so 太亮 means dim the
# screen and 太小 means raise the volume -- the target word is what disambiguates,
# and without one none of these are considered at all.
_UP_MARKERS = (
    "大一點", "大一点", "大聲", "大声", "調大", "调大", "增大", "調高", "调高", "放大",
    "大聲點", "大声点", "太吵", "太響", "太响", "太小聲", "太小声", "太小",
    "太暗", "調亮", "调亮", "亮一點", "亮一点", "高一點", "高一点",
)
_DOWN_MARKERS = (
    "小一點", "小一点", "小聲", "小声", "調小", "调小", "減小", "减小", "調低", "调低",
    "小聲點", "小声点", "太輕", "太轻", "太低", "太大聲", "太大声", "太大", "太亮",
    "暗一點", "暗一点", "調暗", "调暗", "低一點", "低一点",
)
_MAX_MARKERS = ("最大", "最響", "最响", "最大聲", "最大声", "調滿", "调满", "全開", "全开", "滿格", "满格")
_MIN_MARKERS = ("最小", "最輕", "最轻", "最小聲", "最小声", "靜音", "静音", "關掉聲音", "关掉声音", "別出聲", "别出声")

_CN_DIGITS = {
    "零": 0, "〇": 0, "一": 1, "壹": 1, "二": 2, "兩": 2, "两": 2, "貳": 2,
    "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9,
}
_CN_NUMBER = re.compile(r"[零〇一壹二兩两貳三四五六七八九十百]+")
_ARABIC_NUMBER = re.compile(r"(?<![0-9])([0-9]{1,3})(?![0-9])")
_SET_MARKERS = ("調到", "调到", "設成", "设成", "設為", "设为", "改成", "改為", "改为", "變成", "变成", "調成", "调成", "到")

_THEME_VALUES = {
    "dark": ("夜間", "夜间", "深色", "黑色", "暗色", "夜晚", "dark", "night"),
    "light": ("白天", "日間", "日间", "淺色", "浅色", "亮色", "白色", "light", "day"),
}

#: Property names a status payload may use for each target.
_LEVEL_KEYS: dict[str, tuple[str, ...]] = {
    "volume": ("volume", "音量", "speaker", "level"),
    "brightness": ("bright", "亮度", "level"),
}


@dataclass(frozen=True)
class VoiceCommand:
    """A device-setting instruction parsed out of one utterance."""

    target: str
    kind: str  # "set" | "step"
    value: Any  # set: absolute value (int or the theme name); step: +1 / -1
    matched: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"target": self.target, "kind": self.kind, "value": self.value, "matched": self.matched}


def cn_to_int(text: str) -> int | None:
    """``十五`` -> 15, ``六十`` -> 60, ``一百二十`` -> 120.  ``None`` if not a number."""
    section = 0
    number = 0
    seen = False
    for char in text:
        if char in _CN_DIGITS:
            number = _CN_DIGITS[char]
            seen = True
        elif char == "十":
            section += (number or 1) * 10
            number = 0
            seen = True
        elif char == "百":
            section += (number or 1) * 100
            number = 0
            seen = True
        else:
            return None
    if not seen:
        return None
    return section + number


def parse_level(text: str) -> int | None:
    """Pull a 0-100 level out of a sentence, in either script."""
    for match in _ARABIC_NUMBER.finditer(text):
        value = int(match.group(1))
        if 0 <= value <= 100:
            return value
    # "百分之三十" must collapse to "三十" first: leaving it alone makes the 百 a
    # number of its own and turns a request for 30 into 100.
    cleaned = text.replace("百分之", "").replace("百分", "")
    for match in _CN_NUMBER.finditer(cleaned):
        value = cn_to_int(match.group(0))
        if value is not None and 0 <= value <= 100:
            return value
    return None


def _match_target(lowered: str) -> str | None:
    for target, keywords in _TARGETS.items():
        if any(word in lowered for word in keywords):
            return target
    return None


def _number_follows_target(text: str, target: str) -> bool:
    """``音量 60`` (no verb) still names a level -- but only right next to it."""
    for word in _TARGETS[target]:
        index = text.find(word)
        if index < 0:
            continue
        tail = text[index + len(word) : index + len(word) + 6]
        if parse_level(tail) is not None:
            return True
    return False


def parse_voice_command(text: str) -> VoiceCommand | None:
    """Return the device-setting instruction in ``text``, or ``None``."""
    original = (text or "").strip()
    if not original:
        return None
    # "一点" would otherwise be read as the numeral 1, and it is far more likely
    # to be the "a little" of "声音小一点".
    stripped = original
    for filler in _FILLERS:
        stripped = stripped.replace(filler, "")
    lowered = stripped.lower()

    target = _match_target(lowered)
    if target is None:
        return None

    if target == "theme":
        for value, words in _THEME_VALUES.items():
            if any(word in original.lower() for word in words):
                return VoiceCommand("theme", "set", value, matched=original)
        return None

    level = parse_level(stripped)
    if level is not None and any(marker in stripped for marker in _SET_MARKERS):
        return VoiceCommand(target, "set", level, matched=original)
    # A bare number straight after the target ("音量 60") is still a level.
    if level is not None and _number_follows_target(stripped, target):
        return VoiceCommand(target, "set", level, matched=original)

    if any(marker in original for marker in _MAX_MARKERS):
        return VoiceCommand(target, "set", 100, matched=original)
    if any(marker in original for marker in _MIN_MARKERS):
        return VoiceCommand(target, "set", 0, matched=original)
    if any(marker in original for marker in _UP_MARKERS):
        return VoiceCommand(target, "step", 1, matched=original)
    if any(marker in original for marker in _DOWN_MARKERS):
        return VoiceCommand(target, "step", -1, matched=original)
    return None


def select_tool(command: VoiceCommand, tools: Iterable[Any]) -> tuple[Any, str] | None:
    """Find ``(tool, argument_name)`` able to serve ``command``.

    ``tools`` are :class:`~services.device_gateway.xiaozhi.mcp.DeviceTool`-like
    objects; the argument name comes from the tool's own schema, so no firmware
    naming convention is assumed beyond the keyword hints above.
    """
    hints = _TOOL_HINTS.get(command.target, ())
    for tool in tools:
        haystack = f"{getattr(tool, 'name', '')} {getattr(tool, 'description', '')}".lower()
        if not any(hint.lower() in haystack for hint in hints):
            continue
        schema = getattr(tool, "input_schema", None) or {}
        properties = schema.get("properties") if isinstance(schema, dict) else None
        names = [str(key) for key in (properties or {})]
        for preferred in _ARGUMENT_NAMES.get(command.target, ()):
            for name in names:
                if name.lower() == preferred:
                    return tool, name
        # No conventional name: a single scalar property is unambiguous enough.
        scalars = [
            name
            for name in names
            if isinstance(properties.get(name), dict)
            and properties[name].get("type") in {"integer", "number", "string", None}
        ]
        if len(scalars) == 1:
            return tool, scalars[0]
    return None


def clamp_theme(value: str, schema_property: dict | None) -> str:
    """Honour an ``enum`` declared by the firmware for the theme argument."""
    if not isinstance(schema_property, dict):
        return str(value)
    options = schema_property.get("enum")
    if not isinstance(options, list) or not options:
        return str(value)
    lowered = str(value).lower()
    for option in options:
        if str(option).lower() == lowered:
            return str(option)
    aliases = _THEME_VALUES.get(lowered, ())
    for option in options:
        if any(alias in str(option).lower() for alias in aliases):
            return str(option)
    return str(value)


def extract_level(raw: Any, target: str) -> int | None:
    """Find the current 0-100 level for ``target`` inside a status tool result.

    ``self.get_device_status`` answers with a JSON blob whose shape differs per
    board, so the value is located by property name at any depth instead of by a
    fixed path that would silently stop matching on the next firmware revision.
    """
    data = raw
    if isinstance(raw, str):
        try:
            data = json.loads(raw)
        except (TypeError, ValueError):
            return None
    keys = _LEVEL_KEYS.get(target, ())
    if not keys:
        return None
    found: list[int] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if isinstance(value, bool):
                    continue
                if isinstance(value, (int, float)) and any(word in str(key).lower() for word in keys):
                    if 0 <= float(value) <= 100:
                        found.append(int(value))
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(data)
    return found[0] if found else None


def confirmation(command: VoiceCommand, language: str, *, level: int | None = None) -> str:
    """The sentence the device speaks after a rule-matched control.

    Local and instant on purpose: this path exists so that a control never
    depends on a model call, and answering through the model would reintroduce
    exactly that dependency.
    """
    value = level if level is not None else command.value
    yue = language == "yue-HK"
    english = language == "en-US"
    if command.target == "theme":
        dark = str(command.value) == "dark"
        if english:
            return f"Okay, switching the screen to {'dark' if dark else 'light'} mode."
        if yue:
            return f"好嘅，幫你轉做{'深色' if dark else '淺色'}主題。"
        return f"好的，已切换到{'深色' if dark else '浅色'}主题。"
    label = {"volume": ("音量", "volume"), "brightness": ("亮度", "brightness")}[command.target]
    if command.kind == "step":
        # The direction comes from the *step*, never from the level.  The caller
        # passes the resolved absolute level in ``level`` (100 - 20 = 80 for
        # "小声一点"), and reading the sign off that made every non-zero result
        # say "调大" -- the volume really went down while the device announced it
        # had gone up.
        up = int(command.value) > 0
        # Nobody says "亮度调小": each target has its own pair of verbs.
        word = {"volume": ("调大", "调小"), "brightness": ("调亮", "调暗")}[command.target][0 if up else 1]
        yue_word = {"volume": ("調大", "調細"), "brightness": ("調光", "調暗")}[command.target][0 if up else 1]
        if english:
            return f"Okay, {label[1]} {'up' if up else 'down'} a bit."
        if yue:
            return f"好嘅，{label[0]}{yue_word}咗少少。"
        return f"好的，{label[0]}已经{word}了一点。"
    if english:
        return f"Okay, {label[1]} set to {value}."
    if yue:
        return f"好嘅，{label[0]}較到 {value} 喇。"
    return f"好的，{label[0]}已经调到 {value}。"
