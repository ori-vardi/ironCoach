"""Tests for security boundaries.

Each test exists because of a real vulnerability found in the code review.
If any of these fail, it means an attacker could exploit the system.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


class TestAuth:
    """Auth is the gate — if it breaks, everything is exposed."""

    def test_password_not_stored_plaintext(self):
        """Passwords must be salted+hashed, never stored as-is."""
        from auth import hash_password
        hashed = hash_password("mypassword")
        assert "mypassword" not in hashed
        assert hashed.startswith("pbkdf2:")  # format is pbkdf2:salt:hash

    def test_password_verify_roundtrip(self):
        """Hash then verify must work for correct password."""
        from auth import hash_password, verify_password
        hashed = hash_password("test1234")
        valid, new_hash = verify_password("test1234", hashed)
        assert valid is True
        assert new_hash is None  # PBKDF2 hash needs no migration

    def test_wrong_password_rejected(self):
        from auth import hash_password, verify_password
        hashed = hash_password("correct_pass")
        valid, new_hash = verify_password("wrong_pass", hashed)
        assert valid is False
        assert new_hash is None

    def test_same_password_different_hashes(self):
        """Each hash must use a unique salt — no rainbow tables."""
        from auth import hash_password
        h1 = hash_password("samepass")
        h2 = hash_password("samepass")
        assert h1 != h2  # different salts

    def test_jwt_roundtrip(self):
        """Create a JWT, decode it, get the same payload back."""
        from auth import create_jwt, decode_jwt
        payload = {"user_id": 42, "role": "admin"}
        token = create_jwt(payload)
        decoded = decode_jwt(token)
        assert decoded is not None
        assert decoded["user_id"] == 42
        assert decoded["role"] == "admin"

    def test_jwt_tampered_payload_rejected(self):
        """Modifying the payload must invalidate the signature."""
        from auth import create_jwt, decode_jwt, _b64url_encode
        import json
        token = create_jwt({"user_id": 1})
        parts = token.split(".")
        # Tamper the payload to change user_id
        fake_payload = _b64url_encode(json.dumps({"user_id": 999, "exp": 9999999999}).encode())
        tampered_token = f"{parts[0]}.{fake_payload}.{parts[2]}"
        assert decode_jwt(tampered_token) is None

    def test_jwt_expired_token_rejected(self):
        """Expired tokens must not be accepted."""
        import time
        from auth import decode_jwt, _b64url_encode, _b64url_decode, JWT_SECRET
        import json, hmac, hashlib
        # Create a token that expired 1 hour ago
        header = {"alg": "HS256", "typ": "JWT"}
        payload = {"user_id": 1, "exp": int(time.time()) - 3600}
        h = _b64url_encode(json.dumps(header).encode())
        p = _b64url_encode(json.dumps(payload).encode())
        signing_input = f"{h}.{p}"
        sig = hmac.new(JWT_SECRET.encode(), signing_input.encode(), hashlib.sha256).digest()
        token = f"{h}.{p}.{_b64url_encode(sig)}"
        assert decode_jwt(token) is None

    def test_jwt_garbage_rejected(self):
        from auth import decode_jwt
        assert decode_jwt("not.a.jwt") is None
        assert decode_jwt("") is None
        assert decode_jwt("abc") is None


class TestFilePathRestriction:
    """SEC-001: _read_attached_file must not read arbitrary files."""

    def test_blocks_etc_passwd(self):
        from server import _read_attached_file
        name, content = _read_attached_file("/etc/passwd")
        assert "Access denied" in content

    def test_blocks_jwt_secret(self):
        from server import _read_attached_file
        name, content = _read_attached_file(
            str(Path(__file__).parent.parent / "data" / ".jwt_secret")
        )
        assert "Access denied" in content

    def test_blocks_parent_traversal(self):
        from server import _read_attached_file
        name, content = _read_attached_file(
            str(Path(__file__).parent.parent / "data" / "uploads" / ".." / ".jwt_secret")
        )
        assert "Access denied" in content

    def test_allows_upload_dir(self):
        """Files in the uploads directory should be allowed."""
        from server import _read_attached_file
        # This file won't exist but the error should be "not found", not "access denied"
        name, content = _read_attached_file(
            str(Path(__file__).parent.parent / "data" / "uploads" / "test.txt")
        )
        assert "Access denied" not in content  # should say "not found" instead

    def test_allows_training_data(self):
        """Files in training_data should be allowed."""
        from server import _read_attached_file
        name, content = _read_attached_file(
            str(Path(__file__).parent.parent.parent / "training_data" / "nonexistent.csv")
        )
        assert "Access denied" not in content


class TestUidDefault:
    """SEC-011: _uid must never silently default to admin."""

    def test_uid_raises_without_user(self):
        """If no user is authenticated, _uid must raise, not return 1."""
        from server import _uid
        from fastapi import HTTPException

        class FakeRequest:
            class state:
                pass

        try:
            _uid(FakeRequest())
            assert False, "_uid should have raised HTTPException"
        except HTTPException as e:
            assert e.status_code == 401
