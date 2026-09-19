from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_dashboard_assets_do_not_contain_secret_names_or_chat_composer():
    text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (ROOT / "services" / "dashboard").glob("*.html")
        for _ in [0]
    )
    assert "DEEPSEEK_API_KEY" not in text
    assert "Authorization" not in text
    assert "textarea" not in text.lower()
