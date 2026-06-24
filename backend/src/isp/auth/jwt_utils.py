"""
JWT sign / verify utilities for ISP admin sessions.

Tokens are short-lived HS256 JWTs signed with ``settings.jwt_secret``.
Call ``create_token`` after a successful OAuth2 callback and ``verify_token``
in the auth middleware to validate every admin request.
"""
from __future__ import annotations
import time
import jwt
from isp.config import settings


def create_token(email: str, name: str) -> str:
    now = int(time.time())
    payload = {
        "sub": email,
        "name": name,
        "iat": now,
        "exp": now + settings.jwt_ttl_minutes * 60,
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def verify_token(token: str) -> dict:
    """Decode and validate a JWT.  Raises jwt.PyJWTError on failure."""
    return jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
