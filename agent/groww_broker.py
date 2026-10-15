"""
Thin wrapper over Groww's Trade API (growwapi SDK), used only by the live
mirror (agent/live_mirror.py) to place real orders. Market data stays on Kite.

Auth is Groww's TOTP API key flow (no daily approval): a TOTP code from
GROWW_TOTP_SECRET is exchanged for an access token, cached for the IST day in
sessions/_groww_token.json (Groww allows 150 token calls a day). Credentials
live only in the VM .env: GROWW_API_KEY, GROWW_TOTP_SECRET.

Every order carries a caller-chosen order_reference_id, so an ambiguous error
(timeout, 5xx, unreadable body) is resolved by looking the reference up instead
of retrying blind. Response field names marked ASSUMED are documented but not
yet seen live; the read-only live check must confirm them.
"""

import csv
import io
import json
import logging
import os
import threading
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

IST = ZoneInfo("Asia/Kolkata")
SESSIONS_DIR = Path(__file__).resolve().parent.parent / "sessions"
TOKEN_PATH = SESSIONS_DIR / "_groww_token.json"
INSTRUMENTS_PATH = SESSIONS_DIR / "_groww_instruments.csv"
INSTRUMENT_CSV_URL = "https://growwapi-assets.groww.in/instruments/instrument.csv"

# growwapi constant values (GrowwAPI.EXCHANGE_NSE etc.), inlined so tests and
# callers don't need the SDK imported.
EXCHANGE_NSE = "NSE"
SEGMENT_CASH = "CASH"
PRODUCT_MIS = "MIS"
VALIDITY_DAY = "DAY"
LIMIT, MARKET, SL, SL_M = "LIMIT", "MARKET", "SL", "SL_M"
BUY, SELL = "BUY", "SELL"
OCO = "OCO"

FILLED_STATUSES = {"EXECUTED", "COMPLETED", "DELIVERY_AWAITED"}
DEAD_STATUSES = {"REJECTED", "FAILED", "CANCELLED"}
OCO_FINAL = {"CANCELLED", "EXPIRED", "FAILED", "COMPLETED"}
OCO_FIRED = {"TRIGGERED", "COMPLETED"}

REQUEST_TIMEOUT_S = 10
LOGIN_TIMEOUT_S = 20

# ── Failure classification ────────────────────────────────

CATEGORIES = ("not_intraday_allowed", "circuit_limit", "insufficient_margin", "auth",
              "ip_not_whitelisted", "rate_limited", "invalid_price_tick", "market_closed", "unknown")

_RULES = [  # (category, needles) — first match wins
    ("ip_not_whitelisted", ("whitelist", "static ip", "ip address", "ip not", "unregistered ip")),
    ("auth", ("token", "authenticat", "authoris", "authoriz", "unauthor", "forbidden", "session expired",
              "login")),
    ("rate_limited", ("rate limit", "too many", "throttl")),
    ("not_intraday_allowed", ("intraday", "mis not", "mis is not", "product not allowed",
                              "product type not", "not allowed for mis", "square off only", "t2t",
                              "trade to trade", "asm", "gsm", "blocked for trading")),
    ("circuit_limit", ("circuit", "price band", "price range", "dpr", "outside the band")),
    ("insufficient_margin", ("margin", "insufficient", "funds", "balance")),
    ("invalid_price_tick", ("tick", "multiple of")),
    ("market_closed", ("market closed", "market is closed", "outside market", "market hours",
                       "not open", "after market")),
]


def error_text(exc) -> str:
    if isinstance(exc, str):
        return exc
    msg = getattr(exc, "msg", None) or str(exc)
    code = getattr(exc, "code", None)
    return f"{code}: {msg}" if code else f"{type(exc).__name__}: {msg}"


def classify_error(exc_or_text) -> str:
    """Bucket a broker error/remark into one of CATEGORIES."""
    code = str(getattr(exc_or_text, "code", "") or "")
    name = type(exc_or_text).__name__
    text = error_text(exc_or_text).lower()
    if code == "429" or name == "GrowwAPIRateLimitException":
        return "rate_limited"
    for cat, needles in _RULES:
        if any(n in text for n in needles):
            return cat
    if code in ("401", "403") or name in ("GrowwAPIAuthenticationException", "GrowwAPIAuthorisationException"):
        return "auth"
    return "unknown"


def is_auth_error(exc) -> bool:
    """401/403, or a Groww error body that talks about token/login/IP. The SDK
    raises body-level errors (status FAILURE) with Groww's own code before it
    looks at the HTTP status, so codes alone miss these."""
    return classify_error(exc) in ("auth", "ip_not_whitelisted")


# Groww error codes that mean "something broke on our side", not "your order is rejected".
INTERNAL_CODES = {"GA000", "GA003"}


def is_ambiguous(exc) -> bool:
    """Default: ambiguous (the order may have reached Groww). Only an explicit
    rejection response is definitive: a Groww error code that isn't internal,
    or an HTTP 4xx. Timeouts, SSL/proxy/OS errors, 5xx, internal GA codes,
    unreadable JSON and unknown exception types all need a reference lookup."""
    if isinstance(exc, GrowwAuthError):
        return False
    # Raised in our process before any request goes out (SDK method missing,
    # bad arguments): nothing can have reached Groww.
    if type(exc) in (AttributeError, TypeError, NotImplementedError):
        return False
    code = str(getattr(exc, "code", "") or "").strip()
    if not code:
        return True
    if code.isdigit():
        return not code.startswith("4")
    if code.upper().startswith("GA"):
        return code.upper() in INTERNAL_CODES
    return True


class GrowwAuthError(Exception):
    """Login failed, or Groww refused our token / IP after a re-login."""
    code = "401"


class PositionAmbiguous(Exception):
    """Groww's position fields disagree about the size/sign of a position."""


def _ist_today(now=None) -> str:
    return (now or datetime.now(IST)).astimezone(IST).date().isoformat()


def _f(x) -> Optional[float]:
    try:
        v = float(x)
        return v if v == v else None
    except (TypeError, ValueError):
        return None


def norm_order(d: Dict) -> Dict:
    """Normalised order state. filled = whole quantity done; final = no more
    fills can come (fully filled, or rejected/failed/cancelled). EXECUTED with
    filled < quantity is a partial and is NOT final."""
    d = d or {}
    status = str(d.get("order_status") or "").upper()
    qty = int(_f(d.get("quantity")) or 0)
    filled = int(_f(d.get("filled_quantity")) or 0)
    avg = _f(d.get("average_fill_price"))
    full = qty > 0 and filled >= qty
    return {
        "id": d.get("groww_order_id"),
        "ref": d.get("order_reference_id"),
        "symbol": d.get("trading_symbol"),
        "side": d.get("transaction_type"),
        "status": status,
        "quantity": qty,
        "filled_qty": filled,
        "avg_price": avg if avg else None,
        "filled": full,
        "dead": status in DEAD_STATUSES,
        "final": status in DEAD_STATUSES or (status in FILLED_STATUSES and (full or qty == 0)),
        "remark": d.get("remark") or "",
        "created_at": d.get("created_at") or "",
        "order_type": d.get("order_type"),
        "product": d.get("product"),
    }


class GrowwBroker:
    """Orders, OCOs, status and positions for NSE cash intraday (MIS) only."""

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
            box = {}

            def fetch():
                try:
                    import pyotp
                    box["r"] = GrowwAPI.get_access_token(api_key=self.api_key,
                                                         totp=pyotp.TOTP(self.totp_secret).now())
                except Exception as e:      # noqa: BLE001 — reported below
                    box["e"] = e
            t = threading.Thread(target=fetch, daemon=True)
            t.start()
            t.join(LOGIN_TIMEOUT_S)
            if t.is_alive():
                box["abandoned"] = True          # a late token from that thread is never used
                raise GrowwAuthError(f"Groww login timed out after {LOGIN_TIMEOUT_S}s")
            if "e" in box:
                raise GrowwAuthError(f"Groww login failed: {error_text(box['e'])}")
            resp = box.get("r")
            # SDK 1.5.0 returns the token string; its docstring says dict.
            token = resp if isinstance(resp, str) else (resp or {}).get("token") or (resp or {}).get("access_token")
            if not token:
                raise GrowwAuthError("Groww login returned no token")
            self._save_token(token)
            logger.info("🔑 Groww access token generated and cached for today")
        return self._make_client(GrowwAPI, token)

    @staticmethod
    def _make_client(GrowwAPI, token):
        """GrowwAPI(token) fetches a changelog with no timeout and prints it;
        build the client without that network call (same three attributes
        __init__ sets in SDK 1.5.0)."""
        api = GrowwAPI.__new__(GrowwAPI)
        api.domain = "https://api.groww.in/v1"
        api.token = token
        api.instruments = None
        return api

    def _save_token(self, token: str):
        try:
            self.token_path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(self.token_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as f:
                f.write(json.dumps({"date": _ist_today(self._now()), "access_token": token}))
            os.chmod(self.token_path, 0o600)
        except OSError as e:
            logger.warning(f"Could not cache Groww token: {e}")

    def _call(self, name: str, **kwargs):
        """One SDK call. An auth-looking failure is retried once after a fresh
        login; still failing → GrowwAuthError."""
        try:
            return getattr(self.client, name)(timeout=REQUEST_TIMEOUT_S, **kwargs)
        except GrowwAuthError:
            raise
        except Exception as e:
            if not is_auth_error(e):
                raise
            logger.warning(f"Groww {name}: auth rejected ({error_text(e)}) — re-logging in once")
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
                err = GrowwAuthError(f"Groww {name} refused after re-login: {error_text(e)}")
                err.category = classify_error(e)
                raise err
            raise

    # ── Orders ──────────────────────────────────────────────

    def place_order(self, symbol: str, side: str, quantity: int, order_type: str,
                    reference_id: str, price: float = None, trigger_price: float = None) -> str:
        """Place an NSE cash MIS order; returns groww_order_id. Raises on any
        error — callers must treat is_ambiguous() errors as 'maybe placed'."""
        kwargs = dict(
            validity=VALIDITY_DAY, exchange=EXCHANGE_NSE, order_type=order_type,
            product=PRODUCT_MIS, quantity=int(quantity), segment=SEGMENT_CASH,
            trading_symbol=symbol, transaction_type=side, order_reference_id=reference_id,
            price=float(price) if price is not None else 0.0,
        )
        if trigger_price is not None:
            kwargs["trigger_price"] = float(trigger_price)
        resp = self._call("place_order", **kwargs) or {}
        order_id = resp.get("groww_order_id")
        if not order_id:
            err = ValueError(f"json: place_order returned no order id: {resp}")
            raise err                                     # ambiguous: look it up by reference
        return order_id

    def order_status(self, order_id: str) -> Dict:
        return norm_order(self._call("get_order_detail", groww_order_id=order_id, segment=SEGMENT_CASH))

    def order_by_reference(self, reference_id: str) -> Optional[Dict]:
        """Full order state for a reference id, or None if Groww has no such order."""
        try:
            st = self._call("get_order_status_by_reference", order_reference_id=reference_id,
                            segment=SEGMENT_CASH) or {}
        except Exception as e:
            if str(getattr(e, "code", "")) == "404" or type(e).__name__ == "GrowwAPINotFoundException":
                return None
            raise
        oid = st.get("groww_order_id")
        if not oid:
            return None
        return self.order_status(oid)

    def cancel_order(self, order_id: str):
        return self._call("cancel_order", groww_order_id=order_id, segment=SEGMENT_CASH)

    def modify_order(self, order_id: str, order_type: str, quantity: int,
                     price: float = None, trigger_price: float = None):
        return self._call("modify_order", groww_order_id=order_id, segment=SEGMENT_CASH,
                          order_type=order_type, quantity=int(quantity),
                          price=price, trigger_price=trigger_price)

    def order_list(self, symbol: str = None, max_pages: int = 20) -> List[Dict]:
        """Today's orders, normalised. The SDK ignores page_size (server default
        page size), so page until a page is empty or brings nothing new."""
        out, seen = [], set()
        for page in range(max_pages):
            resp = self._call("get_order_list", page=page, segment=SEGMENT_CASH) or {}
            rows = resp.get("order_list") or []
            new = [r for r in rows if r.get("groww_order_id") not in seen]
            if not new:
                break
            for r in new:
                seen.add(r.get("groww_order_id"))
                if symbol is None or r.get("trading_symbol") == symbol:
                    out.append(norm_order(r))
        return out

    def order_by_reference_in_list(self, reference_id: str, symbol: str = None) -> Optional[Dict]:
        for o in self.order_list(symbol):
            if o.get("ref") == reference_id:
                return o
        return None

    def orders_net(self, symbol: str) -> int:
        """Signed net MIS quantity implied by today's filled orders on a symbol
        (all orders, ours and the user's) — the cross-check for positions()."""
        net = 0
        for o in self.order_list(symbol):
            if o.get("product") not in (None, "", PRODUCT_MIS):
                continue
            q = o.get("filled_qty") or 0
            net += q if o.get("side") == BUY else -q
        return net

    # ── OCO (target + stop, one cancels the other) ─────────

    def create_oco(self, symbol: str, quantity: int, exit_side: str, net_position_quantity: int,
                   target_trigger: float, stop_trigger: float, reference_id: str) -> str:
        """MIS OCO on an open position. Target leg MARKET, stop leg SL_M, so a
        trigger-only modify is always possible (leg order_type/price are not
        modifiable). Returns smart_order_id."""
        resp = self._call(
            "create_smart_order", smart_order_type=OCO, segment=SEGMENT_CASH,
            trading_symbol=symbol, quantity=int(quantity), product_type=PRODUCT_MIS,
            exchange=EXCHANGE_NSE, duration=VALIDITY_DAY, reference_id=reference_id,
            net_position_quantity=int(net_position_quantity), transaction_type=exit_side,
            target={"trigger_price": f"{target_trigger:.2f}", "order_type": MARKET, "price": None},
            stop_loss={"trigger_price": f"{stop_trigger:.2f}", "order_type": SL_M, "price": None},
        ) or {}
        sid = resp.get("smart_order_id")
        if not sid:
            raise ValueError(f"json: create_smart_order returned no smart_order_id: {resp}")
        return sid

    def modify_oco(self, smart_order_id: str, quantity: int, target_trigger: float, stop_trigger: float):
        return self._call("modify_smart_order", smart_order_id=smart_order_id, smart_order_type=OCO,
                          segment=SEGMENT_CASH, quantity=int(quantity),
                          target={"trigger_price": f"{target_trigger:.2f}"},
                          stop_loss={"trigger_price": f"{stop_trigger:.2f}"})

    def cancel_oco(self, smart_order_id: str):
        return self._call("cancel_smart_order", segment=SEGMENT_CASH, smart_order_type=OCO,
                          smart_order_id=smart_order_id)

    def oco_status(self, smart_order_id: str) -> Dict:
        d = self._call("get_smart_order", segment=SEGMENT_CASH, smart_order_type=OCO,
                       smart_order_id=smart_order_id) or {}
        status = str(d.get("status") or "").upper()
        return {"id": smart_order_id, "status": status, "final": status in OCO_FINAL,
                "fired": status in OCO_FIRED, "raw": d}

    def supports_oco(self) -> bool:
        return hasattr(self.client, "get_smart_order_list")

    def smart_orders(self, symbol: str = None) -> List[Dict]:
        """OCO smart orders (ASSUMED list key and row fields: reference_id,
        trading_symbol, smart_order_id, status)."""
        out = []
        for page in range(10):
            resp = self._call("get_smart_order_list", smart_order_type=OCO, segment=SEGMENT_CASH,
                              page=page, page_size=50) or {}
            rows = resp.get("smart_orders") or resp.get("order_list") or resp.get("data") or []
            if not rows:
                break
            for r in rows:
                if symbol is None or r.get("trading_symbol") == symbol:
                    out.append({"id": r.get("smart_order_id"), "ref": r.get("reference_id"),
                                "symbol": r.get("trading_symbol"),
                                "status": str(r.get("status") or "").upper(), "raw": r})
            if len(rows) < 50:
                break
        return out

    def find_oco(self, symbol: str, reference_id: str) -> Optional[str]:
        """Smart order id for an OCO whose create call was ambiguous."""
        for r in self.smart_orders(symbol):
            if r["ref"] == reference_id:
                return r["id"]
        return None

    # ── Positions / quotes / margin ─────────────────────────

    @staticmethod
    def _net(p: Dict) -> int:
        """Signed net intraday quantity of one position row. credit − debit is
        the primary source (credit = bought, ASSUMED); `quantity` must agree in
        size. If only `quantity` is present it is taken as signed (ASSUMED)."""
        credit, debit, qty = p.get("credit_quantity"), p.get("debit_quantity"), p.get("quantity")
        if credit is not None or debit is not None:
            net = int(_f(credit) or 0) - int(_f(debit) or 0)
            if qty is not None and abs(int(_f(qty) or 0)) != abs(net):
                raise PositionAmbiguous(
                    f"{p.get('trading_symbol')}: credit−debit={net} but quantity={qty}")
            return net
        if qty is None:
            raise PositionAmbiguous(f"{p.get('trading_symbol')}: no quantity fields")
        return int(_f(qty) or 0)

    def positions(self) -> Dict[str, int]:
        """Net MIS quantity per NSE symbol (+long / −short). Raises
        PositionAmbiguous if any row's fields disagree."""
        resp = self._call("get_positions_for_user", segment=SEGMENT_CASH) or {}
        out: Dict[str, int] = {}
        for p in resp.get("positions", []) or []:
            if (p.get("exchange") or EXCHANGE_NSE) != EXCHANGE_NSE:
                continue
            if p.get("product") and p.get("product") != PRODUCT_MIS:
                continue
            sym = p.get("trading_symbol")
            out[sym] = out.get(sym, 0) + self._net(p)
        return out

    def net_position(self, symbol: str) -> int:
        return self.positions().get(symbol, 0)

    def quote(self, symbol: str) -> Dict:
        d = self._call("get_quote", trading_symbol=symbol, exchange=EXCHANGE_NSE, segment=SEGMENT_CASH) or {}
        return {"ltp": _f(d.get("last_price")), "upper": _f(d.get("upper_circuit_limit")),
                "lower": _f(d.get("lower_circuit_limit"))}

    def margin(self) -> Dict:
        return self._call("get_available_margin_details") or {}


# ── Instrument master (intraday eligibility, tick size) ───

class GrowwInstruments:
    """Groww's public instrument CSV, cached once per IST day in
    sessions/_groww_instruments.csv. NSE CASH rows only."""

    def __init__(self, path: Path = INSTRUMENTS_PATH, fetch=None, now=None):
        self.path = Path(path)
        self._fetch = fetch or self._download
        self._now = now or (lambda: datetime.now(IST))
        self._rows: Dict[str, Dict] = {}
        self._day = None
        self._lock = threading.Lock()

    @staticmethod
    def _download() -> str:
        import requests
        r = requests.get(INSTRUMENT_CSV_URL, timeout=30)
        r.raise_for_status()
        return r.text

    @property
    def _date_path(self) -> Path:
        return self.path.with_name(self.path.name + ".date")

    def _fresh_on_disk(self) -> Optional[str]:
        try:
            if self._date_path.read_text().strip() != _ist_today(self._now()):
                return None
            return self.path.read_text()
        except OSError:
            return None

    def load(self) -> bool:
        today = _ist_today(self._now())
        with self._lock:
            if self._day == today and self._rows:
                return True
        text = self._fresh_on_disk()
        if text is None:
            try:
                text = self._fetch()                       # network: never under the lock
            except Exception as e:
                logger.warning(f"Groww instrument list download failed: {e}")
                text = None
            if text:
                try:
                    self.path.parent.mkdir(parents=True, exist_ok=True)
                    self.path.write_text(text)
                    self._date_path.write_text(today)
                except OSError as e:
                    logger.warning(f"Could not cache Groww instruments: {e}")
        if not text:
            try:
                text = self.path.read_text()               # stale copy beats nothing for tick size
                logger.warning("Using a stale Groww instrument list")
            except OSError:
                return False
        rows = {}
        for r in csv.DictReader(io.StringIO(text)):
            if r.get("exchange") != EXCHANGE_NSE or r.get("segment") != SEGMENT_CASH:
                continue
            sym = r.get("trading_symbol")
            prev = rows.get(sym)
            # Duplicate symbols: prefer series EQ, else keep the first row seen.
            if prev is None or (prev.get("series") != "EQ" and r.get("series") == "EQ"):
                rows[sym] = r
        with self._lock:
            self._rows, self._day = rows, today
        return bool(rows)

    def info(self, symbol: str) -> Optional[Dict]:
        """{'is_intraday', 'buy_allowed', 'sell_allowed', 'tick_size'} or None."""
        if not self.load():
            return None
        r = self._rows.get(symbol)
        if r is None:
            return None

        def flag(k):
            return str(r.get(k, "")).strip() in ("1", "1.0", "true", "True", "TRUE")
        return {"is_intraday": flag("is_intraday"), "buy_allowed": flag("buy_allowed"),
                "sell_allowed": flag("sell_allowed"), "tick_size": _f(r.get("tick_size"))}

    def intraday_allowed(self, symbol: str) -> Optional[bool]:
        i = self.info(symbol)
        return None if i is None else i["is_intraday"]
