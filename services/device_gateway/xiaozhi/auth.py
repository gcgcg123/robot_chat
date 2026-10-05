"""Device-side authentication for the ESP endpoint.

The browser simulator authenticates with the admin session cookie; an ESP32 has
no cookie jar, so it presents its ``device-id`` and (optionally) a bearer token
derived from the shared secret.  The same derivation is used by the OTA handler
to hand the token to the device, so no per-device secret ever has to be stored.

Deliberately stateless: a device that knows the secret can always reconnect,
and rotating ``IOT_ESP_TOKEN_SECRET`` invalidates every device at once.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
from urllib.parse import parse_qs


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def device_token(device_id: str, secret: str) -> str:
    """Derive the bearer token for ``device_id``. Empty when no secret is set."""
    if not secret:
        return ""
    digest = hmac.new(secret.encode("utf-8"), f"device:{device_id}".encode("utf-8"), hashlib.sha256).digest()
    return _b64(digest)


def verify_device_token(device_id: str, token: str, secret: str) -> bool:
    expected = device_token(device_id, secret)
    if not expected or not token:
        return False
    return hmac.compare_digest(expected, token)


def extract_device_headers(headers, query: str = "") -> dict[str, str]:
    """Read ``device-id`` / ``client-id`` / ``authorization`` from headers or query.

    Firmware revisions differ in where they put the identity (header vs
    ``?device-id=``), and the upstream server accepts both, so both are read.
    """
    values = {key.lower(): str(value) for key, value in (headers or {}).items()}
    params = parse_qs(query or "")
    resolved = {
        "device_id": values.get("device-id", "").strip(),
        "client_id": values.get("client-id", "").strip(),
        "authorization": values.get("authorization", "").strip(),
    }
    for key, param in (("device_id", "device-id"), ("client_id", "client-id"), ("authorization", "authorization")):
        if not resolved[key] and params.get(param):
            resolved[key] = str(params[param][0]).strip()
    return resolved


def bearer_token(authorization: str) -> str:
    value = (authorization or "").strip()
    if value.lower().startswith("bearer "):
        return value[7:].strip()
    return value


class DeviceAuthResult:
    __slots__ = ("allowed", "reason", "verified")

    def __init__(self, allowed: bool, reason: str = "", verified: bool = False):
        self.allowed = allowed
        self.reason = reason
        self.verified = verified

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"DeviceAuthResult(allowed={self.allowed}, verified={self.verified}, reason={self.reason!r})"


def authorize_device(settings, *, device_id: str, authorization: str) -> DeviceAuthResult:
    """Decide whether an ESP connection may proceed.

    Rules, in order:

    1. No ``device-id`` -> rejected: without it the device cannot be attributed,
       and an unattributable audio stream is exactly what the privacy design
       forbids.
    2. Device is on ``IOT_ESP_ALLOWED_DEVICES`` -> allowed without a token
       (whitelist bypass, matching upstream semantics).
    3. Token requirement off -> allowed but marked *unverified*, so the dashboard
       can show that the connection was not authenticated.
    4. Token requirement on -> the bearer token must match the derived value.
    """
    if not device_id:
        return DeviceAuthResult(False, "device_id_required")
    if settings.allowed_devices and device_id in settings.allowed_devices:
        return DeviceAuthResult(True, "", True)
    if not settings.require_token:
        return DeviceAuthResult(True, "", False)
    if not settings.token_secret:
        # Fail closed: requiring a token without a secret would accept anything.
        return DeviceAuthResult(False, "token_secret_not_configured")
    if verify_device_token(device_id, bearer_token(authorization), settings.token_secret):
        return DeviceAuthResult(True, "", True)
    return DeviceAuthResult(False, "invalid_device_token")
