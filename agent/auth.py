"""JWT auth + Google OAuth verification + FastAPI dependencies.

Flow:
  1. Frontend gets a Google ID token via Google Identity Services
  2. Frontend POSTs it to /api/auth/google
  3. We verify it with Google's certs, upsert the user, issue our own JWT
  4. JWT goes into an httpOnly cookie + returned for header use
  5. Subsequent /api/* calls use Depends(current_user)
"""

import os
import time
import logging
from typing import Optional

from fastapi import Depends, Header, HTTPException, Cookie

try:
    import jwt as pyjwt  # PyJWT
except ImportError:
    pyjwt = None  # type: ignore

try:
    from google.oauth2 import id_token as google_id_token
    from google.auth.transport import requests as google_requests
except ImportError:
    google_id_token = None  # type: ignore
    google_requests = None  # type: ignore

from .users import upsert_user, is_admin

logger = logging.getLogger(__name__)

JWT_ALGO = "HS256"
JWT_TTL_SECONDS = 60 * 60 * 24 * 7  # 7 days
COOKIE_NAME = "alphaagent_session"


def _jwt_secret() -> str:
    secret = os.environ.get("JWT_SECRET", "")
    if not secret:
        # Dev fallback — DO NOT rely on this in prod (will rotate on every restart)
        secret = "dev-jwt-secret-please-set-JWT_SECRET-env-var"
    return secret


def _google_client_id() -> str:
    cid = os.environ.get("GOOGLE_CLIENT_ID", "").strip()
    if not cid:
        raise HTTPException(500, "GOOGLE_CLIENT_ID not configured on backend")
    return cid


def verify_google_token(token: str) -> dict:
    """Verify a Google ID token and return the claims (email, name, picture)."""
    if google_id_token is None:
        raise HTTPException(500, "google-auth not installed on backend")
    try:
        claims = google_id_token.verify_oauth2_token(
            token, google_requests.Request(), _google_client_id()
        )
    except ValueError as e:
        raise HTTPException(401, f"Invalid Google token: {e}")
    if not claims.get("email_verified"):
        raise HTTPException(401, "Google email not verified")
    return claims


def issue_jwt(email: str, name: str = "", picture: str = "") -> str:
    if pyjwt is None:
        raise HTTPException(500, "PyJWT not installed on backend")
    now = int(time.time())
    payload = {
        "email": email.lower(),
        "name": name,
        "picture": picture,
        "is_admin": is_admin(email),
        "iat": now,
        "exp": now + JWT_TTL_SECONDS,
    }
    return pyjwt.encode(payload, _jwt_secret(), algorithm=JWT_ALGO)


def decode_jwt(token: str) -> Optional[dict]:
    if pyjwt is None or not token:
        return None
    try:
        return pyjwt.decode(token, _jwt_secret(), algorithms=[JWT_ALGO])
    except Exception:
        return None


# ── FastAPI dependencies ─────────────────────────────────────


async def current_user(
    authorization: Optional[str] = Header(None),
    alphaagent_session: Optional[str] = Cookie(None),
) -> dict:
    """Resolve the current user from Authorization header or session cookie.

    Returns the JWT claims dict (email, name, picture, is_admin).
    Raises 401 if no valid token is present.
    """
    token: Optional[str] = None
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization.split(" ", 1)[1].strip()
    elif alphaagent_session:
        token = alphaagent_session

    claims = decode_jwt(token) if token else None
    if not claims or not claims.get("email"):
        raise HTTPException(401, "Not authenticated")
    # Refresh is_admin from env on every request (so admin status flips
    # immediately when ADMIN_EMAILS changes, without forcing logout)
    claims["is_admin"] = is_admin(claims["email"])
    return claims


async def optional_user(
    authorization: Optional[str] = Header(None),
    alphaagent_session: Optional[str] = Cookie(None),
) -> Optional[dict]:
    try:
        return await current_user(authorization, alphaagent_session)
    except HTTPException:
        return None


async def require_admin(user: dict = Depends(current_user)) -> dict:
    if not user.get("is_admin"):
        raise HTTPException(403, "Admin only")
    return user
