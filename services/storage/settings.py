from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

# services/storage/settings.py -> services -> the checkout root.  Runtime data
# lives inside the checkout by default instead of under %LOCALAPPDATA%, so a
# copy of the project carries its own database, simulator token and backups.
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATA_DIR_NAME = "IoTGroup5"


def _resolve(value: str) -> Path:
    """Anchor a configured path to the project root when it is relative.

    The shipped `.env` template uses a relative path.  Resolving it against the
    current working directory meant that starting the service from a different
    folder silently opened a *different* (empty) database, so relative always
    means "relative to the checkout".
    """

    path = Path(value)
    return path if path.is_absolute() else (PROJECT_ROOT / path)


@dataclass(frozen=True)
class RuntimeSettings:
    data_dir: Path
    testing: bool = False
    database_override: Path | None = None
    session_ttl_seconds: int = 3600

    def __post_init__(self) -> None:
        object.__setattr__(self, "data_dir", Path(self.data_dir))
        if self.database_override is not None:
            object.__setattr__(self, "database_override", Path(self.database_override))

    @property
    def database_path(self) -> Path:
        if self.database_override is not None:
            return self.database_override
        return self.data_dir / "emotional_robot.sqlite3"

    @classmethod
    def from_env(cls) -> "RuntimeSettings":
        data_value = os.getenv("IOT_DATA_DIR", "").strip()
        legacy = os.getenv("DATABASE_PATH", "").strip()
        if data_value:
            data_dir = _resolve(data_value)
            override = None
        elif legacy:
            legacy_path = _resolve(legacy)
            data_dir = legacy_path.parent
            override = legacy_path
        else:
            data_dir = PROJECT_ROOT / DEFAULT_DATA_DIR_NAME
            override = None
        testing = os.getenv("IOT_TESTING", "").strip().lower() in {"1", "true", "yes", "on"}
        return cls(data_dir=data_dir, testing=testing, database_override=override)
