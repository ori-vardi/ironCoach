"""Authentication utilities — JWT + password hashing."""

import hashlib
import hmac
import json
import os
import secrets
import time
from pathlib import Path

# JWT secret — auto-generated and persisted
_SECRET_FILE = Path(__file__).resolve().parent / "data" / ".jwt_secret"

def _get_secret() -> str:
    _SECRET_FILE.parent.mkdir(parents=True, exist_ok=True)
    if _SECRET_FILE.exists():
        return _SECRET_FILE.read_text().strip()
    secret = secrets.token_hex(32)
    _SECRET_FILE.write_text(secret)
    os.chmod(_SECRET_FILE, 0o600)
    return secret

JWT_SECRET = _get_secret()
JWT_ALGORITHM = "HS256"
JWT_EXPIRY_HOURS = 72


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac('sha256', password.encode(), salt.encode(), iterations=600_000)
    return f"pbkdf2:{salt}:{h.hex()}"


def verify_password(password: str, stored: str) -> tuple[bool, str | None]:
    """Returns (is_valid, new_hash_or_None). If new_hash is not None, caller should update DB."""
    if ":" not in stored:
        return False, None
    if stored.startswith("pbkdf2:"):
        _, salt, h = stored.split(":")
        check = hashlib.pbkdf2_hmac('sha256', password.encode(), salt.encode(), iterations=600_000)
        return hmac.compare_digest(check.hex(), h), None
    else:
        # Legacy sha256 format
        salt, h = stored.split(":", 1)
        check = hashlib.sha256((salt + password).encode()).hexdigest()
        if hmac.compare_digest(check, h):
            # Migrate to new format
            new_hash = hash_password(password)
            return True, new_hash
        return False, None


def _b64url_encode(data: bytes) -> str:
    import base64
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64url_decode(s: str) -> bytes:
    import base64
    padding = 4 - len(s) % 4
    if padding != 4:
        s += "=" * padding
    return base64.urlsafe_b64decode(s)


def create_jwt(payload: dict) -> str:
    header = {"alg": JWT_ALGORITHM, "typ": "JWT"}
    payload = {**payload, "exp": int(time.time()) + JWT_EXPIRY_HOURS * 3600}
    segments = [
        _b64url_encode(json.dumps(header).encode()),
        _b64url_encode(json.dumps(payload).encode()),
    ]
    signing_input = ".".join(segments)
    sig = hmac.new(JWT_SECRET.encode(), signing_input.encode(), hashlib.sha256).digest()
    segments.append(_b64url_encode(sig))
    return ".".join(segments)


def decode_jwt(token: str) -> dict | None:
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return None
        signing_input = f"{parts[0]}.{parts[1]}"
        sig = hmac.new(JWT_SECRET.encode(), signing_input.encode(), hashlib.sha256).digest()
        if not hmac.compare_digest(sig, _b64url_decode(parts[2])):
            return None
        payload = json.loads(_b64url_decode(parts[1]))
        if payload.get("exp", 0) < time.time():
            return None
        return payload
    except Exception:
        return None
