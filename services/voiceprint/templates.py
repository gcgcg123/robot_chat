from __future__ import annotations

import hashlib


def template_id(user_id: str, embedding: bytes) -> str:
    return hashlib.sha256(user_id.encode() + b":" + embedding).hexdigest()[:32]
