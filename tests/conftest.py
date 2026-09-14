from pathlib import Path
import sys
import os
import tempfile
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_runtime = None


def pytest_configure(config):
    global _runtime
    _runtime = tempfile.TemporaryDirectory(prefix="iot-tests-")
    os.environ["IOT_TESTING"] = "1"
    os.environ["IOT_DATA_DIR"] = _runtime.name
    os.environ["DATABASE_PATH"] = os.path.join(_runtime.name, "emotional_robot.sqlite3")
    os.environ["DEEPSEEK_API_KEY"] = ""


def pytest_unconfigure(config):
    global _runtime
    if _runtime is not None:
        _runtime.cleanup()
        _runtime = None


@pytest.fixture
def runtime_settings(tmp_path):
    from services.storage.settings import RuntimeSettings
    return RuntimeSettings(data_dir=tmp_path / "runtime", testing=True)
