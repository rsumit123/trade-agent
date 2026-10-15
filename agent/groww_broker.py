"""
Thin wrapper over Groww's Trade API (growwapi SDK), used only by the live
mirror to place real orders. Market data stays on Kite.

Auth is Groww's TOTP API key flow (no daily approval): a TOTP code from
GROWW_TOTP_SECRET is exchanged for an access token, which is cached for the
IST day in sessions/_groww_token.json (Groww allows 150 token calls a day).
Credentials live only in the VM .env: GROWW_API_KEY, GROWW_TOTP_SECRET.
"""

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

IST = ZoneInfo("Asia/Kolkata")
TOKEN_PATH = Path(__file__).resolve().parent.parent / "sessions" / "_groww_token.json"

# growwapi constant values (GrowwAPI.EXCHANGE_NSE etc.), inlined so tests and
# callers don't need the SDK imported.
EXCHANGE_NSE = "NSE"
SEGMENT_CASH = "CASH"
PRODUCT_MIS = "MIS"
VALIDITY_DAY = "DAY"
LIMIT, MARKET, SL, SL_M = "LIMIT", "MARKET", "SL", "SL_M"
BUY, SELL = "BUY", "SELL"

FILLED_STATUSES = {"EXECUTED", "COMPLETED", "DELIVERY_AWAITED"}
DEAD_STATUSES = {"REJECTED", "FAILED", "CANCELLED"}

REQUEST_TIMEOUT_S = 10


class GrowwAuthError(Exception):
    """Login failed, or Groww refused our token / IP (401/403). Not retryable today."""


def is_auth_error(exc: Exception) -> bool:
    """401/403 from Groww: expired/invalid token or a non-whitelisted IP."""
    code = str(getattr(exc, "code", "") or "")
    if code in ("401", "403"):
        return True
    name = type(exc).__name__
    return name in ("GrowwAPIAuthenticationException", "GrowwAPIAuthorisationException")


def _ist_today(now=None) -> str:
    return (now or datetime.now(IST)).astimezone(IST).date().isoformat()


class GrowwBroker:
    """Orders, status and positions for NSE cash intraday (MIS) only."""

    def __init__(self, client=None, api_key: str = None, totp_secret: str = None,
                 token_path: Path = TOKEN_PATH, now=None):
        self._client = client               # injectable for tests
        self.api_key = api_key if api_key is not None else os.environ.get("GROWW_API_KEY", "")
        self.totp_secret = totp_secret if totp_secret is not None else os.environ.get("GROWW_TOTP_SECRET", "")
        self.token_path = Path(token_path)
        self._now = now or (lambda: datetime.now(IST))

    # ── Auth ────────────────────────────────────────────────

    @property
    def client(self):
        if self._client is None:
            self._client = self._login()
        return self._client

    def _cached_token(self) -> Optional[str]:
        try:
            data = json.loads(self.token_path.read_text())
        except (OSError, ValueError):
            return None
        if data.get("date") == _ist_today(self._now()) and data.get("access_token"):
            return data["access_token"]
        return None

    def _login(self):
        try:
            from growwapi import GrowwAPI
        except ImportError as e:
            raise GrowwAuthError(f"growwapi not installed: {e}")
        token = self._cached_token()
        if not token:
            if not (self.api_key and self.totp_secret):
                raise GrowwAuthError("GROWW_API_KEY / GROWW_TOTP_SECRET not set")
            try:
                import pyotp
                resp = GrowwAPI.get_access_token(api_key=self.api_key,
                                                 totp=pyotp.TOTP(self.totp_secret).now())
            except Exception as e:
                raise GrowwAuthError(f"Groww login failed: {e}")
            # SDK 1.5.0 returns the token string; its docstring says dict.
            token = resp if isinstance(resp, str) else (resp or {}).get("token") or (resp or {}).get("access_token")
            if not token:
                raise GrowwAuthError("Groww login returned no token")
            self._save_token(token)
            logger.info("🔑 Groww access token generated and cached for today")
        return GrowwAPI(token)

    def _save_token(self, token: str):
        try:
            self.token_path.parent.mkdir(parents=True, exist_ok=True)
            self.token_path.write_text(json.dumps({"date": _ist_today(self._now()), "access_token": token}))
            os.chmod(self.token_path, 0o600)
        except OSError as e:
            logger.warning(f"Could not cache Groww token: {e}")

    def _call(self, name: str, **kwargs):
        """One SDK call. A rejected token is retried once with a fresh login;
        anything still 401/403 becomes GrowwAuthError."""
        try:
            return getattr(self.client, name)(timeout=REQUEST_TIMEOUT_S, **kwargs)
        except GrowwAuthError:
            raise
        except Exception as e:
            if not is_auth_error(e):
                raise
            logger.warning(f"Groww {name}: auth rejected ({e}) — re-logging in once")
        try:
            self.token_path.unlink()
        except OSError:
            pass
        self._client = None
        try:
            return getattr(self.client, name)(timeout=REQUEST_TIMEOUT_S, **kwargs)
        except GrowwAuthError:
            raise
        except Exception as e:
            if is_auth_error(e):
                raise GrowwAuthError(f"Groww {name} refused after re-login (token or static IP): {e}")
            raise

    # ── Orders ──────────────────────────────────────────────

    def place_order(self, symbol: str, side: str, quantity: int, order_type: str,
                    price: float = None, trigger_price: float = None,
                    reference_id: str = None) -> str:
        """Place an NSE cash MIS order; returns the groww_order_id."""
        kwargs = dict(
            validity=VALIDITY_DAY, exchange=EXCHANGE_NSE, order_type=order_type,
            product=PRODUCT_MIS, quantity=int(quantity), segment=SEGMENT_CASH,
            trading_symbol=symbol, transaction_type=side,
            price=float(price) if price is not None else 0.0,
        )
        if trigger_price is not None:
            kwargs["trigger_price"] = float(trigger_price)
        if reference_id:
            kwargs["order_reference_id"] = reference_id
        resp = self._call("place_order", **kwargs) or {}
        order_id = resp.get("groww_order_id")
        if not order_id:
            raise RuntimeError(f"Groww place_order returned no order id: {resp}")
        return order_id

    def cancel_order(self, order_id: str):
        return self._call("cancel_order", groww_order_id=order_id, segment=SEGMENT_CASH)

    def modify_order(self, order_id: str, order_type: str, quantity: int,
                     price: float = None, trigger_price: float = None):
        return self._call("modify_order", groww_order_id=order_id, segment=SEGMENT_CASH,
                          order_type=order_type, quantity=int(quantity),
                          price=price, trigger_price=trigger_price)

    def order_status(self, order_id: str) -> Dict:
        """Normalised order state: status (raw Groww order_status), filled_qty,
        avg_price, quantity, filled (bool: terminal and fully filled), dead
        (bool: terminal without further fills), remark."""
        d = self._call("get_order_detail", groww_order_id=order_id, segment=SEGMENT_CASH) or {}
        status = str(d.get("order_status") or "").upper()
        qty = int(d.get("quantity") or 0)
        filled = int(d.get("filled_quantity") or 0)
        avg = d.get("average_fill_price")
        avg = float(avg) if avg not in (None, "", 0, 0.0) else None
        return {
            "status": status,
            "quantity": qty,
            "filled_qty": filled,
            "avg_price": avg,
            "filled": status in FILLED_STATUSES or (qty > 0 and filled >= qty),
            "dead": status in DEAD_STATUSES,
            "remark": d.get("remark", ""),
        }

    def positions(self) -> Dict[str, int]:
        """Net intraday quantity per NSE symbol (+long / −short), non-zero only."""
        resp = self._call("get_positions_for_user", segment=SEGMENT_CASH) or {}
        out: Dict[str, int] = {}
        for p in resp.get("positions", []) or []:
            if (p.get("exchange") or EXCHANGE_NSE) != EXCHANGE_NSE:
                continue
            if p.get("product") and p.get("product") != PRODUCT_MIS:
                continue
            qty = p.get("quantity")
            if qty is None:
                qty = int(p.get("credit_quantity") or 0) - int(p.get("debit_quantity") or 0)
            qty = int(qty or 0)
            if qty:
                out[p.get("trading_symbol")] = out.get(p.get("trading_symbol"), 0) + qty
        return out

    def margin(self) -> Dict:
        return self._call("get_available_margin_details") or {}
