from services.storage import settings as settings_module
from services.storage.settings import RuntimeSettings


def test_default_runtime_belongs_to_checkout(monkeypatch, tmp_path):
    monkeypatch.delenv("IOT_DATA_DIR", raising=False)
    monkeypatch.delenv("DATABASE_PATH", raising=False)
    project = tmp_path / "checkout"
    monkeypatch.setattr(settings_module, "PROJECT_ROOT", project)
    settings = RuntimeSettings.from_env()
    assert settings.database_path == project / "IoTGroup5" / "emotional_robot.sqlite3"


def test_relative_runtime_path_does_not_change_with_working_directory(monkeypatch, tmp_path):
    project = tmp_path / "checkout"
    monkeypatch.setattr(settings_module, "PROJECT_ROOT", project)
    monkeypatch.setenv("IOT_DATA_DIR", "runtime")
    monkeypatch.setenv("DATABASE_PATH", "ignored.sqlite3")
    monkeypatch.chdir(tmp_path)
    settings = RuntimeSettings.from_env()
    assert settings.database_path == project / "runtime" / "emotional_robot.sqlite3"


def test_legacy_relative_database_path_resolves_inside_checkout(monkeypatch, tmp_path):
    project = tmp_path / "checkout"
    monkeypatch.setattr(settings_module, "PROJECT_ROOT", project)
    monkeypatch.delenv("IOT_DATA_DIR", raising=False)
    monkeypatch.setenv("DATABASE_PATH", "data/custom.sqlite3")
    monkeypatch.chdir(tmp_path)
    settings = RuntimeSettings.from_env()
    assert settings.database_path == project / "data" / "custom.sqlite3"
