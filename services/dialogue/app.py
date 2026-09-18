"""Simulator-first emotional dialogue service with explicit runtime isolation."""
from __future__ import annotations

import json
import os
import time
import uuid
import wave
import asyncio
import io
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, Request, Response, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from services.dashboard.read_model import (
    analytics_overview,
    dashboard_summary,
    emotion_trend,
    list_devices,
    list_user_risk_events,
    record_heartbeat,
    review_risk_event,
)
from services.audio.factory import create_asr_service, describe_asr_backend
from services.audio.normalize import normalize_audio
from services.audio.quality import check_quality
from services.enrollment.languages import Language, LANGUAGES
from services.audio.worker import AsrWorker, QueueFullError
from services.device_gateway.events import normalize_heartbeat
from services.device_gateway.contracts import DeviceEvent as ProtocolEvent
from services.dialogue.deepseek import DeepSeekClient
from services.dialogue.identity import conversation_identity
from services.dialogue.pipeline import process_text
from services.dialogue.turns import TurnRegistry
from services.security.auth import _session, create_session, require_admin, require_device, require_csrf, set_session_cookie, SESSION_COOKIE
from services.security.audit import record_audit
from services.storage.database import open_database
from services.storage.migrations import migrate
from services.storage.settings import RuntimeSettings
from services.users.repository import create_user, delete_user_data, get_user, list_users, update_user
from services.users.schemas import DeleteConfirmation, ProfileInput, ProfilePatch
from services.analysis.risk import analyze_local, merge_risk
from services.voiceprint.matcher import identify as identify_voiceprint
from services.voiceprint.provider import VoiceprintProvider
from services.voiceprint.storage import seal, open_sealed
from services.enrollment.service import EnrollmentService
from services.memory.candidates import valid_slot_key
from services.memory.embeddings import create_embedding_provider
from services.memory.flywheel import MemorySettings, effective_score
from services.memory.recall import select_for_turn
from services.memory.repository import apply_turn, evaluate_tiers, set_tier, tier_counts, upsert_memory
from services.memory.schemas import TIERS
from services.tts.config import create_tts_provider
from services.tts.media_store import MediaStore

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")
DEFAULT_SETTINGS = RuntimeSettings.from_env()
DB_PATH = DEFAULT_SETTINGS.database_path


def detect_emotion(text: str) -> str:
    positive = ("開心", "高興", "謝謝", "棒", "喜歡", "happy", "great")
    negative = ("難過", "傷心", "焦慮", "壓力", "生氣", "討厭", "sad", "angry")
    if any(token in text.lower() for token in positive): return "positive"
    if any(token in text.lower() for token in negative): return "negative"
    return "neutral"


def fallback_reply(text: str, emotion: str) -> str:
    if emotion == "negative": return f"我聽到你說：「{text}」。先慢慢來，我在這裡陪你一起整理感受。"
    if emotion == "positive": return f"聽起來很不錯！關於「{text}」，你最想和我分享哪一部分？"
    return f"我收到你的訊息：「{text}」。你希望我陪你聊天、分析情緒，還是幫你整理下一步？"


def deepseek_reply(text: str, emotion: str) -> tuple[str, str]:
    api_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
    base_url = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")
    model = os.getenv("DEEPSEEK_MODEL", "deepseek-flash")
    if not api_key: return fallback_reply(text, emotion), "fallback-no-key"
    payload = {"model": model, "temperature": 0.6, "messages": [{"role": "system", "content": "你是溫和、簡潔、非醫療診斷的情感陪伴機器人。用繁體中文回答，先同理再提供一個可執行的小建議。"}, {"role": "user", "content": f"情緒標籤：{emotion}\n使用者訊息：{text}"}]}
    try:
        with httpx.Client(timeout=30) as client:
            response = client.post(f"{base_url}/v1/chat/completions", headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}, json=payload)
            response.raise_for_status()
            return response.json()["choices"][0]["message"]["content"].strip(), model
    except Exception:
        return fallback_reply(text, emotion), f"{model}-fallback"


class ChatRequest(BaseModel):
    user_id: str = Field(default="sim-user", min_length=1, max_length=128)
    text: str = Field(min_length=1, max_length=4000)
    device_id: str = Field(default="sim-device", max_length=128)
    voiceprint_id: str | None = None


class ChatResponse(BaseModel):
    conversation_id: str
    user_id: str
    emotion: str
    reply: str
    model: str
    latency_ms: int


class HeartbeatRequest(BaseModel):
    device_id: str = Field(min_length=1, max_length=128)
    user_id: str | None = Field(default=None, max_length=128)
    firmware: str | None = Field(default=None, max_length=64)
    capabilities: list[str] = Field(default_factory=list, max_length=32)
    is_simulator: bool = False
    ip: str | None = Field(default=None, max_length=64)


class LoginRequest(BaseModel):
    actor_id: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=512)


class RiskReviewRequest(BaseModel):
    review_status: str = Field(min_length=1, max_length=32)


class EnrollmentRequest(BaseModel):
    user_id: str = Field(min_length=1, max_length=128)
    language: Language = 'yue-HK'


def create_app(settings: RuntimeSettings | None = None, providers: dict | None = None) -> FastAPI:
    runtime = settings or RuntimeSettings.from_env()
    providers = providers or {}
    # Memory flywheel tuning is read once at startup; the embedding model is
    # resolved here too, but only *loaded* on the first turn that needs it.
    memory_config = MemorySettings.from_env()
    embedding_provider = providers.get("embedding") or create_embedding_provider()
    login_failures: dict[str, deque[float]] = defaultdict(deque)

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        if not runtime.testing and not os.getenv("IOT_DATA_DIR") and not os.getenv("DATABASE_PATH"):
            legacy = ROOT / "data" / "emotional_robot.sqlite3"
            if legacy.exists() and legacy.resolve() != runtime.database_path.resolve():
                raise RuntimeError("migration_required: legacy project database exists")
        with open_database(runtime.database_path) as conn:
            migrate(conn)
        application.state.providers = providers
        asr_service = providers.get("asr")
        if asr_service is None:
            asr_service = create_asr_service(providers)
        application.state.asr_service = asr_service
        application.state.asr_worker = AsrWorker(asr_service, max_queue=int(os.getenv("ASR_MAX_QUEUE", "4")))
        application.state.turn_registry = TurnRegistry()
        application.state.enrollment_service = EnrollmentService()
        application.state.voiceprint_provider = providers.get("voiceprint") or VoiceprintProvider()
        application.state.tts_provider = providers.get("tts") or create_tts_provider()
        application.state.media_store = MediaStore(runtime.data_dir / "media")
        yield
        application.state.asr_worker.shutdown()

    application = FastAPI(title="Emotional Companion Robot", version="0.1.0", lifespan=lifespan)
    application.state.settings = runtime
    application.state.get_db = lambda: open_database(runtime.database_path)
    application.mount("/dashboard-assets", StaticFiles(directory=ROOT / "services" / "dashboard"), name="dashboard-assets")

    def db(): return open_database(runtime.database_path)

    def admin_auth(request: Request, conn, write: bool = False):
        row = require_admin(request, conn)
        if write:
            require_csrf(request, conn)
        return row

    def user_language(user_id: str) -> str:
        with db() as conn:
            if conn.execute('SELECT 1 FROM deleted_users WHERE user_id=?', (user_id,)).fetchone():
                raise HTTPException(status_code=404, detail='user_not_found')
            user = get_user(conn, user_id)
            if user and user['status'] != 'active':
                raise HTTPException(status_code=409, detail='user_disabled')
            return user['preferred_language'] if user else 'zh-CN'

    def save_conversation(req: ChatRequest, reply: str, emotion: str, model: str, latency_ms: int, risk: dict[str, Any] | None = None, llm_status: str = "", served_model: str = "") -> str:
        conversation_id, now = str(uuid.uuid4()), time.time()
        with db() as conn:
            if conn.execute('SELECT 1 FROM deleted_users WHERE user_id=?', (req.user_id,)).fetchone():
                raise HTTPException(status_code=404, detail='user_not_found')
            user = get_user(conn, req.user_id)
            if user and user['status'] != 'active': raise HTTPException(status_code=409, detail='user_disabled')
            conn.execute("INSERT OR IGNORE INTO users(user_id,display_name,created_at) VALUES(?,?,?)", (req.user_id, req.user_id, now))
            # llm_status records why a reply was degraded (timeout/network_error/...).
            # Without it a canned answer is indistinguishable from a real one in the
            # history, which is exactly how a silent fallback hides a broken turn.
            # served_model differs from model when the endpoint normalises the name.
            conn.execute("INSERT INTO conversations VALUES (?, ?, ?, ?, ?, ?, ?)", (conversation_id, req.user_id, req.text, reply, emotion, now, json.dumps({"device_id": req.device_id, "voiceprint_id": req.voiceprint_id, "model": model, "served_model": served_model, "latency_ms": latency_ms, "llm_status": llm_status}, ensure_ascii=False)))
            conn.execute("INSERT INTO emotion_events(conversation_id,user_id,emotion,created_at) VALUES(?,?,?,?)", (conversation_id, req.user_id, emotion, now))
            conn.execute("INSERT OR REPLACE INTO conversation_analysis(conversation_id,user_id,device_id,model,latency_ms,created_at) VALUES(?,?,?,?,?,?)", (conversation_id, req.user_id, req.device_id, model, latency_ms, now))
            if risk and risk.get("risk_level") in {"attention", "urgent"}:
                conn.execute(
                    "INSERT INTO risk_events(risk_id,conversation_id,user_id,risk_level,analysis_status,review_status,evidence_json,created_at) VALUES(?,?,?,?,?,?,?,?)",
                    (
                        str(uuid.uuid4()), conversation_id, req.user_id, risk["risk_level"],
                        risk.get("analysis_status", "partial"), "unreviewed",
                        json.dumps(risk.get("evidence", []), ensure_ascii=False), now,
                    ),
                )
            conn.commit()
        return conversation_id

    @application.get("/health")
    def health() -> dict[str, Any]:
        admin_configured = bool(os.getenv("IOT_ADMIN_PASSWORD", "").strip())
        asr = describe_asr_backend()
        return {
            "status": "ok",
            "service": "dialogue",
            "deepseek_configured": bool(os.getenv("DEEPSEEK_API_KEY", "").strip()),
            "admin_configured": admin_configured,
            "diagnostics": [] if admin_configured else ["admin_password_required"],
            "asr_backend": asr["backend"],
            "asr_model": asr["model_path"],
        }

    @application.get("/", include_in_schema=False)
    def home() -> RedirectResponse: return RedirectResponse(url="/dashboard", status_code=307)

    @application.post("/api/chat", response_model=ChatResponse)
    def chat(req: ChatRequest, request: Request) -> ChatResponse:
        return _chat(req, request)

    def _chat(req: ChatRequest, request: Request | None = None) -> ChatResponse:
        identity = conversation_identity(req.user_id, req.voiceprint_id)
        settings = memory_config
        with db() as conn:
            if request is not None:
                admin_auth(request, conn, write=True)
            # Memory belongs to the accompanied user, so the cascade runs per turn.
            recall = select_for_turn(
                conn,
                user_id=req.user_id,
                query=req.text,
                identity=identity,
                embedder=embedding_provider.embed_queries if embedding_provider.available else None,
                settings=settings,
            )
            memories = recall.selected
        llm = None if runtime.testing else providers.get("deepseek") or DeepSeekClient()
        result = process_text(
            req.text,
            user_id=req.user_id,
            language=user_language(req.user_id),
            identity=identity,
            session_id=request.headers.get("X-Simulator-Session", "http-chat") if request is not None else "internal-chat",
            llm=llm,
            rag_provider=providers.get("rag"),
            memories=memories,
            request_id=str(uuid.uuid4()),
            memory_settings=settings,
        )
        model = "testing-disabled" if runtime.testing else str(result.model.get("name") or result.model.get("status") or "none")
        latency_ms = result.latency_ms["total"]
        conversation_id = save_conversation(req, result.reply, result.emotion, model, latency_ms, result.risk, llm_status=str(result.model.get("status") or ""), served_model=str(result.model.get("served_model") or ""))
        # Close the flywheel: reward what was used, store what the model proposed.
        # Bookkeeping must never cost the user their reply, so a failure here is
        # reported and dropped rather than turned into a 500.
        with db() as conn:
            try:
                apply_turn(
                    conn,
                    user_id=req.user_id,
                    used_chunk_ids=recall.chunk_ids,
                    candidates=result.memory_candidates,
                    embedder=embedding_provider.embed_queries if embedding_provider.available else None,
                    statement_embedder=embedding_provider.embed_documents if embedding_provider.available else None,
                    settings=settings,
                )
            except Exception as exc:
                print(f"[memory] apply_turn failed for user={req.user_id}: {exc}", flush=True)
        return ChatResponse(conversation_id=conversation_id, user_id=req.user_id, emotion=result.emotion, reply=result.reply, model=model, latency_ms=latency_ms)

    @application.post("/api/transcribe")
    async def transcribe(request: Request, file: UploadFile = File(...), language: str | None = Form(default=None)) -> dict[str, Any]:
        with db() as conn: admin_auth(request, conn, write=True)
        audio = await file.read()
        if len(audio) > 10 * 1024 * 1024:
            raise HTTPException(status_code=413, detail="audio_too_large")
        try:
            normalized = normalize_audio(audio, file.content_type or "")
            if normalized.duration_ms > 60_000:
                raise HTTPException(status_code=413, detail="audio_too_long")
            try:
                future = application.state.asr_worker.submit(normalized, language)
            except QueueFullError as exc:
                raise HTTPException(status_code=429, detail="asr_queue_full") from exc
            result = await asyncio.wrap_future(future)
            return result
        except ValueError as exc:
            raise HTTPException(status_code=415, detail=str(exc)) from exc
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"ASR inference failed: {exc}") from exc

    @application.get("/api/conversations")
    def conversations(request: Request, limit: int = 50) -> list[dict[str, Any]]:
        with db() as conn:
            admin_auth(request, conn)
            rows = conn.execute("SELECT * FROM conversations ORDER BY created_at DESC LIMIT ?", (max(1, min(limit, 200)),)).fetchall()
        return [dict(row) for row in rows]

    @application.post("/api/device/heartbeat")
    def device_heartbeat(req: HeartbeatRequest, request: Request) -> dict[str, Any]:
        try: event = normalize_heartbeat(req.model_dump())
        except ValueError as exc: raise HTTPException(status_code=422, detail=str(exc)) from exc
        with db() as conn:
            require_device(request, conn, req.device_id)
            require_csrf(request, conn)
            record_heartbeat(conn, event)
            device = next(item for item in list_devices(conn) if item["device_id"] == event["device_id"])
        return {"ok": True, "device": device}

    @application.get("/api/devices")
    def devices(request: Request) -> list[dict[str, Any]]:
        with db() as conn:
            admin_auth(request, conn)
            return list_devices(conn)

    @application.get("/api/dashboard/summary")
    def dashboard_summary_api(request: Request) -> dict[str, Any]:
        with db() as conn:
            admin_auth(request, conn)
            return dashboard_summary(conn)

    @application.get("/api/users/{user_id}/emotion-trend")
    def user_emotion_trend(user_id: str, request: Request, days: int = 7) -> list[dict[str, Any]]:
        with db() as conn:
            admin_auth(request, conn)
            return emotion_trend(conn, user_id, days=days)

    @application.get("/api/analytics/overview")
    def analytics_overview_api(request: Request, days: int = 7):
        with db() as conn:
            admin_auth(request, conn)
            try:
                return analytics_overview(conn, days)
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc

    @application.get("/api/users/{user_id}/risk-events")
    def user_risk_events(user_id: str, request: Request):
        with db() as conn:
            admin_auth(request, conn)
            if not get_user(conn, user_id):
                raise HTTPException(status_code=404, detail="user_not_found")
            return list_user_risk_events(conn, user_id)

    @application.patch("/api/risk-events/{risk_id}")
    def patch_risk_event(risk_id: str, payload: RiskReviewRequest, request: Request):
        with db() as conn:
            admin_auth(request, conn, write=True)
            try:
                item = review_risk_event(conn, risk_id, payload.review_status)
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            if item is None:
                raise HTTPException(status_code=404, detail="risk_event_not_found")
            return item

    @application.post("/api/users", status_code=201)
    def create_user_api(profile: ProfileInput, request: Request):
        with db() as conn:
            admin_auth(request, conn, write=True)
            return create_user(conn, profile.model_dump())

    @application.get("/api/users")
    def users(request: Request, q: str = "", status: str | None = None, page: int = 1, page_size: int = 20):
        with db() as conn:
            admin_auth(request, conn)
            items, total = list_users(conn, q, status, page, page_size)
            return {"items": items, "total": total, "page": max(1, page), "page_size": min(max(1, page_size), 100)}

    @application.get("/api/users/{user_id}")
    def user_detail(user_id: str, request: Request):
        with db() as conn:
            admin_auth(request, conn)
            user = get_user(conn, user_id)
            if not user: raise HTTPException(status_code=404, detail="user_not_found")
            return user

    @application.patch("/api/users/{user_id}")
    def patch_user(user_id: str, patch: ProfilePatch, request: Request):
        with db() as conn:
            admin_auth(request, conn, write=True)
            user = update_user(conn, user_id, patch.model_dump(exclude_unset=True))
            if not user: raise HTTPException(status_code=404, detail="user_not_found")
            return user

    @application.delete("/api/users/{user_id}")
    def delete_user(user_id: str, confirmation: DeleteConfirmation, request: Request):
        with db() as conn:
            actor_id = admin_auth(request, conn, write=True)
            if confirmation.confirm_user_id != user_id: raise HTTPException(status_code=400, detail="confirmation_mismatch")
            if not get_user(conn, user_id): raise HTTPException(status_code=404, detail="user_not_found")
            counts = delete_user_data(conn, user_id)
            application.state.enrollment_service.cancel_user(user_id)
            record_audit(conn, actor_id, "delete_user", target_type="user", target_id=user_id, metadata={"deleted": counts})
            return {"ok": True, "deleted": counts}

    @application.get("/api/users/{user_id}/summary")
    def user_summary(user_id: str, request: Request, period: str = "week"):
        if period not in {"day", "week"}: raise HTTPException(status_code=422, detail="invalid_period")
        with db() as conn:
            admin_auth(request, conn)
            if not get_user(conn, user_id): raise HTTPException(status_code=404, detail="user_not_found")
            seconds = 86400 if period == "day" else 7 * 86400
            since = time.time() - seconds
            rows = conn.execute("SELECT emotion, COUNT(*) AS count FROM conversations WHERE user_id=? AND created_at>=? GROUP BY emotion", (user_id, since)).fetchall()
            distribution = {name: 0 for name in ("positive", "neutral", "negative")}
            for row in rows: distribution[row["emotion"]] = row["count"]
            recent = conn.execute("SELECT input_text, emotion, created_at, metadata_json FROM conversations WHERE user_id=? AND created_at>=? ORDER BY created_at DESC LIMIT 50", (user_id, since)).fetchall()
            latest_risk = "none"
            for row in recent:
                latest_risk = max(latest_risk, analyze_local(row["input_text"])["risk_level"], key={"none": 0, "attention": 1, "urgent": 2}.get)
            return {"user_id": user_id, "period": period, "emotion_distribution": distribution, "conversation_count": sum(distribution.values()), "risk": {"risk_level": latest_risk, "review_status": "unreviewed", "requires_human_review": latest_risk != "none", "note": "需人工確認，可能誤判"}}

    @application.get("/api/users/{user_id}/memories")
    def memories(user_id: str, request: Request, tier: str | None = None, include_inactive: bool = False):
        """List a user's memories, newest first, with the live flywheel numbers.

        ``score`` is not stored: it is the decayed value recomputed from
        ``importance``/``hits``/``last_hit_at`` at read time, which is why a row
        that stops being mentioned sinks on its own without any background job.
        """

        if tier is not None and tier not in TIERS: raise HTTPException(status_code=422, detail="invalid_tier")
        with db() as conn:
            admin_auth(request, conn)
            if not get_user(conn, user_id): raise HTTPException(status_code=404, detail="user_not_found")
            sql = (
                "SELECT chunk_id,owner_user_id,text,source_id,source_kind,approved,active,created_at,"
                "probe,slot_key,tier,importance,hits,last_hit_at,updated_at FROM memory_chunks "
                "WHERE owner_user_id=?"
            )
            params: list[Any] = [user_id]
            if not include_inactive: sql += " AND active=1"
            if tier is not None: sql += " AND tier=?"; params.append(tier)
            sql += " ORDER BY created_at DESC"
            rows = conn.execute(sql, params).fetchall()
            moment = time.time()
            items = []
            for row in rows:
                item = dict(row)
                item["score"] = round(effective_score(
                    importance=float(item["importance"] or memory_config.default_importance),
                    hits=int(item["hits"] or 0),
                    last_hit_at=item["last_hit_at"],
                    created_at=float(item["created_at"] or moment),
                    now=moment,
                    settings=memory_config,
                ), 4)
                item["approved"] = bool(item["approved"]); item["active"] = bool(item["active"])
                items.append(item)
            return {
                "user_id": user_id,
                "counts": tier_counts(conn, user_id),
                "thresholds": {"promote_score": memory_config.promote_score, "demote_score": memory_config.demote_score,
                               "half_life_days": memory_config.half_life_days, "slot_capacity": memory_config.slot_capacity,
                               "relevance_floor": memory_config.relevance_floor},
                "embedding_available": bool(embedding_provider.available),
                "items": items,
            }

    @application.post("/api/users/{user_id}/memories", status_code=201)
    def add_memory(user_id: str, payload: dict[str, Any], request: Request):
        """Store a memory by hand.

        This goes through ``upsert_memory`` rather than a raw INSERT so the row
        gets a probe and an embedding: without them the retriever can never score
        it, and a hand-written memory that the robot cannot recall is worse than
        no UI at all.
        """

        with db() as conn:
            actor = admin_auth(request, conn, write=True)
            if not get_user(conn, user_id): raise HTTPException(status_code=404, detail="user_not_found")
            text_value = str(payload.get("text", "")).strip()
            if not text_value or len(text_value) > 2000: raise HTTPException(status_code=422, detail="invalid_memory")
            probe = str(payload.get("probe", "") or "").strip() or None
            slot_key = str(payload.get("slot_key", "") or "").strip() or None
            if slot_key is not None and not valid_slot_key(slot_key): raise HTTPException(status_code=422, detail="invalid_slot_key")
            try: importance = float(payload.get("importance", memory_config.default_importance))
            except (TypeError, ValueError): raise HTTPException(status_code=422, detail="invalid_importance")
            importance = min(max(importance, 0.0), 2.0)
            vector = None
            if embedding_provider.available:
                try: vector = embedding_provider.embed_queries([probe or text_value])[0]
                except Exception: vector = None
            stored = upsert_memory(
                conn, user_id=user_id, text=text_value, probe=probe, slot_key=slot_key,
                importance=importance, approved=bool(payload.get("approved", True)),
                source_id=str(payload.get("source_id", "manual"))[:128], source_kind="manual",
                embedding=vector,
                statement_embedder=embedding_provider.embed_documents if embedding_provider.available else None,
                settings=memory_config,
            )
            evaluate_tiers(conn, user_id, settings=memory_config)
            record_audit(conn, actor, "add_memory", target_type="user", target_id=user_id,
                         metadata={"chunk_id": stored["chunk_id"], "action": stored["action"]})
            return stored

    @application.patch("/api/users/{user_id}/memories/{memory_id}")
    def update_memory(user_id: str, memory_id: str, payload: dict[str, Any], request: Request):
        """Edit a memory, or move it between tiers by hand."""

        with db() as conn:
            actor = admin_auth(request, conn, write=True)
            existing = conn.execute(
                "SELECT * FROM memory_chunks WHERE chunk_id=? AND owner_user_id=?", (memory_id, user_id)
            ).fetchone()
            if not existing: raise HTTPException(status_code=404, detail="memory_not_found")
            fields: list[str] = []
            params: list[Any] = []
            if "text" in payload:
                text_value = str(payload["text"]).strip()
                if not text_value or len(text_value) > 2000: raise HTTPException(status_code=422, detail="invalid_memory")
                fields.append("text=?"); params.append(text_value)
            if "probe" in payload:
                fields.append("probe=?"); params.append(str(payload["probe"] or "").strip() or None)
            if "slot_key" in payload:
                slot_key = str(payload["slot_key"] or "").strip() or None
                if slot_key is not None and not valid_slot_key(slot_key): raise HTTPException(status_code=422, detail="invalid_slot_key")
                fields.append("slot_key=?"); params.append(slot_key)
            if "importance" in payload:
                try: importance = float(payload["importance"])
                except (TypeError, ValueError): raise HTTPException(status_code=422, detail="invalid_importance")
                fields.append("importance=?"); params.append(min(max(importance, 0.0), 2.0))
            if "approved" in payload:
                fields.append("approved=?"); params.append(int(bool(payload["approved"])))
            if "active" in payload:
                fields.append("active=?"); params.append(int(bool(payload["active"])))
            if "tier" in payload and payload["tier"] not in TIERS:
                raise HTTPException(status_code=422, detail="invalid_tier")
            if payload.get("reset_decay"):
                fields.append("hits=0"); fields.append("last_hit_at=NULL")
            if not fields and "tier" not in payload: raise HTTPException(status_code=422, detail="nothing_to_update")
            # The stored vector is the embedding of the *probe* (the canonical
            # question), because that is what the retriever compares a question
            # against; the statement text only contributes a lexical signal.  So a
            # vector refresh is needed when the probe changes, or when there is no
            # probe to speak of and the text itself is the key.
            if embedding_provider.available and ("probe" in payload or (not existing["probe"] and "text" in payload)):
                probe = str(payload.get("probe") or payload.get("text") or existing["probe"] or existing["text"])
                try: fields.append("embedding_json=?"); params.append(json.dumps(embedding_provider.embed_queries([probe])[0]))
                except Exception: pass
            fields.append("updated_at=?"); params.append(time.time())
            params.extend([memory_id, user_id])
            conn.execute(f"UPDATE memory_chunks SET {', '.join(fields)} WHERE chunk_id=? AND owner_user_id=?", params)
            conn.commit()
            if "tier" in payload: set_tier(conn, memory_id, payload["tier"])
            record_audit(conn, actor, "update_memory", target_type="user", target_id=user_id,
                         metadata={"chunk_id": memory_id, "fields": sorted(set(payload))})
            row = conn.execute("SELECT * FROM memory_chunks WHERE chunk_id=?", (memory_id,)).fetchone()
            return dict(row)

    @application.delete("/api/users/{user_id}/memories/{memory_id}")
    def delete_memory(user_id: str, memory_id: str, request: Request):
        with db() as conn:
            actor = admin_auth(request, conn, write=True)
            cur = conn.execute("DELETE FROM memory_chunks WHERE chunk_id=? AND owner_user_id=?", (memory_id, user_id)); conn.commit()
            if cur.rowcount == 0: raise HTTPException(status_code=404, detail="memory_not_found")
            record_audit(conn, actor, "delete_memory", target_type="user", target_id=user_id, metadata={"chunk_id": memory_id})
            return {"ok": True}

    @application.post("/api/simulator/sessions")
    def simulator_session(request: Request):
        with db() as conn: admin_auth(request, conn, write=True)
        session_id, device_id = str(uuid.uuid4()), f"pc-sim-{uuid.uuid4().hex[:8]}"
        return {"session_id": session_id, "device_id": device_id, "expires_in": 600, "capabilities": {"mic": True, "speaker": True, "display": {"width": 320, "height": 240}, "playback_ack": True, "enrollment_v1": True}}

    @application.get("/api/simulator/preferences")
    def get_simulator_preferences(request: Request):
        with db() as conn:
            actor_id = admin_auth(request, conn)
            row = conn.execute("SELECT selected_user_id FROM simulator_preferences WHERE actor_id=?", (actor_id,)).fetchone()
            return {"selected_user_id": row["selected_user_id"] if row else None}

    @application.put("/api/simulator/preferences")
    def set_simulator_preferences(payload: dict[str, Any], request: Request):
        with db() as conn:
            actor_id = admin_auth(request, conn, write=True)
            selected = payload.get("selected_user_id")
            if selected:
                if not get_user(conn, selected): raise HTTPException(status_code=404, detail="user_not_found")
            conn.execute(
                "INSERT INTO simulator_preferences(actor_id,selected_user_id,updated_at) VALUES(?,?,?) "
                "ON CONFLICT(actor_id) DO UPDATE SET selected_user_id=excluded.selected_user_id, updated_at=excluded.updated_at",
                (actor_id, selected, time.time()),
            )
            conn.commit()
            return {"ok": True, "selected_user_id": selected}

    @application.get('/api/enrollment-languages')
    def enrollment_languages(request: Request):
        with db() as conn: admin_auth(request, conn)
        return LANGUAGES

    @application.delete('/api/enrollments/{enrollment_id}')
    def cancel_enrollment(enrollment_id: str, request: Request):
        with db() as conn: admin_auth(request, conn, write=True)
        item = application.state.enrollment_service.items.get(enrollment_id)
        if not item: raise HTTPException(status_code=404, detail='enrollment_not_found')
        item.state = 'canceled'
        return {'ok': True}

    @application.post("/api/enrollments", status_code=201)
    def start_enrollment(payload: EnrollmentRequest, request: Request):
        with db() as conn:
            admin_auth(request, conn, write=True)
            user = get_user(conn, payload.user_id)
            if not user:
                raise HTTPException(status_code=404, detail="user_not_found")
            if user['status'] != 'active': raise HTTPException(status_code=409, detail='user_disabled')
            saved_steps = [r["step"] for r in conn.execute("SELECT step FROM voiceprint_samples WHERE user_id=? ORDER BY step", (payload.user_id,)).fetchall()]
        item = application.state.enrollment_service.start(payload.user_id, payload.language)
        application.state.enrollment_service.transition(item.enrollment_id, "collecting_samples")
        return {"enrollment_id": item.enrollment_id, "user_id": item.user_id, "state": "collecting_samples", "required_samples": 3, "expires_at": item.expires_at, 'language': item.language, 'prompts': LANGUAGES[item.language]['prompts'], "sample_count": len(saved_steps), "saved_steps": saved_steps}

    @application.post("/api/enrollments/{enrollment_id}/samples")
    async def enrollment_sample(enrollment_id: str, request: Request, file: UploadFile = File(...), step: int | None = Form(default=None)):
        with db() as conn: admin_auth(request, conn, write=True)
        item = application.state.enrollment_service.items.get(enrollment_id)
        if not item: raise HTTPException(status_code=404, detail="enrollment_not_found")
        if step is None or not 1 <= step <= 3:
            raise HTTPException(status_code=422, detail="invalid_sample_step")
        sample = await file.read()
        if len(sample) > 10 * 1024 * 1024: raise HTTPException(status_code=413, detail="audio_too_large")
        if len(sample) < 1000: raise HTTPException(status_code=422, detail="sample_too_short")
        try:
            application.state.enrollment_service.collecting(enrollment_id)
            normalized = normalize_audio(sample, file.content_type or '')
            quality = check_quality(normalized, enrollment=True)
            if normalized.duration_ms > 20_000:
                raise ValueError('audio_too_long')
            if not quality['accepted']: raise ValueError(quality['reason'])
            embedding = application.state.voiceprint_provider.embed(normalized.pcm16)
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        sample_id, now = str(uuid.uuid4()), time.time()
        with db() as conn:
            conn.execute(
                "INSERT INTO voiceprint_samples(sample_id,user_id,step,embedding_json,quality_json,model_version,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(user_id,step) DO UPDATE SET sample_id=excluded.sample_id, embedding_json=excluded.embedding_json, quality_json=excluded.quality_json, model_version=excluded.model_version, updated_at=excluded.updated_at",
                (sample_id, item.user_id, step, seal(embedding), json.dumps(quality, ensure_ascii=False), "pc-baseline-v2", now, now),
            )
            conn.commit()
            sample_count = conn.execute("SELECT COUNT(*) FROM voiceprint_samples WHERE user_id=?", (item.user_id,)).fetchone()[0]
        quality.update({'duration_ms': normalized.duration_ms, 'status': 'accepted'})
        return {"enrollment_id": enrollment_id, "state": item.state, "sample_count": sample_count, "step": step, "quality": quality}

    @application.post("/api/enrollments/{enrollment_id}/complete")
    def complete_enrollment(enrollment_id: str, request: Request):
        with db() as conn: actor = admin_auth(request, conn, write=True)
        item = application.state.enrollment_service.items.get(enrollment_id)
        if not item: raise HTTPException(status_code=404, detail="enrollment_not_found")
        if item.state == 'completed' and item.result:
            with db() as conn:
                if not get_user(conn, item.user_id): raise HTTPException(status_code=404, detail='user_not_found')
            return item.result
        with db() as conn:
            user = get_user(conn, item.user_id)
            if not user: raise HTTPException(status_code=404, detail='user_not_found')
            if user['status'] != 'active': raise HTTPException(status_code=409, detail='user_disabled')
            rows = conn.execute("SELECT step, embedding_json FROM voiceprint_samples WHERE user_id=? ORDER BY step", (item.user_id,)).fetchall()
        if len(rows) < 3: raise HTTPException(status_code=422, detail="insufficient_samples")
        try:
            application.state.enrollment_service.collecting(enrollment_id)
            vectors = [open_sealed(row["embedding_json"]) for row in rows]
            embedding = [sum(values) / len(values) for values in zip(*vectors)]
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        template_id = str(uuid.uuid4()); now = time.time()
        with db() as conn:
            conn.execute("UPDATE voiceprint_templates SET active=0 WHERE user_id=?", (item.user_id,))
            conn.execute('UPDATE users SET enrollment_language=? WHERE user_id=?', (item.language, item.user_id))
            conn.execute("INSERT INTO voiceprint_templates(template_id,user_id,embedding_json,model_version,active,created_at) VALUES(?,?,?,?,?,?)", (template_id, item.user_id, seal(embedding), "pc-baseline-v2", 1, now))
            conn.commit(); record_audit(conn, actor, "voiceprint_enrollment", target_type="user", target_id=item.user_id, metadata={"samples": len(rows), "template_id": template_id})
        item.state = "completed"
        item.result = {"enrollment_id": enrollment_id, "user_id": item.user_id, "state": item.state, "decision": "accepted", "template_id": template_id, "model_version": "pc-baseline-v2"}
        return item.result

    @application.post("/api/voiceprint/identify")
    async def identify_voice(request: Request, file: UploadFile = File(...)):
        with db() as conn: admin_auth(request, conn, write=True)
        sample = await file.read()
        if not sample: raise HTTPException(status_code=422, detail="empty_audio")
        try:
            normalized = normalize_audio(sample, file.content_type or '')
            vector = application.state.voiceprint_provider.embed(normalized.pcm16)
            with db() as conn:
                rows = conn.execute("SELECT t.template_id,t.user_id,t.embedding_json,t.active FROM voiceprint_templates t JOIN users u ON u.user_id=t.user_id WHERE t.active=1 AND u.status='active' AND t.model_version='pc-baseline-v2'").fetchall()
            templates = []
            for row in rows:
                try: values = open_sealed(row["embedding_json"])
                except (ValueError, TypeError): continue
                templates.append({"template_id": row["template_id"], "user_id": row["user_id"], "embedding": values, "active": bool(row["active"])})
            result = identify_voiceprint(vector, templates)
            return {"decision": result.decision, "user_id": result.user_id, "best_score": result.best_score, "second_score": result.second_score, "template_id": result.template_id, "provider": result.provider}
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @application.get("/api/users/{user_id}/voiceprint/samples")
    def voiceprint_samples(user_id: str, request: Request):
        with db() as conn:
            admin_auth(request, conn)
            if not get_user(conn, user_id): raise HTTPException(status_code=404, detail="user_not_found")
            rows = conn.execute("SELECT step, quality_json, model_version, updated_at FROM voiceprint_samples WHERE user_id=? ORDER BY step", (user_id,)).fetchall()
            return [{"step": r["step"], "quality": json.loads(r["quality_json"] or "{}"), "model_version": r["model_version"], "updated_at": r["updated_at"]} for r in rows]

    @application.get("/api/media/{media_id}")
    def media(media_id: str, request: Request):
        with db() as conn:
            row = _session(request, conn)
        try:
            payload = application.state.media_store.get(media_id, row["session_id"])
        except (FileNotFoundError, AttributeError):
            raise HTTPException(status_code=404, detail="media_not_found")
        return Response(content=payload, media_type="audio/wav", headers={"Cache-Control": "no-store"})

    @application.websocket("/ws/simulator/{session_id}")
    async def simulator_websocket(websocket: WebSocket, session_id: str):
        # Browser simulator uses the existing admin cookie; reject unauthenticated sockets.
        try:
            with db() as conn:
                auth_row = _session(websocket, conn)
                if auth_row["role"] != "admin":
                    raise HTTPException(status_code=403, detail="admin_required")
        except HTTPException:
            await websocket.close(code=4401)
            return
        await websocket.accept()
        registry = getattr(application.state, "turn_registry", TurnRegistry())
        try:
            while True:
                message = await websocket.receive_json()
                message_type = str(message.get("type", ""))
                if message_type == "turn.cancel":
                    turn_id = str(message.get("turn_id", ""))
                    cancelled = registry.cancel(turn_id)
                    await websocket.send_json(ProtocolEvent("pc-sim", session_id, turn_id, "turn.interrupted", {"cancelled": cancelled}).as_dict())
                    continue
                if message_type != "chat":
                    await websocket.send_json(ProtocolEvent("pc-sim", session_id, "", "turn.failed", {"error": "unsupported_message_type"}).as_dict())
                    continue
                text = str(message.get("text", "")).strip()
                if not text:
                    await websocket.send_json(ProtocolEvent("pc-sim", session_id, "", "turn.failed", {"error": "text_required"}).as_dict())
                    continue
                request_id = str(message.get("request_id") or uuid.uuid4())
                try:
                    turn_id = registry.begin(session_id, request_id)
                except RuntimeError as exc:
                    await websocket.send_json(ProtocolEvent("pc-sim", session_id, "", "turn.failed", {"error": str(exc)}).as_dict())
                    continue
                device_id = str(message.get("device_id") or "pc-sim")
                await websocket.send_json(ProtocolEvent(device_id, session_id, turn_id, "turn.started", {"request_id": request_id}).as_dict())
                await websocket.send_json(ProtocolEvent(device_id, session_id, turn_id, "stt.final", {"text": text}).as_dict())
                await websocket.send_json(ProtocolEvent(device_id, session_id, turn_id, "display.state", {"state": "thinking", "emotion": "neutral", "caption": "正在理解…"}).as_dict())
                req = ChatRequest(user_id=str(message.get("user_id") or "sim-user"), text=text, device_id=device_id, voiceprint_id=message.get("voiceprint_id"))
                identity = conversation_identity(req.user_id, req.voiceprint_id)
                with db() as conn:
                    recall = select_for_turn(
                        conn,
                        user_id=req.user_id,
                        query=text,
                        identity=identity,
                        embedder=embedding_provider.embed_queries if embedding_provider.available else None,
                        settings=memory_config,
                    )
                try:
                    result = await asyncio.to_thread(process_text,
                        text,
                        user_id=req.user_id,
                        language=user_language(req.user_id),
                        identity=identity,
                        session_id=session_id,
                        turn_id=turn_id,
                        llm=None if runtime.testing else providers.get("deepseek") or DeepSeekClient(),
                        rag_provider=providers.get("rag"),
                        memories=recall.selected,
                        request_id=request_id,
                        memory_settings=memory_config,
                    )
                    model = "testing-disabled" if runtime.testing else str(result.model.get("name") or result.model.get("status") or "none")
                    conversation_id = save_conversation(req, result.reply, result.emotion, model, result.latency_ms["total"], result.risk, llm_status=str(result.model.get("status") or ""), served_model=str(result.model.get("served_model") or ""))
                    with db() as conn:
                        try:
                            apply_turn(
                                conn,
                                user_id=req.user_id,
                                used_chunk_ids=recall.chunk_ids,
                                candidates=result.memory_candidates,
                                embedder=embedding_provider.embed_queries if embedding_provider.available else None,
                    statement_embedder=embedding_provider.embed_documents if embedding_provider.available else None,
                                settings=memory_config,
                            )
                        except Exception as exc:
                            print(f"[memory] apply_turn failed for user={req.user_id}: {exc}", flush=True)
                    result_payload = result.as_dict() | {"conversation_id": conversation_id}
                    for index, segment in enumerate(result.segments):
                        media_id = None
                        try:
                            artifact = application.state.tts_provider.synthesize(segment)
                            wav_buffer = io.BytesIO()
                            with wave.open(wav_buffer, "wb") as wav_file:
                                wav_file.setnchannels(artifact.channels); wav_file.setsampwidth(2); wav_file.setframerate(artifact.sample_rate); wav_file.writeframes(artifact.pcm16)
                            media_id = application.state.media_store.put(wav_buffer.getvalue(), auth_row["session_id"], ".wav").ref
                        except Exception:
                            pass
                        payload = {"index": index, "sequence": index, "segment_id": f"{turn_id}-{index}", "text": segment, "language": user_language(req.user_id), "media_url": f"/api/media/{media_id}" if media_id else None, "final": index == len(result.segments) - 1, "result": result_payload}
                        await websocket.send_json(ProtocolEvent(device_id, session_id, turn_id, "tts.segment", payload).as_dict())
                    await websocket.send_json(ProtocolEvent(device_id, session_id, turn_id, "tts.end", {"result": result_payload}).as_dict())
                except Exception as exc:
                    await websocket.send_json(ProtocolEvent(device_id, session_id, turn_id, "display.state", {"state": "error", "emotion": "neutral", "caption": "處理失敗"}).as_dict())
                    await websocket.send_json(ProtocolEvent(device_id, session_id, turn_id, "turn.failed", {"error": str(exc)}).as_dict())
                finally:
                    registry.complete(turn_id)
        except WebSocketDisconnect:
            return

    @application.get("/simulator", response_class=HTMLResponse)
    def simulator_page(request: Request):
        with db() as conn: admin_auth(request, conn)
        page = ROOT / "services" / "dashboard" / "simulator.html"
        return HTMLResponse(page.read_text(encoding="utf-8"))

    @application.get("/api/system/status")
    def system_status(request: Request):
        with db() as conn: require_admin(request, conn)
        asr = application.state.asr_service.snapshot() if hasattr(application.state, "asr_service") else {"status": "unavailable"}
        return {"status": "ready", "configured": bool(providers), "loading": asr.get("status") == "loading", "degraded": asr.get("status") == "degraded", "error": asr.get("last_error"), "last_test_at": None, "asr": asr}

    @application.post("/api/auth/login")
    def login(req: LoginRequest, request: Request, response: Response):
        key = f"{request.client.host if request.client else 'unknown'}:{req.actor_id}"
        now = time.time()
        failures = login_failures[key]
        while failures and failures[0] < now - 60:
            failures.popleft()
        if len(failures) >= 5:
            raise HTTPException(status_code=429, detail="login_rate_limited")
        expected = os.getenv("IOT_ADMIN_PASSWORD", "").strip()
        if not expected: raise HTTPException(status_code=503, detail="admin_auth_not_configured")
        configured_actor = os.getenv("IOT_ADMIN_ACTOR_ID", "").strip()
        if configured_actor and req.actor_id != configured_actor:
            raise HTTPException(status_code=401, detail="invalid_credentials")
        if req.password != expected:
            failures.append(now)
            raise HTTPException(status_code=401, detail="invalid_credentials")
        failures.clear()
        with db() as conn:
            token = create_session(conn, req.actor_id, "admin", ttl_seconds=runtime.session_ttl_seconds)
            record_audit(conn, req.actor_id, "login")
        set_session_cookie(response, token, request, max_age=runtime.session_ttl_seconds)
        return {"ok": True}

    @application.post("/api/auth/logout")
    def logout(request: Request, response: Response):
        with db() as conn:
            row = require_csrf(request, conn)
            conn.execute("UPDATE sessions SET revoked_at=? WHERE session_id=?", (time.time(), row["session_id"]))
            conn.commit(); record_audit(conn, row["actor_id"], "logout")
        response.delete_cookie(SESSION_COOKIE)
        return {"ok": True}

    @application.get("/api/auth/session")
    def session(request: Request):
        with db() as conn:
            row = _session(request, conn)
            return {"actor_id": row["actor_id"], "role": row["role"], "device_id": row["device_id"], "csrf_token": row["csrf_token"]}

    @application.get("/dashboard", response_class=HTMLResponse)
    def dashboard() -> str: return (ROOT / "services" / "dashboard" / "index.html").read_text(encoding="utf-8")

    @application.get("/dashboard/user/{user_id}", response_class=HTMLResponse)
    def dashboard_user(user_id: str, request: Request) -> str:
        with db() as conn: admin_auth(request, conn)
        return (ROOT / "services" / "dashboard" / "user_detail.html").read_text(encoding="utf-8")

    return application


app = create_app()
