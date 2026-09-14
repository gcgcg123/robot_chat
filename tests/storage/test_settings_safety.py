from services.storage.settings import RuntimeSettings


def test_default_runtime_is_local_appdata(monkeypatch):
    monkeypatch.delenv("IOT_DATA_DIR", raising=False)
    monkeypatch.delenv("DATABASE_PATH", raising=False)
    settings = RuntimeSettings.from_env()
    assert settings.data_dir == settings.data_dir.parent / "IoTGroup5"
    assert "OneDrive" not in str(settings.data_dir)

