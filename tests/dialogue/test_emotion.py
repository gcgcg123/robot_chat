from services.dialogue.pipeline import detect_emotion


def test_distress_words_override_sentiment_model():
    assert detect_emotion("我現在心情不好，很焦慮，是不是抑鬱症啊？") == "negative"
    assert detect_emotion("我现在心情不好，很焦虑，是不是抑郁症啊？") == "negative"


def test_positive_rule_remains_available():
    assert detect_emotion("我今天很開心，謝謝你") == "positive"
