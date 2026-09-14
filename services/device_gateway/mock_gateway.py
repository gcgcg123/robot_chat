"""Protocol placeholder: maps future MQTT/UDP/WS device events to /api/chat."""
from dataclasses import dataclass

@dataclass
class DeviceEvent:
    device_id: str
    user_id: str
    audio_path: str | None = None
    text: str | None = None

def to_chat_payload(event: DeviceEvent) -> dict:
    if not event.text:
        raise ValueError('mock gateway currently accepts text replay; audio goes through /api/transcribe')
    return {'device_id': event.device_id, 'user_id': event.user_id, 'text': event.text}

