# Device Protocol v1 (P1 loopback)

P1 uses MQTT for registration, heartbeat, capability and control metadata. WebSocket is the interactive event channel for the PC simulator (turn, subtitle, TTS segment and playback acknowledgement). UDP is not the production audio path yet: the loopback adapter only validates a framed payload with `IOT1`, UTF-8 session id, big-endian sequence and payload bytes.

`decode_frame(..., expected_session=...)` rejects malformed and wrong-session packets. `LoopbackPeer` drops duplicate or stale sequence numbers. ESP codec, Opus framing, encryption, broker ACLs and Wi-Fi behaviour are P2 acceptance items.

Reference: xiaozhi server/gateway MQTT+UDP design in `upstream/xiaozhi-esp32-server`; this project does not claim wire compatibility until fixtures are captured and reviewed.
