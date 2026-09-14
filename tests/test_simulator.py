from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def test_heartbeat_simulator_exists_and_has_once_mode():
    text = (ROOT / "simulator" / "heartbeat.py").read_text(encoding="utf-8")
    assert "--once" in text
    assert "/api/device/heartbeat" in text
