from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


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
            data_dir = Path(data_value)
            override = None
        elif legacy:
            legacy_path = Path(legacy)
            data_dir = legacy_path.parent
            override = legacy_path
        else:
            local_appdata = os.getenv("LOCALAPPDATA", "").strip()
            data_dir = (Path(local_appdata) if local_appdata else Path.home() / "AppData" / "Local") / "IoTGroup5"
            override = None
        testing = os.getenv("IOT_TESTING", "").strip().lower() in {"1", "true", "yes", "on"}
        return cls(data_dir=data_dir, testing=testing, database_override=override)
