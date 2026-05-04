"""
Kite Auth — Automated Zerodha Kite Connect authentication with TOTP.

Handles the full login flow:
1. POST user_id + password → request_id
2. POST request_id + TOTP code → authenticated session
3. GET connect/login → follow redirects → extract request_token
4. Exchange request_token for access_token

Access token is cached to disk and reused until it expires (midnight IST).
"""

import json
import logging
import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse, parse_qs

import requests

logger = logging.getLogger(__name__)


class KiteAuth:
    """Handle Zerodha Kite Connect authentication with TOTP automation."""

    TOKEN_FILE = "sessions/_kite_token.json"

    def __init__(
        self,
        api_key: str = None,
        api_secret: str = None,
        user_id: str = None,
        password: str = None,
        totp_secret: str = None,
        token_dir: str = None,
    ):
        self.api_key = api_key or os.environ.get("KITE_API_KEY", "")
        self.api_secret = api_secret or os.environ.get("KITE_API_SECRET", "")
        self.user_id = user_id or os.environ.get("KITE_USER_ID", "")
        self.password = password or os.environ.get("KITE_PASSWORD", "")
        self.totp_secret = totp_secret or os.environ.get("KITE_TOTP_SECRET", "")

        if token_dir:
            self.token_path = Path(token_dir) / "_kite_token.json"
        else:
            self.token_path = Path(self.TOKEN_FILE)
        self.token_path.parent.mkdir(parents=True, exist_ok=True)

    def get_authenticated_client(self):
        """Return an authenticated KiteConnect client, auto-refreshing if needed."""
        from kiteconnect import KiteConnect

        kite = KiteConnect(api_key=self.api_key)

        # Try cached token first
        token = self._load_cached_token()
        if token and not self._is_expired(token):
            kite.set_access_token(token["access_token"])
            logger.info(f"🔑 Using cached Kite token (expires {token.get('expires_at', '?')})")
            return kite

        # Need fresh login
        logger.info("🔑 Kite token expired or missing — performing auto-login...")
        access_token = self._auto_login()
        self._cache_token(access_token)
        kite.set_access_token(access_token)
        logger.info("✅ Kite auto-login successful")
        return kite

    def _auto_login(self) -> str:
        """Perform fully automated login: password → TOTP → request_token → access_token."""
        import pyotp
        from kiteconnect import KiteConnect

        if not all([self.api_key, self.api_secret, self.user_id, self.password, self.totp_secret]):
            missing = []
            if not self.api_key: missing.append("KITE_API_KEY")
            if not self.api_secret: missing.append("KITE_API_SECRET")
            if not self.user_id: missing.append("KITE_USER_ID")
            if not self.password: missing.append("KITE_PASSWORD")
            if not self.totp_secret: missing.append("KITE_TOTP_SECRET")
            raise ValueError(f"Missing Kite credentials: {', '.join(missing)}")

        session = requests.Session()

        # Step 1: POST login with user_id + password
        resp = session.post("https://kite.zerodha.com/api/login", data={
            "user_id": self.user_id,
            "password": self.password,
        })
        if resp.json().get("status") != "success":
            raise ValueError(f"Kite login failed: {resp.json().get('message', 'unknown error')}")
        request_id = resp.json()["data"]["request_id"]

        # Step 2: POST TOTP
        totp_code = pyotp.TOTP(self.totp_secret).now()
        resp2 = session.post("https://kite.zerodha.com/api/twofa", data={
            "user_id": self.user_id,
            "request_id": request_id,
            "twofa_value": totp_code,
            "twofa_type": "totp",
        })
        if resp2.json().get("status") != "success":
            raise ValueError(f"Kite TOTP failed: {resp2.json().get('message', 'unknown error')}")

        # Step 3: Follow redirect chain to get request_token
        login_url = f"https://kite.zerodha.com/connect/login?api_key={self.api_key}&v=3"
        resp3 = session.get(login_url, allow_redirects=False)

        request_token = None
        max_redirects = 10
        while resp3.status_code in (301, 302) and max_redirects > 0:
            next_url = resp3.headers.get("Location", "")
            # Check if redirect goes to our callback (contains request_token)
            if "127.0.0.1" in next_url or "localhost" in next_url:
                params = parse_qs(urlparse(next_url).query)
                request_token = params.get("request_token", [None])[0]
                break
            resp3 = session.get(next_url, allow_redirects=False)
            max_redirects -= 1

        if not request_token:
            raise ValueError("Failed to extract request_token from Kite redirect")

        # Step 4: Exchange for access_token
        kite = KiteConnect(api_key=self.api_key)
        data = kite.generate_session(request_token, api_secret=self.api_secret)
        return data["access_token"]

    def _cache_token(self, access_token: str):
        """Save access token to disk with expiry timestamp."""
        # Kite tokens expire at midnight IST (18:30 UTC)
        from zoneinfo import ZoneInfo
        now_ist = datetime.now(ZoneInfo("Asia/Kolkata"))
        # Token expires at 6:00 AM IST next day (when new login is needed)
        if now_ist.hour >= 6:
            expires = now_ist.replace(hour=6, minute=0, second=0, microsecond=0) + timedelta(days=1)
        else:
            expires = now_ist.replace(hour=6, minute=0, second=0, microsecond=0)

        data = {
            "access_token": access_token,
            "created_at": datetime.now().isoformat(),
            "expires_at": expires.isoformat(),
            "user_id": self.user_id,
        }
        self.token_path.write_text(json.dumps(data, indent=2))
        logger.info(f"🔑 Kite token cached until {expires.strftime('%Y-%m-%d %H:%M IST')}")

    def _load_cached_token(self) -> Optional[dict]:
        """Load cached token from disk."""
        if not self.token_path.exists():
            return None
        try:
            return json.loads(self.token_path.read_text())
        except Exception:
            return None

    def _is_expired(self, token: dict) -> bool:
        """Check if cached token has expired. Comparisons are done in UTC
        with timezone-aware datetimes — earlier code stripped tz off the
        stored '+05:30' expiry and compared against naive datetime.now()
        (which is UTC inside the container), causing a 5h30m drift that
        kept revoked tokens looking valid."""
        from datetime import timezone
        expires_at = token.get("expires_at")
        if not expires_at:
            return True
        try:
            exp = datetime.fromisoformat(expires_at)
            if exp.tzinfo is None:
                exp = exp.replace(tzinfo=timezone.utc)
            return datetime.now(timezone.utc) >= exp
        except Exception:
            return True
