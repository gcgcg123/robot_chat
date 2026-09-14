from __future__ import annotations

from typing import Any


def normalize_heartbeat(payload: dict[str, Any]) -> dict[str, Any]:
    device_id = str(payload.get("device_id", "")).strip()
    if not device_id or len(device_id) > 128:
        raise ValueError("device_id is required and must be at most 128 characters")
    raw_caps = payload.get("capabilities") or []
    capabilities = list(dict.fromkeys(str(cap).strip() for cap in raw_caps if str(cap).strip()))[:32]
    return {
        "device_id": device_id,
        "user_id": str(payload["user_id"]).strip() if payload.get("user_id") else None,
        "firmware": str(payload["firmware"]).strip()[:64] if payload.get("firmware") else None,
        "capabilities": capabilities,
        "is_simulator": bool(payload.get("is_simulator", False)),
        "ip": str(payload["ip"]).strip()[:64] if payload.get("ip") else None,
    }
