from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_dashboard_is_read_only_monitoring_surface():
    html = (ROOT / "services" / "dashboard" / "index.html").read_text(encoding="utf-8")
    assert 'id="device-health"' not in html or 'id="devices"' in html
    assert 'id="emotions"' in html
    assert 'id="conversations"' in html
    assert 'id="events"' in html
    assert '<textarea' not in html.lower()
    assert 'contenteditable' not in html.lower()
    assert 'id="refresh-btn"' in html


def test_dashboard_assets_exist():
    assert (ROOT / "services" / "dashboard" / "dashboard.css").exists()
    assert (ROOT / "services" / "dashboard" / "dashboard.js").exists()


def test_login_overlay_hidden_attribute_overrides_grid_layout():
    html = (ROOT / "services" / "dashboard" / "index.html").read_text(encoding="utf-8")
    assert ".login-screen[hidden]" in html
    assert "display:none!important" in html.replace(" ", "")
