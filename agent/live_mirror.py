"""
Real-money mirror of a paper session on Groww.

Every paper event (entry, exit from any path, stop/target change) is replayed
as a real NSE intraday (MIS) order at the same quantity, so paper and real can
be compared trade by trade. Paper always books first and stays the record the
agent and learner see; nothing here ever raises into the agent loop.

Threading: LivePortfolio books paper on the agent thread and hands the event
to a LiveWorker, a single thread that owns every broker call. The worker runs
the events serially and ticks the mirror every few seconds, so the agent loop
never waits on Groww.

Leg state machine (one leg per paper trade):
  pending       ledger row written with the entry reference id, before placing
  entry_working entry order placed (or ambiguous), waiting for a final state
  unknown       entry state can't be read; resolved every tick, never guessed
  open          entry final with a fill; protected by an OCO (or a plain SL)
  exiting       paper exited: protective cancelled to a final state, then exit
                orders capped at the broker's net position until flat
  closed        flat at the broker for this leg
  stuck         not flat by 15:19 (Groww squares off MIS at 15:20) — loud alert
  missed / rejected / blocked / skipped   no position was ever taken

Groww has no trailing stop; Opus trails by calling update_levels, which
modifies the OCO's two triggers.

Plan: docs/plans/2026-10-15-groww-live-mirror.md
"""

import fcntl
import json
import logging
import math
import os
import queue
import threading
import time
from dataclasses import replace
from datetime import datetime, time as dtime
from pathlib import Path
from typing import Callable, Dict, List, Optional
from zoneinfo import ZoneInfo

from .groww_broker import (BUY, SELL, LIMIT, MARKET, SL, GrowwAuthError, PositionAmbiguous,
                           classify_error, error_text, is_ambiguous, is_auth_error)
from .portfolio import Portfolio, Trade

logger = logging.getLogger(__name__)

IST = ZoneInfo("Asia/Kolkata")

# Only these sessions can ever place real orders, and only if also listed in
# the GROWW_LIVE_SESSIONS env var and configured with live_broker: groww.
LIVE_SESSION_ALLOWLIST = {"nse_opus_live"}
KILL_SWITCH_FILE = "LIVE_KILL"
LIQUIDATE_REQUEST_FILE = "LIVE_LIQUIDATE"
LEDGER_FILE = "live_orders.jsonl"
LOCK_FILE = "live.lock"

MAX_EXPOSURE = 50_000.0          # ₹, open real positions at entry value
MAX_ORDER_VALUE = 50_000.0       # ₹, one entry
DAILY_LOSS_CAP = 1_500.0         # ₹ realised real loss → no new real entries today
MAX_ORDERS_PER_DAY = 40
MAX_CONSECUTIVE_FAILURES = 3     # broker errors in a row → no new entries today

ENTRY_BUFFER = 0.003             # limit = paper price ± 0.3%
ENTRY_TIMEOUT_S = 30             # then cancel → missed / partial
POLL_S = 2
SL_LIMIT_PAD = 0.01              # fallback SL-limit price = trigger ∓ 1%
EXIT_BUFFER = 0.005              # exit limit = LTP ∓ 0.5% × attempt
EXIT_TRIES = 3                   # limit attempts before MARKET
EXIT_REPRICE_S = 10
REF_LOOKUPS = 3                  # "not found" lookups before an ambiguous order counts as never placed
REF_GRACE_S = 10                 # ... and at least this long after it was sent
RECONCILE_EVERY_S = 60
EXIT_DEADLINE = dtime(15, 19)    # Groww's MIS auto square-off is 15:20 (₹50+GST per position)
MARKET_OPEN, MARKET_CLOSE = dtime(9, 15), dtime(15, 30)
SMOKE_SYMBOL_ENV = "GROWW_SMOKE_SYMBOL"
DEFAULT_SMOKE_SYMBOL = "ITC.NS"
MAX_SMOKE_ATTEMPTS = 2

EXPOSURE_STATES = ("pending", "entry_working", "unknown", "open", "exiting", "stuck")
ACTIVE_STATES = ("pending", "entry_working", "unknown", "exiting")


def live_enabled_for(session) -> bool:
    """All three gates: hard allowlist, env allowlist, session config."""
    if session is None or getattr(session, "backtest_mode", False):
        return False
    sid = getattr(session, "session_id", "")
    env = {s.strip() for s in os.environ.get("GROWW_LIVE_SESSIONS", "").split(",") if s.strip()}
    return (sid in LIVE_SESSION_ALLOWLIST and sid in env
            and (getattr(session, "live_broker", None) or "").lower() == "groww")


def ledger_has_exposure(session_dir, today: str = None) -> bool:
    """True if today's ledger still has a real position (or an unresolved order)."""
    today = today or datetime.now(IST).date().isoformat()
    legs = {}
    try:
        for line in (Path(session_dir) / LEDGER_FILE).read_text().splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if row.get("date") == today and row.get("event") == "leg":
                legs[row["leg"]["paper_trade_id"]] = row["leg"]
    except OSError:
        return False
    return any(l.get("status") in EXPOSURE_STATES for l in legs.values())


# ── Charges (Groww help page schedule, modelled — not from the API) ─────

def order_charges(side: str, value: float) -> float:
    """Charges for one executed NSE intraday order of `value` rupees."""
    value = abs(value or 0.0)
    if value <= 0:
        return 0.0
    brokerage = min(20.0, 0.001 * value)
    if brokerage < 5.0:
        brokerage = min(5.0, 0.025 * value)
    stt = 0.00025 * value if side == SELL else 0.0
    exchange = 0.0000297 * value
    sebi = 0.000001 * value
    stamp = 0.00003 * value if side == BUY else 0.0
    ipft = 0.000001 * value
    gst = 0.18 * (brokerage + exchange + sebi)
    return round(brokerage + stt + exchange + sebi + stamp + ipft + gst, 2)


def band_tick(price: float) -> float:
    """NSE equity tick by price band (fallback when no instrument data)."""
    p = price or 0
    if p < 250:
        return 0.01
    if p < 1000:
        return 0.05
    if p < 5000:
        return 0.10
    if p < 10000:
        return 0.50
    if p < 20000:
        return 1.00
    return 5.00


def round_to_tick(price: float, tick: float, up: bool) -> float:
    tick = tick if tick and tick > 0 else band_tick(price)
    n = price / tick
    n = math.ceil(n - 1e-9) if up else math.floor(n + 1e-9)
    return round(n * tick, 2)


def _symbol(ticker: str) -> Optional[str]:
    """'RELIANCE.NS' → 'RELIANCE'; None for anything that isn't an NSE equity."""
    if not ticker or not ticker.endswith(".NS"):
        return None
    sym = ticker[:-3]
    return sym if sym and all(c.isalnum() or c in "-&" for c in sym) else None


def _ref_part(x) -> str:
    return "".join(c for c in str(x) if c.isalnum())[:8] or "X"


class LiveMirror:
    """Broker-side state machine. Call only from one thread (the LiveWorker)."""

    def __init__(self, broker, session_id: str, session_dir, market_data=None, *,
                 instruments=None,
                 is_backtest: Callable[[], bool] = lambda: False,
                 alert: Callable[[str], object] = None,
                 now: Callable[[], datetime] = None,
                 sleep: Callable[[float], None] = time.sleep,
                 monotonic: Callable[[], float] = time.monotonic,
                 forced_close_time: Callable[[datetime], Optional[datetime]] = None,
                 paper_open_ids: Callable[[], set] = None,
                 exit_only: bool = False):
        self.broker = broker
        self.session_id = session_id
        self.session_dir = Path(session_dir)
        self.market_data = market_data
        self.instruments = instruments
        self.is_backtest = is_backtest
        if alert is None:
            from .health import send_telegram as alert
        self._alert_fn = alert
        self._now = now or (lambda: datetime.now(IST))
        self._sleep = sleep
        self._mono = monotonic
        self._forced_close_time = forced_close_time
        self._paper_open_ids = paper_open_ids
        self.exit_only = exit_only
        self.standby = False                     # another process holds the live lock
        self.ledger_path = self.session_dir / LEDGER_FILE
        self._ledger_lock = threading.Lock()
        self._lock_fh = None
        self.legs: Dict[str, Dict] = {}          # str(paper_trade_id) → leg
        self._day = None
        self._load_ledger()

    # ── Day state / ledger ─────────────────────────────────

    def _today(self) -> str:
        return self._now().astimezone(IST).date().isoformat()

    def _yymmdd(self) -> str:
        return self._now().astimezone(IST).strftime("%y%m%d")

    def _ref(self, tid, kind: str, n: int = 1) -> str:
        """Deterministic order_reference_id: 8–20 alphanumerics, ≤ 2 hyphens."""
        return f"{self._yymmdd()}-{_ref_part(tid)}-{kind}{n}"[:20]

    def _reset_day_state(self):
        self._orders_today = 0
        self._realized_today = 0.0
        self._disabled = None                    # (date, reason) → no new entries
        self._smoke = None                       # (date, passed)
        self._smoke_attempts = 0
        self.market_ok = True                    # MARKET orders accepted (smoke test)
        self.oco_ok = True                       # OCO smart orders accepted
        self._consecutive_failures = 0
        self._last_reconcile = None
        self._mismatch_alerted = set()
        self._once = set()                       # one-shot alert keys

    def _roll_day(self):
        today = self._today()
        if self._day != today:
            self._day = today
            self._reset_day_state()
            self.legs = {k: v for k, v in self.legs.items()
                         if v.get("date") == today or v.get("status") in EXPOSURE_STATES}

    def _write(self, row: Dict):
        row = {"ts": self._now().isoformat(), "date": self._today(), **row}
        with self._ledger_lock:
            try:
                self.session_dir.mkdir(parents=True, exist_ok=True)
                with open(self.ledger_path, "a") as f:
                    f.write(json.dumps(row, default=str) + "\n")
            except OSError as e:
                logger.warning(f"Live ledger write failed: {e}")

    def _save_leg(self, leg: Dict):
        self.legs[leg["paper_trade_id"]] = leg
        snap = {k: v for k, v in leg.items() if not k.startswith("_")}
        snap["exit_attempts"] = [{k: v for k, v in a.items() if not k.startswith("_")}
                                 for a in leg.get("exit_attempts") or []]
        self._write({"event": "leg", "leg": json.loads(json.dumps(snap, default=str))})

    def _load_ledger(self):
        """Rebuild today's legs and counters, so a restart still knows which
        real orders and positions exist."""
        self._day = None
        self._roll_day()
        today = self._day
        try:
            lines = self.ledger_path.read_text().splitlines()
        except OSError:
            return
        for line in lines:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if row.get("date") != today:
                continue
            ev = row.get("event")
            if ev == "leg":
                leg = row["leg"]
                self.legs[leg["paper_trade_id"]] = leg
            elif ev == "order":
                self._orders_today += 1
            elif ev == "disabled":
                self._disabled = (today, row.get("reason", ""))
            elif ev == "smoke":
                self._smoke = (today, bool(row.get("passed")))
                self.market_ok = row.get("market_ok", True) is not False
                self.oco_ok = row.get("oco_ok", True) is not False
            elif ev == "smoke_start":
                self._smoke_attempts += 1
        self._realized_today = sum((l.get("real_pnl") or 0.0) for l in self.legs.values()
                                   if l.get("status") == "closed" and not l.get("smoke"))
        for leg in self.legs.values():
            leg["_t"] = self._mono()              # restart the in-process timers
            for a in leg.get("exit_attempts") or []:
                a["_t"] = self._mono()
            # A smoke position left by a crash is flattened like any other.
            if leg.get("smoke") and leg.get("status") == "open":
                leg["status"] = "exiting"

    # ── Locks / alerts / failures ──────────────────────────

    def acquire_lock(self) -> bool:
        """Exclusive per-session process lock: only one process (agent or the
        dashboard's liquidator) may drive the broker for this session."""
        if self._lock_fh is not None:
            return True
        try:
            self.session_dir.mkdir(parents=True, exist_ok=True)
            fh = open(self.session_dir / LOCK_FILE, "a+")
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            try:
                fh.close()
            except Exception:
                pass
            return False
        self._lock_fh = fh
        return True

    def release_lock(self):
        if self._lock_fh is not None:
            try:
                fcntl.flock(self._lock_fh.fileno(), fcntl.LOCK_UN)
                self._lock_fh.close()
            finally:
                self._lock_fh = None

    def _alert(self, text: str, once_key=None):
        if once_key is not None:
            if once_key in self._once:
                return
            self._once.add(once_key)
        try:
            self._alert_fn(f"[{self.session_id} LIVE] {text}")
        except Exception as e:
            logger.warning(f"Live alert failed: {e}")

    def disable_for_today(self, reason: str):
        """No new real entries today. Exits and protection keep working."""
        self._roll_day()
        if self._disabled and self._disabled[0] == self._day:
            return
        self._disabled = (self._day, reason)
        self._write({"event": "disabled", "reason": reason})
        logger.error(f"🛑 Live entries disabled for today: {reason}")
        self._alert(f"Real ENTRIES DISABLED for today: {reason}. Exits continue; paper trading continues.")

    def disabled_reason(self) -> Optional[str]:
        self._roll_day()
        return self._disabled[1] if self._disabled and self._disabled[0] == self._day else None

    def _b(self, where: str, fn, *args, **kwargs):
        """One broker call with failure accounting. Re-raises."""
        try:
            out = fn(*args, **kwargs)
        except Exception as e:
            self._consecutive_failures += 1
            cat = getattr(e, "category", None) or classify_error(e)
            self._write({"event": "error", "where": where, "category": cat, "message": error_text(e)})
            logger.error(f"Live {where} failed [{cat}]: {error_text(e)}")
            if isinstance(e, GrowwAuthError) or is_auth_error(e):
                self.disable_for_today(f"Groww {cat} failure during {where}: {error_text(e)}")
            elif self._consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                self.disable_for_today(f"{self._consecutive_failures} broker errors in a row "
                                       f"(last: {where}: {error_text(e)})")
            raise
        self._consecutive_failures = 0
        return out

    def _try(self, where: str, fn, *args, **kwargs):
        """_b that returns (result, exc) instead of raising."""
        try:
            return self._b(where, fn, *args, **kwargs), None
        except Exception as e:
            return None, e

    # ── Market helpers ─────────────────────────────────────

    def _in_market(self, now=None) -> bool:
        now = (now or self._now()).astimezone(IST)
        return now.weekday() < 5 and MARKET_OPEN <= now.time() < MARKET_CLOSE

    def _past_deadline(self) -> bool:
        return self._now().astimezone(IST).time() >= EXIT_DEADLINE

    def _after_forced_close(self, grace_s: float = 0) -> bool:
        if self._forced_close_time is None:
            return False
        now = self._now()
        at = self._forced_close_time(now)
        return at is not None and (now - at).total_seconds() >= grace_s

    def _tick_size(self, ticker: str, price: float) -> float:
        sym = _symbol(ticker)
        try:
            info = self.instruments.info(sym) if (self.instruments is not None and sym) else None
            if info and info.get("tick_size"):
                return float(info["tick_size"])
        except Exception:
            pass
        try:
            t = self.market_data.tick_size(ticker) if self.market_data is not None else None
            if t and float(t) > 0:
                return float(t)
        except Exception:
            pass
        return band_tick(price)

    def _quote(self, symbol: str) -> Dict:
        q, _ = self._try(f"quote {symbol}", self.broker.quote, symbol)
        return q or {}

    def _ltp(self, leg: Dict, fallback: float) -> float:
        q = self._quote(leg["symbol"])
        leg["_quote"] = q
        if q.get("ltp"):
            return float(q["ltp"])
        try:
            p = self.market_data.get_current_prices([leg["ticker"]]).get(leg["ticker"]) if self.market_data else None
            if p:
                return float(p)
        except Exception:
            pass
        return fallback

    @staticmethod
    def _clamp(price: float, side: str, q: Dict) -> float:
        up, lo = (q or {}).get("upper"), (q or {}).get("lower")
        if side == BUY and up:
            return min(price, up)
        if side == SELL and lo:
            return max(price, lo)
        return price

    # ── Order placement (ledger first, reference ids, ambiguity) ──

    def _place(self, leg: Dict, kind: str, ref: str, side: str, qty: int, order_type: str,
               price: float = None, trigger: float = None) -> Dict:
        """Place one order. Returns {'id', 'ref', 'error', 'category', 'ambiguous'}.
        The ledger row (with the reference id) is written BEFORE the call, so a
        crash mid-call can be resolved on restart by looking the reference up."""
        self._orders_today += 1
        row = {"event": "order", "paper_trade_id": leg["paper_trade_id"], "leg": kind, "ref": ref,
               "symbol": leg["symbol"], "side": side, "order_type": order_type, "req_qty": qty,
               "req_price": price, "trigger": trigger}
        self._write({**row, "status": "sending"})
        try:
            oid = self._b(f"{kind} {side} {qty} {leg['symbol']}", self.broker.place_order,
                          leg["symbol"], side, qty, order_type, reference_id=ref,
                          price=price, trigger_price=trigger)
        except Exception as e:
            amb = is_ambiguous(e)
            cat = getattr(e, "category", None) or classify_error(e)
            self._write({**row, "status": "ambiguous" if amb else "rejected", "category": cat,
                         "message": error_text(e)})
            return {"id": None, "ref": ref, "error": error_text(e), "category": cat, "ambiguous": amb}
        self._write({**row, "status": "placed", "groww_order_id": oid})
        return {"id": oid, "ref": ref, "error": None, "category": None, "ambiguous": False}

    def _read(self, order_id: Optional[str], ref: Optional[str], where: str):
        """('ok', state) | ('missing', None) | ('error', None)."""
        if order_id:
            st, err = self._try(f"status {where}", self.broker.order_status, order_id)
            return ("ok", st) if err is None else ("error", None)
        if ref:
            st, err = self._try(f"lookup {where}", self.broker.order_by_reference, ref)
            if err is not None:
                return "error", None
            return ("ok", st) if st else ("missing", None)
        return "missing", None

    def _cancel(self, order_id: str, where: str):
        """Best effort: cancelling an order that already filled errors at most
        brokers, so failures don't count toward the error streak; the status
        read that always follows decides."""
        try:
            self.broker.cancel_order(order_id)
        except Exception as e:
            logger.info(f"Cancel {where} ({order_id}): {error_text(e)}")
            if is_auth_error(e):
                self.disable_for_today(f"Groww auth failure during cancel {where}: {error_text(e)}")

    def _reject_note(self, leg: Dict, category: str, message: str, what: str):
        leg.setdefault("failures", []).append({"what": what, "category": category, "message": message})
        self._alert(f"{what} {leg['symbol']} REJECTED [{category}]: {message}")

    # ── Guards ─────────────────────────────────────────────

    def _exposure_value(self) -> float:
        total = 0.0
        for l in self.legs.values():
            if l.get("status") in EXPOSURE_STATES and not l.get("smoke"):
                qty = l.get("filled_qty") if l.get("status") in ("open", "exiting", "stuck") else l.get("paper_qty")
                total += (qty or 0) * (l.get("entry_fill") or l.get("paper_entry") or 0)
        return total

    def _eligibility(self, symbol: str, side: str) -> Optional[str]:
        if self.instruments is None:
            return None
        try:
            info = self.instruments.info(symbol)
        except Exception:
            info = None
        if info is None:
            return "eligibility_unknown"
        if not info.get("is_intraday"):
            return "not_intraday_allowed"
        if side == BUY and not info.get("buy_allowed"):
            return "buy_not_allowed"
        if side == SELL and not info.get("sell_allowed"):
            return "sell_not_allowed"
        return None

    def _entry_block(self, ticker: str, qty: int, price: float) -> Optional[str]:
        if self.is_backtest():
            return "backtest"
        if self.exit_only:
            return "exit-only mode"
        if self.standby:
            return "standby (another process holds the live lock)"
        if self.disabled_reason():
            return f"disabled: {self.disabled_reason()}"
        if (self.session_dir / KILL_SWITCH_FILE).exists():
            return "kill switch"
        if not (self._smoke and self._smoke[0] == self._day and self._smoke[1]):
            return "smoke test not passed today"
        if _symbol(ticker) is None:
            return f"not an NSE equity: {ticker}"
        if self._past_deadline() or self._after_forced_close():
            return "after the forced-close time"
        value = qty * price
        if value > MAX_ORDER_VALUE:
            return f"order value ₹{value:,.0f} > ₹{MAX_ORDER_VALUE:,.0f}"
        if self._exposure_value() + value > MAX_EXPOSURE:
            return f"exposure ₹{self._exposure_value() + value:,.0f} > ₹{MAX_EXPOSURE:,.0f}"
        if self._realized_today <= -DAILY_LOSS_CAP:
            return f"daily real loss ₹{self._realized_today:,.0f} hit the cap"
        if self._orders_today + 3 > MAX_ORDERS_PER_DAY:      # entry + protection + an exit
            return f"order cap {MAX_ORDERS_PER_DAY}/day reached"
        return None

    # ── Paper events ───────────────────────────────────────

    def on_entry(self, trade: Trade):
        self._roll_day()
        direction = (trade.direction or "long").lower()
        leg = {
            "paper_trade_id": str(trade.id), "date": self._day, "ticker": trade.ticker,
            "symbol": _symbol(trade.ticker), "direction": direction,
            "paper_qty": trade.quantity, "paper_entry": trade.entry_price,
            "stop": trade.stop_price, "target": getattr(trade, "target_price", None),
            "filled_qty": 0, "entry_fill": None, "entry_order": None, "entry_ref": None,
            "sl_order": None, "protect_kind": None, "protect_final": False,
            "exit_attempts": [], "exit_fills": [], "status": "pending",
        }
        side = BUY if direction == "long" else SELL
        elig = self._eligibility(leg["symbol"], side) if leg["symbol"] else None
        if elig:
            leg.update(status="blocked", category=elig, note=elig)
            self._save_leg(leg)
            logger.warning(f"🚫 Live entry blocked for {trade.ticker} (paper #{trade.id}): {elig}")
            self._alert(f"Blocked real {direction.upper()} {trade.quantity} {trade.ticker}: {elig}")
            return
        block = self._entry_block(trade.ticker, trade.quantity, trade.entry_price)
        if block:
            leg.update(status="skipped", note=block)
            self._save_leg(leg)
            logger.warning(f"🚫 Live entry skipped for {trade.ticker} (paper #{trade.id}): {block}")
            if block != "backtest":
                self._alert(f"Skipped real {direction.upper()} {trade.quantity} {trade.ticker}: {block}")
            return
        self._start_entry(leg, trade.quantity, trade.entry_price)

    def _start_entry(self, leg: Dict, qty: int, price: float):
        long = leg["direction"] == "long"
        side = BUY if long else SELL
        q = self._quote(leg["symbol"])
        tick = self._tick_size(leg["ticker"], price)
        limit = round_to_tick(price * (1 + ENTRY_BUFFER) if long else price * (1 - ENTRY_BUFFER), tick, up=long)
        limit = self._clamp(limit, side, q)
        ref = self._ref(leg["paper_trade_id"], "E")
        leg.update(entry_ref=ref, entry_limit=limit, entry_qty=qty, status="pending", _t=self._mono())
        self._save_leg(leg)                                   # M5: pending leg before the call
        r = self._place(leg, "entry", ref, side, qty, LIMIT, price=limit)
        if r["id"] or r["ambiguous"]:
            leg.update(entry_order=r["id"], status="entry_working", _t=self._mono())
            if r["ambiguous"]:
                leg["note"] = f"entry reply ambiguous ({r['error']}) — resolving by reference"
            self._save_leg(leg)
            self._advance_entry(leg)
            return
        leg.update(status="rejected", category=r["category"], note=r["error"])
        self._save_leg(leg)
        self._reject_note(leg, r["category"], r["error"], f"Real {side} {qty}")

    def _advance_entry(self, leg: Dict):
        """Drive pending/entry_working/unknown to a final broker state. Never
        records missed/rejected without one (B1/B4)."""
        long = leg["direction"] == "long"
        side = BUY if long else SELL
        res, st = self._read(leg.get("entry_order"), leg.get("entry_ref"), f"entry {leg['symbol']}")
        if res == "error":
            if leg["status"] != "unknown":
                leg["status"] = "unknown"
                self._save_leg(leg)
            self._alert(f"Can't read the real {side} order for {leg['symbol']} — status UNKNOWN, retrying",
                        once_key=("unknown", leg["paper_trade_id"]))
            return
        if res == "missing":
            leg["lookups"] = leg.get("lookups", 0) + 1
            if leg["lookups"] < REF_LOOKUPS or self._mono() - leg.get("_t", self._mono()) < REF_GRACE_S:
                self._save_leg(leg)
                return
            leg.update(status="missed", note="entry order not found at Groww (never placed)")
            self._save_leg(leg)
            self._alert(f"Real {side} {leg['symbol']}: no order found at Groww for ref "
                        f"{leg['entry_ref']} — treated as not placed")
            return
        leg["entry_order"] = st["id"] or leg.get("entry_order")
        if not st["final"]:
            if leg["status"] == "unknown":
                leg["status"] = "entry_working"
                self._save_leg(leg)
            if self._mono() - leg.get("_t", self._mono()) >= ENTRY_TIMEOUT_S or leg.get("cancel_entry"):
                leg["cancel_entry"] = True
                self._cancel(st["id"], f"entry {leg['symbol']}")
                res, st2 = self._read(st["id"], None, f"entry {leg['symbol']}")
                if res != "ok" or not st2["final"]:
                    if res == "error":
                        leg["status"] = "unknown"
                    self._save_leg(leg)
                    return
                st = st2
            else:
                return
        # Final.
        filled = st["filled_qty"]
        leg["entry_status"] = st["status"]
        if filled <= 0:
            if st["status"] == "CANCELLED" and leg.get("cancel_entry"):
                leg.update(status="missed", note="no fill within timeout")
            else:
                cat = classify_error(st["remark"] or st["status"])
                leg.update(status="rejected", category=cat, note=st["remark"] or st["status"])
                self._write({"event": "error", "where": f"entry {leg['symbol']} (exchange)", "category": cat,
                             "message": leg["note"]})
                self._reject_note(leg, cat, leg["note"], f"Real {side} {leg.get('entry_qty')}")
            self._save_leg(leg)
            if leg["status"] == "missed":
                self._alert(f"Real {side} {leg.get('entry_qty')} {leg['symbol']} MISSED "
                            f"(limit ₹{leg.get('entry_limit', 0):,.2f})")
            return
        leg["filled_qty"] = int(filled)
        leg["entry_fill"] = st["avg_price"] or leg.get("entry_limit")
        leg["entry_fills"] = [{"order": st["id"], "qty": int(filled), "price": leg["entry_fill"]}]
        want_exit = leg.get("exit_requested")
        leg["status"] = "exiting" if want_exit else "open"
        self._save_leg(leg)
        part = "" if filled >= (leg.get("entry_qty") or filled) else f" (PARTIAL {filled}/{leg.get('entry_qty')})"
        self._alert(f"Real {side} {filled} {leg['symbol']} filled @ ₹{leg['entry_fill']:,.2f}{part}; "
                    f"paper @ ₹{leg['paper_entry']:,.2f}")
        if want_exit:
            self._advance_exit(leg)
        elif leg.get("stop"):
            self._protect(leg)

    # ── Protection (OCO, plain SL fallback) ────────────────

    def _held(self, leg: Dict) -> int:
        return int(leg.get("filled_qty") or 0) - sum(int(f.get("qty") or 0) for f in leg.get("exit_fills") or [])

    def _protect(self, leg: Dict):
        held = self._held(leg)
        if held <= 0 or not leg.get("stop"):
            return
        long = leg["direction"] == "long"
        exit_side = SELL if long else BUY
        n = leg.get("protect_n", 0) + 1
        leg["protect_n"] = n
        if self.oco_ok and leg.get("target"):
            ref = self._ref(leg["paper_trade_id"], "O", n)
            leg.update(protect_ref=ref, protect_kind="oco", protect_final=False)
            self._save_leg(leg)
            self._write({"event": "order", "paper_trade_id": leg["paper_trade_id"], "leg": "oco", "ref": ref,
                         "symbol": leg["symbol"], "side": exit_side, "req_qty": held,
                         "target": leg["target"], "trigger": leg["stop"], "status": "sending"})
            self._orders_today += 1
            try:
                sid = self._b(f"oco {leg['symbol']}", self.broker.create_oco, leg["symbol"], held, exit_side,
                              held if long else -held, leg["target"], leg["stop"], ref)
            except Exception as e:
                if is_ambiguous(e):
                    found, _ = self._try(f"find oco {leg['symbol']}", self.broker.find_oco, leg["symbol"], ref)
                    if found:
                        sid = found
                    else:
                        leg.update(sl_order=None, protect_kind="oco_unknown")
                        self._save_leg(leg)
                        self._alert(f"OCO for {leg['symbol']} may or may not exist (reply lost: {error_text(e)}). "
                                    f"Check the Groww app.", once_key=("oco_unknown", leg["paper_trade_id"]))
                        return
                else:
                    cat = classify_error(e)
                    leg.setdefault("failures", []).append(
                        {"what": "oco", "category": cat, "message": error_text(e)})
                    if isinstance(e, (AttributeError, TypeError, NotImplementedError)):
                        self.oco_ok = False
                    self._alert(f"OCO for {leg['symbol']} failed [{cat}]: {error_text(e)} — using a plain stop",
                                once_key=("oco_fail", leg["paper_trade_id"]))
                    sid = None
            if sid:
                leg.update(sl_order=sid, protect_stop=leg["stop"], protect_target=leg["target"])
                self._save_leg(leg)
                return
        self._protect_plain_sl(leg, held, n)

    def _protect_plain_sl(self, leg: Dict, held: int, n: int):
        long = leg["direction"] == "long"
        trigger, limit = self._stop_prices(leg, leg["stop"])
        ref = self._ref(leg["paper_trade_id"], "P", n)
        leg.update(protect_ref=ref, protect_kind="sl", protect_final=False)
        self._save_leg(leg)
        r = self._place(leg, "stop", ref, SELL if long else BUY, held, SL, price=limit, trigger=trigger)
        if r["ambiguous"]:
            res, st = self._read(None, ref, f"stop {leg['symbol']}")
            r["id"] = st["id"] if res == "ok" else None
            if not r["id"]:
                leg["protect_kind"] = "sl_unknown"
        leg.update(sl_order=r["id"], protect_stop=leg["stop"])
        self._save_leg(leg)
        if not r["id"] and not r["ambiguous"]:
            self._reject_note(leg, r["category"], r["error"], "Protective stop")

    def _stop_prices(self, leg: Dict, stop: float):
        long = leg["direction"] == "long"
        tick = self._tick_size(leg["ticker"], stop)
        trigger = round_to_tick(stop, tick, up=not long)
        limit = round_to_tick(stop * (1 - SL_LIMIT_PAD) if long else stop * (1 + SL_LIMIT_PAD), tick, up=not long)
        return trigger, limit

    def on_levels(self, trade: Trade):
        self._roll_day()
        leg = self.legs.get(str(trade.id))
        if not leg:
            return
        new_stop = trade.stop_price if trade.stop_price is not None else leg.get("stop")
        tgt = getattr(trade, "target_price", None)
        new_target = tgt if tgt is not None else leg.get("target")
        if leg.get("status") != "open":
            if leg.get("status") in ("pending", "entry_working", "unknown"):
                leg.update(stop=new_stop, target=new_target)          # used once it fills
                self._save_leg(leg)
            return
        leg.update(stop=new_stop, target=new_target)
        kind = leg.get("protect_kind") or ""
        if not leg.get("sl_order"):
            if kind.endswith("_unknown"):
                self._alert(f"Can't move the protective order for {leg['symbol']}: its state is unknown",
                            once_key=("levels_unknown", leg["paper_trade_id"]))
            else:
                self._protect(leg)
            self._save_leg(leg)
            return
        stop_changed = new_stop != leg.get("protect_stop")
        target_changed = kind == "oco" and new_target != leg.get("protect_target")
        if not (stop_changed or target_changed):
            self._save_leg(leg)
            return                                                   # nothing changed at the broker
        held = self._held(leg)
        try:
            if kind == "oco":
                self._b(f"modify oco {leg['symbol']}", self.broker.modify_oco, leg["sl_order"], held,
                        new_target, new_stop)
                leg.update(protect_stop=new_stop, protect_target=new_target)
            else:
                trigger, limit = self._stop_prices(leg, new_stop)
                self._b(f"modify stop {leg['symbol']}", self.broker.modify_order, leg["sl_order"], SL, held,
                        price=limit, trigger_price=trigger)
                leg["protect_stop"] = new_stop
            self._write({"event": "modify", "paper_trade_id": leg["paper_trade_id"],
                         "id": leg["sl_order"], "stop": new_stop, "target": new_target})
        except Exception as e:
            self._alert(f"Could not move the real stop/target for {leg['symbol']} "
                        f"(stop ₹{new_stop}, target ₹{new_target}): {error_text(e)}")
        self._save_leg(leg)

    def on_exit(self, trade: Trade):
        self._roll_day()
        leg = self.legs.get(str(trade.id))
        if not leg:
            return
        leg["paper_exit"] = trade.exit_price
        leg["exit_type"] = trade.exit_type
        if leg.get("status") in ("pending", "entry_working", "unknown"):
            leg["exit_requested"] = True                       # exit as soon as the entry resolves
            self._save_leg(leg)
            self._advance_entry(leg)
            return
        if leg.get("status") != "open":
            self._save_leg(leg)
            return
        leg["status"] = "exiting"
        self._save_leg(leg)
        self._advance_exit(leg)

    # ── Exit engine ────────────────────────────────────────

    def _advance_exit(self, leg: Dict):
        for _ in range(6):
            if self._exit_step(leg) != "again":
                return

    def _exit_step(self, leg: Dict) -> str:
        """One step of the exit state machine: 'again' (progress, continue now),
        'wait' (try next tick) or 'done'."""
        if leg.get("status") != "exiting":
            return "done"
        long = leg["direction"] == "long"
        side = SELL if long else BUY
        sym = leg["symbol"]

        # 1. The protective order must reach a FINAL state first (B3/M6).
        if leg.get("sl_order") and not leg.get("protect_final"):
            kind = leg.get("protect_kind")
            if kind == "oco":
                self._try(f"cancel oco {sym}", self.broker.cancel_oco, leg["sl_order"])
                st, err = self._try(f"oco status {sym}", self.broker.oco_status, leg["sl_order"])
                if err is not None or not st["final"]:
                    leg["note"] = "waiting for the OCO to reach a final state"
                    self._save_leg(leg)
                    return "wait"
                leg["protect_status"] = st["status"]
                leg["protect_fired"] = bool(st["fired"])
            else:
                self._cancel(leg["sl_order"], f"stop {sym}")
                res, st = self._read(leg["sl_order"], None, f"stop {sym}")
                if res != "ok" or not st["final"]:
                    leg["note"] = "waiting for the stop order to reach a final state"
                    self._save_leg(leg)
                    return "wait"
                leg["protect_status"] = st["status"]
                if st["filled_qty"]:
                    leg["exit_fills"].append({"order": st["id"], "qty": st["filled_qty"],
                                              "price": st["avg_price"] or leg.get("stop"), "source": "stop"})
            leg["protect_final"] = True
            self._save_leg(leg)
            return "again"

        # 2. Every earlier exit order must be final before anything new is sent (B2).
        for a in leg["exit_attempts"]:
            if a.get("final"):
                continue
            res, st = self._read(a.get("order"), a.get("ref"), f"exit {sym}")
            if res == "error":
                return "wait"
            if res == "missing":
                a["lookups"] = a.get("lookups", 0) + 1
                if a["lookups"] < REF_LOOKUPS or self._mono() - a.get("_t", self._mono()) < REF_GRACE_S:
                    self._save_leg(leg)
                    return "wait"
                a.update(final=True, note="not found at Groww (never placed)")
                self._save_leg(leg)
                continue
            a["order"] = st["id"]
            if not st["final"]:
                if self._mono() - a.get("_t", self._mono()) < EXIT_REPRICE_S and not a.get("cancel"):
                    return "wait"
                a["cancel"] = True
                self._cancel(st["id"], f"exit {sym}")
                res, st = self._read(st["id"], None, f"exit {sym}")
                if res != "ok" or not st["final"]:
                    self._save_leg(leg)
                    return "wait"
            a.update(final=True, status=st["status"])
            if st["filled_qty"]:
                leg["exit_fills"].append({"order": st["id"], "qty": st["filled_qty"],
                                          "price": st["avg_price"] or a.get("price"), "source": a.get("kind")})
            elif st["dead"] and st["status"] != "CANCELLED":
                cat = classify_error(st["remark"] or st["status"])
                self._write({"event": "error", "where": f"exit {sym} (exchange)", "category": cat,
                             "message": st["remark"] or st["status"]})
                self._reject_note(leg, cat, st["remark"] or st["status"], f"Exit {side}")
            self._save_leg(leg)

        # 3. Cap the next exit at what the broker actually holds (cross-cutting).
        owned = self._held(leg)
        try:
            net = self._b(f"position {sym}", self.broker.net_position, sym)
        except PositionAmbiguous as e:
            self._alert(f"Position fields for {sym} disagree ({e}) — NOT exiting automatically. Check Groww.",
                        once_key=("ambiguous", sym))
            return "wait"
        except Exception:
            return "wait"
        same_side = net > 0 if long else net < 0
        cap = min(owned, abs(net)) if same_side else 0
        if cap <= 0:
            self._finish_exit(leg, owned)
            return "done"
        if self._past_deadline():
            if leg["status"] != "stuck":
                leg["status"] = "stuck"
                self._save_leg(leg)
                self._alert(f"🚨 {cap} {sym} STILL OPEN at 15:19 — Groww squares off MIS at 15:20 "
                            f"(₹50+GST). Check the Groww app NOW.")
            return "done"

        # 4. Next attempt: re-priced limits, then MARKET.
        n = len(leg["exit_attempts"]) + 1
        ltp = self._ltp(leg, leg.get("paper_exit") or leg.get("entry_fill") or 0.0)
        q = leg.get("_quote") or {}
        if (long and q.get("lower") and ltp <= q["lower"]) or (not long and q.get("upper") and ltp >= q["upper"]):
            self._alert(f"🚨 {sym} is at its {'lower' if long else 'upper'} circuit — can't exit "
                        f"{cap} {'long' if long else 'short'} right now", once_key=("circuit", sym, self._day))
        use_market = n > EXIT_TRIES and self.market_ok
        price = None
        if not use_market:
            pad = EXIT_BUFFER * n
            tick = self._tick_size(leg["ticker"], ltp)
            price = self._clamp(round_to_tick(ltp * (1 - pad) if long else ltp * (1 + pad), tick, up=not long),
                                side, q)
        ref = self._ref(leg["paper_trade_id"], "X", n)
        a = {"n": n, "ref": ref, "kind": "market" if use_market else f"limit{n}", "qty": cap,
             "price": price, "final": False, "_t": self._mono()}
        leg["exit_attempts"].append(a)
        self._save_leg(leg)
        r = self._place(leg, a["kind"], ref, side, cap, MARKET if use_market else LIMIT, price=price)
        if r["id"]:
            a["order"] = r["id"]
        elif not r["ambiguous"]:
            a.update(final=True, error=r["error"], category=r["category"])
            self._reject_note(leg, r["category"], r["error"], f"Exit {side} {cap}")
            if use_market:
                self.market_ok = False
        self._save_leg(leg)
        return "again" if (r["id"] or r["ambiguous"]) else "wait"

    def _finish_exit(self, leg: Dict, unexplained: int):
        """Broker is flat for this leg. Fills not seen on our own exit orders
        came from the protective order (OCO leg) or outside — look them up."""
        long = leg["direction"] == "long"
        side = SELL if long else BUY
        if unexplained > 0 and leg.get("protect_kind") == "oco" and leg.get("protect_fired"):
            ours = {a.get("order") for a in leg["exit_attempts"]} | {f.get("order") for f in leg["exit_fills"]}
            ours |= {leg.get("entry_order")}
            rows, _ = self._try(f"orders {leg['symbol']}", self.broker.order_list, leg["symbol"])
            for o in rows or []:
                if unexplained <= 0:
                    break
                if o["side"] != side or o["id"] in ours or not o["filled_qty"]:
                    continue
                if (o.get("ref") or "").startswith(self._yymmdd() + "-"):
                    continue                                 # one of our own orders
                q = min(o["filled_qty"], unexplained)
                leg["exit_fills"].append({"order": o["id"], "qty": q, "price": o["avg_price"], "source": "oco"})
                unexplained -= q
        if unexplained > 0:
            leg["note"] = f"exit fill unknown for {unexplained} (closed outside our orders)"
            leg["exit_unknown_qty"] = unexplained
        self._settle(leg)
        leg["status"] = "closed"
        if not leg.get("smoke") and leg.get("real_pnl") is not None:
            self._realized_today += leg["real_pnl"]
        self._save_leg(leg)
        pnl = leg.get("real_pnl")
        self._alert(f"Real {side} {leg.get('filled_qty')} {leg['symbol']} closed @ "
                    f"{'₹%.2f' % leg['exit_fill'] if leg.get('exit_fill') else 'unknown price'} "
                    f"(paper ₹{(leg.get('paper_exit') or 0):,.2f}); real net "
                    f"{'₹%+.2f' % pnl if pnl is not None else 'unknown'}")

    def _settle(self, leg: Dict):
        """P&L and per-executed-order charges from the recorded fills."""
        long = leg["direction"] == "long"
        fills = [f for f in leg.get("exit_fills") or [] if f.get("qty") and f.get("price")]
        qty = sum(f["qty"] for f in fills)
        entry = leg.get("entry_fill")
        if not qty or not entry or leg.get("exit_unknown_qty"):
            leg.update(exit_fill=(sum(f["qty"] * f["price"] for f in fills) / qty) if qty else None,
                       real_gross=None, real_pnl=None, charges=None)
            return
        avg = sum(f["qty"] * f["price"] for f in fills) / qty
        gross = (avg - entry) * qty if long else (entry - avg) * qty
        entry_side, exit_side = (BUY, SELL) if long else (SELL, BUY)
        charges = sum(order_charges(entry_side, (f.get("qty") or 0) * (f.get("price") or 0))
                      for f in leg.get("entry_fills") or [{"qty": qty, "price": entry}])
        charges += sum(order_charges(exit_side, f["qty"] * f["price"]) for f in fills)
        leg.update(exit_fill=round(avg, 4), real_gross=round(gross, 2), charges=round(charges, 2),
                   real_pnl=round(gross - charges, 2))

    # ── Tick: everything that progresses without a paper event ──

    def busy(self) -> bool:
        return any(l.get("status") in ACTIVE_STATES for l in self.legs.values())

    def tick(self):
        self._roll_day()
        if self.is_backtest():
            return
        if self.standby:
            if not self.acquire_lock():
                return
            self.standby = False
            self._load_ledger()                         # another process may have changed it
        if (self.session_dir / LIQUIDATE_REQUEST_FILE).exists():
            try:
                (self.session_dir / LIQUIDATE_REQUEST_FILE).unlink()
            except OSError:
                pass
            self.liquidate("dashboard liquidate request")
        self._advance_all()
        if self._in_market():
            if (not self.exit_only and not self.smoke_done_today() and not self.disabled_reason()
                    and not self._after_forced_close() and self._smoke_attempts < MAX_SMOKE_ATTEMPTS):
                self.smoke_test()
            if self._after_forced_close(grace_s=60) and not self._past_deadline():
                for leg in list(self.legs.values()):
                    if leg.get("status") == "open":
                        leg["status"] = "exiting"
                        leg["exit_type"] = leg.get("exit_type") or "flatten_after_close"
                        self._save_leg(leg)
                        self._advance_exit(leg)
            self.reconcile()

    def _advance_all(self):
        paper_open = None
        if self._paper_open_ids is not None:
            try:
                paper_open = self._paper_open_ids()
            except Exception:
                paper_open = None
        for leg in list(self.legs.values()):
            st = leg.get("status")
            try:
                if st in ("pending", "entry_working", "unknown"):
                    self._advance_entry(leg)
                elif st == "open":
                    if (paper_open is not None and not leg.get("smoke")
                            and leg["paper_trade_id"] not in paper_open):
                        leg["status"] = "exiting"            # paper closed while we weren't looking
                        self._save_leg(leg)
                        self._advance_exit(leg)
                    elif (not leg.get("sl_order") and leg.get("stop") and not leg.get("protect_n")
                          and not leg.get("smoke")):
                        self._protect(leg)
                elif st == "exiting":
                    self._advance_exit(leg)
            except Exception as e:                           # one bad leg must not stop the others
                logger.exception(f"Live leg {leg.get('paper_trade_id')} step failed: {e}")

    def liquidate(self, reason: str = "liquidate") -> int:
        """Flatten everything this mirror owns; no new entries today."""
        self._roll_day()
        self.disable_for_today(reason)
        n = 0
        for leg in list(self.legs.values()):
            st = leg.get("status")
            if st == "open":
                leg["status"] = "exiting"
                leg["exit_type"] = "liquidate"
                self._save_leg(leg)
                self._advance_exit(leg)
                n += 1
            elif st in ("pending", "entry_working", "unknown"):
                leg["exit_requested"] = True
                leg["cancel_entry"] = True
                self._save_leg(leg)
                self._advance_entry(leg)
                n += 1
        return n

    # ── Smoke test ─────────────────────────────────────────

    def smoke_done_today(self) -> bool:
        self._roll_day()
        return bool(self._smoke and self._smoke[0] == self._day)

    def _wait_entry(self, leg: Dict, timeout_s: float):
        deadline = self._mono() + timeout_s + 5
        while leg["status"] in ("pending", "entry_working", "unknown") and self._mono() < deadline:
            self._sleep(POLL_S)
            self._advance_entry(leg)

    def _wait_exit(self, leg: Dict, timeout_s: float):
        deadline = self._mono() + timeout_s
        while leg["status"] == "exiting" and self._mono() < deadline:
            self._sleep(POLL_S)
            self._advance_exit(leg)

    def smoke_test(self, ticker: str = None) -> Dict:
        """BUY 1 (limit) → OCO far from the price → modify both triggers →
        cancel the OCO to a final state → SELL 1 MARKET (limit fallback).
        Each step writes a ledger row first and uses its own reference id, so
        a restart resumes instead of duplicating. BUY/SELL failing → no real
        entries today; only the OCO failing → plain-SL fallback."""
        self._roll_day()
        ticker = ticker or os.environ.get(SMOKE_SYMBOL_ENV, DEFAULT_SMOKE_SYMBOL)
        if self.is_backtest():
            return {"passed": False, "reason": "backtest"}
        self._smoke_attempts += 1
        attempt = self._smoke_attempts
        self._write({"event": "smoke_start", "attempt": attempt, "ticker": ticker})
        sym = _symbol(ticker)
        q = self._quote(sym) if sym else {}
        price = q.get("ltp") or 0.0
        if not price:
            try:
                price = float((self.market_data.get_current_prices([ticker]) or {}).get(ticker) or 0)
            except Exception:
                price = 0.0
        if not price or sym is None:
            return self._smoke_result(False, f"no live price / bad symbol for {ticker}")
        tid = f"S{attempt}"
        leg = {"paper_trade_id": f"smoke{attempt}-{self._day}", "date": self._day, "ticker": ticker,
               "symbol": sym, "direction": "long", "paper_qty": 1, "paper_entry": price,
               "stop": None, "target": None, "filled_qty": 0, "entry_fill": None, "entry_order": None,
               "sl_order": None, "protect_kind": None, "protect_final": False,
               "exit_attempts": [], "exit_fills": [], "status": "pending", "smoke": True}
        tick = self._tick_size(ticker, price)
        limit = self._clamp(round_to_tick(price * (1 + ENTRY_BUFFER), tick, up=True), BUY, q)
        ref = self._ref(tid, "B")
        leg.update(entry_ref=ref, entry_limit=limit, entry_qty=1, _t=self._mono())
        self._save_leg(leg)
        r = self._place(leg, "smoke_buy", ref, BUY, 1, LIMIT, price=limit)
        if not (r["id"] or r["ambiguous"]):
            leg.update(status="rejected", category=r["category"], note=r["error"])
            self._save_leg(leg)
            return self._smoke_result(False, f"BUY rejected [{r['category']}]: {r['error']}")
        leg.update(entry_order=r["id"], status="entry_working")
        self._save_leg(leg)
        self._advance_entry(leg)
        self._wait_entry(leg, ENTRY_TIMEOUT_S)
        if leg["status"] != "open":
            return self._smoke_result(False, f"BUY {leg['status']}: {leg.get('note', '')}")

        # OCO: create far away, modify both triggers, cancel to a final state.
        oco_ok, oco_note = False, ""
        oref = self._ref(tid, "O")
        self._write({"event": "smoke_step", "step": "oco_create", "ref": oref})
        sid, err = self._try("smoke oco", self.broker.create_oco, sym, 1, SELL, 1,
                             round(price * 1.20, 2), round(price * 0.80, 2), oref)
        if err is not None and is_ambiguous(err):
            sid, _ = self._try("smoke find oco", self.broker.find_oco, sym, oref)
        if sid:
            self._write({"event": "smoke_step", "step": "oco_modify", "id": sid})
            _, merr = self._try("smoke modify oco", self.broker.modify_oco, sid, 1,
                                round(price * 1.25, 2), round(price * 0.75, 2))
            self._write({"event": "smoke_step", "step": "oco_cancel", "id": sid})
            self._try("smoke cancel oco", self.broker.cancel_oco, sid)
            final = None
            for _ in range(5):
                st, _e = self._try("smoke oco status", self.broker.oco_status, sid)
                if st and st["final"]:
                    final = st
                    break
                self._sleep(POLL_S)
            if final is None:
                # Can't prove the OCO is gone: don't sell into a possibly live OCO.
                leg.update(sl_order=sid, protect_kind="oco", protect_final=False)
                oco_note = "OCO did not reach a final state"
            else:
                leg.update(sl_order=sid, protect_kind="oco", protect_final=True,
                           protect_status=final["status"], protect_fired=final["fired"])
                oco_ok = merr is None and not final["fired"]
                oco_note = "ok" if oco_ok else (f"modify failed: {error_text(merr)}" if merr else "OCO fired")
        else:
            oco_note = f"create failed: {error_text(err) if err else 'unknown'}"
        self.oco_ok = oco_ok
        self._save_leg(leg)

        # SELL 1 via MARKET first (tests MARKET acceptance), then the normal exit engine.
        leg["status"] = "exiting"
        self._save_leg(leg)
        mref = self._ref(tid, "M")
        if leg.get("protect_final") or not leg.get("sl_order"):
            r = self._place(leg, "smoke_sell", mref, SELL, 1, MARKET)
            a = {"n": 0, "ref": mref, "kind": "market", "qty": 1, "price": None, "final": False,
                 "_t": self._mono(), "order": r["id"]}
            if r["id"] or r["ambiguous"]:
                leg["exit_attempts"].append(a)
            else:
                self.market_ok = False
                leg.setdefault("failures", []).append(
                    {"what": "smoke MARKET sell", "category": r["category"], "message": r["error"]})
            self._save_leg(leg)
        self._advance_exit(leg)
        self._wait_exit(leg, 60)
        market_filled = any(f.get("source") == "market" for f in leg["exit_fills"])
        if not market_filled:
            self.market_ok = False
        if leg["status"] == "closed":
            return self._smoke_result(True, f"ok (MARKET {'ok' if self.market_ok else 'not used'}; "
                                            f"OCO {oco_note})")
        return self._smoke_result(False, f"could not close the 1-share test position ({leg['status']})")

    def _smoke_result(self, passed: bool, reason: str) -> Dict:
        self._smoke = (self._day, passed)
        self._write({"event": "smoke", "passed": passed, "reason": reason,
                     "market_ok": self.market_ok, "oco_ok": self.oco_ok})
        if passed:
            self._alert(f"Smoke test passed ({reason}). Real orders are ON for today.")
        else:
            self.disable_for_today(f"smoke test failed: {reason}")
        return {"passed": passed, "reason": reason, "market_ok": self.market_ok, "oco_ok": self.oco_ok}

    # ── Reconciliation ─────────────────────────────────────

    def reconcile(self, force: bool = False):
        """Resolve pending legs, then compare Groww's net MIS positions with
        what the ledger owns (throttled). Alerts once per mismatch; acts only
        through the legs' own state machines."""
        self._roll_day()
        if self.is_backtest():
            return
        if force:
            self._advance_all()
        now_m = self._mono()
        if not force and self._last_reconcile is not None and now_m - self._last_reconcile < RECONCILE_EVERY_S:
            return
        traded = {l["symbol"] for l in self.legs.values()
                  if l.get("symbol") and (l.get("entry_ref") or l.get("entry_order"))}
        if not traded:
            return
        self._last_reconcile = now_m
        try:
            actual = self._b("positions", self.broker.positions)
        except PositionAmbiguous as e:
            self._alert(f"Groww position fields disagree ({e}) — reconcile skipped", once_key=("pos_amb", str(e)))
            return
        except Exception:
            return
        expected: Dict[str, int] = {}
        for l in self.legs.values():
            if l.get("status") in ("open", "exiting", "stuck"):
                q = self._held(l)
                expected[l["symbol"]] = expected.get(l["symbol"], 0) + (q if l["direction"] == "long" else -q)
        for sym in traded:
            want, got = expected.get(sym, 0), actual.get(sym, 0)
            if want != got and (sym, got) not in self._mismatch_alerted:
                pending = any(l.get("symbol") == sym and l.get("status") in ("pending", "entry_working", "unknown")
                              for l in self.legs.values())
                if pending:
                    continue                               # an order is still resolving
                self._mismatch_alerted.add((sym, got))
                self._write({"event": "mismatch", "symbol": sym, "expected": want, "actual": got})
                self._alert(f"Position mismatch {sym}: Groww {got:+d}, ledger expects {want:+d}")

    # ── Status / end-of-day report ─────────────────────────

    def open_legs_summary(self) -> str:
        legs = [l for l in self.legs.values() if l.get("status") in EXPOSURE_STATES]
        if not legs:
            return "no real positions open"
        return "; ".join(f"{l.get('symbol')} {l.get('direction')} {self._held(l)} [{l.get('status')}, "
                         f"protect {l.get('protect_kind') or 'none'}]" for l in legs)

    def _failures(self) -> List[Dict]:
        out = []
        try:
            for line in self.ledger_path.read_text().splitlines():
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if row.get("date") != self._day:
                    continue
                if row.get("event") == "error":
                    out.append(row)
        except OSError:
            pass
        return out

    def eod_report(self) -> str:
        """Never raises; unknown/stuck legs first."""
        self._roll_day()
        try:
            return self._eod_report()
        except Exception as e:                                # noqa: BLE001
            logger.exception("EOD report failed")
            return f"{self.session_id} LIVE {self._day}: report failed ({e}); legs: {self.open_legs_summary()}"

    def _eod_report(self) -> str:
        legs = [l for l in self.legs.values() if not l.get("smoke")]
        lines = [f"{self.session_id} LIVE vs PAPER — {self._day}"]
        attention = [l for l in legs if l.get("status") in EXPOSURE_STATES]
        if attention:
            lines.append("⚠️ NEEDS ATTENTION:")
            for l in attention:
                lines.append(f"  #{l.get('paper_trade_id')} {l.get('symbol')} {l.get('direction')} "
                             f"held {self._held(l)} — {l.get('status')} {l.get('note') or ''}".rstrip())
        if not legs:
            lines.append("no paper trades today.")
        tot_paper = tot_real = tot_charges = 0.0
        slips, counts = [], {}
        for l in sorted(legs, key=lambda x: str(x.get("paper_trade_id"))):
            status = l.get("status") or "?"
            counts[status] = counts.get(status, 0) + 1
            head = f"#{l.get('paper_trade_id')} {(l.get('direction') or '?').upper()} {l.get('ticker')}"
            if status != "closed":
                if status not in EXPOSURE_STATES:
                    lines.append(f"{head}: {status}{(' — ' + str(l.get('note'))) if l.get('note') else ''}")
                continue
            q = l.get("filled_qty") or 0
            sign = 1 if l.get("direction") == "long" else -1
            pe, rf = l.get("paper_entry"), l.get("entry_fill")
            px, xf = l.get("paper_exit"), l.get("exit_fill")
            s_in = sign * (rf - pe) / pe * 1e4 if (pe and rf) else None
            s_out = -sign * (xf - px) / px * 1e4 if (px and xf) else None
            paper_pnl = sign * (px - pe) * q if (px and pe) else None
            slips += [s for s in (s_in, s_out) if s is not None]
            tot_paper += paper_pnl or 0.0
            tot_real += l.get("real_pnl") or 0.0
            tot_charges += l.get("charges") or 0.0
            fmt = lambda v, f: f.format(v) if v is not None else "?"
            lines.append(
                f"{head} x{q}: in {fmt(pe, '₹{:,.2f}')}→{fmt(rf, '₹{:,.2f}')} ({fmt(s_in, '{:+.1f}bps')}), "
                f"out {fmt(px, '₹{:,.2f}')}→{fmt(xf, '₹{:,.2f}')} ({fmt(s_out, '{:+.1f}bps')}), "
                f"paper {fmt(paper_pnl, '₹{:+,.2f}')} vs real net {fmt(l.get('real_pnl'), '₹{:+,.2f}')} "
                f"(charges {fmt(l.get('charges'), '₹{:,.2f}')})")
        avg_slip = sum(slips) / len(slips) if slips else 0.0
        lines.append(
            f"TOTAL (same qty): paper ₹{tot_paper:+,.2f} | real net ₹{tot_real:+,.2f} | "
            f"charges ₹{tot_charges:,.2f} (modelled) | avg slippage {avg_slip:+.1f}bps/side | "
            + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())))
        fails = self._failures()
        if fails:
            by_cat: Dict[str, int] = {}
            for f in fails:
                by_cat[f.get("category") or "unknown"] = by_cat.get(f.get("category") or "unknown", 0) + 1
            lines.append("FAILURES: " + ", ".join(f"{k} {v}" for k, v in sorted(by_cat.items())))
            for f in fails[-10:]:
                lines.append(f"  [{f.get('category')}] {f.get('where') or f.get('leg') or ''}: "
                             f"{f.get('message') or ''}")
        return "\n".join(lines)

    def send_eod_report(self) -> str:
        text = self.eod_report()
        self._write({"event": "eod_report", "text": text})
        self._alert(text)
        return text


# ── Worker thread (actor) ─────────────────────────────────

class LiveWorker:
    """Single thread that owns every broker call. Events run serially; the
    mirror is ticked every BUSY_TICK_S while orders are resolving, else every
    IDLE_TICK_S. synchronous=True runs events inline (tests)."""

    BUSY_TICK_S = 2.0
    IDLE_TICK_S = 15.0

    def __init__(self, mirror: LiveMirror, synchronous: bool = False):
        self.mirror = mirror
        self.synchronous = synchronous
        self._q: "queue.Queue" = queue.Queue()
        self._stop = threading.Event()
        self._idle = threading.Event()
        self._idle.set()
        self._thread = None

    def start(self):
        if self.synchronous or self._thread is not None:
            return
        if not self.mirror.acquire_lock():
            self.mirror.standby = True
            self.mirror._alert("Another process holds the live lock — real orders on standby until it's free")
        self._thread = threading.Thread(target=self._loop, name="live-mirror", daemon=True)
        self._thread.start()

    def submit(self, method: str, *args):
        if self.synchronous:
            self._run(method, args)
            return
        self._idle.clear()
        self._q.put((method, args))

    def tick(self):
        self._run("tick", ())

    def _run(self, method: str, args):
        try:
            getattr(self.mirror, method)(*args)
        except Exception as e:                                 # never kill the worker
            logger.exception(f"Live worker {method} failed: {e}")

    def _loop(self):
        last_tick = 0.0
        while not self._stop.is_set():
            wait = self.BUSY_TICK_S if self.mirror.busy() else self.IDLE_TICK_S
            try:
                ev = self._q.get(timeout=max(0.1, wait - (time.monotonic() - last_tick)))
            except queue.Empty:
                ev = None
            if ev is not None:
                self._run(*ev)
            if ev is None or time.monotonic() - last_tick >= self.BUSY_TICK_S:
                self._run("tick", ())
                last_tick = time.monotonic()
            if self._q.empty():
                self._idle.set()

    def drain(self, timeout: float) -> bool:
        """Wait until every queued event has run (e.g. on SIGTERM)."""
        if self.synchronous:
            return True
        return self._idle.wait(timeout)

    def stop(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
        self.mirror.release_lock()


class LivePortfolio(Portfolio):
    """Paper portfolio whose every booking is mirrored to real orders. Paper
    books first on the caller's thread; the live side runs on the worker."""

    def __init__(self, db_path: str, starting_capital: float, worker):
        super().__init__(db_path, starting_capital)
        if isinstance(worker, LiveMirror):
            worker = LiveWorker(worker, synchronous=True)
        self.worker = worker
        self.mirror = worker.mirror

    def _mirror(self, method: str, trade):
        if trade is None:
            return
        try:
            self.worker.submit(method, replace(trade))
        except Exception as e:
            logger.exception(f"Live mirror {method} failed (paper unaffected): {e}")

    def execute_buy(self, *args, **kwargs) -> Trade:
        trade = super().execute_buy(*args, **kwargs)
        self._mirror("on_entry", trade)
        return trade

    def execute_short(self, *args, **kwargs) -> Trade:
        trade = super().execute_short(*args, **kwargs)
        self._mirror("on_entry", trade)
        return trade

    def execute_sell(self, *args, **kwargs) -> Trade:
        trade = super().execute_sell(*args, **kwargs)
        self._mirror("on_exit", trade)
        return trade

    def execute_cover(self, *args, **kwargs) -> Trade:
        trade = super().execute_cover(*args, **kwargs)
        self._mirror("on_exit", trade)
        return trade

    def update_levels(self, *args, **kwargs):
        trade = super().update_levels(*args, **kwargs)
        self._mirror("on_levels", trade)
        return trade


# ── Construction helpers (runner + dashboard) ─────────────

def build_mirror(session, market_data=None, *, exit_only=False, is_backtest=lambda: False,
                 forced_close_time=None, db_path=None) -> LiveMirror:
    from .groww_broker import GrowwBroker, GrowwInstruments
    paper_open = None
    if db_path:
        reader = Portfolio(db_path, 0)
        paper_open = lambda: {str(p.id) for p in reader.get_open_positions()}
    return LiveMirror(GrowwBroker(), session.session_id, session.session_dir, market_data=market_data,
                      instruments=GrowwInstruments(), is_backtest=is_backtest,
                      forced_close_time=forced_close_time, paper_open_ids=paper_open, exit_only=exit_only)


def liquidate_from_dashboard(session, *, mirror: LiveMirror = None, wait_lock_s: float = 25.0,
                             run_until_flat: bool = True, sleep=time.sleep) -> Optional[Dict]:
    """Kill switch for the real side. If the agent still holds the live lock,
    leave a request file it acts on at its next tick; otherwise flatten here,
    on a background thread that keeps retrying until flat (or the market closes)."""
    if mirror is None and not ledger_has_exposure(session.session_dir):
        return None
    mirror = mirror or build_mirror(session, exit_only=True)
    waited = 0.0
    while not mirror.acquire_lock():
        if waited >= wait_lock_s:
            (Path(session.session_dir) / LIQUIDATE_REQUEST_FILE).write_text(datetime.now(IST).isoformat())
            mirror._alert("Liquidate requested while the agent holds the live lock — it will flatten on its next tick")
            return {"live": "requested_from_agent"}
        sleep(0.5)
        waited += 0.5
    mirror._load_ledger()
    n = mirror.liquidate("manual liquidation from the dashboard")

    def run():
        try:
            while mirror.busy() and mirror._in_market() and not mirror._past_deadline():
                sleep(LiveWorker.BUSY_TICK_S)
                mirror.tick()
            if mirror.busy():
                mirror._alert(f"Liquidation incomplete: {mirror.open_legs_summary()}")
        finally:
            mirror.release_lock()
    if run_until_flat:
        threading.Thread(target=run, name="live-liquidate", daemon=True).start()
    else:
        mirror.release_lock()
    return {"live": f"flattening {n} legs", "open": mirror.open_legs_summary()}
