"""Small authenticated envelope for voiceprint templates.

The database stores only encrypted embedding bytes.  The key is supplied by
``VOICEPRINT_TEMPLATE_KEY`` (or a process-local development key); raw audio is
never written by this module.
"""
from __future__ import annotations
import base64, hashlib, hmac, os, secrets

_PREFIX = b"VP1"

def _key() -> bytes:
    value = os.getenv("VOICEPRINT_TEMPLATE_KEY", "").encode("utf-8")
    return hashlib.sha256(value or b"local-development-voiceprint-key").digest()

def seal(values: list[float]) -> str:
    plain = (",".join(f"{float(v):.9g}" for v in values)).encode()
    nonce = secrets.token_bytes(16)
    stream = hashlib.sha256(_key() + nonce).digest()
    cipher = bytes(byte ^ stream[i % len(stream)] for i, byte in enumerate(plain))
    body = _PREFIX + nonce + cipher
    return base64.urlsafe_b64encode(body + hmac.new(_key(), body, hashlib.sha256).digest()).decode()

def open_sealed(token: str) -> list[float]:
    raw = base64.urlsafe_b64decode(token.encode())
    if len(raw) < 3 + 16 + 32 or raw[:3] != _PREFIX: raise ValueError("invalid_template")
    body, tag = raw[:-32], raw[-32:]
    if not hmac.compare_digest(hmac.new(_key(), body, hashlib.sha256).digest(), tag): raise ValueError("invalid_template")
    nonce, cipher = body[3:19], body[19:]
    stream = hashlib.sha256(_key() + nonce).digest()
    plain = bytes(byte ^ stream[i % len(stream)] for i, byte in enumerate(cipher))
    return [float(x) for x in plain.decode().split(",") if x]
