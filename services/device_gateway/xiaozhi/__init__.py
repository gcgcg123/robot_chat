"""ESP32 access layer for the emotional companion robot.

Speaks the xiaozhi (小智) wire protocol so stock ESP32 firmware can drive this
project's own dialogue pipeline.  The upstream server is a *reference* for the
protocol, not a dependency: nothing here imports it.

Layout:

``protocol``    text messages, emotion vocabulary
``opus_codec``  Opus framing plus the Windows ``opus.dll`` bootstrap
``vad``         server-side endpoint detection
``session``     one device session: handshake -> audio in -> pipeline -> audio out
``ota``         address/token delivery and firmware inventory
``store``       devices / device_sessions / device_commands / ota_requests
``router``      FastAPI routes (device-facing and admin-facing)
``registry``    live session snapshots + observer fan-out + command bus
"""
from __future__ import annotations

__all__ = ["config", "context", "network", "opus_codec", "ota", "pcm", "protocol", "registry", "router", "session", "store", "vad"]
