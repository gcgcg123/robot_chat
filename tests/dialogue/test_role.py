from services.dialogue.role import load_role_prompt


def test_elderly_companion_role_is_loaded():
    prompt = load_role_prompt()
    assert "\u8001\u5e74\u4eba\u4e4b\u53cb" in prompt
    assert "\u4e0d\u4f5c\u8bca\u65ad" in prompt
    assert "\u957f\u671f\u8bb0\u5fc6" in prompt
