from __future__ import annotations

from pathlib import Path
import os


ROOT = Path(__file__).resolve().parents[2]
ROLE_PATH = ROOT / "config" / "roles" / "elderly_companion.md"
DEFAULT_ROLE = (
    "你是老年人之友。先回应感受，再用简洁、尊重、容易理解的中文回答；"
    "不作医疗诊断，不泄露其他用户资料。"
)


def load_role_prompt() -> str:
    """Load the trusted, repository-owned role instructions without network access."""
    configured = os.getenv("IOT_ROLE_FILE", "").strip()
    role_path = Path(configured)
    if configured and not role_path.is_absolute():
        role_path = ROOT / role_path
    if not configured:
        role_path = ROLE_PATH
    try:
        text = role_path.read_text(encoding="utf-8").strip()
    except OSError:
        return DEFAULT_ROLE
    return text or DEFAULT_ROLE
