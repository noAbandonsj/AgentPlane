from __future__ import annotations

import hashlib
import hmac
import secrets
from uuid import UUID


def hash_password(password: str, iterations: int) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    return f"pbkdf2_sha256${iterations}${salt.hex()}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        scheme, raw_iterations, raw_salt, expected = encoded.split("$", maxsplit=3)
        if scheme != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode(),
            bytes.fromhex(raw_salt),
            int(raw_iterations),
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(digest.hex(), expected)


def create_session_token() -> str:
    return secrets.token_urlsafe(32)


def hash_session_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_application_token(credential_id: UUID) -> str:
    return f"ap_{credential_id.hex}.{secrets.token_urlsafe(32)}"


def parse_application_token(token: str) -> UUID | None:
    try:
        raw_credential_id, secret = token.split(".", maxsplit=1)
        if not raw_credential_id.startswith("ap_") or not secret:
            return None
        return UUID(hex=raw_credential_id.removeprefix("ap_"))
    except (ValueError, AttributeError):
        return None


def hash_application_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def application_token_matches(token: str, expected_hash: str) -> bool:
    return hmac.compare_digest(hash_application_token(token), expected_hash)
