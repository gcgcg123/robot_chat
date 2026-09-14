from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_zip_bootstrap_and_docker_assets_are_present_and_credentials_are_ignored():
    assert (ROOT / "一鍵安裝並啟動.bat").exists()
    assert (ROOT / "scripts" / "bootstrap-project.ps1").exists()
    assert (ROOT / "Dockerfile").exists()
    assert (ROOT / "docker-compose.yml").exists()
    assert (ROOT / ".env.docker.example").exists()
    ignored = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert ".env.*" in ignored
    assert "models/**" in ignored


def test_docker_compose_mounts_runtime_data_and_models_read_only():
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert "iotgroup5-data:/var/lib/iotgroup5" in compose
    assert "./models:/models:ro" in compose
    assert "127.0.0.1:8080:8080" in compose
