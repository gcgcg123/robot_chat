"""Create a consistent SQLite backup without copying a live database file."""
from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path


def backup_database(source: str | Path, destination: str | Path) -> None:
    source, destination = Path(source), Path(destination)
    if not source.exists():
        raise FileNotFoundError(source)
    if destination.exists() and destination.stat().st_size > 0:
        raise FileExistsError(f"refusing to overwrite non-empty destination: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(source) as src, sqlite3.connect(destination) as dst:
        src.backup(dst)
        if dst.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("backup_integrity_failed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    backup_database(args.source, args.destination)
    print(f"backup source={args.source} destination={args.destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

