from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from simulator.heartbeat import proxy_allowed  # noqa: E402


def test_heartbeat_simulator_exists_and_has_once_mode():
    text = (ROOT / "simulator" / "heartbeat.py").read_text(encoding="utf-8")
    assert "--once" in text
    assert "/api/device/heartbeat" in text


def test_loopback_heartbeat_never_uses_a_system_proxy():
    # httpx honours the Windows registry proxy but ignores ProxyOverride, which
    # otherwise turns a localhost heartbeat into a 502 from the local proxy.
    assert proxy_allowed("http://127.0.0.1:8080/api/device/heartbeat") is False
    assert proxy_allowed("http://localhost:8080/api/device/heartbeat") is False
    assert proxy_allowed("http://[::1]:8080/api/device/heartbeat") is False
    # a genuinely remote backend may still honour the configured proxy
    assert proxy_allowed("https://example.invalid/api/device/heartbeat") is True
