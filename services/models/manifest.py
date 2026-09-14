from __future__ import annotations

from dataclasses import dataclass, asdict, field
from pathlib import Path
import hashlib


def hash_files(root: str | Path, names: list[str]) -> dict[str, str]:
    return {name: hashlib.sha256((Path(root) / name).read_bytes()).hexdigest() for name in names}


@dataclass
class ModelManifest:
    name: str
    source: str = ""
    revision: str = ""
    license: str = ""
    sha256_files: dict[str, str] = field(default_factory=dict)
    package_versions: dict[str, str] = field(default_factory=dict)
    device: str | None = None
    compute_type: str | None = None
    verified_at: str | None = None
    verification_status: str = "unverified"
    def to_dict(self): return asdict(self)
    @classmethod
    def from_dict(cls, value): return cls(**value)
    def verify(self, root: str | Path):
        missing, mismatched = [], []
        for name, expected in self.sha256_files.items():
            path = Path(root) / name
            if not path.exists(): missing.append(name)
            elif hashlib.sha256(path.read_bytes()).hexdigest() != expected: mismatched.append(name)
        ok = not missing and not mismatched
        return {"ok": ok, "missing": missing, "mismatched": mismatched}
