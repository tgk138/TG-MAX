"""Telegram API endpoints (auth + channels)."""

from __future__ import annotations

import asyncio
import logging
import re
import time
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from telethon.errors import SessionPasswordNeededError

from app.api.deps import get_current_user, get_db
from app.api.request_parsing import parse_payload
from app.models.tg_connection import TgConnection, TgConnectionStatus
from app.models.user import User
from app.services import secrets
from app.services.session_auth import create_user_session, set_session_cookie
from app.services.telegram import TelegramService

router = APIRouter(prefix="/api/tg", tags=["telegram"])
logger = logging.getLogger("uvicorn.error")

AUTH_FLOW_COOKIE = "tg_auth_flow"
AUTH_FLOW_TTL_SECONDS = 30 * 60


class SendCodeRequest(BaseModel):
    phone: str


class ConfirmCodeRequest(BaseModel):
    phone: str
    code: str


class ConfirmPasswordRequest(BaseModel):
    phone: str
    password: str


class QrPasswordRequest(BaseModel):
    password: str


# flow_id -> state
_auth_flows: dict[str, dict] = {}


def _delivery_info(sent_code) -> tuple[str | None, str]:
    """Build a user-facing hint where Telegram sent the code."""
    code_type = getattr(sent_code, "type", None)
    type_name = code_type.__class__.__name__ if code_type else ""

    mapping = {
        "SentCodeTypeApp": (
            "app",
            "Код приходит только в приложение Telegram (не по SMS). "
            "Откройте Telegram на телефоне или ПК — запрос на вход появится уведомлением или в разделе «Настройки» → «Устройства» / в чате «Telegram». "
            "Проверьте, что у приложения Telegram включены уведомления.",
        ),
        "SentCodeTypeSms": ("sms", "Код отправлен по SMS."),
        "SentCodeTypeCall": ("call", "Код будет продиктован звонком."),
        "SentCodeTypeFlashCall": ("flash_call", "Код придет через flash call."),
        "SentCodeTypeMissedCall": ("missed_call", "Код придет через пропущенный звонок."),
        "SentCodeTypeEmailCode": ("email", "Код отправлен на e-mail."),
    }
    method, hint = mapping.get(
        type_name,
        (None, "Запрос кода отправлен. Проверьте приложение Telegram, SMS и звонки."),
    )
    return method, hint


def _mask_phone(phone: str) -> str:
    digits = "".join(ch for ch in phone if ch.isdigit())
    if len(digits) <= 4:
        return "***"
    return f"+***{digits[-4:]}"


def _normalize_phone(phone: str) -> str:
    """Normalize user input to E.164-like format (+<digits>)."""
    if not phone or not phone.strip():
        raise HTTPException(422, "Phone is required.")

    cleaned = re.sub(r"[^\d+]", "", phone.strip())
    if cleaned.startswith("00"):
        cleaned = f"+{cleaned[2:]}"
    if not cleaned.startswith("+"):
        cleaned = f"+{cleaned}"

    digits = "".join(ch for ch in cleaned if ch.isdigit())
    if len(digits) < 8 or len(digits) > 15:
        raise HTTPException(422, "Phone format is invalid. Use international format, e.g. +79031234567.")

    return f"+{digits}"


def _cleanup_expired_flows() -> None:
    now = time.time()
    expired = [
        flow_id
        for flow_id, flow in _auth_flows.items()
        if now - float(flow.get("created_at", now)) > AUTH_FLOW_TTL_SECONDS
    ]
    for flow_id in expired:
        _drop_flow(flow_id)


def _set_flow_cookie(response: Response, flow_id: str) -> None:
    response.set_cookie(
        key=AUTH_FLOW_COOKIE,
        value=flow_id,
        path="/",
        httponly=True,
        samesite="lax",
        max_age=AUTH_FLOW_TTL_SECONDS,
    )


def _clear_flow_cookie(response: Response) -> None:
    response.delete_cookie(
        key=AUTH_FLOW_COOKIE,
        path="/",
        httponly=True,
        samesite="lax",
    )


def _ensure_flow(request: Request, response: Response) -> tuple[str, dict]:
    _cleanup_expired_flows()
    flow_id = request.cookies.get(AUTH_FLOW_COOKIE)
    if flow_id and flow_id in _auth_flows:
        _set_flow_cookie(response, flow_id)
        return flow_id, _auth_flows[flow_id]

    flow_id = str(uuid4())
    flow = {
        "created_at": time.time(),
        "phone_sessions": {},
        "qr": None,
    }
    _auth_flows[flow_id] = flow
    _set_flow_cookie(response, flow_id)
    return flow_id, flow


def _get_flow_or_error(request: Request) -> tuple[str, dict]:
    _cleanup_expired_flows()
    flow_id = request.cookies.get(AUTH_FLOW_COOKIE)
    if not flow_id:
        raise HTTPException(400, "No active auth flow. Start with send_code or qr_start.")
    flow = _auth_flows.get(flow_id)
    if not flow:
        raise HTTPException(400, "Auth flow expired. Start again.")
    return flow_id, flow


def _drop_flow(flow_id: str) -> None:
    flow = _auth_flows.pop(flow_id, None)
    if not flow:
        return

    for tg in flow.get("phone_sessions", {}).values():
        try:
            asyncio.create_task(tg.disconnect())
        except Exception:
            pass

    qr_entry = flow.get("qr")
    if qr_entry:
        task = qr_entry.get("task")
        if task and not task.done():
            task.cancel()
        tg = qr_entry.get("tg")
        if tg:
            try:
                asyncio.create_task(tg.disconnect())
            except Exception:
                pass


async def _upsert_user_by_tg_id(db: AsyncSession, tg_id: int) -> User:
    result = await db.execute(select(User).where(User.tg_id == tg_id).limit(1))
    user = result.scalar_one_or_none()
    if user:
        return user

    user = User(tg_id=tg_id)
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


async def _upsert_tg_connection(
    db: AsyncSession,
    user: User,
    phone: str,
    session_string: str,
) -> None:
    encrypted = secrets.encrypt(session_string)
    result = await db.execute(
        select(TgConnection)
        .where(TgConnection.user_id == user.id)
        .order_by(TgConnection.updated_at.desc(), TgConnection.created_at.desc())
        .limit(1)
    )
    conn = result.scalars().first()
    if conn:
        await db.execute(
            update(TgConnection)
            .where(TgConnection.id == conn.id)
            .values(
                phone=phone,
                session_encrypted=encrypted,
                status=TgConnectionStatus.authed,
            )
        )
    else:
        db.add(
            TgConnection(
                user_id=user.id,
                phone=phone,
                status=TgConnectionStatus.authed,
                session_encrypted=encrypted,
            )
        )
    await db.commit()


async def _finalize_login_from_session(
    db: AsyncSession,
    response: Response,
    flow_id: str,
    session_string: str,
    phone_fallback: str = "",
) -> None:
    tg = TelegramService(session_string)
    try:
        await tg.connect()
        me = await tg.get_me()
    finally:
        await tg.disconnect()

    user = await _upsert_user_by_tg_id(db, me.id)
    phone = phone_fallback or (f"+{me.phone}" if me.phone else "")
    await _upsert_tg_connection(db, user, phone, session_string)

    user_session = await create_user_session(db, user.id)
    set_session_cookie(response, user_session.session_key)

    _drop_flow(flow_id)
    _clear_flow_cookie(response)


async def _wait_qr(flow_id: str) -> None:
    flow = _auth_flows.get(flow_id)
    if not flow:
        return

    qr_entry = flow.get("qr")
    if not qr_entry:
        return
    tg = qr_entry["tg"]

    try:
        await tg.wait_qr_complete(timeout=120.0)
        qr_entry["session_string"] = tg.get_session_string()
        qr_entry["done"] = True
        qr_entry["needs_password"] = False
        qr_entry["error"] = None
    except SessionPasswordNeededError:
        qr_entry["done"] = False
        qr_entry["needs_password"] = True
        qr_entry["error"] = None
    except TimeoutError:
        qr_entry["done"] = True
        qr_entry["needs_password"] = False
        qr_entry["error"] = "QR-код истек. Нажмите «Войти по QR-коду» и отсканируйте заново."
    except Exception as exc:
        logger.exception("QR login wait failed")
        qr_entry["done"] = True
        qr_entry["needs_password"] = False
        qr_entry["error"] = str(exc)
    finally:
        if not qr_entry.get("needs_password"):
            try:
                await tg.disconnect()
            except Exception:
                pass


@router.post("/qr_start")
async def qr_start(
    request: Request,
    response: Response,
):
    """Start QR login: returns URL to show as QR. Poll GET /api/tg/qr_status until done."""
    flow_id, flow = _ensure_flow(request, response)

    old_qr = flow.get("qr")
    if old_qr:
        old_task = old_qr.get("task")
        if old_task and not old_task.done():
            old_task.cancel()
        old_tg = old_qr.get("tg")
        if old_tg:
            try:
                await old_tg.disconnect()
            except Exception:
                pass

    tg = TelegramService()
    try:
        qr_url = await tg.start_qr_login()
    except Exception as exc:
        await tg.disconnect()
        raise HTTPException(400, str(exc))

    task = asyncio.create_task(_wait_qr(flow_id))
    flow["qr"] = {
        "tg": tg,
        "task": task,
        "done": False,
        "error": None,
        "needs_password": False,
        "session_string": None,
        "finalized": False,
    }
    return {"ok": True, "qr_url": qr_url}


@router.get("/qr_status")
async def qr_status(
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    """Poll after qr_start: returns status pending | done | error."""
    flow_id = request.cookies.get(AUTH_FLOW_COOKIE)
    if not flow_id:
        return {"status": "idle"}
    flow = _auth_flows.get(flow_id)
    if not flow:
        return {"status": "idle"}

    qr_entry = flow.get("qr")
    if not qr_entry:
        return {"status": "idle"}
    if qr_entry.get("needs_password"):
        return {"status": "needs_password"}
    if qr_entry.get("error"):
        return {"status": "error", "message": qr_entry["error"], "authed": False}

    if qr_entry.get("done"):
        if qr_entry.get("finalized"):
            return {"status": "done", "authed": True}
        session_string = qr_entry.get("session_string")
        if not session_string:
            return {"status": "error", "message": "QR session completed without auth.", "authed": False}
        await _finalize_login_from_session(
            db,
            response,
            flow_id=flow_id,
            session_string=session_string,
        )
        qr_entry["finalized"] = True
        return {"status": "done", "authed": True}

    return {"status": "pending"}


@router.post("/qr_password")
async def qr_password(
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    """Complete QR login when Telegram asks for 2FA password."""
    body = await parse_payload(request, QrPasswordRequest)
    flow_id, flow = _get_flow_or_error(request)

    qr_entry = flow.get("qr")
    if not qr_entry:
        raise HTTPException(400, "No active QR session. Start QR login first.")
    if not qr_entry.get("needs_password"):
        if qr_entry.get("done") and not qr_entry.get("error"):
            return {"ok": True, "authed": True}
        raise HTTPException(400, "QR session does not require password.")

    tg: TelegramService = qr_entry["tg"]
    try:
        await tg.sign_in_password(body.password)
        session_string = tg.get_session_string()
    except Exception as exc:
        raise HTTPException(400, str(exc))
    finally:
        await tg.disconnect()

    await _finalize_login_from_session(
        db,
        response,
        flow_id=flow_id,
        session_string=session_string,
    )
    qr_entry["done"] = True
    qr_entry["needs_password"] = False
    qr_entry["error"] = None
    qr_entry["finalized"] = True
    return {"ok": True, "authed": True}


@router.post("/send_code")
async def send_code(
    request: Request,
    response: Response,
):
    """Send Telegram auth code to phone."""
    body = await parse_payload(request, SendCodeRequest)
    phone = _normalize_phone(body.phone)
    _, flow = _ensure_flow(request, response)

    tg = TelegramService()
    try:
        sent_code = await tg.send_code(phone)
    except Exception as exc:
        await tg.disconnect()
        raise HTTPException(400, str(exc))

    existing = flow["phone_sessions"].get(phone)
    if existing:
        try:
            await existing.disconnect()
        except Exception:
            pass
    flow["phone_sessions"][phone] = tg

    method, hint = _delivery_info(sent_code)
    timeout = getattr(sent_code, "timeout", None)
    logger.info(
        "Telegram code requested for %s via %s",
        _mask_phone(phone),
        method or "unknown",
    )
    return {
        "ok": True,
        "needs_code": True,
        "delivery_method": method,
        "delivery_hint": hint,
        "delivery_timeout_sec": timeout,
        "phone_normalized": phone,
    }


@router.post("/confirm_code")
async def confirm_code(
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    """Confirm Telegram auth code."""
    body = await parse_payload(request, ConfirmCodeRequest)
    phone = _normalize_phone(body.phone)
    flow_id, flow = _get_flow_or_error(request)

    tg = flow["phone_sessions"].get(phone)
    if not tg:
        raise HTTPException(400, "No pending auth session. Send code first.")

    try:
        await tg.sign_in_code(phone, body.code)
    except SessionPasswordNeededError:
        return {"ok": True, "needs_password": True}
    except Exception as exc:
        raise HTTPException(400, str(exc))

    session_string = tg.get_session_string()
    await tg.disconnect()
    flow["phone_sessions"].pop(phone, None)

    await _finalize_login_from_session(
        db,
        response,
        flow_id=flow_id,
        session_string=session_string,
        phone_fallback=phone,
    )
    return {"ok": True, "authed": True}


@router.post("/confirm_password")
async def confirm_password(
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    """Confirm Telegram 2FA password."""
    body = await parse_payload(request, ConfirmPasswordRequest)
    phone = _normalize_phone(body.phone)
    flow_id, flow = _get_flow_or_error(request)

    tg = flow["phone_sessions"].get(phone)
    if not tg:
        raise HTTPException(400, "No pending auth session.")

    try:
        await tg.sign_in_password(body.password)
        session_string = tg.get_session_string()
    except Exception as exc:
        raise HTTPException(400, str(exc))
    finally:
        await tg.disconnect()

    flow["phone_sessions"].pop(phone, None)

    await _finalize_login_from_session(
        db,
        response,
        flow_id=flow_id,
        session_string=session_string,
        phone_fallback=phone,
    )
    return {"ok": True, "authed": True}


@router.get("/channels")
async def list_channels(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """List Telegram channels accessible to user."""
    result = await db.execute(
        select(TgConnection).where(
            TgConnection.user_id == user.id,
            TgConnection.status == TgConnectionStatus.authed,
        )
        .order_by(TgConnection.updated_at.desc(), TgConnection.created_at.desc())
        .limit(1)
    )
    conn = result.scalars().first()
    if not conn or not conn.session_encrypted:
        raise HTTPException(400, "No authenticated Telegram connection.")

    session_str = secrets.decrypt(conn.session_encrypted)
    tg = TelegramService(session_str)
    try:
        await tg.connect()
        channels = await tg.list_channels()
        return [
            {
                "peer_id": ch.peer_id,
                "title": ch.title,
                "username": ch.username,
                "is_admin": ch.is_admin,
            }
            for ch in channels
        ]
    finally:
        await tg.disconnect()
