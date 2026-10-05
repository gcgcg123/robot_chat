"""OTA endpoints: the device's first contact with this service.

A freshly flashed board asks ``POST /xiaozhi/ota/`` where to connect.  Two
things matter and both are easy to get wrong:

* the address handed back must be a **LAN** address (``127.0.0.1`` makes the
  device dial itself), which is why :mod:`network` exists;
* the token must be derived from the same secret the WebSocket endpoint
  verifies, otherwise enabling ``IOT_ESP_REQUIRE_TOKEN`` locks every device out.

Firmware files follow upstream's ``{model}_{version}.bin`` naming so an existing
firmware drop-in directory keeps working.
"""
from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any

from services.device_gateway.xiaozhi import network
from services.device_gateway.xiaozhi.auth import device_token
from services.device_gateway.xiaozhi.config import EspSettings

FIRMWARE_PATTERN = re.compile(r"^(.+?)_([0-9][A-Za-z0-9._-]*)\.bin$")
SAFE_FILENAME = re.compile(r"^[A-Za-z0-9._-]+\.bin$")


def _version_tuple(value: str) -> tuple[int, ...]:
    parts = re.findall(r"\d+", value or "")
    return tuple(int(part) for part in parts) if parts else (0,)


def is_newer(candidate: str, current: str) -> bool:
    left, right = _version_tuple(candidate), _version_tuple(current)
    length = max(len(left), len(right))
    for index in range(length):
        a = left[index] if index < len(left) else 0
        b = right[index] if index < len(right) else 0
        if a != b:
            return a > b
    return False


def firmware_inventory(bin_dir: Path) -> dict[str, list[tuple[str, str]]]:
    """Map ``model -> [(version, filename), ...]`` sorted newest first."""
    inventory: dict[str, list[tuple[str, str]]] = {}
    if not bin_dir.is_dir():
        return inventory
    for path in sorted(bin_dir.glob("*.bin")):
        match = FIRMWARE_PATTERN.match(path.name)
        if not match:
            continue
        inventory.setdefault(match.group(1), []).append((match.group(2), path.name))
    for items in inventory.values():
        items.sort(key=lambda item: _version_tuple(item[0]), reverse=True)
    return inventory


def list_firmware(bin_dir: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for model, items in firmware_inventory(bin_dir).items():
        for version, filename in items:
            path = bin_dir / filename
            rows.append(
                {
                    "model": model,
                    "version": version,
                    "filename": filename,
                    "size_bytes": path.stat().st_size if path.is_file() else 0,
                    "modified_at": path.stat().st_mtime if path.is_file() else None,
                }
            )
    rows.sort(key=lambda row: (row["model"], _version_tuple(row["version"])), reverse=True)
    return rows


def select_firmware(bin_dir: Path, model: str, current_version: str) -> tuple[str, str]:
    """Return ``(version, filename)`` of a newer build, or ``("", "")``."""
    for version, filename in firmware_inventory(bin_dir).get(model, []):
        if is_newer(version, current_version or "0.0.0"):
            return version, filename
    return "", ""


def resolve_firmware_path(bin_dir: Path, filename: str) -> Path | None:
    """Resolve a download request, refusing anything outside ``bin_dir``."""
    if not filename or not SAFE_FILENAME.match(filename):
        return None
    root = bin_dir.resolve()
    candidate = (root / filename).resolve()
    if candidate.parent != root or not candidate.is_file():
        return None
    return candidate


def build_ota_payload(
    settings: EspSettings,
    *,
    device_id: str,
    client_id: str = "",
    board_model: str = "",
    device_version: str = "",
    port: int = 8080,
    timezone_offset: int = 8,
    now: float | None = None,
) -> dict[str, Any]:
    """Build the OTA response body (pure function so it can be unit tested)."""
    now = time.time() if now is None else now
    model = board_model or "default"
    version, filename = select_firmware(settings.ota_bin_dir, model, device_version)
    urls = network.build_urls(port=port, esp_path=settings.path, base_url=settings.ota_base_url)
    payload: dict[str, Any] = {
        "server_time": {
            "timestamp": int(round(now * 1000)),
            "timezone_offset": timezone_offset * 60,
        },
        "firmware": {
            "version": version or device_version or "0.0.0",
            "url": f"{urls['origin']}/xiaozhi/ota/download/{filename}" if (filename and settings.ota_enabled) else "",
        },
        "websocket": {
            "url": urls["websocket_url"],
            "token": device_token(device_id, settings.token_secret) if settings.require_token else "",
        },
    }
    return payload


def build_ota_probe(settings: EspSettings, *, port: int = 8080) -> dict[str, Any]:
    """Health view of the OTA surface for the dashboard."""
    urls = network.build_urls(port=port, esp_path=settings.path, base_url=settings.ota_base_url)
    firmware = list_firmware(settings.ota_bin_dir) if settings.ota_enabled else []
    return {
        "ota_enabled": settings.ota_enabled,
        "ota_url": urls["ota_url"],
        "websocket_url": urls["websocket_url"],
        "reachable_locally": urls["reachable_locally"],
        "addresses": urls["addresses"],
        "primary_address": urls["primary"],
        "firmware_count": len(firmware),
    }
