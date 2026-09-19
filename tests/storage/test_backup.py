import importlib.util
import sqlite3


def _module():
    spec = importlib.util.spec_from_file_location("backup_runtime", "scripts/backup-runtime.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_backup_uses_sqlite_backup_and_refuses_nonempty_destination(tmp_path):
    source = tmp_path / "source.sqlite3"
    destination = tmp_path / "backup.sqlite3"
    with sqlite3.connect(source) as conn:
        conn.execute("CREATE TABLE data (value TEXT)")
        conn.execute("INSERT INTO data VALUES ('preserved')")
    _module().backup_database(source, destination)
    with sqlite3.connect(destination) as conn:
        assert conn.execute("SELECT value FROM data").fetchone()[0] == "preserved"
    destination.write_bytes(b"existing")
    try:
        _module().backup_database(source, destination)
    except FileExistsError:
        pass
    else:
        raise AssertionError("non-empty destination should be rejected")

