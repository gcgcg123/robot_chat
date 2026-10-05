"""LAN address discovery for OTA and for the dashboard's onboarding page.

The ESP must be told a *routable* address: ``localhost``/``127.0.0.1`` makes the
device try to connect to itself, which is the single most common reason a
correctly flashed board never shows up.  So the dashboard shows every LAN IPv4
address this host has, and OTA picks the primary one.
"""
from __future__ import annotations

import socket


def _is_usable(address: str) -> bool:
    if not address or address.startswith("127.") or address.startswith("169.254."):
        return False
    parts = address.split(".")
    if len(parts) != 4:
        return False
    return all(part.isdigit() and 0 <= int(part) <= 255 for part in parts)


def primary_lan_ip() -> str:
    """Best-effort LAN IPv4 for this host.

    The UDP "connect" performs no handshake and sends no packet -- it only asks
    the routing table which local address would be used to reach a public
    address, which is exactly the interface a device on the same LAN can reach.
    """
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("8.8.8.8", 80))
        address = probe.getsockname()[0]
        if _is_usable(address):
            return address
    except OSError:
        pass
    finally:
        probe.close()
    try:
        for address in socket.gethostbyname_ex(socket.gethostname())[2]:
            if _is_usable(address):
                return address
    except OSError:
        pass
    return "127.0.0.1"


def lan_ipv4_addresses() -> list[str]:
    """All usable LAN IPv4 addresses, primary first (multi-NIC machines exist)."""
    found: list[str] = []
    primary = primary_lan_ip()
    if _is_usable(primary):
        found.append(primary)
    try:
        for address in socket.gethostbyname_ex(socket.gethostname())[2]:
            if _is_usable(address) and address not in found:
                found.append(address)
    except OSError:
        pass
    return found or (["127.0.0.1"] if not found else found)


def build_urls(*, port: int, esp_path: str, ota_path: str = "/xiaozhi/ota/", base_url: str = "") -> dict:
    """Return the addresses the device needs, plus whether they are reachable.

    ``reachable_locally`` is False when every candidate is a loopback address --
    the case where the operator must switch to a LAN bind or fix the firewall.
    """
    addresses = lan_ipv4_addresses()
    primary = addresses[0]
    if base_url:
        origin = base_url.rstrip("/")
        # ``IOT_OTA_BASE_URL`` names an origin, so derive both schemes from it:
        # ``websocket_url`` travels to the device as its WebSocket endpoint and
        # must be ws/wss, while ``ota_url``/firmware download are plain HTTP.
        # Accepting either spelling means a wrong-scheme config cannot silently
        # hand the device an undialable address.
        scheme, _, rest = origin.partition("://")
        scheme = scheme.lower()
        if scheme in {"http", "https"}:
            ws_origin, http_origin = f"{'wss' if scheme == 'https' else 'ws'}://{rest}", origin
        elif scheme in {"ws", "wss"}:
            ws_origin, http_origin = origin, f"{'https' if scheme == 'wss' else 'http'}://{rest}"
        else:  # no recognisable scheme: pass through, the operator owns the value
            ws_origin = http_origin = origin
        return {
            "addresses": addresses,
            "primary": primary,
            "origin": http_origin,
            "websocket_url": f"{ws_origin}{esp_path}",
            "ota_url": f"{http_origin}{ota_path}",
            "reachable_locally": not http_origin.startswith(("http://127.", "http://localhost")),
        }
    origin = f"http://{primary}:{port}"
    return {
        "addresses": addresses,
        "primary": primary,
        "origin": origin,
        "websocket_url": f"ws://{primary}:{port}{esp_path}",
        "ota_url": f"{origin}{ota_path}",
        "reachable_locally": primary != "127.0.0.1",
    }
