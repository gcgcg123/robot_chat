from fastapi.testclient import TestClient

from services.dialogue.app import create_app
from services.storage.settings import RuntimeSettings


def test_app_uses_only_explicit_test_runtime(tmp_path):
    settings = RuntimeSettings(data_dir=tmp_path / "isolated", testing=True)
    app = create_app(settings=settings, providers={})
    assert not settings.database_path.exists()
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        assert client.get("/api/users").status_code == 401
    assert settings.database_path.exists()


def test_runtime_settings_prefers_iot_data_dir_over_legacy_database_path(monkeypatch, tmp_path):
    data_dir = tmp_path / "runtime"
    monkeypatch.setenv("IOT_DATA_DIR", str(data_dir))
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "legacy.sqlite3"))
    settings = RuntimeSettings.from_env()
    assert settings.database_path == data_dir / "emotional_robot.sqlite3"

