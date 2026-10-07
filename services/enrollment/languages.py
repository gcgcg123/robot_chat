from typing import Literal

Language = Literal['yue-HK', 'zh-CN', 'en-US']
LANGUAGES = {
    'yue-HK': {'label': '粵語', 'asr': 'yue', 'tts': 'zh-HK', 'instruction': '預設以自然粵語口語、繁體字回覆。', 'prompts': [
        '你好，我想同你傾吓偈，分享我今日遇到嘅事情。',
        '今日我出咗去行吓，見到沿途嘅風景，覺得幾舒服。',
        '有時我會開心，有時會有啲攰，希望你可以慢慢聽我講。',
        '今日天氣唔錯，我諗住遲啲出去行吓，順便買啲嘢返嚟。',
        '多謝你肯陪我傾偈，我有啲心事想慢慢同你講。']},
    'zh-CN': {'label': '普通話', 'asr': 'zh', 'tts': 'zh-CN', 'instruction': '預設以自然普通話措辭、繁體字回覆。', 'prompts': [
        '你好，我想和你聊聊天，分享今天遇到的事情。',
        '今天我出去散步，看了看沿途的風景，覺得很舒服。',
        '有時我很開心，有時也會覺得疲倦，希望你能耐心聽我說。',
        '今天的天氣還不錯，我打算晚一點出去走走，順便買點東西。',
        '謝謝你願意陪我說話，我有些事情想慢慢講給你聽。']},
    'en-US': {'label': 'English', 'asr': 'en', 'tts': 'en-US', 'instruction': 'Reply in natural English by default.', 'prompts': [
        'Hello, I would like to talk with you and share something about my day.',
        'Today I went for a walk, enjoyed the scenery, and felt relaxed.',
        'Sometimes I feel happy, and sometimes I feel tired. I hope you can listen patiently.',
        'The weather is nice today, so I may go out for a walk later and buy a few things.',
        'Thank you for talking with me. There is something I would like to tell you slowly.']},
}
