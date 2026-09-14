"""Copy a runtime SQLite database through the migration layer."""
from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

from services.storage.database import open_database
from services.storage.migrations import migrate


def migrate_runtime(source: str | Path, destination: str | Path) -> int:
    source, destination = Path(source), Path(destination)
    if not source.exists():
        raise FileNotFoundError(source)
    if destination.exists() and destination.stat().st_size > 0:
        raise FileExistsError(f"refusing to overwrite non-empty destination: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(source) as src, open_database(destination) as dst:
        src.backup(dst)
        version = migrate(dst)
    return version


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    print(f"source={args.source} destination={args.destination}")
    print(f"schema_version={migrate_runtime(args.source, args.destination)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

