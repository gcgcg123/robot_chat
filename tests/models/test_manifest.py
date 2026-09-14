import hashlib

from services.models.manifest import ModelManifest, hash_files


def test_manifest_defaults_to_unverified_and_round_trips(tmp_path):
    model_file = tmp_path / "model.bin"
    model_file.write_bytes(b"weights")
    digest = hashlib.sha256(b"weights").hexdigest()
    manifest = ModelManifest(
        name="whisper-large-v3-turbo-ct2",
        source="local",
        revision="test",
        sha256_files={"model.bin": digest},
    )

    assert manifest.verification_status == "unverified"
    assert hash_files(tmp_path, ["model.bin"]) == {"model.bin": digest}
    restored = ModelManifest.from_dict(manifest.to_dict())
    assert restored == manifest


def test_manifest_verify_reports_mismatch(tmp_path):
    (tmp_path / "model.bin").write_bytes(b"changed")
    manifest = ModelManifest(name="model", sha256_files={"model.bin": "wrong"})

    result = manifest.verify(tmp_path)

    assert result["ok"] is False
    assert result["missing"] == []
    assert result["mismatched"] == ["model.bin"]

