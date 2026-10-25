from __future__ import annotations

import asyncio
import difflib
import io
import json
import logging
import os
import re
import secrets
import socket
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request as UrlRequest, urlopen
from zoneinfo import ZoneInfo

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pypdf import PdfReader

from .content import attachment_ids, dump_doc, legacy_text_doc, load_doc, sanitize_doc

from .db import audit, conn, init_db, now_iso
from .ha_admin import HAAdminDirectoryUnavailable, list_ha_administrators, ha_user_is_current_admin, reset_admin_cache
from .security import (
    decrypt_secret,
    encrypt_secret,
    generate_totp_secret,
    hash_password,
    otpauth_uri,
    qr_data_uri,
    safe_next,
    token,
    token_hash,
    verify_password,
    verify_totp,
)

BASE_DIR = Path(__file__).resolve().parent
DEVICE_COOKIE = "notice_device"
CHALLENGE_COOKIE = "notice_auth"
CSRF_COOKIE = "notice_csrf"
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "true").lower() == "true"
DEVICE_DAYS = int(os.getenv("DEVICE_TRUST_DAYS", "180"))
MAX_DEVICES = int(os.getenv("MAX_DEVICES_PER_USER", "3"))
TOTP_ISSUER = os.getenv("TOTP_ISSUER", "Notice Board")
DISPLAY_TZ = ZoneInfo(os.getenv("DISPLAY_TIMEZONE", "Asia/Taipei"))
SCHEDULER_INTERVAL = max(10, int(os.getenv("SCHEDULER_INTERVAL_SECONDS", "30")))
UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR", str(BASE_DIR.parent / "data" / "uploads")))
MAX_PDF_UPLOAD_BYTES = max(1, int(os.getenv("MAX_PDF_UPLOAD_MB", "25"))) * 1024 * 1024
INLINE_PDF_MAX_PAGES = 10
NOTIFICATION_GATEWAY_ENABLED = os.getenv("NOTIFICATION_GATEWAY_ENABLED", "false").lower() == "true"
NOTIFICATION_GATEWAY_URL = os.getenv("NOTIFICATION_GATEWAY_URL", "").strip()
NOTIFICATION_GATEWAY_TOKEN = os.getenv("NOTIFICATION_GATEWAY_TOKEN", "").strip()
NOTIFICATION_GATEWAY_TIMEOUT = max(1, int(os.getenv("NOTIFICATION_GATEWAY_TIMEOUT_SECONDS", "5")))
INGRESS_PROXY_IP = os.getenv("INGRESS_PROXY_IP", "").strip()
LOGGER = logging.getLogger("noticeboard.ingress")

ACCOUNT_LEVEL_LABELS = {
    "SYSADMIN": "系統管理員",
    "USER": "一般使用者",
    "BOT": "機器人",
}
IMPORTANCE_LABELS = {"NORMAL": "一般", "IMPORTANT": "重要", "URGENT": "緊急"}
ANNOUNCEMENT_STATE_LABELS = {
    "DRAFT": "草稿",
    "PUBLISHED": "已發布",
    "WITHDRAWN": "已撤下",
    "EXPIRED": "已到期",
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    task = asyncio.create_task(scheduler_loop())
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


app = FastAPI(title="Notice Board", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")
templates.env.globals["ACCOUNT_LEVEL_LABELS"] = ACCOUNT_LEVEL_LABELS
templates.env.globals["ANNOUNCEMENT_STATE_LABELS"] = ANNOUNCEMENT_STATE_LABELS
templates.env.globals["IMPORTANCE_LABELS"] = IMPORTANCE_LABELS


def fmt_dt(value: str | None) -> str:
    if not value:
        return "—"
    try:
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(DISPLAY_TZ).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return value


def local_dt_input(value: str | None) -> str:
    if not value:
        return ""
    try:
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(DISPLAY_TZ).strftime("%Y-%m-%dT%H:%M")
    except Exception:
        return ""


templates.env.filters["fmt_dt"] = fmt_dt
templates.env.filters["local_dt_input"] = local_dt_input


def trusted_ingress_proxy_addresses() -> set[str]:
    addresses: set[str] = set()
    if INGRESS_PROXY_IP:
        addresses.update(part.strip() for part in INGRESS_PROXY_IP.split(",") if part.strip())
    try:
        addresses.update(
            item[4][0]
            for item in socket.getaddrinfo("supervisor", None, type=socket.SOCK_STREAM)
            if item and item[4]
        )
    except socket.gaierror:
        pass
    return addresses


def is_ingress_transport(request: Request) -> bool:
    # Presentation-only check. This does not grant authentication.
    return bool(request.headers.get("x-ingress-path"))


def is_ingress_request(request: Request) -> bool:
    # SSO trust remains stricter than presentation routing: identity headers are
    # accepted only when the socket peer resolves to Supervisor.
    client_host = request.client.host if request.client else ""
    trusted_peers = trusted_ingress_proxy_addresses()
    return bool(
        client_host
        and client_host in trusted_peers
        and request.headers.get("x-ingress-path")
        and request.headers.get("x-remote-user-id")
    )


def ingress_base_path(request: Request) -> str:
    if not is_ingress_transport(request):
        return ""
    return request.headers.get("x-ingress-path", "").rstrip("/")


@app.middleware("http")
async def security_middleware(request: Request, call_next):
    csrf = request.cookies.get(CSRF_COOKIE) or token()
    request.state.csrf_token = csrf
    response = await call_next(request)
    ingress_transport = is_ingress_transport(request)
    ingress_identity = is_ingress_request(request)
    base_path = ingress_base_path(request)

    if ingress_transport and not ingress_identity and request.url.path == "/":
        client_host = request.client.host if request.client else ""
        LOGGER.warning(
            "Ingress transport detected but SSO identity was not trusted: "
            "peer=%s trusted_peer=%s remote_user_id=%s remote_user_name=%s hass_source=%s",
            client_host or "unknown",
            client_host in trusted_ingress_proxy_addresses() if client_host else False,
            bool(request.headers.get("x-remote-user-id")),
            bool(request.headers.get("x-remote-user-name")),
            request.headers.get("x-hass-source", "missing"),
        )

    if not request.cookies.get(CSRF_COOKIE):
        response.set_cookie(CSRF_COOKIE, csrf, httponly=False, secure=COOKIE_SECURE, samesite="strict", max_age=60 * 60 * 24 * 365)

    # Starlette routes are mounted at / inside the container. Home Assistant
    # Ingress exposes them under a session-specific prefix, so rewrite local
    # redirects back into that prefix without changing public/direct access.
    location = response.headers.get("location")
    if ingress_transport and base_path and location and location.startswith("/") and not location.startswith(base_path + "/"):
        response.headers["location"] = base_path + location

    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    if request.url.path.startswith("/files/"):
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
        response.headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'self'"
    elif ingress_transport:
        # The response is displayed inside the Home Assistant frontend frame.
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
        response.headers["Content-Security-Policy"] = "default-src 'self'; img-src 'self' data: https:; style-src 'self'; script-src 'self' https://cdn.jsdelivr.net; frame-src 'self'; object-src 'self'; base-uri 'self'; frame-ancestors 'self'; form-action 'self'"
    else:
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Content-Security-Policy"] = "default-src 'self'; img-src 'self' data: https:; style-src 'self'; script-src 'self' https://cdn.jsdelivr.net; frame-src 'self'; object-src 'self'; base-uri 'self'; frame-ancestors 'none'; form-action 'self'"
    if COOKIE_SECURE:
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


def csrf_ok(request: Request, submitted: str) -> bool:
    cookie = request.cookies.get(CSRF_COOKIE, "")
    return bool(cookie and secrets.compare_digest(cookie, submitted))


def require_csrf(request: Request, submitted: str) -> None:
    if not csrf_ok(request, submitted):
        raise HTTPException(403, "CSRF validation failed")


def parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value)


def local_form_to_utc(value: str | None) -> str | None:
    value = (value or "").strip()
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=DISPLAY_TZ)
        return dt.astimezone(timezone.utc).isoformat()
    except ValueError:
        return None


def login_attempt_key(request: Request, username: str) -> str:
    if os.getenv("TRUST_CLOUDFLARE", "false").lower() == "true":
        ip = request.headers.get("cf-connecting-ip") or (request.client.host if request.client else "unknown")
    else:
        ip = request.client.host if request.client else "unknown"
    return token_hash(f"{ip}|{username.strip().lower()}")


def too_many_login_failures(c, key: str) -> bool:
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=15)).isoformat()
    c.execute("DELETE FROM login_attempts WHERE failed_at<?", (cutoff,))
    n = c.execute("SELECT COUNT(*) AS n FROM login_attempts WHERE attempt_key=? AND failed_at>=?", (key, cutoff)).fetchone()["n"]
    return n >= 5


def record_login_failure(c, key: str) -> None:
    c.execute("INSERT INTO login_attempts (id,attempt_key,failed_at) VALUES (?,?,?)", (str(uuid.uuid4()), key, now_iso()))


def _ingress_username(c, requested: str, hass_user_id: str) -> str:
    base = (requested or "").strip() or f"ha-{hass_user_id[:8]}"
    candidate = base[:80]
    row = c.execute("SELECT id FROM users WHERE username=? COLLATE NOCASE", (candidate,)).fetchone()
    if not row:
        return candidate
    fallback = f"ha-{hass_user_id[:12]}"[:80]
    row = c.execute("SELECT id FROM users WHERE username=? COLLATE NOCASE", (fallback,)).fetchone()
    if not row:
        return fallback
    return f"ha-{hass_user_id[:8]}-{secrets.token_hex(2)}"[:80]


def ingress_user(request: Request):
    if not is_ingress_request(request):
        return None
    hass_user_id = request.headers.get("x-remote-user-id", "").strip()
    hass_username = request.headers.get("x-remote-user-name", "").strip()
    hass_display_name = request.headers.get("x-remote-user-display-name", "").strip() or hass_username or "Home Assistant 使用者"
    if not hass_user_id:
        return None

    audit_event = None
    user_id = None
    with conn() as c:
        existing = c.execute("SELECT * FROM users WHERE hass_user_id=?", (hass_user_id,)).fetchone()
        if existing:
            if not existing["is_active"]:
                return None
            if existing["role"] == "SYSADMIN" and not ha_user_is_current_admin(hass_user_id):
                return None
            result = dict(existing)
            result.update({"device_id": None, "device_name": "Home Assistant Ingress", "auth_source": "INGRESS"})
            return result

        match = None
        if hass_username:
            match = c.execute(
                "SELECT * FROM users WHERE username=? COLLATE NOCASE AND hass_user_id IS NULL AND role='USER' AND is_active=1",
                (hass_username,),
            ).fetchone()
        if match:
            user_id = match["id"]
            c.execute(
                "UPDATE users SET hass_user_id=?, updated_at=? WHERE id=?",
                (hass_user_id, now_iso(), user_id),
            )
            audit_event = ("auth.ingress_link", {"hass_user_id": hass_user_id, "hass_username": hass_username})
        else:
            user_id = str(uuid.uuid4())
            username = _ingress_username(c, hass_username, hass_user_id)
            t = now_iso()
            # Ingress-provisioned accounts authenticate through Home Assistant.
            # The unknown random password prevents accidental direct password login;
            # a sysadmin can reset it later if external login is also desired.
            c.execute(
                """INSERT INTO users
                (id,username,display_name,password_hash,role,must_change_password,hass_user_id,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?)""",
                (user_id, username, hass_display_name[:120], hash_password(token()), "USER", 1, hass_user_id, t, t),
            )
            audit_event = ("auth.ingress_provision", {"hass_user_id": hass_user_id, "hass_username": hass_username})

    if audit_event:
        audit(user_id, audit_event[0], "user", user_id, audit_event[1])
    with conn() as c:
        row = c.execute("SELECT * FROM users WHERE id=? AND is_active=1", (user_id,)).fetchone()
        if not row:
            return None
        result = dict(row)
        result.update({"device_id": None, "device_name": "Home Assistant Ingress", "auth_source": "INGRESS"})
        return result


def current_user(request: Request):
    ingress = ingress_user(request)
    if ingress:
        return ingress

    raw = request.cookies.get(DEVICE_COOKIE)
    if not raw:
        return None
    now = datetime.now(timezone.utc)
    with conn() as c:
        row = c.execute(
            """
            SELECT u.*, d.id AS device_id, d.name AS device_name, d.expires_at AS device_expires_at
            FROM devices d JOIN users u ON u.id=d.user_id
            WHERE d.token_hash=? AND d.revoked_at IS NULL AND u.is_active=1
            """,
            (token_hash(raw),),
        ).fetchone()
        if not row or parse_iso(row["device_expires_at"]) <= now:
            return None
        c.execute("UPDATE devices SET last_seen_at=? WHERE id=?", (now_iso(), row["device_id"]))
        result = dict(row)
        result["auth_source"] = "DEVICE"
        return result


def unread_count_for_user(user: dict | None) -> int:
    if not user or user.get("role") == "BOT":
        return 0
    with conn() as c:
        return int(c.execute(
            """
            SELECT COUNT(*) AS n
            FROM announcements a
            WHERE a.deleted_at IS NULL
              AND a.status='PUBLISHED'
              AND a.current_version>0
              AND NOT EXISTS (
                SELECT 1 FROM announcement_reads_v2 r
                WHERE r.announcement_id=a.id
                  AND r.version_no=a.current_version
                  AND r.user_id=?
              )
            """,
            (user["id"],),
        ).fetchone()["n"])


def require_user(request: Request, next_url: str | None = None):
    user = current_user(request)
    if user:
        return user
    dest = safe_next(next_url or request.url.path)
    raise HTTPException(status_code=401, detail=dest)


def require_sysadmin(user: dict) -> None:
    if user["role"] != "SYSADMIN":
        raise HTTPException(403, "只有系統管理員可執行此操作")


def require_announcement_author(user: dict) -> None:
    if user["role"] not in {"USER", "SYSADMIN"}:
        raise HTTPException(403, "此操作僅限一般使用者或系統管理員")


def auth_redirect_for(exc: HTTPException) -> RedirectResponse:
    next_url = exc.detail if isinstance(exc.detail, str) else "/"
    return RedirectResponse("/login?" + urlencode({"next": next_url}), status_code=303)


def get_challenge(request: Request):
    raw = request.cookies.get(CHALLENGE_COOKIE)
    if not raw:
        return None
    with conn() as c:
        row = c.execute(
            "SELECT ac.*, u.username, u.display_name, u.must_change_password, u.totp_secret_enc, u.is_active FROM auth_challenges ac JOIN users u ON u.id=ac.user_id WHERE ac.token_hash=?",
            (token_hash(raw),),
        ).fetchone()
        if not row or not row["is_active"] or parse_iso(row["expires_at"]) <= datetime.now(timezone.utc):
            return None
        return dict(row)


def challenge_cookie(response: RedirectResponse, raw: str) -> None:
    response.set_cookie(CHALLENGE_COOKIE, raw, httponly=True, secure=COOKIE_SECURE, samesite="lax", max_age=20 * 60)


def clear_challenge_cookie(response: RedirectResponse) -> None:
    response.delete_cookie(CHALLENGE_COOKIE)


def default_device_name(ua: str) -> str:
    u = ua.lower()
    if "android" in u:
        return "Android 裝置"
    if "iphone" in u:
        return "iPhone"
    if "ipad" in u:
        return "iPad"
    if "windows" in u:
        return "Windows 電腦"
    if "macintosh" in u or "mac os" in u:
        return "Mac"
    return "瀏覽器裝置"


def active_devices(c, user_id: str):
    return [dict(r) for r in c.execute(
        "SELECT * FROM devices WHERE user_id=? AND revoked_at IS NULL AND expires_at>? ORDER BY last_seen_at DESC",
        (user_id, now_iso()),
    ).fetchall()]


def finish_bind(request: Request, challenge: dict, device_name: str) -> RedirectResponse:
    raw_device = token()
    now = datetime.now(timezone.utc)
    expires = now + timedelta(days=DEVICE_DAYS)
    with conn() as c:
        devices = active_devices(c, challenge["user_id"])
        if len(devices) >= MAX_DEVICES:
            c.execute("UPDATE auth_challenges SET stage='MFA_OK', device_name=? WHERE id=?", (device_name, challenge["id"]))
            return RedirectResponse("/setup/device-limit", status_code=303)
        device_id = str(uuid.uuid4())
        c.execute(
            "INSERT INTO devices (id,user_id,name,token_hash,user_agent,first_seen_at,last_seen_at,expires_at) VALUES (?,?,?,?,?,?,?,?)",
            (device_id, challenge["user_id"], device_name[:80], token_hash(raw_device), request.headers.get("user-agent", "")[:500], now.isoformat(), now.isoformat(), expires.isoformat()),
        )
        c.execute("DELETE FROM auth_challenges WHERE id=?", (challenge["id"],))
    audit(challenge["user_id"], "device.bind", "device", device_id, {"name": device_name})
    response = RedirectResponse(safe_next(challenge["next_url"]), status_code=303)
    response.set_cookie(DEVICE_COOKIE, raw_device, httponly=True, secure=COOKIE_SECURE, samesite="lax", max_age=DEVICE_DAYS * 86400)
    clear_challenge_cookie(response)
    return response


def render(request: Request, name: str, **context):
    user = current_user(request)
    context.update({
        "request": request,
        "csrf_token": request.state.csrf_token,
        "user": user,
        "unread_count": unread_count_for_user(user),
        "is_ingress": is_ingress_request(request),
        "base_path": ingress_base_path(request),
        "current_year": datetime.now(DISPLAY_TZ).year,
    })
    return templates.TemplateResponse(request=request, name=name, context=context)


@app.exception_handler(401)
async def unauthenticated_handler(request: Request, exc: HTTPException):
    return auth_redirect_for(exc)


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request, next: str = "/"):
    if current_user(request):
        return RedirectResponse(safe_next(next), status_code=303)
    return render(request, "login.html", next=safe_next(next), error=None)


@app.post("/auth/login")
def login_action(request: Request, username: str = Form(...), password: str = Form(...), next: str = Form("/"), csrf_token: str = Form(...)):
    require_csrf(request, csrf_token)
    key = login_attempt_key(request, username)
    with conn() as c:
        if too_many_login_failures(c, key):
            return render(request, "login.html", next=safe_next(next), error="登入嘗試過多，請稍後再試。")
        user = c.execute("SELECT * FROM users WHERE username=? COLLATE NOCASE AND is_active=1", (username.strip(),)).fetchone()
        if not user or not verify_password(password, user["password_hash"]):
            record_login_failure(c, key)
            return render(request, "login.html", next=safe_next(next), error="帳號或密碼錯誤")
        c.execute("DELETE FROM login_attempts WHERE attempt_key=?", (key,))
        raw = token()
        t = datetime.now(timezone.utc)
        c.execute("DELETE FROM auth_challenges WHERE expires_at<=?", (t.isoformat(),))
        c.execute(
            "INSERT INTO auth_challenges (id,token_hash,user_id,stage,next_url,created_at,expires_at) VALUES (?,?,?,?,?,?,?)",
            (str(uuid.uuid4()), token_hash(raw), user["id"], "PASSWORD_OK", safe_next(next), t.isoformat(), (t + timedelta(minutes=20)).isoformat()),
        )
    audit(user["id"], "auth.password_ok", "user", user["id"])
    if user["must_change_password"]:
        path = "/setup/password"
    elif not user["totp_secret_enc"]:
        path = "/setup/2fa"
    else:
        path = "/login/2fa"
    response = RedirectResponse(path, status_code=303)
    challenge_cookie(response, raw)
    return response


@app.get("/setup/password", response_class=HTMLResponse)
def setup_password_page(request: Request):
    ch = get_challenge(request)
    if not ch:
        return RedirectResponse("/login", status_code=303)
    if not ch["must_change_password"]:
        return RedirectResponse("/setup/2fa" if not ch["totp_secret_enc"] else "/login/2fa", status_code=303)
    return render(request, "setup_password.html", error=None)


@app.post("/setup/password")
def setup_password_action(request: Request, password: str = Form(...), confirm: str = Form(...), csrf_token: str = Form(...)):
    require_csrf(request, csrf_token)
    ch = get_challenge(request)
    if not ch:
        return RedirectResponse("/login", status_code=303)
    if len(password) < 12 or password != confirm:
        return render(request, "setup_password.html", error="新密碼至少 12 碼，且兩次輸入必須一致。")
    with conn() as c:
        c.execute("UPDATE users SET password_hash=?, must_change_password=0, updated_at=? WHERE id=?", (hash_password(password), now_iso(), ch["user_id"]))
        c.execute("UPDATE auth_challenges SET stage='PASSWORD_CHANGED' WHERE id=?", (ch["id"],))
    audit(ch["user_id"], "auth.password_changed", "user", ch["user_id"])
    return RedirectResponse("/setup/2fa" if not ch["totp_secret_enc"] else "/login/2fa", status_code=303)


@app.get("/setup/2fa", response_class=HTMLResponse)
def setup_2fa_page(request: Request):
    ch = get_challenge(request)
    if not ch:
        return RedirectResponse("/login", status_code=303)
    if ch["must_change_password"]:
        return RedirectResponse("/setup/password", status_code=303)
    if ch["totp_secret_enc"]:
        return RedirectResponse("/login/2fa", status_code=303)
    secret_enc = ch["tmp_totp_secret_enc"]
    if secret_enc:
        secret = decrypt_secret(secret_enc)
    else:
        secret = generate_totp_secret()
        with conn() as c:
            c.execute("UPDATE auth_challenges SET tmp_totp_secret_enc=?, stage='TOTP_SETUP' WHERE id=?", (encrypt_secret(secret), ch["id"]))
    uri = otpauth_uri(secret, ch["username"], TOTP_ISSUER)
    return render(request, "setup_2fa.html", secret=secret, qr=qr_data_uri(uri), error=None, device_name=default_device_name(request.headers.get("user-agent", "")))


@app.post("/setup/2fa")
def setup_2fa_action(request: Request, code: str = Form(...), device_name: str = Form(...), csrf_token: str = Form(...)):
    require_csrf(request, csrf_token)
    ch = get_challenge(request)
    if not ch or not ch["tmp_totp_secret_enc"]:
        return RedirectResponse("/login", status_code=303)
    secret = decrypt_secret(ch["tmp_totp_secret_enc"])
    if not verify_totp(secret, code):
        uri = otpauth_uri(secret, ch["username"], TOTP_ISSUER)
        return render(request, "setup_2fa.html", secret=secret, qr=qr_data_uri(uri), error="驗證碼不正確，請確認手機時間為自動校時。", device_name=device_name)
    with conn() as c:
        c.execute("UPDATE users SET totp_secret_enc=?, updated_at=? WHERE id=?", (encrypt_secret(secret), now_iso(), ch["user_id"]))
        c.execute("UPDATE auth_challenges SET stage='MFA_OK', device_name=? WHERE id=?", (device_name[:80], ch["id"]))
    audit(ch["user_id"], "auth.totp_enabled", "user", ch["user_id"])
    ch = get_challenge(request)
    return finish_bind(request, ch, device_name or default_device_name(request.headers.get("user-agent", "")))


@app.get("/login/2fa", response_class=HTMLResponse)
def login_2fa_page(request: Request):
    ch = get_challenge(request)
    if not ch:
        return RedirectResponse("/login", status_code=303)
    if ch["must_change_password"]:
        return RedirectResponse("/setup/password", status_code=303)
    if not ch["totp_secret_enc"]:
        return RedirectResponse("/setup/2fa", status_code=303)
    return render(request, "login_2fa.html", error=None, device_name=default_device_name(request.headers.get("user-agent", "")))


@app.post("/login/2fa")
def login_2fa_action(request: Request, code: str = Form(...), device_name: str = Form(...), csrf_token: str = Form(...)):
    require_csrf(request, csrf_token)
    ch = get_challenge(request)
    if not ch or not ch["totp_secret_enc"]:
        return RedirectResponse("/login", status_code=303)
    if not verify_totp(decrypt_secret(ch["totp_secret_enc"]), code):
        return render(request, "login_2fa.html", error="驗證碼不正確。", device_name=device_name)
    with conn() as c:
        c.execute("UPDATE auth_challenges SET stage='MFA_OK', device_name=? WHERE id=?", (device_name[:80], ch["id"]))
    audit(ch["user_id"], "auth.totp_ok", "user", ch["user_id"])
    ch = get_challenge(request)
    return finish_bind(request, ch, device_name or default_device_name(request.headers.get("user-agent", "")))


@app.get("/setup/device-limit", response_class=HTMLResponse)
def device_limit_page(request: Request):
    ch = get_challenge(request)
    if not ch or ch["stage"] != "MFA_OK":
        return RedirectResponse("/login", status_code=303)
    with conn() as c:
        devices = active_devices(c, ch["user_id"])
    return render(request, "device_limit.html", devices=devices, max_devices=MAX_DEVICES, device_name=ch.get("device_name") or default_device_name(request.headers.get("user-agent", "")), error=None)


@app.post("/setup/device-limit/revoke/{device_id}")
def device_limit_revoke(request: Request, device_id: str, csrf_token: str = Form(...)):
    require_csrf(request, csrf_token)
    ch = get_challenge(request)
    if not ch or ch["stage"] != "MFA_OK":
        return RedirectResponse("/login", status_code=303)
    with conn() as c:
        c.execute("UPDATE devices SET revoked_at=? WHERE id=? AND user_id=? AND revoked_at IS NULL", (now_iso(), device_id, ch["user_id"]))
    audit(ch["user_id"], "device.revoke_during_login", "device", device_id)
    return RedirectResponse("/setup/device-limit", status_code=303)


@app.post("/setup/device-limit/bind")
def device_limit_bind(request: Request, device_name: str = Form(...), csrf_token: str = Form(...)):
    require_csrf(request, csrf_token)
    ch = get_challenge(request)
    if not ch or ch["stage"] != "MFA_OK":
        return RedirectResponse("/login", status_code=303)
    with conn() as c:
        if len(active_devices(c, ch["user_id"])) >= MAX_DEVICES:
            devices = active_devices(c, ch["user_id"])
            return render(request, "device_limit.html", devices=devices, max_devices=MAX_DEVICES, device_name=device_name, error="請先解除至少一台舊裝置。")
    return finish_bind(request, ch, device_name)


@app.post("/logout")
def logout(request: Request, csrf_token: str = Form(...)):
    require_csrf(request, csrf_token)
    require_user(request)
    # Legacy callers must use the verified device-management flow too.
    return RedirectResponse("/me/devices", status_code=303)


def announcement_url(request: Request, slug: str) -> str:
    base = os.getenv("APP_BASE_URL")
    if base:
        return base.rstrip("/") + "/a/" + slug
    return str(request.base_url).rstrip("/") + "/a/" + slug


def slugify_title(title: str) -> str:
    base = re.sub(r"[^a-zA-Z0-9]+", "-", title.lower()).strip("-")[:24]
    prefix = datetime.now().strftime("%Y%m%d")
    suffix = secrets.token_hex(3)
    return f"{prefix}-{base + '-' if base else ''}{suffix}"


def is_owner(user: dict, ann) -> bool:
    return user["role"] in {"USER", "SYSADMIN"} and user["id"] == ann["created_by"]


def announcement_context(user: dict, ann) -> str:
    if is_owner(user, ann):
        return "發布者" if ann["current_version"] > 0 else "編輯者"
    return "閱覽者"


def can_edit(user: dict, ann) -> bool:
    return is_owner(user, ann)


def can_publish(user: dict, ann) -> bool:
    return is_owner(user, ann)


def can_withdraw(user: dict, ann) -> bool:
    return (is_owner(user, ann) or user["role"] == "SYSADMIN") and ann["status"] == "PUBLISHED"


def can_delete(user: dict, ann) -> bool:
    return is_owner(user, ann) and ann["status"] != "PUBLISHED"


def _diff_tokens(text: str) -> list[str]:
    """Tokenize for readable inline diffs: Latin words stay whole; CJK is compared per character."""
    return re.findall(r"\s+|[A-Za-z0-9_]+|[\u3400-\u4dbf\u4e00-\u9fff]|[^\w\s]", text, flags=re.UNICODE)


def inline_revision_diff(old_text: str, new_text: str) -> list[dict[str, str]]:
    """Return safe structured diff segments. Deleted text is struck; inserted text remains normal."""
    old_tokens = _diff_tokens(old_text or "")
    new_tokens = _diff_tokens(new_text or "")
    matcher = difflib.SequenceMatcher(a=old_tokens, b=new_tokens, autojunk=False)
    segments: list[dict[str, str]] = []

    def add(kind: str, tokens: list[str]) -> None:
        text = "".join(tokens)
        if not text:
            return
        if segments and segments[-1]["kind"] == kind:
            segments[-1]["text"] += text
        else:
            segments.append({"kind": kind, "text": text})

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            add("same", old_tokens[i1:i2])
        elif tag == "delete":
            add("delete", old_tokens[i1:i2])
        elif tag == "insert":
            add("insert", new_tokens[j1:j2])
        else:  # replace: old text first, then its replacement
            add("delete", old_tokens[i1:i2])
            add("insert", new_tokens[j1:j2])
    return segments


def _attachment_lookup(c, user: dict, announcement_id: str | None, upload_session: str | None):
    def lookup(file_id: str):
        row = c.execute("SELECT * FROM attachments WHERE id=? AND deleted_at IS NULL", (file_id,)).fetchone()
        if not row or row["uploaded_by"] != user["id"]:
            return None
        if row["announcement_id"]:
            if not announcement_id or row["announcement_id"] != announcement_id:
                return None
        elif not upload_session or row["upload_session"] != upload_session:
            return None
        return dict(row)
    return lookup


def _submission_doc(c, user: dict, body_json: str, body: str, announcement_id: str | None, upload_session: str | None):
    raw = body_json.strip() if body_json else ""
    doc, text = sanitize_doc(raw or None, _attachment_lookup(c, user, announcement_id, upload_session), fallback_text=body)
    return doc, text


def _bind_pending_attachments(c, announcement_id: str, user_id: str, upload_session: str | None, doc: dict) -> None:
    ids = attachment_ids(doc)
    if not ids:
        return
    t = now_iso()
    for file_id in ids:
        row = c.execute("SELECT * FROM attachments WHERE id=? AND deleted_at IS NULL", (file_id,)).fetchone()
        if not row or row["uploaded_by"] != user_id:
            raise ValueError("公告包含無法使用的 PDF 附件")
        if row["announcement_id"] not in (None, announcement_id):
            raise ValueError("PDF 附件已屬於其他公告")
        if row["announcement_id"] is None:
            if not upload_session or row["upload_session"] != upload_session:
                raise ValueError("PDF 上傳工作階段已失效")
            c.execute("UPDATE attachments SET announcement_id=?,attached_at=? WHERE id=?", (announcement_id, t, file_id))


def _editor_doc(ann) -> dict:
    if not ann:
        return legacy_text_doc("")
    if ann["has_unpublished_changes"]:
        return load_doc(ann["draft_body_json"], ann["draft_body"] or "")
    return load_doc(ann["body_json"], ann["body"] or "")


def _publish_in_connection(c, ann, actor_user_id: str, automated: bool = False) -> tuple[bool, int]:
    ann = dict(ann)
    if ann["current_version"] > 0 and ann["status"] == "PUBLISHED" and not ann["has_unpublished_changes"]:
        return False, ann["current_version"]

    if ann["current_version"] > 0 and ann["has_unpublished_changes"]:
        title = ann["draft_title"] or ann["title"]
        body = ann["draft_body"] or ann["body"]
        body_json = ann["draft_body_json"] or ann["body_json"] or dump_doc(legacy_text_doc(body))
    else:
        title = ann["title"]
        body = ann["body"]
        body_json = ann["body_json"] or dump_doc(legacy_text_doc(body))

    importance = (ann.get("draft_importance") if ann["has_unpublished_changes"] else None) or ann["importance"]
    new_version = int(ann["current_version"] or 0) + 1
    t = now_iso()
    c.execute(
        """
        UPDATE announcements SET title=?,body=?,body_json=?,importance=?,status='PUBLISHED',current_version=?,draft_title=NULL,draft_body=NULL,draft_body_json=NULL,draft_importance=NULL,
        has_unpublished_changes=0,scheduled_publish_at=NULL,published_at=?,withdrawn_at=NULL,expired_at=NULL,
        updated_by=?,updated_at=? WHERE id=?
        """,
        (title, body, body_json, importance, new_version, t, actor_user_id, t, ann["id"]),
    )
    c.execute(
        """INSERT INTO announcement_revisions
        (id,announcement_id,version_no,title,body,body_json,status_snapshot,edited_by,created_at,published_at)
        VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (str(uuid.uuid4()), ann["id"], new_version, title, body, body_json, "PUBLISHED", actor_user_id, t, t),
    )
    return True, new_version


def _outbound_notice_url(slug: str) -> str:
    base = os.getenv("APP_BASE_URL", "").strip().rstrip("/")
    return f"{base}/a/{slug}" if base else f"/a/{slug}"


def _hass_notice_path(slug: str) -> str:
    return f"/app/notice_board/a/{slug}"


def queue_notification_event(announcement_id: str, event_type: str, source: str, version: int | None = None) -> int:
    """Queue one recipient-specific event for the Notification Gateway.

    Notice Board owns event creation and retry state. The Gateway owns HASS/ntfy routing.
    """
    if not NOTIFICATION_GATEWAY_ENABLED or not NOTIFICATION_GATEWAY_URL:
        return 0
    queued = 0
    with conn() as c:
        ann = c.execute(
            """SELECT a.*, u.username AS creator_username, u.display_name AS creator_display_name
            FROM announcements a JOIN users u ON u.id=a.created_by WHERE a.id=?""",
            (announcement_id,),
        ).fetchone()
        if not ann:
            return 0
        recipients = c.execute(
            "SELECT username FROM users WHERE is_active=1 AND role!='BOT' ORDER BY username COLLATE NOCASE"
        ).fetchall()
        for recipient in recipients:
            event_id = str(uuid.uuid4())
            current_version = int(version if version is not None else ann["current_version"] or 0)
            payload = {
                "event_id": event_id,
                "event": event_type,
                "announcement_id": ann["id"],
                "slug": ann["slug"],
                "version": current_version,
                "previous_version": current_version - 1 if current_version > 1 else None,
                "title": ann["title"],
                "url": _outbound_notice_url(ann["slug"]),
                "hass_path": _hass_notice_path(ann["slug"]),
                "source": source,
                "published_at": ann["published_at"],
                "recipient": recipient["username"],
                "creator": {
                    "username": ann["creator_username"],
                    "display_name": ann["creator_display_name"],
                },
            }
            t = now_iso()
            c.execute(
                """INSERT INTO webhook_deliveries
                (id,event_id,event_type,announcement_id,version_no,recipient_username,payload_json,status,attempts,created_at,next_attempt_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (str(uuid.uuid4()), event_id, event_type, ann["id"], current_version, recipient["username"], json.dumps(payload, ensure_ascii=False, separators=(",", ":")), "PENDING", 0, t, t),
            )
            queued += 1
    return queued


def cleanup_stale_pending_uploads(hours: int = 24) -> int:
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    removed = 0
    with conn() as c:
        rows = c.execute(
            "SELECT id,storage_name FROM attachments WHERE announcement_id IS NULL AND deleted_at IS NULL AND created_at<?",
            (cutoff,),
        ).fetchall()
        for row in rows:
            try:
                (UPLOAD_DIR / row["storage_name"]).unlink(missing_ok=True)
            except OSError:
                continue
            c.execute("UPDATE attachments SET deleted_at=? WHERE id=?", (now_iso(), row["id"]))
            removed += 1
    return removed


def deliver_pending_notification_events(limit: int = 50) -> dict[str, int]:
    if not NOTIFICATION_GATEWAY_ENABLED or not NOTIFICATION_GATEWAY_URL:
        return {"delivered": 0, "failed": 0}
    delivered = 0
    failed = 0
    now = now_iso()
    with conn() as c:
        rows = c.execute(
            """SELECT * FROM webhook_deliveries
            WHERE status IN ('PENDING','FAILED') AND attempts<5
              AND (next_attempt_at IS NULL OR next_attempt_at<=?)
            ORDER BY created_at ASC LIMIT ?""",
            (now, limit),
        ).fetchall()
    for row in rows:
        attempt_no = int(row["attempts"] or 0) + 1
        error = None
        try:
            body = row["payload_json"].encode("utf-8")
            headers = {
                "Content-Type": "application/json",
                "User-Agent": "NoticeBoard/2.3",
            }
            if NOTIFICATION_GATEWAY_TOKEN:
                headers["X-Notification-Gateway-Token"] = NOTIFICATION_GATEWAY_TOKEN
            req = UrlRequest(NOTIFICATION_GATEWAY_URL, data=body, method="POST", headers=headers)
            with urlopen(req, timeout=NOTIFICATION_GATEWAY_TIMEOUT) as response:
                code = getattr(response, "status", 200)
                if code < 200 or code >= 300:
                    raise RuntimeError(f"HTTP {code}")
        except (HTTPError, URLError, TimeoutError, OSError, RuntimeError) as exc:
            error = str(exc)[:500]

        t = now_iso()
        with conn() as c:
            if error is None:
                c.execute(
                    "UPDATE webhook_deliveries SET status='DELIVERED',attempts=?,last_error=NULL,last_attempt_at=?,next_attempt_at=NULL,delivered_at=? WHERE id=?",
                    (attempt_no, t, t, row["id"]),
                )
                delivered += 1
            else:
                delay_seconds = min(3600, 30 * (2 ** max(0, attempt_no - 1)))
                next_try = (datetime.now(timezone.utc) + timedelta(seconds=delay_seconds)).isoformat()
                c.execute(
                    "UPDATE webhook_deliveries SET status='FAILED',attempts=?,last_error=?,last_attempt_at=?,next_attempt_at=? WHERE id=?",
                    (attempt_no, error, t, next_try, row["id"]),
                )
                failed += 1
    return {"delivered": delivered, "failed": failed}


def run_scheduler_once() -> dict[str, int | bool]:
    """Run one publish/expiry scan. An active BOT account is required for automated changes."""
    events: list[tuple[str, str, str, dict]] = []
    published = 0
    expired = 0
    with conn() as c:
        bot = c.execute("SELECT * FROM users WHERE role='BOT' AND is_active=1 ORDER BY created_at ASC LIMIT 1").fetchone()
        if not bot:
            return {"bot_available": False, "published": 0, "expired": 0}
        now = now_iso()
        due = c.execute(
            """
            SELECT * FROM announcements
            WHERE deleted_at IS NULL AND scheduled_publish_at IS NOT NULL AND scheduled_publish_at<=?
              AND (current_version=0 OR has_unpublished_changes=1 OR status IN ('WITHDRAWN','EXPIRED'))
            ORDER BY scheduled_publish_at ASC
            """,
            (now,),
        ).fetchall()
        for ann in due:
            changed, version = _publish_in_connection(c, ann, bot["id"], automated=True)
            if changed:
                published += 1
                events.append((bot["id"], "announcement.scheduled_publish", ann["id"], {"version": version}))

        expiring = c.execute(
            """SELECT * FROM announcements
            WHERE deleted_at IS NULL AND status='PUBLISHED' AND expires_at IS NOT NULL AND expires_at<=?""",
            (now,),
        ).fetchall()
        for ann in expiring:
            t = now_iso()
            c.execute("UPDATE announcements SET status='EXPIRED', expired_at=?, updated_by=?, updated_at=? WHERE id=?", (t, bot["id"], t, ann["id"]))
            expired += 1
            events.append((bot["id"], "announcement.expire", ann["id"], {"version": ann["current_version"]}))

    for actor, action, entity_id, metadata in events:
        audit(actor, action, "announcement", entity_id, metadata)
        if action == "announcement.scheduled_publish":
            version = int(metadata.get("version") or 0)
            queue_notification_event(entity_id, "announcement.published" if version == 1 else "announcement.updated", "scheduler", version)
        elif action == "announcement.expire":
            queue_notification_event(entity_id, "announcement.expired", "scheduler", int(metadata.get("version") or 0))
    deliver_pending_notification_events()
    return {"bot_available": True, "published": published, "expired": expired}


async def scheduler_loop() -> None:
    while True:
        try:
            await asyncio.to_thread(run_scheduler_once)
            # Delivery retries and abandoned upload cleanup do not depend on a BOT account being active.
            await asyncio.to_thread(deliver_pending_notification_events)
            await asyncio.to_thread(cleanup_stale_pending_uploads)
        except Exception as exc:
            print(f"[noticeboard] scheduler error: {exc}")
        await asyncio.sleep(SCHEDULER_INTERVAL)


def _announcement_editor_context(c, ann_id: str):
    ann = c.execute(
        "SELECT a.*, u.display_name AS author_name FROM announcements a JOIN users u ON u.id=a.created_by WHERE a.id=? AND a.deleted_at IS NULL",
        (ann_id,),
    ).fetchone()
    if not ann:
        raise HTTPException(404)
    revs = c.execute(
        """
        SELECT r.*, u.display_name AS editor_name,
          (SELECT COUNT(*) FROM announcement_reads_v2 ar WHERE ar.announcement_id=r.announcement_id AND ar.version_no=r.version_no) AS read_count
        FROM announcement_revisions r JOIN users u ON u.id=r.edited_by
        WHERE r.announcement_id=? ORDER BY COALESCE(r.version_no,0) DESC, r.created_at DESC LIMIT 30
        """,
        (ann_id,),
    ).fetchall()
    bot = c.execute("SELECT id,display_name FROM users WHERE role='BOT' AND is_active=1 ORDER BY created_at ASC LIMIT 1").fetchone()
    return ann, revs, bot


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    try:
        user = require_user(request, "/")
    except HTTPException as exc:
        return auth_redirect_for(exc)
    with conn() as c:
        rows = c.execute(
            """
            SELECT a.*, u.display_name AS author_name,
              (SELECT COUNT(*) FROM announcement_reads_v2 r WHERE r.announcement_id=a.id AND r.version_no=a.current_version) AS read_count,
              EXISTS(SELECT 1 FROM announcement_reads_v2 rr WHERE rr.announcement_id=a.id AND rr.version_no=a.current_version AND rr.user_id=?) AS has_read_current
            FROM announcements a JOIN users u ON u.id=a.created_by
            WHERE a.deleted_at IS NULL AND a.status='PUBLISHED'
            ORDER BY COALESCE(a.published_at,a.updated_at) DESC
            """,
            (user["id"],),
        ).fetchall()
    reads_by_announcement = {}
    if rows:
        with conn() as c:
            ids = [r["id"] for r in rows]
            placeholders = ",".join("?" for _ in ids)
            receipts = c.execute(
                f"""SELECT r.announcement_id,r.first_seen_at,u.display_name,u.username,u.avatar_url
                    FROM announcement_reads_v2 r
                    JOIN announcements a ON a.id=r.announcement_id AND a.current_version=r.version_no
                    JOIN users u ON u.id=r.user_id
                    WHERE r.announcement_id IN ({placeholders}) AND a.status='PUBLISHED'
                    ORDER BY r.first_seen_at""", ids,
            ).fetchall()
            for receipt in receipts:
                item = dict(receipt)
                reads_by_announcement.setdefault(item["announcement_id"], []).append(item)
    anns = []
    for r in rows:
        d = dict(r) | {"url": announcement_url(request, r["slug"])}
        d["reads"] = reads_by_announcement.get(r["id"], [])
        d["body_doc"] = load_doc(d.get("body_json"), d.get("body", ""))
        d["context_label"] = announcement_context(user, d)
        d["can_edit"] = can_edit(user, d)
        d["can_withdraw"] = can_withdraw(user, d)
        anns.append(d)
    today = datetime.now(DISPLAY_TZ)
    weekdays = ("星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日")
    dashboard_summary = {
        "total": len(anns),
        "published": sum(1 for ann in anns if ann["status"] == "PUBLISHED"),
        "unread": sum(1 for ann in anns if ann["status"] == "PUBLISHED" and not ann["has_read_current"]),
        "pending": sum(1 for ann in anns if ann["has_unpublished_changes"]),
    }
    return render(
        request,
        "dashboard.html",
        announcements=anns,
        dashboard_date=today.strftime("%Y年%m月%d日"),
        dashboard_weekday=weekdays[today.weekday()],
        dashboard_summary=dashboard_summary,
    )


@app.get("/api/unread-count")
def unread_count_api(request: Request):
    user = require_user(request, "/")
    return {"unread_count": unread_count_for_user(user)}


@app.post("/api/announcements/{announcement_id}/read")
def mark_announcement_read(request: Request, announcement_id: str, version: int = Form(...), csrf_token: str = Form(...)):
    """A short expanded card or a fully scrolled document can create a read receipt."""
    require_csrf(request, csrf_token)
    user = require_user(request)
    if user["role"] == "BOT":
        raise HTTPException(403, "機器人不能產生已閱紀錄")
    with conn() as c:
        ann = c.execute(
            "SELECT id,current_version,status FROM announcements WHERE id=? AND deleted_at IS NULL",
            (announcement_id,),
        ).fetchone()
        if not ann or ann["status"] != "PUBLISHED":
            raise HTTPException(404, "公告不存在")
        if int(ann["current_version"]) != version:
            raise HTTPException(409, "公告已更新，請重新讀取新版")
        c.execute(
            "INSERT OR IGNORE INTO announcement_reads_v2 (announcement_id,version_no,user_id,first_seen_at) VALUES (?,?,?,?)",
            (ann["id"], version, user["id"], now_iso()),
        )
        count = c.execute(
            "SELECT COUNT(*) AS n FROM announcement_reads_v2 WHERE announcement_id=? AND version_no=?",
            (ann["id"], version),
        ).fetchone()["n"]
    return {"ok": True, "read_count": count, "reader": {"display_name": user["display_name"], "username": user["username"]}}


@app.get("/a/{slug}", response_class=HTMLResponse)
def announcement_view(request: Request, slug: str):
    try:
        user = require_user(request, f"/a/{slug}")
    except HTTPException as exc:
        return auth_redirect_for(exc)
    with conn() as c:
        ann = c.execute(
            "SELECT a.*, u.display_name AS author_name FROM announcements a JOIN users u ON u.id=a.created_by WHERE a.slug=? AND a.deleted_at IS NULL",
            (slug,),
        ).fetchone()
        if not ann:
            raise HTTPException(404, "公告不存在")
        owner_preview = is_owner(user, ann)
        if ann["status"] != "PUBLISHED" and not owner_preview:
            raise HTTPException(404, "公告不存在")
        comparison = None
        if ann["status"] == "PUBLISHED" and user["role"] != "BOT" and ann["current_version"] > 0:
            # Compare the current version with this user's most recent previously viewed version.
            # Intentionally skip versions the user never viewed: v1 -> v3 is compared directly
            # when v2 was missed. The current-version read receipt is then recorded separately.
            previous_read = c.execute(
                """
                SELECT MAX(version_no) AS version_no
                FROM announcement_reads_v2
                WHERE announcement_id=? AND user_id=? AND version_no<?
                """,
                (ann["id"], user["id"], ann["current_version"]),
            ).fetchone()
            previous_version = previous_read["version_no"] if previous_read else None
            if previous_version:
                previous_revision = c.execute(
                    "SELECT version_no,title,body,body_json,published_at FROM announcement_revisions WHERE announcement_id=? AND version_no=?",
                    (ann["id"], previous_version),
                ).fetchone()
                if previous_revision:
                    body_segments = inline_revision_diff(previous_revision["body"], ann["body"])
                    comparison = {
                        "from_version": previous_revision["version_no"],
                        "to_version": ann["current_version"],
                        "body_segments": body_segments,
                        "body_changed": any(seg["kind"] != "same" for seg in body_segments),
                        "rich_changed": (previous_revision["body_json"] or "") != (ann["body_json"] or ""),
                    }
        reads = c.execute(
            """
            SELECT r.first_seen_at, u.id, u.username, u.display_name, u.avatar_url
            FROM announcement_reads_v2 r JOIN users u ON u.id=r.user_id
            WHERE r.announcement_id=? AND r.version_no=? ORDER BY r.first_seen_at ASC
            """,
            (ann["id"], ann["current_version"]),
        ).fetchall()
        comments = c.execute(
            """
            SELECT cm.*, u.display_name, u.username, u.avatar_url
            FROM comments cm JOIN users u ON u.id=cm.user_id
            WHERE cm.announcement_id=? AND cm.deleted_at IS NULL ORDER BY cm.created_at ASC
            """,
            (ann["id"],),
        ).fetchall()
    ann_d = dict(ann)
    ann_d["body_doc"] = load_doc(ann_d.get("body_json"), ann_d.get("body", ""))
    return render(
        request,
        "announcement.html",
        announcement=ann_d,
        reads=[dict(r) for r in reads],
        comments=[dict(r) for r in comments],
        context_label=announcement_context(user, ann_d),
        can_edit=can_edit(user, ann_d),
        can_withdraw=can_withdraw(user, ann_d),
        comparison=comparison,
    )


@app.post("/a/{slug}/comments")
def comment_add(request: Request, slug: str, body: str = Form(...), csrf_token: str = Form(...)):
    require_csrf(request, csrf_token)
    try:
        user = require_user(request, f"/a/{slug}")
    except HTTPException as exc:
        return auth_redirect_for(exc)
    if user["role"] == "BOT":
        raise HTTPException(403, "機器人帳號不可留言")
    body = body.strip()
    if not body or len(body) > 2000:
        return RedirectResponse(f"/a/{slug}", status_code=303)
    with conn() as c:
        ann = c.execute("SELECT id,current_version FROM announcements WHERE slug=? AND status='PUBLISHED' AND deleted_at IS NULL", (slug,)).fetchone()
        if not ann:
            raise HTTPException(404)
        cid = str(uuid.uuid4())
        c.execute(
            "INSERT INTO comments (id,announcement_id,user_id,version_no,body,created_at) VALUES (?,?,?,?,?,?)",
            (cid, ann["id"], user["id"], ann["current_version"], body, now_iso()),
        )
    audit(user["id"], "comment.create", "comment", cid, {"announcement_id": ann["id"], "version": ann["current_version"]})
    return RedirectResponse(f"/a/{slug}#comments", status_code=303)


@app.post("/comments/{comment_id}/delete")
def comment_delete(request: Request, comment_id: str, csrf_token: str = Form(...)):
    require_csrf(request, csrf_token)
    try:
        user = require_user(request)
    except HTTPException as exc:
        return auth_redirect_for(exc)
    with conn() as c:
        row = c.execute("SELECT cm.*, a.slug FROM comments cm JOIN announcements a ON a.id=cm.announcement_id WHERE cm.id=?", (comment_id,)).fetchone()
        if not row:
            raise HTTPException(404)
        # Comments stay flat; each user may remove only their own comment.
        if row["user_id"] != user["id"]:
            raise HTTPException(403)
        c.execute("UPDATE comments SET deleted_at=? WHERE id=?", (now_iso(), comment_id))
    audit(user["id"], "comment.delete", "comment", comment_id)
    return RedirectResponse(f"/a/{row['slug']}#comments", status_code=303)


@app.post("/api/attachments/pdf")
async def pdf_upload(
    request: Request,
    file: UploadFile = File(...),
    upload_session: str = Form(...),
    announcement_id: str = Form(""),
    csrf_token: str = Form(...),
):
    require_csrf(request, csrf_token)
    user = require_user(request)
    require_announcement_author(user)
    if not upload_session or len(upload_session) > 200:
        return JSONResponse({"error": "上傳工作階段無效"}, status_code=400)
    if announcement_id:
        with conn() as c:
            ann = c.execute("SELECT * FROM announcements WHERE id=? AND deleted_at IS NULL", (announcement_id,)).fetchone()
            if not ann or not can_edit(user, ann):
                raise HTTPException(403, "只有公告建立者可以上傳附件")

    data = await file.read(MAX_PDF_UPLOAD_BYTES + 1)
    if len(data) > MAX_PDF_UPLOAD_BYTES:
        return JSONResponse({"error": f"PDF 不可超過 {MAX_PDF_UPLOAD_BYTES // (1024*1024)} MB"}, status_code=413)
    if not data.startswith(b"%PDF-"):
        return JSONResponse({"error": "檔案不是有效的 PDF"}, status_code=400)
    try:
        reader = PdfReader(io.BytesIO(data), strict=False)
        if reader.is_encrypted:
            try:
                reader.decrypt("")
            except Exception:
                pass
        page_count = len(reader.pages)
    except Exception:
        return JSONResponse({"error": "無法讀取 PDF；請確認檔案未損毀或受密碼保護"}, status_code=400)
    if page_count < 1:
        return JSONResponse({"error": "PDF 沒有可讀取的頁面"}, status_code=400)

    file_id = str(uuid.uuid4())
    original_name = Path(file.filename or "attachment.pdf").name[:180] or "attachment.pdf"
    storage_name = f"{file_id}.pdf"
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    (UPLOAD_DIR / storage_name).write_bytes(data)
    t = now_iso()
    with conn() as c:
        c.execute(
            """INSERT INTO attachments
            (id,announcement_id,upload_session,uploaded_by,original_name,storage_name,content_type,size_bytes,page_count,created_at,attached_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (file_id, None, upload_session, user["id"], original_name, storage_name, "application/pdf", len(data), page_count, t, None),
        )
    audit(user["id"], "attachment.pdf_upload", "attachment", file_id, {"pages": page_count, "size_bytes": len(data), "announcement_id": announcement_id or None})
    return {
        "file_id": file_id,
        "file_name": original_name,
        "page_count": page_count,
        "size_bytes": len(data),
        "inline_allowed": page_count <= INLINE_PDF_MAX_PAGES,
        "default_display_mode": "attachment",
    }


@app.get("/files/{attachment_id}")
def attachment_file(request: Request, attachment_id: str, download: bool = False):
    user = require_user(request, request.url.path)
    with conn() as c:
        row = c.execute(
            """SELECT at.*, a.status AS announcement_status, a.created_by
            FROM attachments at LEFT JOIN announcements a ON a.id=at.announcement_id
            WHERE at.id=? AND at.deleted_at IS NULL""",
            (attachment_id,),
        ).fetchone()
        if not row:
            raise HTTPException(404)
        if row["announcement_id"]:
            owner = user["role"] in {"USER", "SYSADMIN"} and user["id"] == row["created_by"]
            if not owner:
                if row["announcement_status"] != "PUBLISHED":
                    raise HTTPException(404)
                published = c.execute("SELECT body,body_json FROM announcements WHERE id=?", (row["announcement_id"],)).fetchone()
                if not published or attachment_id not in attachment_ids(load_doc(published["body_json"], published["body"] or "")):
                    # A PDF uploaded for an unpublished update must not leak through its UUID.
                    raise HTTPException(404)
        elif row["uploaded_by"] != user["id"]:
            raise HTTPException(404)
        path = UPLOAD_DIR / row["storage_name"]
        if not path.is_file():
            raise HTTPException(404)
        filename = row["original_name"]
    return FileResponse(path, media_type="application/pdf", filename=filename, content_disposition_type="attachment" if download else "inline")


@app.get("/manage/announcements/new", response_class=HTMLResponse)
def announcement_new_page(request: Request):
    try:
        user = require_user(request, "/manage/announcements/new")
        require_announcement_author(user)
    except HTTPException as exc:
        if exc.status_code == 401:
            return auth_redirect_for(exc)
        raise
    with conn() as c:
        bot = c.execute("SELECT id,display_name FROM users WHERE role='BOT' AND is_active=1 ORDER BY created_at ASC LIMIT 1").fetchone()
    return render(request, "announcement_edit.html", announcement=None, revisions=[], error=None, active_bot=dict(bot) if bot else None, can_edit=True, can_publish=True, can_withdraw=False, can_delete=False, editor_doc=legacy_text_doc(""), upload_session=token())


@app.post("/manage/announcements/new")
def announcement_new_action(
    request: Request,
    title: str = Form(...),
    body: str = Form(""),
    body_json: str = Form(""),
    upload_session: str = Form(""),
    importance: str = Form("NORMAL"),
    scheduled_publish_at: str = Form(""),
    expires_at: str = Form(""),
    publish_now: str = Form(""),
    csrf_token: str = Form(...),
):
    require_csrf(request, csrf_token)
    user = require_user(request)
    require_announcement_author(user)
    title = title.strip()
    aid = str(uuid.uuid4())
    slug = slugify_title(title)
    t = now_iso()
    scheduled = local_form_to_utc(scheduled_publish_at)
    expires = local_form_to_utc(expires_at)
    published_version = None
    try:
        if importance not in IMPORTANCE_LABELS:
            raise ValueError("公告重要性無效")
        with conn() as c:
            doc, text = _submission_doc(c, user, body_json, body, None, upload_session)
            if not title or not text:
                raise ValueError("標題與內容不可空白")
            c.execute(
                """INSERT INTO announcements
                (id,slug,title,body,body_json,importance,status,current_version,scheduled_publish_at,expires_at,created_by,updated_by,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (aid, slug, title, text, dump_doc(doc), importance, "DRAFT", 0, scheduled, expires, user["id"], user["id"], t, t),
            )
            _bind_pending_attachments(c, aid, user["id"], upload_session, doc)
            if publish_now:
                fresh = c.execute("SELECT * FROM announcements WHERE id=?", (aid,)).fetchone()
                changed, version = _publish_in_connection(c, fresh, user["id"])
                if changed:
                    published_version = version
    except ValueError as exc:
        try:
            submitted_doc = load_doc(body_json, body)
        except Exception:
            submitted_doc = legacy_text_doc(body)
        return render(request, "announcement_edit.html", announcement={"title": title, "body": body, "importance": importance}, revisions=[], error=str(exc), active_bot=None, can_edit=True, can_publish=True, can_withdraw=False, can_delete=False, editor_doc=submitted_doc, upload_session=upload_session or token())
    audit(user["id"], "announcement.create_draft", "announcement", aid, {"scheduled_publish_at": scheduled, "expires_at": expires})
    if published_version is not None:
        audit(user["id"], "announcement.publish", "announcement", aid, {"version": published_version})
        queue_notification_event(aid, "announcement.published", "manual", published_version)
    return RedirectResponse(f"/manage/announcements/{aid}/edit", status_code=303)


@app.get("/manage/announcements/{announcement_id}/edit", response_class=HTMLResponse)
def announcement_edit_page(request: Request, announcement_id: str):
    try:
        user = require_user(request, request.url.path)
    except HTTPException as exc:
        return auth_redirect_for(exc)
    with conn() as c:
        ann, revs, bot = _announcement_editor_context(c, announcement_id)
        if not can_edit(user, ann):
            raise HTTPException(403, "只有公告建立者可以編輯")
    ann_d = dict(ann) | {"url": announcement_url(request, ann["slug"])}
    return render(
        request,
        "announcement_edit.html",
        announcement=ann_d,
        revisions=[dict(r) for r in revs],
        error=None,
        active_bot=dict(bot) if bot else None,
        can_edit=True,
        can_publish=True,
        can_withdraw=can_withdraw(user, ann_d),
        can_delete=can_delete(user, ann_d),
        editor_doc=_editor_doc(ann),
        upload_session=token(),
    )


@app.post("/manage/announcements/{announcement_id}/edit")
def announcement_edit_action(
    request: Request,
    announcement_id: str,
    title: str = Form(...),
    body: str = Form(""),
    body_json: str = Form(""),
    upload_session: str = Form(""),
    importance: str = Form("NORMAL"),
    scheduled_publish_at: str = Form(""),
    expires_at: str = Form(""),
    publish_now: str = Form(""),
    csrf_token: str = Form(...),
):
    require_csrf(request, csrf_token)
    user = require_user(request)
    title = title.strip()
    scheduled = local_form_to_utc(scheduled_publish_at)
    expires = local_form_to_utc(expires_at)
    with conn() as c:
        ann, revs, bot = _announcement_editor_context(c, announcement_id)
        if not can_edit(user, ann):
            raise HTTPException(403, "只有公告建立者可以編輯")
        try:
            if importance not in IMPORTANCE_LABELS:
                raise ValueError("公告重要性無效")
            doc, text = _submission_doc(c, user, body_json, body, announcement_id, upload_session)
            if not title or not text:
                raise ValueError("標題與內容不可空白")
            _bind_pending_attachments(c, announcement_id, user["id"], upload_session, doc)
        except ValueError as exc:
            ann_d = dict(ann)
            ann_d["draft_title"] = title
            ann_d["draft_body"] = body
            ann_d["draft_body_json"] = body_json or dump_doc(legacy_text_doc(body))
            ann_d["draft_importance"] = importance
            ann_d["has_unpublished_changes"] = 1 if ann["current_version"] else 0
            return render(request, "announcement_edit.html", announcement=ann_d, revisions=[dict(r) for r in revs], error=str(exc), active_bot=dict(bot) if bot else None, can_edit=True, can_publish=True, can_withdraw=can_withdraw(user, ann_d), can_delete=can_delete(user, ann_d), editor_doc=load_doc(ann_d["draft_body_json"], body), upload_session=upload_session or token())
        t = now_iso()
        doc_json = dump_doc(doc)
        if ann["current_version"] > 0:
            c.execute(
                """UPDATE announcements SET draft_title=?,draft_body=?,draft_body_json=?,draft_importance=?,has_unpublished_changes=1,
                scheduled_publish_at=?,expires_at=?,updated_by=?,updated_at=? WHERE id=?""",
                (title, text, doc_json, importance, scheduled, expires, user["id"], t, announcement_id),
            )
        else:
            c.execute(
                """UPDATE announcements SET title=?,body=?,body_json=?,importance=?,scheduled_publish_at=?,expires_at=?,updated_by=?,updated_at=? WHERE id=?""",
                (title, text, doc_json, importance, scheduled, expires, user["id"], t, announcement_id),
            )

        published_version = None
        if publish_now:
            fresh = c.execute("SELECT * FROM announcements WHERE id=? AND deleted_at IS NULL", (announcement_id,)).fetchone()
            changed, version = _publish_in_connection(c, fresh, user["id"])
            if changed:
                published_version = version

    audit(user["id"], "announcement.edit_draft", "announcement", announcement_id, {"published_version": ann["current_version"], "scheduled_publish_at": scheduled, "expires_at": expires})
    if published_version is not None:
        audit(user["id"], "announcement.publish", "announcement", announcement_id, {"version": published_version})
        queue_notification_event(
            announcement_id,
            "announcement.published" if published_version == 1 else "announcement.updated",
            "manual",
            published_version,
        )
    return RedirectResponse(f"/manage/announcements/{announcement_id}/edit", status_code=303)


@app.post("/manage/announcements/{announcement_id}/publish")
def announcement_publish(request: Request, announcement_id: str, csrf_token: str = Form(...)):
    require_csrf(request, csrf_token)
    user = require_user(request)
    with conn() as c:
        ann = c.execute("SELECT * FROM announcements WHERE id=? AND deleted_at IS NULL", (announcement_id,)).fetchone()
        if not ann:
            raise HTTPException(404)
        if not can_publish(user, ann):
            raise HTTPException(403, "只有公告建立者可以發布")
        changed, version = _publish_in_connection(c, ann, user["id"])
    if changed:
        audit(user["id"], "announcement.publish", "announcement", announcement_id, {"version": version})
        queue_notification_event(announcement_id, "announcement.published" if version == 1 else "announcement.updated", "manual", version)
    return RedirectResponse(f"/manage/announcements/{announcement_id}/edit", status_code=303)


@app.post("/manage/announcements/{announcement_id}/withdraw")
@app.post("/manage/announcements/{announcement_id}/unpublish")
def announcement_withdraw(request: Request, announcement_id: str, csrf_token: str = Form(...)):
    require_csrf(request, csrf_token)
    user = require_user(request)
    with conn() as c:
        ann = c.execute("SELECT * FROM announcements WHERE id=? AND deleted_at IS NULL", (announcement_id,)).fetchone()
        if not ann:
            raise HTTPException(404)
        if not can_withdraw(user, ann):
            raise HTTPException(403, "你沒有撤下這則公告的權限")
        t = now_iso()
        c.execute("UPDATE announcements SET status='WITHDRAWN',withdrawn_at=?,scheduled_publish_at=NULL,updated_by=?,updated_at=? WHERE id=?", (t, user["id"], t, announcement_id))
    audit(user["id"], "announcement.withdraw", "announcement", announcement_id, {"version": ann["current_version"]})
    queue_notification_event(announcement_id, "announcement.withdrawn", "manual", int(ann["current_version"] or 0))
    if is_owner(user, ann):
        return RedirectResponse(f"/manage/announcements/{announcement_id}/edit", status_code=303)
    return RedirectResponse("/", status_code=303)


@app.post("/manage/announcements/{announcement_id}/delete")
def announcement_delete(request: Request, announcement_id: str, csrf_token: str = Form(...)):
    require_csrf(request, csrf_token)
    user = require_user(request)
    with conn() as c:
        ann = c.execute("SELECT * FROM announcements WHERE id=? AND deleted_at IS NULL", (announcement_id,)).fetchone()
        if not ann:
            raise HTTPException(404)
        if not can_delete(user, ann):
            raise HTTPException(403, "只有公告建立者可刪除未發布／已撤下公告")
        c.execute("UPDATE announcements SET deleted_at=?,updated_by=?,updated_at=? WHERE id=?", (now_iso(), user["id"], now_iso(), announcement_id))
    audit(user["id"], "announcement.soft_delete", "announcement", announcement_id)
    return RedirectResponse("/", status_code=303)



@app.get("/manage/hass-admin", response_class=HTMLResponse)
def hass_admin_settings(request: Request):
    try:
        user = require_user(request, "/manage/hass-admin")
        require_sysadmin(user)
    except HTTPException as exc:
        if exc.status_code == 401:
            return auth_redirect_for(exc)
        raise
    error = None
    try:
        admins = list_ha_administrators()
    except HAAdminDirectoryUnavailable as exc:
        admins = []
        error = str(exc)
    with conn() as c:
        linked = [dict(r) for r in c.execute(
            "SELECT username,display_name FROM users WHERE role='SYSADMIN' AND is_active=1 AND hass_user_id IS NOT NULL ORDER BY display_name"
        ).fetchall()]
    return render(request, "hass_admin.html", admins=admins, linked=linked, error=error)


@app.post("/manage/hass-admin/bind")
def hass_admin_bind(request: Request, hass_user_id: str = Form(...), csrf_token: str = Form(...)):
    require_csrf(request, csrf_token)
    admin = require_user(request)
    require_sysadmin(admin)
    eligible = {row["id"] for row in list_ha_administrators()}
    if hass_user_id not in eligible:
        raise HTTPException(400, "所選帳號不是目前有效的 HA 管理員")
    with conn() as c:
        existing = c.execute("SELECT id,role FROM users WHERE hass_user_id=?", (hass_user_id,)).fetchone()
        if existing:
            if existing["role"] == "BOT":
                raise HTTPException(409, "機器人帳號不能綁定管理員")
            c.execute("UPDATE users SET role='SYSADMIN',updated_at=? WHERE id=?", (now_iso(), existing["id"]))
            target_id = existing["id"]
        else:
            current = c.execute("SELECT hass_user_id FROM users WHERE id=?", (admin["id"],)).fetchone()
            if not current or current["hass_user_id"] not in (None, hass_user_id):
                raise HTTPException(409, "目前帳號已綁定另一位 HA 使用者，請先在帳號管理解除舊綁定")
            c.execute("UPDATE users SET hass_user_id=?,updated_at=? WHERE id=?", (hass_user_id, now_iso(), admin["id"]))
            target_id = admin["id"]
    reset_admin_cache()
    audit(admin["id"], "account.bind_hass_admin", "user", target_id, {"hass_user_id": hass_user_id})
    return RedirectResponse("/manage/hass-admin", status_code=303)


@app.get("/manage/accounts", response_class=HTMLResponse)
def accounts_page(request: Request):
    try:
        user = require_user(request, "/manage/accounts")
        require_sysadmin(user)
    except HTTPException as exc:
        if exc.status_code == 401:
            return auth_redirect_for(exc)
        raise
    with conn() as c:
        users = [dict(r) for r in c.execute(
            """SELECT u.*, (SELECT COUNT(*) FROM devices d WHERE d.user_id=u.id AND d.revoked_at IS NULL AND d.expires_at>?) AS device_count
            FROM users u ORDER BY u.display_name COLLATE NOCASE""", (now_iso(),)
        ).fetchall()]
    return render(request, "accounts.html", users=users, error=None)


@app.post("/manage/accounts/create")
def account_create(request: Request, username: str = Form(...), display_name: str = Form(...), role: str = Form(...), temp_password: str = Form(...), csrf_token: str = Form(...)):
    require_csrf(request, csrf_token)
    admin = require_user(request)
    require_sysadmin(admin)
    username, display_name = username.strip(), display_name.strip()
    if role not in ACCOUNT_LEVEL_LABELS or len(temp_password) < 12 or not username or not display_name:
        return RedirectResponse("/manage/accounts", status_code=303)
    uid = str(uuid.uuid4())
    t = now_iso()
    try:
        with conn() as c:
            c.execute("INSERT INTO users (id,username,display_name,password_hash,role,must_change_password,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)", (uid, username, display_name, hash_password(temp_password), role, 1, t, t))
    except Exception:
        return RedirectResponse("/manage/accounts?error=duplicate", status_code=303)
    audit(admin["id"], "account.create", "user", uid, {"account_level": role})
    return RedirectResponse("/manage/accounts", status_code=303)


@app.post("/manage/accounts/{user_id}/role")
def account_role(request: Request, user_id: str, role: str = Form(...), csrf_token: str = Form(...)):
    require_csrf(request, csrf_token)
    admin = require_user(request)
    require_sysadmin(admin)
    if role not in ACCOUNT_LEVEL_LABELS or user_id == admin["id"]:
        return RedirectResponse("/manage/accounts", status_code=303)
    with conn() as c:
        c.execute("UPDATE users SET role=?, updated_at=? WHERE id=?", (role, now_iso(), user_id))
    audit(admin["id"], "account.level_change", "user", user_id, {"account_level": role})
    return RedirectResponse("/manage/accounts", status_code=303)


@app.post("/manage/accounts/{user_id}/toggle-active")
def account_toggle_active(request: Request, user_id: str, csrf_token: str = Form(...)):
    require_csrf(request, csrf_token)
    admin = require_user(request)
    require_sysadmin(admin)
    if user_id == admin["id"]:
        return RedirectResponse("/manage/accounts", status_code=303)
    with conn() as c:
        row = c.execute("SELECT is_active FROM users WHERE id=?", (user_id,)).fetchone()
        if row:
            new = 0 if row["is_active"] else 1
            c.execute("UPDATE users SET is_active=?, updated_at=? WHERE id=?", (new, now_iso(), user_id))
            if not new:
                c.execute("UPDATE devices SET revoked_at=? WHERE user_id=? AND revoked_at IS NULL", (now_iso(), user_id))
    audit(admin["id"], "account.toggle_active", "user", user_id)
    return RedirectResponse("/manage/accounts", status_code=303)


@app.post("/manage/accounts/{user_id}/reset-password")
def account_reset_password(request: Request, user_id: str, temp_password: str = Form(...), csrf_token: str = Form(...)):
    require_csrf(request, csrf_token)
    admin = require_user(request)
    require_sysadmin(admin)
    if len(temp_password) < 12:
        return RedirectResponse("/manage/accounts", status_code=303)
    with conn() as c:
        c.execute("UPDATE users SET password_hash=?, must_change_password=1, updated_at=? WHERE id=?", (hash_password(temp_password), now_iso(), user_id))
        c.execute("UPDATE devices SET revoked_at=? WHERE user_id=? AND revoked_at IS NULL", (now_iso(), user_id))
    audit(admin["id"], "account.reset_password", "user", user_id)
    return RedirectResponse("/manage/accounts", status_code=303)


@app.post("/manage/accounts/{user_id}/reset-2fa")
def account_reset_2fa(request: Request, user_id: str, csrf_token: str = Form(...)):
    require_csrf(request, csrf_token)
    admin = require_user(request)
    require_sysadmin(admin)
    with conn() as c:
        c.execute("UPDATE users SET totp_secret_enc=NULL, updated_at=? WHERE id=?", (now_iso(), user_id))
        c.execute("UPDATE devices SET revoked_at=? WHERE user_id=? AND revoked_at IS NULL", (now_iso(), user_id))
    audit(admin["id"], "account.reset_2fa", "user", user_id)
    return RedirectResponse("/manage/accounts", status_code=303)


@app.post("/manage/accounts/{user_id}/unlink-hass")
def account_unlink_hass(request: Request, user_id: str, csrf_token: str = Form(...)):
    require_csrf(request, csrf_token)
    admin = require_user(request)
    require_sysadmin(admin)
    with conn() as c:
        row = c.execute("SELECT hass_user_id FROM users WHERE id=?", (user_id,)).fetchone()
        if row and row["hass_user_id"]:
            old_hass_user_id = row["hass_user_id"]
            c.execute("UPDATE users SET hass_user_id=NULL, updated_at=? WHERE id=?", (now_iso(), user_id))
        else:
            old_hass_user_id = None
    if old_hass_user_id:
        audit(admin["id"], "account.unlink_hass", "user", user_id, {"hass_user_id": old_hass_user_id})
    return RedirectResponse("/manage/accounts", status_code=303)


@app.get("/me/account", response_class=HTMLResponse)
def my_account(request: Request):
    try:
        user = require_user(request, "/me/account")
    except HTTPException as exc:
        return auth_redirect_for(exc)
    return render(request, "account.html", error=None, success=None)


@app.get("/terms", response_class=HTMLResponse)
def terms_page(request: Request):
    return render(request, "legal.html", legal_title="服務條款", legal_kicker="Terms of Service", legal_paragraphs=[
        "公告系統提供團隊公告的建立、發布、閱讀與裝置驗證功能。使用本系統即表示你同意依組織規範使用服務。",
        "系統管理員可依稽核與資安需要管理帳號、公告版本及已認證裝置。服務內容與管理規則可能依部署環境調整。",
    ])


@app.get("/user-terms", response_class=HTMLResponse)
def user_terms_page(request: Request):
    return render(request, "legal.html", legal_title="使用者條款", legal_kicker="User Terms", legal_paragraphs=[
        "使用者應妥善保管帳號、密碼與第二階段驗證資訊，不得將帳號提供給未獲授權的人員使用。",
        "使用者發布公告時，應確認內容正確且符合組織的資訊安全與資料使用規範。若發現帳號或裝置異常，請立即在設定的裝置頁解除。",
    ])


@app.post("/me/account/password", response_class=HTMLResponse)
def my_password_change(
    request: Request,
    current_password: str = Form(...),
    new_password: str = Form(...),
    confirm: str = Form(...),
    csrf_token: str = Form(...),
):
    require_csrf(request, csrf_token)
    user = require_user(request)
    if len(new_password) < 12 or new_password != confirm:
        return render(request, "account.html", error="新密碼至少 12 碼，且兩次輸入必須一致。", success=None)
    with conn() as c:
        stored = c.execute("SELECT password_hash FROM users WHERE id=?", (user["id"],)).fetchone()
        if not stored or not verify_password(current_password, stored["password_hash"]):
            return render(request, "account.html", error="目前密碼不正確。", success=None)
        c.execute("UPDATE users SET password_hash=?,must_change_password=0,updated_at=? WHERE id=?", (hash_password(new_password), now_iso(), user["id"]))
    audit(user["id"], "account.self_password_change", "user", user["id"])
    return render(request, "account.html", error=None, success="密碼已更新。目前這台已認證裝置仍保持登入。")


@app.get("/me/devices", response_class=HTMLResponse)
def my_devices(request: Request):
    try:
        user = require_user(request, "/me/devices")
    except HTTPException as exc:
        return auth_redirect_for(exc)
    with conn() as c:
        devices = active_devices(c, user["id"])
    return render(request, "devices.html", devices=devices, current_device_id=user.get("device_id"), max_devices=MAX_DEVICES, error=None)


@app.post("/me/devices/{device_id}/revoke")
def my_device_revoke(request: Request, device_id: str, current_password: str = Form(""), csrf_token: str = Form(...)):
    require_csrf(request, csrf_token)
    user = require_user(request)
    key = login_attempt_key(request, "device-revoke:" + user["id"])
    error = None
    with conn() as c:
        device = c.execute("SELECT id FROM devices WHERE id=? AND user_id=? AND revoked_at IS NULL", (device_id, user["id"])).fetchone()
        if not device:
            raise HTTPException(404, "裝置不存在或已解除")
        stored = c.execute("SELECT password_hash FROM users WHERE id=?", (user["id"],)).fetchone()
        if too_many_login_failures(c, key):
            error = "驗證嘗試過多，請稍後再試。未解除裝置。"
        elif not stored or not verify_password(current_password, stored["password_hash"]):
            record_login_failure(c, key)
            error = "目前密碼不正確，未解除裝置。"
        if error:
            devices = active_devices(c, user["id"])
        else:
            c.execute("DELETE FROM login_attempts WHERE attempt_key=?", (key,))
            c.execute("UPDATE devices SET revoked_at=? WHERE id=? AND user_id=? AND revoked_at IS NULL", (now_iso(), device_id, user["id"]))
    if error:
        return render(request, "devices.html", devices=devices, current_device_id=user.get("device_id"), max_devices=MAX_DEVICES, error=error)
    audit(user["id"], "device.revoke", "device", device_id)
    if device_id == user.get("device_id"):
        response = RedirectResponse("/login", status_code=303)
        response.delete_cookie(DEVICE_COOKIE)
        return response
    return RedirectResponse("/me/devices", status_code=303)
