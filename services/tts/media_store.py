from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import secrets
import time


@dataclass
class MediaItem:
    ref: str
    path: Path
    session_id: str
    expires_at: float


class MediaStore:
    def __init__(self, root: str | Path, ttl_seconds: int = 600):
        self.root = Path(root); self.root.mkdir(parents=True, exist_ok=True)
        self.ttl_seconds = ttl_seconds
        self.items: dict[str, MediaItem] = {}

    def put(self, data: bytes, session_id: str, suffix: str = ".pcm") -> MediaItem:
        ref = secrets.token_urlsafe(18)
        path = self.root / f"{ref}{suffix}"
        path.write_bytes(data)
        item = MediaItem(ref, path, session_id, time.time() + self.ttl_seconds)
        self.items[ref] = item
        return item

    def get(self, ref: str, session_id: str) -> bytes:
        item = self.items.get(ref)
        if not item or item.session_id != session_id or item.expires_at < time.time():
            raise FileNotFoundError(ref)
        return item.path.read_bytes()

    def revoke(self, ref: str) -> None:
        item = self.items.pop(ref, None)
        if item:
            item.path.unlink(missing_ok=True)

    def cleanup(self) -> int:
        now = time.time(); removed = 0
        for ref, item in list(self.items.items()):
            if item.expires_at < now:
                self.revoke(ref); removed += 1
        return removed
