"""Opus codec for the xiaozhi ESP transport.

``opuslib_next`` finds libopus through ``ctypes.util.find_library("opus")``.
On Windows that function unconditionally returns ``None`` (CPython implements
it for POSIX only), so the import fails even when ``opus.dll`` sits right next
to the interpreter.  This module therefore resolves the DLL first and patches
``find_library`` for the duration of the import, restoring it afterwards.

DLL search order:

1. ``IOT_OPUS_DLL`` -- explicit override, always wins
2. ``<project>/vendor/opus/opus.dll`` -- vendored copy
3. ``<pyogg>/opus.dll`` -- ``pip install pyogg`` ships one (no import needed)
4. ``opus.dll`` on ``PATH``

Nothing is ever silently degraded: :func:`opus_status` reports whether the
codec is usable and, when it is not, why.
"""
from __future__ import annotations

import ctypes
import ctypes.util
import importlib
import importlib.util
import os
import sys
import threading
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
DLL_FILENAME = "opus.dll"
VENDOR_DLL = ROOT / "vendor" / "opus" / DLL_FILENAME
PACKAGE = "opuslib_next"

#: Frames the ESP firmware streams; 60 ms keeps the packet count low without
#: adding noticeable latency (matches the upstream welcome message).
DEFAULT_FRAME_DURATION_MS = 60
DEFAULT_UPLINK_SAMPLE_RATE = 16000
DEFAULT_DOWNLINK_SAMPLE_RATE = 24000

_import_lock = threading.Lock()
_import_error: str | None = None


class OpusUnavailable(RuntimeError):
    """Raised when libopus or ``opuslib_next`` cannot be made available."""


def frame_samples(sample_rate: int, frame_duration_ms: int) -> int:
    return sample_rate * frame_duration_ms // 1000


def _path_lookup() -> str | None:
    for entry in os.environ.get("PATH", "").split(os.pathsep):
        if not entry:
            continue
        candidate = Path(entry) / DLL_FILENAME
        if candidate.is_file():
            return str(candidate)
    return None


@lru_cache(maxsize=1)
def locate_opus_dll() -> str | None:
    """Return the ``opus.dll`` path, or ``None`` on non-Windows platforms."""
    if sys.platform != "win32":
        return None
    override = os.environ.get("IOT_OPUS_DLL", "").strip()
    if override and Path(override).is_file():
        return str(Path(override).resolve())
    if VENDOR_DLL.is_file():
        return str(VENDOR_DLL)
    # PyOgg vendors opus.dll in its wheel; only the file is used, never the package.
    try:
        spec = importlib.util.find_spec("pyogg")
    except (ImportError, ValueError):
        spec = None
    if spec is not None and spec.origin:
        candidate = Path(spec.origin).parent / DLL_FILENAME
        if candidate.is_file():
            return str(candidate)
    return _path_lookup()


def _clear_partial_import() -> None:
    for name in [key for key in sys.modules if key == PACKAGE or key.startswith(PACKAGE + ".")]:
        sys.modules.pop(name, None)


def _import_opuslib():
    """Import ``opuslib_next``, retrying once with the DLL path patched in."""
    global _import_error
    with _import_lock:
        if PACKAGE in sys.modules:
            return sys.modules[PACKAGE]
        try:
            return importlib.import_module(PACKAGE)
        except Exception as first_error:  # noqa: BLE001 - reported verbatim below
            _clear_partial_import()
            dll = locate_opus_dll()
            if dll is None:
                _import_error = (
                    "opus_not_available_on_windows: install it with "
                    "`pip install pyogg`, or drop opus.dll into vendor/opus/, "
                    "or point IOT_OPUS_DLL at it "
                    f"(import failed with: {first_error})"
                    if sys.platform == "win32"
                    else f"opuslib_next import failed: {first_error}"
                )
                raise OpusUnavailable(_import_error) from first_error
            if hasattr(os, "add_dll_directory"):
                try:
                    os.add_dll_directory(str(Path(dll).parent))
                except OSError:
                    pass
            original = ctypes.util.find_library
            ctypes.util.find_library = lambda name, _o=original, _p=dll: _p if name == "opus" else _o(name)
            try:
                module = importlib.import_module(PACKAGE)
            except Exception as second_error:  # noqa: BLE001
                _clear_partial_import()
                _import_error = f"opus_dll_rejected:{dll}:{second_error}"
                raise OpusUnavailable(_import_error) from second_error
            finally:
                ctypes.util.find_library = original
            _import_error = None
            return module


@lru_cache(maxsize=1)
def opus_status() -> dict:
    """Report whether Opus encoding/decoding is usable, and where the DLL came from."""
    try:
        module = _import_opuslib()
    except OpusUnavailable as exc:
        return {"available": False, "dll": None, "reason": str(exc)}
    return {"available": True, "dll": locate_opus_dll(), "reason": "", "module": getattr(module, "__name__", PACKAGE)}


def is_available() -> bool:
    return bool(opus_status()["available"])


class OpusDecoder:
    """Uplink decoder: the ESP sends 16 kHz mono Opus packets.

    Not thread-safe -- one instance per session.  ``decode`` accepts any frame
    size the device chooses and returns however many samples that packet held,
    so a firmware sending 20 ms frames works without configuration.
    """

    def __init__(self, sample_rate: int = DEFAULT_UPLINK_SAMPLE_RATE, frame_duration_ms: int = DEFAULT_FRAME_DURATION_MS):
        module = _import_opuslib()
        self.sample_rate = sample_rate
        self.frame_samples = frame_samples(sample_rate, frame_duration_ms)
        self._decoder = module.Decoder(sample_rate, 1)

    def decode(self, packet: bytes) -> bytes:
        if not packet:
            return b""
        try:
            return self._decoder.decode(packet, self.frame_samples)
        except Exception as exc:  # noqa: BLE001 - a corrupt packet must not kill the session
            raise ValueError(f"opus_decode_failed:{exc}") from exc


class OpusEncoder:
    """Downlink encoder: TTS PCM16 -> Opus packets the ESP can play.

    Not thread-safe -- one instance per session.  ``encode`` returns whole
    60 ms frames; a trailing partial frame is zero-padded, which is what every
    Opus encoder in this ecosystem does and is inaudible at the end of a
    sentence.
    """

    def __init__(self, sample_rate: int = DEFAULT_DOWNLINK_SAMPLE_RATE, frame_duration_ms: int = DEFAULT_FRAME_DURATION_MS):
        module = _import_opuslib()
        constants = importlib.import_module(f"{PACKAGE}.constants")
        self.sample_rate = sample_rate
        self.frame_duration_ms = frame_duration_ms
        self.frame_samples = frame_samples(sample_rate, frame_duration_ms)
        self._frame_bytes = self.frame_samples * 2
        self._encoder = module.Encoder(sample_rate, 1, constants.APPLICATION_VOIP)

    def encode(self, pcm16: bytes) -> list[bytes]:
        """Split PCM16 into Opus packets of exactly ``frame_samples`` samples."""
        if not pcm16:
            return []
        packets: list[bytes] = []
        for offset in range(0, len(pcm16), self._frame_bytes):
            chunk = pcm16[offset:offset + self._frame_bytes]
            if len(chunk) < self._frame_bytes:
                chunk = chunk + b"\x00" * (self._frame_bytes - len(chunk))
            packets.append(self._encoder.encode(chunk, self.frame_samples))
        return packets
