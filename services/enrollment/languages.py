from typing import Literal

Language = Literal['yue-HK', 'zh-CN', 'en-US']
LANGUAGES = {
    'yue-HK': {'label': '粵語', 'asr': 'yue', 'tts': 'zh-HK', 'instruction': '預設以自然粵語口語、繁體字回覆。', 'prompts': [
        '你好，我想同你傾吓偈，分享我今日遇到嘅事情。',
        '今日我出咗去行吓，見到沿途嘅風景，覺得幾舒服。',
        '有時我會開心，有時會有啲攰，希望你可以慢慢聽我講。']},
    'zh-CN': {'label': '普通話', 'asr': 'zh', 'tts': 'zh-CN', 'instruction': '預設以自然普通話措辭、繁體字回覆。', 'prompts': [
        '你好，我想和你聊聊天，分享今天遇到的事情。',
        '今天我出去散步，看了看沿途的風景，覺得很舒服。',
        '有時我很開心，有時也會覺得疲倦，希望你能耐心聽我說。']},
    'en-US': {'label': 'English', 'asr': 'en', 'tts': 'en-US', 'instruction': 'Reply in natural English by default.', 'prompts': [
        'Hello, I would like to talk with you and share something about my day.',
        'Today I went for a walk, enjoyed the scenery, and felt relaxed.',
        'Sometimes I feel happy, and sometimes I feel tired. I hope you can listen patiently.']},
}
