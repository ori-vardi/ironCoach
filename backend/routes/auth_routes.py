"""Auth endpoints — login, logout, signup, session switching, profile."""

import asyncio
import time
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

import database as db
from auth import hash_password, verify_password, create_jwt, decode_jwt
from config import logger
from routes.deps import _uid

router = APIRouter()

# Rate limiting for login
_login_attempts = {}  # ip -> [(timestamp, ...)]
MAX_LOGIN_ATTEMPTS = 5
RATE_LIMIT_WINDOW_SECONDS = 900  # 15 min

# Token settings
TOKEN_MAX_AGE_HOURS = 72
TOKEN_MAX_AGE_SECONDS = TOKEN_MAX_AGE_HOURS * 3600

# Username validation
MIN_USERNAME_LENGTH = 2
MAX_USERNAME_LENGTH = 50

# Password validation
MIN_PASSWORD_LENGTH = 8


def _validate_password(password: str):
    if len(password) < MIN_PASSWORD_LENGTH:
        raise HTTPException(400, f"Password must be at least {MIN_PASSWORD_LENGTH} characters")


def _check_rate_limit(ip: str) -> bool:
    """Check if IP has exceeded login rate limit."""
    now = time.time()
    attempts = _login_attempts.get(ip, [])
    attempts = [t for t in attempts if now - t < RATE_LIMIT_WINDOW_SECONDS]
    _login_attempts[ip] = attempts
    return len(attempts) >= MAX_LOGIN_ATTEMPTS


def _record_attempt(ip: str):
    """Record a failed login attempt."""
    now = time.time()
    _login_attempts.setdefault(ip, []).append(now)


def _reset_attempts(ip: str):
    """Reset login attempts on successful login."""
    _login_attempts.pop(ip, None)


def _set_token_cookie(resp: JSONResponse, token: str, request: Request):
    is_localhost = request.client and request.client.host in ("127.0.0.1", "::1", "localhost")
    resp.set_cookie("token", token, httponly=True, samesite="lax",
                    max_age=TOKEN_MAX_AGE_SECONDS, secure=not is_localhost)


def _validate_username(username: str):
    if not username or len(username) < MIN_USERNAME_LENGTH or len(username) > MAX_USERNAME_LENGTH:
        raise HTTPException(400, f"Username must be {MIN_USERNAME_LENGTH}-{MAX_USERNAME_LENGTH} characters")
    if not username.replace('_', '').replace('-', '').replace('.', '').isalnum():
        raise HTTPException(400, "Username can only contain letters, numbers, underscores, hyphens, and dots")


@router.post("/api/auth/login")
async def login(request: Request):
    # Rate limiting
    client_ip = request.client.host if request.client else "unknown"
    if _check_rate_limit(client_ip):
        raise HTTPException(429, "Too many login attempts. Please try again later.")

    body = await request.json()
    username = body.get("username", "").strip()
    password = body.get("password", "")
    if not username or not password:
        raise HTTPException(400, "Username and password required")
    conn = await db.get_db()
    try:
        user = await db.user_get_by_username(conn, username)
    finally:
        await conn.close()
    if not user:
        _record_attempt(client_ip)
        raise HTTPException(401, "Invalid credentials")
    valid, new_hash = verify_password(password, user["password_hash"])
    if not valid:
        _record_attempt(client_ip)
        raise HTTPException(401, "Invalid credentials")
    # Migrate legacy password hash to PBKDF2 on successful login
    if new_hash:
        migrate_conn = await db.get_db()
        try:
            await migrate_conn.execute("UPDATE users SET password_hash = ? WHERE id = ?", (new_hash, user["id"]))
            await migrate_conn.commit()
        finally:
            await migrate_conn.close()
    # Success: reset rate limit
    _reset_attempts(client_ip)
    token = create_jwt({"user_id": user["id"], "username": user["username"], "role": user["role"],
                        "token_version": user.get("token_version") or 0})
    resp = JSONResponse({"id": user["id"], "username": user["username"],
                         "display_name": user["display_name"], "role": user["role"], "token": token})
    _set_token_cookie(resp, token, request)
    # Check for missed weekly nutrition auto-suggest (non-blocking)
    from services.nutrition_scheduler import check_missed_run
    asyncio.create_task(check_missed_run(user["id"]))
    return resp


@router.post("/api/auth/logout")
async def logout():
    resp = JSONResponse({"ok": True})
    resp.delete_cookie("token")
    return resp


@router.post("/api/auth/switch")
async def auth_switch(request: Request):
    """Switch to a previously authenticated user using their stored token."""
    body = await request.json()
    token = body.get("token", "")
    if not token:
        raise HTTPException(400, "Token required")
    payload = decode_jwt(token)
    if not payload or "user_id" not in payload:
        raise HTTPException(401, "Invalid or expired session")
    conn = await db.get_db()
    try:
        user = await db.user_get_by_id(conn, payload["user_id"])
    finally:
        await conn.close()
    if not user:
        raise HTTPException(401, "User not found")
    # Issue a fresh token
    new_token = create_jwt({"user_id": user["id"], "username": user["username"], "role": user["role"],
                            "token_version": user.get("token_version") or 0})
    resp = JSONResponse({"id": user["id"], "username": user["username"],
                         "display_name": user["display_name"], "role": user["role"], "token": new_token})
    _set_token_cookie(resp, new_token, request)
    return resp


@router.get("/api/auth/me")
async def auth_me(request: Request):
    user = getattr(request.state, "user", None)
    if not user:
        raise HTTPException(401, "Not authenticated")
    return {"id": user["id"], "username": user["username"],
            "display_name": user["display_name"], "role": user["role"]}


@router.get("/api/auth/has-users")
async def auth_has_users():
    conn = await db.get_db()
    try:
        cursor = await conn.execute("SELECT COUNT(*) FROM users")
        count = (await cursor.fetchone())[0]
        return {"has_users": count > 0}
    finally:
        await conn.close()


@router.post("/api/auth/setup")
async def auth_setup(request: Request):
    """Create the first user as admin. Only works when no users exist."""
    conn = await db.get_db()
    try:
        cursor = await conn.execute("SELECT COUNT(*) FROM users")
        count = (await cursor.fetchone())[0]
        if count > 0:
            raise HTTPException(400, "Setup already completed — users exist")
        body = await request.json()
        username = body.get("username", "").strip()
        password = body.get("password", "")
        display_name = body.get("display_name", username)
        if not username or not password:
            raise HTTPException(400, "Username and password required")
        _validate_username(username)
        _validate_password(password)
        uid = await db.user_create(conn, username, hash_password(password), display_name, "admin")
        profile = {k: body[k] for k in ("height_cm", "birth_date", "sex") if body.get(k)}
        if profile:
            await db.user_update_profile(conn, uid, profile)
        token = create_jwt({"user_id": uid, "username": username, "role": "admin", "token_version": 0})
        resp = JSONResponse({"id": uid, "username": username,
                             "display_name": display_name, "role": "admin", "token": token})
        _set_token_cookie(resp, token, request)
        return resp
    finally:
        await conn.close()


@router.post("/api/auth/signup")
async def auth_signup(request: Request):
    """Public self-registration. New users get 'user' role."""
    # Check if registration is enabled (admin-configurable)
    gate_conn = await db.get_db()
    try:
        allow = await db.setting_get(gate_conn, "allow_registration", "1")
    finally:
        await gate_conn.close()
    if allow == "0":
        raise HTTPException(403, "Registration is currently disabled")
    body = await request.json()
    username = body.get("username", "").strip()
    password = body.get("password", "")
    display_name = body.get("display_name", username)
    if not username or not password:
        raise HTTPException(400, "Username and password required")
    _validate_username(username)
    _validate_password(password)
    conn = await db.get_db()
    try:
        existing = await db.user_get_by_username(conn, username)
        if existing:
            raise HTTPException(409, "Username already taken")
        uid = await db.user_create(conn, username, hash_password(password), display_name, "user")
        profile = {k: body[k] for k in ("height_cm", "birth_date", "sex") if body.get(k)}
        if profile:
            await db.user_update_profile(conn, uid, profile)
        token = create_jwt({"user_id": uid, "username": username, "role": "user", "token_version": 0})
        resp = JSONResponse({"id": uid, "username": username,
                             "display_name": display_name, "role": "user", "token": token})
        _set_token_cookie(resp, token, request)
        return resp
    finally:
        await conn.close()


@router.get("/api/auth/profile")
async def auth_get_profile(request: Request):
    """Get current user's profile (height, weight, birth_date, sex)."""
    uid = _uid(request)
    conn = await db.get_db()
    try:
        return await db.user_get_profile(conn, uid) or {}
    finally:
        await conn.close()


@router.put("/api/auth/profile")
async def auth_update_profile(request: Request):
    """Update current user's profile."""
    uid = _uid(request)
    body = await request.json()
    conn = await db.get_db()
    try:
        await db.user_update_profile(conn, uid, body)
        return {"ok": True}
    finally:
        await conn.close()


@router.get("/api/auth/hr-settings")
async def auth_get_hr_settings(request: Request):
    """Get user's resolved HR settings (DB > calculated > config fallback)."""
    from data_processing.hr_zones import (
        resolve_hr_settings, _age_from_profile,
        compute_default_hr_max, compute_default_hr_rest, compute_default_hr_lthr,
        compute_zones_from_hr, detect_hr_max_from_workouts, detect_hr_rest_from_recovery,
    )
    from data_processing import _load_summary, _load_recovery_data
    from routes.deps import _user_data_dir

    uid = _uid(request)
    conn = await db.get_db()
    try:
        hr_db = await db.hr_settings_get(conn, uid)
        profile = await db.user_get_profile(conn, uid)
    finally:
        await conn.close()

    resolved = resolve_hr_settings(hr_db, profile)

    # Also provide calculated (from age/sex) and detected (from data) for the UI
    age = _age_from_profile(profile)
    sex = (profile or {}).get("sex", "male")
    calc_max = compute_default_hr_max(age, sex)
    calc_rest = compute_default_hr_rest(sex)
    calc_lthr = compute_default_hr_lthr(calc_max)
    calc_zones = compute_zones_from_hr(calc_max, calc_rest)

    dd = _user_data_dir(uid)
    detected = {}
    try:
        workouts = _load_summary(dd)
        det_max = detect_hr_max_from_workouts(workouts)
        if det_max:
            detected["hr_max"] = det_max
        recovery_raw = _load_recovery_data(dd)
        det_rest = detect_hr_rest_from_recovery(recovery_raw)
        if det_rest:
            detected["hr_rest"] = det_rest
    except Exception:
        pass

    return {
        **resolved,
        "hr_zones": [list(z) for z in resolved["hr_zones"]],
        "calculated": {
            "hr_max": calc_max, "hr_rest": calc_rest, "hr_lthr": calc_lthr,
            "hr_zones": [list(z) for z in calc_zones],
        },
        "detected": detected,
    }


@router.put("/api/auth/hr-settings")
async def auth_update_hr_settings(request: Request):
    """Update user's HR settings. Setting any value sets locked=true.
    Send {locked: false} to reset to auto mode."""
    from datetime import datetime, timezone
    from data_processing.hr_zones import (
        compute_zones_from_hr, zone_boundaries,
        detect_hr_max_from_workouts, detect_hr_rest_from_recovery,
        compute_default_hr_max, compute_default_hr_rest, compute_default_hr_lthr,
        _age_from_profile,
    )
    from data_processing import _load_summary, _load_recovery_data
    from routes.deps import _user_data_dir

    uid = _uid(request)
    body = await request.json()

    conn = await db.get_db()
    try:
        if body.get("locked") is False:
            # Reset to auto mode — recalculate from best available source
            profile = await db.user_get_profile(conn, uid)
            dd = _user_data_dir(uid)
            age = _age_from_profile(profile)
            sex = (profile or {}).get("sex", "male")

            # Try detected values first, fall back to calculated
            hr_max = compute_default_hr_max(age, sex)
            hr_rest = compute_default_hr_rest(sex)
            try:
                workouts = _load_summary(dd)
                det_max = detect_hr_max_from_workouts(workouts)
                if det_max:
                    hr_max = det_max
                recovery_raw = _load_recovery_data(dd)
                det_rest = detect_hr_rest_from_recovery(recovery_raw)
                if det_rest:
                    hr_rest = det_rest
            except Exception:
                pass

            hr_lthr = compute_default_hr_lthr(hr_max)
            zones = compute_zones_from_hr(hr_max, hr_rest)
            data = {
                "hr_max": hr_max, "hr_rest": hr_rest, "hr_lthr": hr_lthr,
                **zone_boundaries(zones),
                "locked": 0,
                "source": "apple_health" if det_max or det_rest else "calculated",
                "updated_at": datetime.now(tz=timezone.utc).isoformat(),
            }
            await db.hr_settings_upsert(conn, uid, data)
            return {"ok": True, "mode": "auto"}

        # Manual update — lock the values
        data = {"locked": 1, "source": "manual",
                "updated_at": datetime.now(tz=timezone.utc).isoformat()}
        for field in ("hr_max", "hr_rest", "hr_lthr"):
            if field in body:
                data[field] = float(body[field])
        for field in ("zone1_upper", "zone2_upper", "zone3_upper", "zone4_upper"):
            if field in body:
                data[field] = float(body[field])

        # If HR max/rest changed but zones not explicitly set, recompute zones
        if ("hr_max" in body or "hr_rest" in body) and "zone1_upper" not in body:
            current = await db.hr_settings_get(conn, uid)
            hr_max = data.get("hr_max", (current or {}).get("hr_max", 182))
            hr_rest = data.get("hr_rest", (current or {}).get("hr_rest", 55))
            zones = compute_zones_from_hr(hr_max, hr_rest)
            data.update(zone_boundaries(zones))
            if "hr_lthr" not in body:
                data["hr_lthr"] = compute_default_hr_lthr(hr_max)

        await db.hr_settings_upsert(conn, uid, data)
        return {"ok": True, "mode": "locked"}
    finally:
        await conn.close()


@router.post("/api/auth/change-password")
async def auth_change_password(request: Request):
    """Any authenticated user can change their own password."""
    user = getattr(request.state, "user", None)
    if not user:
        raise HTTPException(401, "Not authenticated")
    body = await request.json()
    current_pw = body.get("current_password", "")
    new_pw = body.get("new_password", "")
    if not current_pw or not new_pw:
        raise HTTPException(400, "Current and new password required")
    _validate_password(new_pw)
    valid, _ = verify_password(current_pw, user["password_hash"])
    if not valid:
        raise HTTPException(401, "Current password is incorrect")
    uid = user["id"]
    conn = await db.get_db()
    try:
        await conn.execute(
            "UPDATE users SET password_hash = ? WHERE id = ?",
            (hash_password(new_pw), uid)
        )
        await conn.execute(
            "UPDATE users SET token_version = COALESCE(token_version, 0) + 1 WHERE id = ?", (uid,))
        await conn.commit()
        row = await conn.execute_fetchone("SELECT token_version FROM users WHERE id = ?", (uid,))
        new_tv = (row[0] or 0) if row else 1
        token = create_jwt({"user_id": uid, "username": user["username"],
                            "role": user["role"], "token_version": new_tv})
        resp = JSONResponse({"ok": True, "token": token})
        _set_token_cookie(resp, token, request)
        return resp
    finally:
        await conn.close()
