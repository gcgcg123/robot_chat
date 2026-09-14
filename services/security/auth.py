from __future__ import annotations

import hashlib
import secrets
import time
import uuid
from typing import Any

from fastapi import HTTPException, Request, Response


SESSION_COOKIE = "iot_session"
CSRF_HEADER = "X-CSRF-Token"


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_session(conn, actor_id: str, role: str, device_id: str | None = None, ttl_seconds: int = 3600) -> str:
    token = secrets.token_urlsafe(32)
    csrf = secrets.token_urlsafe(24)
    now = time.time()
    conn.execute(
        "INSERT INTO sessions(session_id,token_hash,actor_id,role,device_id,csrf_token,created_at,expires_at) VALUES(?,?,?,?,?,?,?,?)",
        (str(uuid.uuid4()), _hash(token), actor_id, role, device_id, csrf, now, now + ttl_seconds),
    )
    conn.commit()
    return token


def _session(request: Request, conn):
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        authorization = request.headers.get("authorization", "")
        if authorization.lower().startswith("bearer "):
            token = authorization[7:].strip()
    if not token:
        raise HTTPException(status_code=401, detail="authentication_required")
    row = conn.execute(
        "SELECT * FROM sessions WHERE token_hash=? AND revoked_at IS NULL AND expires_at>?",
        (_hash(token), time.time()),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=401, detail="invalid_session")
    return row


def _connection(request: Request, conn):
    if conn is not None:
        return conn
    getter = getattr(request.app.state, "get_db", None)
    if getter is None:
        raise RuntimeError("database dependency is not configured")
    return getter()


def require_admin(request: Request, conn=None) -> str:
    row = _session(request, _connection(request, conn))
    if row["role"] != "admin":
        raise HTTPException(status_code=403, detail="admin_required")
    return str(row["actor_id"])


def require_device(request: Request, conn_or_device, device_id: str | None = None) -> str:
    """Require a device-bound session; accepts (request, device_id) or (request, conn, device_id)."""
    if device_id is None:
        conn, device_id = None, str(conn_or_device)
    else:
        conn = conn_or_device
    row = _session(request, _connection(request, conn))
    if row["role"] != "device" or row["device_id"] != device_id:
        raise HTTPException(status_code=403, detail="device_not_authorized")
    return str(row["actor_id"])


def require_csrf(request: Request, conn=None):
    row = _session(request, _connection(request, conn))
    if not request.cookies.get(SESSION_COOKIE) and request.headers.get("authorization", "").lower().startswith("bearer "):
        return row
    supplied = request.headers.get(CSRF_HEADER, "")
    if not supplied or not secrets.compare_digest(supplied, row["csrf_token"]):
        raise HTTPException(status_code=403, detail="csrf_required")
    origin = request.headers.get("origin")
    if origin:
        expected = f"{request.url.scheme}://{request.url.netloc}"
        if origin.rstrip("/") != expected.rstrip("/"):
            raise HTTPException(status_code=403, detail="origin_not_allowed")
    return row


def set_session_cookie(response: Response, token: str, request: Request | None = None, max_age: int = 3600) -> None:
    secure = bool(request and request.url.scheme == "https")
    response.set_cookie(SESSION_COOKIE, token, httponly=True, samesite="lax", secure=secure, max_age=max_age)
