"""What did the board's voiceprint actually decide? (read-only)

Written for the calibration step the README asks for: after enrolling someone on the device, let
that person and someone else each say a few sentences, then read this.  It prints the per-utterance
evidence the turn recorded (score, runner-up, streak, continuity, audio quality) instead of only the
verdict, because on 2026-10-06 the owner (0.601/0.6453) and another person in the room (0.5965)
landed in the *same* band -- a single threshold cannot separate those, and the only way to see that
is to look at both groups side by side.

    python scripts\\voiceprint-report.py                 # last 40 decisions on every device
    python scripts\\voiceprint-report.py --device 58:e6:c5:71:4b:f4 --limit 20
    python scripts\\voiceprint-report.py --samples        # also compare the stored enrollment samples
"""
from __future__ import annotations

import argparse
import datetime
import json
import math
import os
import sqlite3
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
# Run directly (python scripts\voiceprint-report.py): the project root has to be importable for
# ``services.voiceprint.storage``, exactly as the launcher does it for the service.
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

#: Reported in the header, and the only way a developer's shell sees them: the values live in .env.
WATCHED = (
    "VOICEPRINT_THRESHOLD",
    "VOICEPRINT_MIN_MARGIN",
    "IOT_ESP_VOICEPRINT_THRESHOLD",
    "IOT_ESP_VOICEPRINT_CONFIRM_TURNS",
    "IOT_ESP_VOICEPRINT_CONTINUITY_FLOOR",
    "IOT_ESP_ENROLL_SAMPLES",
    "IOT_ESP_ENROLL_MIN_SIMILARITY",
    "IOT_MEMORY_REQUIRE_IDENTITY",
)


def env_file() -> dict[str, str]:
    """``.env`` as plain ``KEY: VALUE``, because the service reads it but this shell does not."""

    path = ROOT / ".env"
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        values[key.strip()] = value.strip()
    return values


def default_database() -> Path:
    configured = os.environ.get("DATABASE_PATH", "").strip()
    if configured:
        candidate = Path(configured)
        if not candidate.is_absolute():
            candidate = ROOT / candidate
        if candidate.is_file():
            return candidate
    return ROOT / "IoTGroup5" / "emotional_robot.sqlite3"


def cosine(a: list[float], b: list[float]) -> float:
    denominator = math.sqrt(sum(x * x for x in a) * sum(x * x for x in b)) or 1.0
    return sum(x * y for x, y in zip(a, b)) / denominator


def stamp(value: float) -> str:
    try:
        return datetime.datetime.fromtimestamp(float(value)).strftime("%m-%d %H:%M:%S")
    except (TypeError, ValueError, OSError):
        return "?"


def show_decisions(conn: sqlite3.Connection, device: str, limit: int) -> None:
    query = (
        "SELECT device_id, created_at, payload_json FROM device_events"
        " WHERE event_type='speaker_identified'"
    )
    params: list[object] = []
    if device:
        query += " AND device_id=?"
        params.append(device)
    query += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)
    rows = conn.execute(query, params).fetchall()
    print(f"== last {len(rows)} speaker decisions" + (f" on {device}" if device else "") + " ==")
    if not rows:
        print("  (none recorded yet)")
        return
    print(
        "  time             device             decision  verified  best    second  user"
        "                runner-up           streak/need  continuity  ms    rms   threshold"
    )
    for row in reversed(rows):
        payload = json.loads(row["payload_json"])
        streak = payload.get("streak")
        need = payload.get("confirm_turns")
        streak_text = f"{streak}/{need}" if streak is not None and need is not None else "—"
        print(
            f"  {stamp(row['created_at'])}  {str(row['device_id'])[:17]:17} "
            f"{str(payload.get('decision')):9} {str(payload.get('verified') if payload.get('verified') is not None else '—'):8} "
            f"{_number(payload.get('best_score')):7} {_number(payload.get('second_score')):7} "
            f"{str(payload.get('user_id'))[:19]:19} {str(payload.get('second_user_id') or '—')[:19]:19} "
            f"{streak_text:>10} "
            f"{_number(payload.get('continuity_score')):>10} {str(payload.get('audio_ms') or '—'):>6} "
            f"{str(payload.get('audio_rms') or '—'):>5} {_number(payload.get('threshold')):>9}"
        )

    print()
    print("== score bands per account (only utterances that named someone) ==")
    bands: dict[str, list[float]] = {}
    for row in rows:
        payload = json.loads(row["payload_json"])
        candidate = payload.get("candidate_user_id") or payload.get("user_id")
        score = payload.get("best_score")
        if candidate and isinstance(score, (int, float)) and payload.get("decision") == "accepted":
            bands.setdefault(str(candidate), []).append(float(score))
    for user, scores in sorted(bands.items()):
        print(f"  {user}: n={len(scores)} min={min(scores):.4f} max={max(scores):.4f}")
    rejected = [
        float(json.loads(row["payload_json"]).get("best_score") or 0)
        for row in rows
        if json.loads(row["payload_json"]).get("decision") != "accepted"
    ]
    if rejected:
        print(
            f"  rejected utterances: n={len(rejected)} min={min(rejected):.4f} max={max(rejected):.4f}"
            "  <- a threshold above the rejected maximum plus below the accepted minimum is the only"
            " one that separates the two groups; if the bands overlap, no single value does"
        )


def _number(value: object) -> str:
    return f"{float(value):.4f}" if isinstance(value, (int, float)) else "—"


def show_templates(conn: sqlite3.Connection) -> None:
    print()
    print("== templates (one per enrollment recording; matching takes the best score) ==")
    rows = conn.execute(
        "SELECT t.user_id, t.active, t.created_at, u.display_name"
        " FROM voiceprint_templates t LEFT JOIN users u ON u.user_id=t.user_id"
        " ORDER BY t.user_id, t.created_at"
    ).fetchall()
    grouped: dict[str, list[sqlite3.Row]] = {}
    for row in rows:
        grouped.setdefault(str(row["user_id"]), []).append(row)
    if not grouped:
        print("  (nobody has enrolled)")
        return
    for user, items in grouped.items():
        active = sum(1 for item in items if item["active"])
        name = items[0]["display_name"] or ""
        latest = stamp(max(item["created_at"] for item in items))
        print(f"  {user} ({name}): {active} active / {len(items)} stored, newest {latest}")


def show_samples(conn: sqlite3.Connection) -> None:
    """Cross-sample similarity of the stored enrollment recordings.

    One template per sample means this matrix answers "was a second voice recorded as a sample?":
    a contaminated batch has one row far from the others (measured same-speaker range on this board:
    0.767-0.812; browser: 0.574-0.728).
    """

    from services.voiceprint.storage import open_sealed

    print()
    print("== enrollment samples: do they look like one person? ==")
    rows = conn.execute(
        "SELECT user_id, step, embedding_json, quality_json, updated_at FROM voiceprint_samples"
        " ORDER BY user_id, step"
    ).fetchall()
    if not rows:
        print("  (no samples stored)")
        return
    grouped: dict[str, list[sqlite3.Row]] = {}
    for row in rows:
        grouped.setdefault(str(row["user_id"]), []).append(row)
    for user, items in grouped.items():
        vectors = [(item, open_sealed(item["embedding_json"])) for item in items]
        print(f"  {user}:")
        for index, (item, vector) in enumerate(vectors):
            quality = json.loads(item["quality_json"] or "{}")
            # The browser path stores the upload's ``duration_ms``; the device path stores
            # ``check_quality``'s ``speech_duration_ms`` -- both mean "how long was the speech".
            duration = quality.get("duration_ms") or quality.get("speech_duration_ms")
            others = [
                cosine(vector, other)
                for position, (_, other) in enumerate(vectors)
                if position != index and other
            ]
            worst = f"{min(others):+.3f}" if others else "—"
            print(
                f"    step{item['step']} {stamp(item['updated_at'])} "
                f"rms={quality.get('rms')} speech_ms={duration} worst-match-to-other={worst}"
            )
        if len(vectors) > 1:
            pairwise = [
                cosine(vectors[i][1], vectors[j][1])
                for i in range(len(vectors))
                for j in range(i + 1, len(vectors))
                if vectors[i][1] and vectors[j][1]
            ]
            if pairwise:
                print(f"    pairwise min={min(pairwise):+.3f} max={max(pairwise):+.3f}")


def show_enrollment_events(conn: sqlite3.Connection, device: str, limit: int) -> None:
    """Every sample the device accepted or refused, and the moment templates were built.

    This is the answer to "I enrolled and it still does not know me": the samples and the reason each
    rejection gave ("no_speech", "speaker_mismatch", ...) are recorded per attempt, so a *finished*
    enrollment is still visible after the pending item has been dropped from memory.
    """

    query = (
        "SELECT device_id, event_type, created_at, payload_json FROM device_events"
        " WHERE event_type IN ('voiceprint_enrollment','voiceprint_enrolled')"
    )
    params: list[object] = []
    if device:
        query += " AND device_id=?"
        params.append(device)
    query += " ORDER BY created_at DESC LIMIT ?"
    params.append(max(1, limit))
    rows = conn.execute(query, params).fetchall()
    print()
    print(f"== enrollment attempts (last {len(rows)}) ==")
    if not rows:
        print("  (none recorded -- events of this kind only exist from 2026-10-06 on)")
        return
    for row in reversed(rows):
        payload = json.loads(row["payload_json"])
        if row["event_type"] == "voiceprint_enrolled":
            print(
                f"  {stamp(row['created_at'])}  {str(row['device_id'])[:17]:17} ENROLLED "
                f"ok={payload.get('ok')} user={payload.get('user_id')} "
                f"samples={payload.get('samples')}/{payload.get('required_samples')} "
                f"templates={payload.get('templates')} kept_previous={payload.get('kept_previous')}"
                + (f" reason={payload.get('reason')}" if payload.get("reason") else "")
            )
        else:
            print(
                f"  {stamp(row['created_at'])}  {str(row['device_id'])[:17]:17} sample "
                f"step={payload.get('step')} accepted={payload.get('accepted')} "
                f"count={payload.get('sample_count')}/{payload.get('required_samples')} "
                f"ms={payload.get('audio_ms')} rms={payload.get('audio_rms')}"
                + (f" reason={payload.get('reason')}" if payload.get("reason") else "")
            )


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only voiceprint decisions and templates.")
    parser.add_argument("--db", default="", help="database file (default: DATABASE_PATH / IoTGroup5)")
    parser.add_argument("--device", default="", help="only this device id")
    parser.add_argument("--limit", type=int, default=40, help="how many decisions to show")
    parser.add_argument("--samples", action="store_true", help="also compare the stored samples")
    parser.add_argument("--templates", action="store_true", help="also list the stored templates")
    args = parser.parse_args()

    database = Path(args.db) if args.db else default_database()
    if not database.is_file():
        print(f"database not found: {database}", file=sys.stderr)
        return 2
    conn = sqlite3.connect(database)
    conn.row_factory = sqlite3.Row

    print(f"database: {database}")
    configured = env_file()
    for name in WATCHED:
        value = os.environ.get(name) or configured.get(name) or "(unset)"
        source = "env" if os.environ.get(name) else (".env" if configured.get(name) else "")
        print(f"  {name}={value}" + (f"   [{source}]" if source else ""))
    print()
    show_decisions(conn, args.device, max(1, args.limit))
    show_templates(conn)
    show_enrollment_events(conn, args.device, max(1, args.limit))
    if args.samples:
        show_samples(conn)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
