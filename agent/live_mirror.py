"""
Real-money mirror of a paper session on Groww.

Every paper event (entry, exit from any path, stop/target change) is replayed
as a real NSE intraday (MIS) order at the same quantity, so paper and real can
be compared trade by trade. Paper always books first and stays the record the
agent and learner see; nothing here ever raises into the agent loop.

Threading: LivePortfolio books paper on the agent thread and hands the event
to a LiveWorker, a single thread that owns every broker call. The worker is
started only by the agent's run_loop (never by one-shot/status/dashboard
paths) and holds a per-session process lock; without the lock the mirror is
on standby and drops events (paper only).

Leg state machine (one leg per paper trade):
  pending       ledger row written with the entry reference id, before placing
  entry_working entry order placed (or reply ambiguous), waiting for a final state
  unknown       entry state can't be read; resolved every tick, never guessed
  open          entry final with a fill; protected by an OCO (or a plain SL)
  exiting       paper exited. Each step: earlier exit orders final → a trusted
                position read (positions == today's MIS order fills) → cancel
                protection to a final state → exit capped at the net position.
                Never leaves this state while protection is unresolved or an
                orphan protective order of ours is still live on the symbol.
  closed        flat at the broker for this leg, no live protection left
  stuck         not flat at 15:19, or positions and fills disagree for >15s
                (then re-protected and real entries are disabled) — loud alert
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

from .groww_broker import (BUY, SELL, LIMIT, MARKET, SL, SL_M, GrowwAuthError, PositionAmbiguous,
                           OCO_FINAL, classify_error, error_text, is_ambiguous, is_auth_error)
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
REF_LOOKUPS = 3                  # "not found" lookups (by reference AND in the order list) ...
ABSENCE_S = 30                   # ... over at least this long before an order counts as never placed
DISAGREE_READS, DISAGREE_S = 3, 15   # positions vs fills mismatch this long → stuck + re-protect
OCO_TRIGGER_WAIT_S = 20          # a TRIGGERED OCO's child order gets this long to show up
FALLBACK_STOP_PCT = 0.015        # re-protection when the paper stop is unknown
RECONCILE_EVERY_S = 60
EXIT_DEADLINE = dtime(15, 19)    # Groww's MIS auto square-off is 15:20 (₹50+GST per position)
MARKET_OPEN, MARKET_CLOSE = dtime(9, 15), dtime(15, 30)
SMOKE_SYMBOL_ENV = "GROWW_SMOKE_SYMBOL"
DEFAULT_SMOKE_SYMBOL = "ITC.NS"
MAX_SMOKE_ATTEMPTS = 2
SMOKE_OCO_PCT, SMOKE_OCO_MODIFY_PCT = 0.025, 0.03
KNOWN_OCO_STATUSES = {"ACTIVE", "TRIGGERED", "CANCELLED", "EXPIRED", "FAILED", "COMPLETED"}

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


def _ist_today() -> str:
    return datetime.now(IST).date().isoformat()


def ledger_has_exposure(session_dir, today: str = None) -> bool:
    """True if today's ledger still has a real position (or an unresolved order)."""
    today = today or _ist_today()
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


def request_liquidation(session_dir, today: str = None):
    """Ask whoever drives the broker (the agent's worker, or the dashboard
    liquidator) to flatten today. Dated, so a stale file is ignored tomorrow."""
    Path(session_dir).mkdir(parents=True, exist_ok=True)
    (Path(session_dir) / LIQUIDATE_REQUEST_FILE).write_text(today or _ist_today())


def liquidation_requested(session_dir, today: str = None) -> bool:
    path = Path(session_dir) / LIQUIDATE_REQUEST_FILE
    try:
        stamp = path.read_text().strip()[:10]
    except OSError:
        return False
    if stamp == (today or _ist_today()):
        return True
    try:
        path.unlink()                                   # stale (another day): clear it
    except OSError:
        pass
    return False


def clear_liquidation_request(session_dir):
    try:
        (Path(session_dir) / LIQUIDATE_REQUEST_FILE).unlink()
    except OSError:
        pass


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


def _parse_ts(s) -> Optional[datetime]:
    if not s:
        return None
    try:
        d = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=IST)


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
        self.standby = False                     # no live lock: drop events, no broker calls
        self.ledger_path = self.session_dir / LEDGER_FILE
        self._ledger_lock = threading.Lock()
        self._lock_fh = None
        self._smoke_running = False
        self.legs: Dict[str, Dict] = {}          # str(paper_trade_id) → leg
        self._day = None
        self._load_ledger()

    # ── Day state / ledger ─────────────────────────────────

    def _today(self) -> str:
        return self._now().astimezone(IST).date().isoformat()

    def _yymmdd(self) -> str:
        return self._now().astimezone(IST).strftime("%y%m%d")

    def _ours(self, ref) -> bool:
        return bool(ref) and str(ref).startswith(self._yymmdd() + "-")

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
        self.legs = {}
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
            elif ev == "order" and row.get("status") == "sending":   # each order once
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

    # ── Locks / alerts / failures ──────────────────────────

    def acquire_lock(self) -> bool:
        """Exclusive per-session process lock: only one process (the agent's
        loop or the dashboard's liquidator) may drive the broker."""
        if self._lock_fh is not None:
            return True
        fh = None
        try:
            self.session_dir.mkdir(parents=True, exist_ok=True)
            fh = open(self.session_dir / LOCK_FILE, "a+")
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            if fh is not None:
                fh.close()
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
            if isinstance(e, PositionAmbiguous):
                raise
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

    def _standby_drop(self, what: str) -> bool:
        if self.standby:
            logger.warning(f"Live mirror on standby (lock held elsewhere) — dropped {what}; paper only")
            return True
        return False

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

    def _position_view(self, sym: str) -> Optional[Dict]:
        """{'net': positions net (None if its fields disagree), 'orders_net':
        net from today's MIS fills}. None if either read failed."""
        try:
            net = self._b(f"position {sym}", self.broker.net_position, sym)
        except PositionAmbiguous as e:
            net = None
            self._alert(f"Position fields for {sym} disagree ({e})", once_key=("pos_amb", sym))
        except Exception:
            return None
        orders_net, err = self._try(f"orders {sym}", self.broker.orders_net, sym)
        if err is not None:
            return None
        return {"net": net, "orders_net": orders_net}

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

    def _read(self, order_id: Optional[str], ref: Optional[str], symbol: str, where: str):
        """('ok', state) | ('missing', None) | ('error', None). A reference that
        the lookup can't see is also searched in the order list (the lookup
        endpoint can lag)."""
        if order_id:
            st, err = self._try(f"status {where}", self.broker.order_status, order_id)
            return ("ok", st) if err is None else ("error", None)
        if not ref:
            return "missing", None
        st, err = self._try(f"lookup {where}", self.broker.order_by_reference, ref)
        if err is not None:
            return "error", None
        if st:
            return "ok", st
        st, err = self._try(f"order list {where}", self.broker.order_by_reference_in_list, ref, symbol)
        if err is not None:
            return "error", None
        return ("ok", st) if st else ("missing", None)

    def _absent(self, holder: Dict) -> bool:
        """Count a 'not found' read; True once it's safe to call it never placed."""
        holder["lookups"] = holder.get("lookups", 0) + 1
        return holder["lookups"] >= REF_LOOKUPS and self._mono() - holder.get("_t", self._mono()) >= ABSENCE_S

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

    def _paper_is_open(self, tid) -> Optional[bool]:
        if self._paper_open_ids is None:
            return None
        try:
            return str(tid) in self._paper_open_ids()
        except Exception:
            return None

    # ── Paper events ───────────────────────────────────────

    def on_entry(self, trade: Trade):
        self._roll_day()
        if self._standby_drop(f"entry #{trade.id}"):
            return
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
        if self._paper_is_open(trade.id) is False:
            leg.update(status="skipped", note="paper trade already closed before the order could go out")
            self._save_leg(leg)
            return
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

    def _start_entry(self, leg: Dict, qty: int, price: float, kind: str = "entry", ref: str = None):
        long = leg["direction"] == "long"
        side = BUY if long else SELL
        q = self._quote(leg["symbol"])
        tick = self._tick_size(leg["ticker"], price)
        limit = round_to_tick(price * (1 + ENTRY_BUFFER) if long else price * (1 - ENTRY_BUFFER), tick, up=long)
        limit = self._clamp(limit, side, q)
        ref = ref or self._ref(leg["paper_trade_id"], "E")
        leg.update(entry_ref=ref, entry_limit=limit, entry_qty=qty, status="pending", _t=self._mono())
        self._save_leg(leg)                                   # pending leg before the call
        r = self._place(leg, kind, ref, side, qty, LIMIT, price=limit)
        if r["id"] or r["ambiguous"]:
            leg.update(entry_order=r["id"], status="entry_working", _t=self._mono())
            if r["ambiguous"]:
                leg["note"] = f"entry reply ambiguous ({r['error']}) — resolving by reference"
            self._save_leg(leg)
            self._advance_entry(leg)
            return r
        leg.update(status="rejected", category=r["category"], note=r["error"])
        self._save_leg(leg)
        self._reject_note(leg, r["category"], r["error"], f"Real {side} {qty}")
        return r

    def _advance_entry(self, leg: Dict):
        """Drive pending/entry_working/unknown to a final broker state. Never
        records missed/rejected without one."""
        long = leg["direction"] == "long"
        side = BUY if long else SELL
        res, st = self._read(leg.get("entry_order"), leg.get("entry_ref"), leg["symbol"], f"entry {leg['symbol']}")
        if res == "error":
            if leg["status"] != "unknown":
                leg["status"] = "unknown"
                self._save_leg(leg)
            self._alert(f"Can't read the real {side} order for {leg['symbol']} — status UNKNOWN, retrying",
                        once_key=("unknown", leg["paper_trade_id"]))
            return
        if res == "missing":
            if not self._absent(leg):
                self._save_leg(leg)
                return
            leg.update(status="missed", note="entry order not found at Groww after "
                                              f"{leg['lookups']} lookups over {ABSENCE_S}s (never placed)")
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
                res, st2 = self._read(st["id"], None, leg["symbol"], f"entry {leg['symbol']}")
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
        want_exit = leg.get("exit_requested") or (leg.get("smoke") and not self._smoke_running)
        leg["status"] = "exiting" if want_exit else "open"
        self._save_leg(leg)
        part = "" if filled >= (leg.get("entry_qty") or filled) else f" (PARTIAL {filled}/{leg.get('entry_qty')})"
        self._alert(f"Real {side} {filled} {leg['symbol']} filled @ ₹{leg['entry_fill']:,.2f}{part}; "
                    f"paper @ ₹{leg['paper_entry']:,.2f}")
        if want_exit:
            self._advance_exit(leg)
        elif leg.get("stop") and not leg.get("smoke"):
            self._protect(leg)

    # ── Protection (OCO, plain SL fallback) ────────────────

    def _held(self, leg: Dict) -> int:
        return int(leg.get("filled_qty") or 0) - sum(int(f.get("qty") or 0) for f in leg.get("exit_fills") or [])

    def _triggers(self, leg: Dict, stop: float, target: Optional[float]):
        """Tick-rounded triggers: a long's stop rounds down and target up; a
        short's stop rounds up and target down."""
        long = leg["direction"] == "long"
        tick = self._tick_size(leg["ticker"], stop or target or 0)
        s = round_to_tick(stop, tick, up=not long) if stop else None
        t = round_to_tick(target, tick, up=long) if target else None
        return s, t

    def _protection_active(self, leg: Dict) -> bool:
        return bool(leg.get("sl_order")) and not leg.get("protect_final")

    def _protect(self, leg: Dict, stop: float = None):
        held = self._held(leg)
        stop = stop or leg.get("stop")
        if held <= 0 or not stop:
            return
        long = leg["direction"] == "long"
        exit_side = SELL if long else BUY
        n = leg.get("protect_n", 0) + 1
        leg["protect_n"] = n
        s_trig, t_trig = self._triggers(leg, stop, leg.get("target"))
        if self.oco_ok and t_trig and not self._oco_supported():
            self.oco_ok = False
        if self.oco_ok and t_trig:
            ref = self._ref(leg["paper_trade_id"], "O", n)
            leg.update(protect_ref=ref, protect_kind="oco", protect_final=False, sl_order=None,
                       protect_created_at=self._now().isoformat(), protect_qty=held, _pt=self._mono())
            self._save_leg(leg)
            self._write({"event": "order", "paper_trade_id": leg["paper_trade_id"], "leg": "oco", "ref": ref,
                         "symbol": leg["symbol"], "side": exit_side, "req_qty": held,
                         "target": t_trig, "trigger": s_trig, "status": "sending"})
            self._orders_today += 1
            try:
                sid = self._b(f"oco {leg['symbol']}", self.broker.create_oco, leg["symbol"], held, exit_side,
                              held if long else -held, t_trig, s_trig, ref)
            except Exception as e:
                if is_ambiguous(e):
                    found, _ = self._try(f"find oco {leg['symbol']}", self.broker.find_oco, leg["symbol"], ref)
                    if found:
                        sid = found
                    else:
                        leg.update(protect_kind="oco_unknown")
                        self._save_leg(leg)
                        self._alert(f"OCO for {leg['symbol']} may or may not exist (reply lost: {error_text(e)}). "
                                    f"Resolving by reference; the leg can't close until it is found or swept.",
                                    once_key=("oco_unknown", leg["paper_trade_id"], n))
                        return
                else:
                    cat = classify_error(e)
                    leg.setdefault("failures", []).append({"what": "oco", "category": cat, "message": error_text(e)})
                    if isinstance(e, (AttributeError, TypeError, NotImplementedError)):
                        self.oco_ok = False
                    self._alert(f"OCO for {leg['symbol']} failed [{cat}]: {error_text(e)} — using a plain stop",
                                once_key=("oco_fail", leg["paper_trade_id"], n))
                    sid = None
            if sid:
                leg.update(sl_order=sid, protect_stop=s_trig, protect_target=t_trig)
                self._save_leg(leg)
                return
        self._protect_plain_sl(leg, held, n, s_trig)

    def _protect_plain_sl(self, leg: Dict, held: int, n: int, trigger: float):
        long = leg["direction"] == "long"
        tick = self._tick_size(leg["ticker"], trigger)
        limit = round_to_tick(trigger * (1 - SL_LIMIT_PAD) if long else trigger * (1 + SL_LIMIT_PAD), tick, up=not long)
        ref = self._ref(leg["paper_trade_id"], "P", n)
        leg.update(protect_ref=ref, protect_kind="sl", protect_final=False, sl_order=None,
                   protect_created_at=self._now().isoformat(), protect_qty=held, _pt=self._mono())
        self._save_leg(leg)
        r = self._place(leg, "stop", ref, SELL if long else BUY, held, SL, price=limit, trigger=trigger)
        if r["ambiguous"]:
            res, st = self._read(None, ref, leg["symbol"], f"stop {leg['symbol']}")
            r["id"] = st["id"] if res == "ok" else None
            if not r["id"]:
                leg["protect_kind"] = "sl_unknown"
                self._alert(f"Protective stop for {leg['symbol']} may or may not exist — resolving by reference",
                            once_key=("sl_unknown", leg["paper_trade_id"], n))
        leg.update(sl_order=r["id"], protect_stop=trigger)
        self._save_leg(leg)
        if not r["id"] and not r["ambiguous"]:
            self._reject_note(leg, r["category"], r["error"], "Protective stop")

    def _resolve_protection(self, leg: Dict):
        """*_unknown protection: look its reference up every tick."""
        kind = leg.get("protect_kind") or ""
        if not kind.endswith("_unknown") or not leg.get("protect_ref"):
            return
        ref = leg["protect_ref"]
        if kind == "oco_unknown":
            sid, err = self._try(f"find oco {leg['symbol']}", self.broker.find_oco, leg["symbol"], ref)
            if sid:
                leg.update(sl_order=sid, protect_kind="oco")
                self._save_leg(leg)
                self._alert(f"Found the OCO for {leg['symbol']} ({sid})")
            return
        res, st = self._read(None, ref, leg["symbol"], f"stop {leg['symbol']}")
        if res == "ok" and st:
            leg.update(sl_order=st["id"], protect_kind="sl")
            self._save_leg(leg)

    def on_levels(self, trade: Trade):
        self._roll_day()
        if self._standby_drop(f"levels #{trade.id}"):
            return
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
        s_trig, t_trig = self._triggers(leg, new_stop, new_target)
        stop_changed = s_trig != leg.get("protect_stop")
        target_changed = kind == "oco" and t_trig != leg.get("protect_target")
        if not (stop_changed or target_changed):
            self._save_leg(leg)
            return                                                   # nothing changed at the broker
        held = self._held(leg)
        try:
            if kind == "oco":
                self._b(f"modify oco {leg['symbol']}", self.broker.modify_oco, leg["sl_order"], held,
                        t_trig, s_trig)
                leg.update(protect_stop=s_trig, protect_target=t_trig)
            else:
                long = leg["direction"] == "long"
                tick = self._tick_size(leg["ticker"], s_trig)
                limit = round_to_tick(s_trig * (1 - SL_LIMIT_PAD) if long else s_trig * (1 + SL_LIMIT_PAD),
                                      tick, up=not long)
                self._b(f"modify stop {leg['symbol']}", self.broker.modify_order, leg["sl_order"], SL, held,
                        price=limit, trigger_price=s_trig)
                leg["protect_stop"] = s_trig
            self._write({"event": "modify", "paper_trade_id": leg["paper_trade_id"],
                         "id": leg["sl_order"], "stop": s_trig, "target": t_trig})
        except Exception as e:
            self._alert(f"Could not move the real stop/target for {leg['symbol']} "
                        f"(stop ₹{s_trig}, target ₹{t_trig}): {error_text(e)}")
        self._save_leg(leg)

    def on_exit(self, trade: Trade):
        self._roll_day()
        if self._standby_drop(f"exit #{trade.id}"):
            return
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
        for _ in range(8):
            if self._exit_step(leg) != "again":
                return

    def _resolve_attempts(self, leg: Dict) -> str:
        """Every earlier exit order must be final before anything new is sent.
        An ambiguous attempt is final only once absence is confirmed."""
        sym = leg["symbol"]
        side = SELL if leg["direction"] == "long" else BUY
        for a in leg["exit_attempts"]:
            if a.get("final"):
                continue
            res, st = self._read(a.get("order"), a.get("ref"), sym, f"exit {sym}")
            if res == "error":
                return "wait"
            if res == "missing":
                if not self._absent(a):
                    self._save_leg(leg)
                    return "wait"
                a.update(final=True, note=f"not found at Groww after {a['lookups']} lookups (never placed)")
                self._save_leg(leg)
                continue
            a["order"] = st["id"]
            if not st["final"]:
                if self._mono() - a.get("_t", self._mono()) < EXIT_REPRICE_S and not a.get("cancel"):
                    return "wait"
                a["cancel"] = True
                self._cancel(st["id"], f"exit {sym}")
                res, st = self._read(st["id"], None, sym, f"exit {sym}")
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
        return "ok"

    def _oco_children(self, leg: Dict) -> Optional[List[Dict]]:
        """Orders a fired OCO could have produced: exit side, same symbol, not
        ours, created at/after the OCO, quantity ≤ the protected qty. None if
        the order list can't be read."""
        side = SELL if leg["direction"] == "long" else BUY
        rows, err = self._try(f"orders {leg['symbol']}", self.broker.order_list, leg["symbol"])
        if err is not None:
            return None
        since = _parse_ts(leg.get("protect_created_at"))
        known = {a.get("order") for a in leg["exit_attempts"]} | {f.get("order") for f in leg["exit_fills"]}
        known |= {leg.get("entry_order")}
        out = []
        for o in rows or []:
            if o["side"] != side or o["id"] in known or self._ours(o.get("ref")):
                continue
            if (o.get("quantity") or 0) > (leg.get("protect_qty") or leg.get("filled_qty") or 0):
                continue
            created = _parse_ts(o.get("created_at"))
            o["_timed"] = bool(created and since)
            if created and since and created < since:
                continue
            out.append(o)
        return out

    def _cancel_protection(self, leg: Dict) -> str:
        """Cancel the live protective order to a FINAL state. 'final' | 'wait'."""
        sym = leg["symbol"]
        if leg.get("protect_kind") == "oco":
            self._try(f"cancel oco {sym}", self.broker.cancel_oco, leg["sl_order"])
            st, err = self._try(f"oco status {sym}", self.broker.oco_status, leg["sl_order"])
            if err is not None:
                return "wait"
            if st["status"] == "TRIGGERED":
                # A leg fired: its child order must be final (or cancelled) first.
                leg.setdefault("_trig_t", self._mono())
                kids = self._oco_children(leg)
                if kids is None:
                    return "wait"
                live = [k for k in kids if not k["final"]]
                for k in live:
                    self._cancel(k["id"], f"oco child {sym}")
                if live:
                    return "wait"
                if not kids and self._mono() - leg["_trig_t"] < OCO_TRIGGER_WAIT_S:
                    leg["note"] = "OCO triggered — waiting for its order"
                    return "wait"
                leg.update(protect_status="TRIGGERED", protect_fired=True, protect_final=True)
                return "final"
            if not st["final"]:
                leg["note"] = "waiting for the OCO to reach a final state"
                return "wait"
            leg.update(protect_status=st["status"], protect_fired=bool(st["fired"]), protect_final=True)
            return "final"
        self._cancel(leg["sl_order"], f"stop {sym}")
        res, st = self._read(leg["sl_order"], None, sym, f"stop {sym}")
        if res != "ok" or not st["final"]:
            leg["note"] = "waiting for the stop order to reach a final state"
            return "wait"
        leg.update(protect_status=st["status"], protect_final=True)
        if st["filled_qty"]:
            leg["exit_fills"].append({"order": st["id"], "qty": st["filled_qty"],
                                      "price": st["avg_price"] or leg.get("protect_stop"), "source": "stop"})
        return "final"

    def _position_disagreement(self, leg: Dict, view: Dict) -> str:
        """Positions and today's fills disagree: lag, or a misread of Groww's
        position fields. Re-read for a while; if it persists → stuck, keep (or
        restore) protection, and stop real entries for the day."""
        sym = leg["symbol"]
        leg.setdefault("_dis_t", self._mono())
        leg["_dis_n"] = leg.get("_dis_n", 0) + 1
        leg["note"] = f"positions {view['net']} vs fills {view['orders_net']} — re-reading"
        if leg["_dis_n"] < DISAGREE_READS or self._mono() - leg["_dis_t"] < DISAGREE_S:
            return "wait"
        if leg.get("status") != "stuck":
            leg.update(status="stuck", stuck_reason="positions")
            self._save_leg(leg)
            self._alert(f"🚨 {sym}: Groww positions say {view['net']} but today's fills say "
                        f"{view['orders_net']} — NOT exiting blind. Re-protecting {self._held(leg)} and "
                        f"stopping real entries. Check the Groww app.")
            self.disable_for_today(f"positions untrusted on {sym} (positions {view['net']}, fills {view['orders_net']})")
        if self._held(leg) > 0 and not self._protection_active(leg) and not (leg.get("protect_kind") or "").endswith("_unknown"):
            stop = leg.get("stop")
            if not stop:
                ref_px = leg.get("entry_fill") or leg.get("paper_entry") or 0
                stop = ref_px * (1 - FALLBACK_STOP_PCT) if leg["direction"] == "long" else ref_px * (1 + FALLBACK_STOP_PCT)
            self._protect(leg, stop)
        self._save_leg(leg)
        return "done"

    def _exit_step(self, leg: Dict) -> str:
        """One step of the exit state machine: 'again' (progress, continue now),
        'wait' (try next tick) or 'done'."""
        if leg.get("status") not in ("exiting",) and not (leg.get("status") == "stuck"
                                                          and leg.get("stuck_reason") == "positions"):
            return "done"
        long = leg["direction"] == "long"
        side = SELL if long else BUY
        sym = leg["symbol"]

        self._resolve_protection(leg)
        if self._resolve_attempts(leg) == "wait":
            return "wait"

        # Trusted position read FIRST; protection stays until we have one.
        view = self._position_view(sym)
        if view is None:
            return "wait"
        net = view["net"]
        if net != view["orders_net"]:
            if leg.get("smoke") and view["orders_net"] is not None:
                net = view["orders_net"]               # our own 1-share test: trust our fills
            else:
                return self._position_disagreement(leg, view)
        for k in ("_dis_t", "_dis_n"):
            leg.pop(k, None)
        if leg.get("status") == "stuck":
            leg["status"] = "exiting"
            leg.pop("stuck_reason", None)
            self._alert(f"{sym}: positions and fills agree again — exiting", once_key=("agree", leg["paper_trade_id"]))
        owned = self._held(leg)
        same_side = net > 0 if long else net < 0
        cap = min(owned, abs(net)) if same_side else 0

        # Protection: cancel to a final state only now, with a good read in hand.
        if self._protection_active(leg):
            if self._cancel_protection(leg) == "wait":
                self._save_leg(leg)
                return "wait"
            self._save_leg(leg)
            return "again"                                # its fills change the picture: re-read

        if cap <= 0:
            return self._flat_for_leg(leg, owned)
        if self._past_deadline():
            if leg["status"] != "stuck":
                leg.update(status="stuck", stuck_reason="deadline")
                self._save_leg(leg)
                self._alert(f"🚨 {cap} {sym} STILL OPEN at 15:19 — Groww squares off MIS at 15:20 "
                            f"(₹50+GST). Check the Groww app NOW.")
            return "done"

        # Next attempt: re-priced limits, then MARKET.
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

    def _flat_for_leg(self, leg: Dict, owned: int) -> str:
        """The broker (positions AND fills) shows nothing left for this leg.
        Attribute unexplained shares (fired OCO), sweep every protective order
        of ours on the symbol, and only then close."""
        if owned > 0 and leg.get("protect_kind") == "oco" and leg.get("protect_fired"):
            kids = self._oco_children(leg)
            if kids is None:
                return "wait"
            done = [k for k in kids if k["final"] and k["filled_qty"]]
            if len(done) == 1 and done[0]["_timed"] and done[0]["filled_qty"] <= owned:
                k = done[0]
                leg["exit_fills"].append({"order": k["id"], "qty": k["filled_qty"], "price": k["avg_price"],
                                          "source": "oco"})
                owned -= k["filled_qty"]
            elif done:
                leg["oco_fill_ambiguous"] = True
        if (leg.get("protect_kind") or "").endswith("_unknown"):
            self._resolve_protection(leg)
            if (leg.get("protect_kind") or "").endswith("_unknown"):
                leg["note"] = "flat, but a protective order of unknown state may still be live — not closing"
                self._save_leg(leg)
                self._alert(f"{leg['symbol']}: position is flat but our protective order's state is unknown "
                            f"(ref {leg.get('protect_ref')}). Not closing until it is found and cancelled.",
                            once_key=("flat_unknown", leg["paper_trade_id"]))
                return "wait"
            if self._protection_active(leg):
                return "again"
        if not self._sweep_symbol(leg["symbol"], exclude_leg=leg["paper_trade_id"]):
            leg["note"] = "flat; cancelling a leftover protective order"
            self._save_leg(leg)
            return "wait"
        if owned > 0:
            leg["note"] = f"exit fill unknown for {owned} (closed outside our orders)"
            leg["exit_unknown_qty"] = owned
        self._settle(leg)
        leg["status"] = "closed"
        if not leg.get("smoke") and leg.get("real_pnl") is not None:
            self._realized_today += leg["real_pnl"]
        self._save_leg(leg)
        long = leg["direction"] == "long"
        pnl = leg.get("real_pnl")
        self._alert(f"Real {'SELL' if long else 'BUY'} {leg.get('filled_qty')} {leg['symbol']} closed @ "
                    f"{'₹%.2f' % leg['exit_fill'] if leg.get('exit_fill') else 'unknown price'} "
                    f"(paper ₹{(leg.get('paper_exit') or 0):,.2f}); real net "
                    f"{'₹%+.2f' % pnl if pnl is not None else 'unknown'}")
        return "done"

    def _protective_ids(self, exclude_leg=None) -> set:
        """Protective order ids/refs of legs that still hold shares."""
        out = set()
        for l in self.legs.values():
            if l["paper_trade_id"] == exclude_leg or l.get("status") not in ("open", "exiting", "stuck"):
                continue
            out |= {l.get("sl_order"), l.get("protect_ref")}
        return out - {None}

    def _sweep_symbol(self, sym: str, exclude_leg=None) -> bool:
        """Cancel to a final state every live SL order / ACTIVE OCO on `sym`
        that carries today's reference prefix and isn't protecting another
        open leg. True when nothing of ours is left live. Never touches orders
        without our prefix (the user's own)."""
        keep = self._protective_ids(exclude_leg)
        clean = True
        rows, err = self._try(f"orders {sym}", self.broker.order_list, sym)
        if err is not None:
            return False
        for o in rows or []:
            if (not self._ours(o.get("ref")) or o["id"] in keep or o.get("ref") in keep
                    or o.get("order_type") not in (SL, SL_M) or o["final"]):
                continue
            self._cancel(o["id"], f"orphan stop {sym}")
            res, st = self._read(o["id"], None, sym, f"orphan stop {sym}")
            if res != "ok" or not st["final"]:
                clean = False
            else:
                self._alert(f"Cancelled a leftover protective stop on {sym} ({o['id']})")
        if not self._oco_supported():
            return clean
        ocos, err = self._try(f"ocos {sym}", self.broker.smart_orders, sym)
        if err is not None:
            return False
        for s in ocos or []:
            if not self._ours(s.get("ref")) or s["id"] in keep or s.get("ref") in keep:
                continue
            if s["status"] != "ACTIVE":             # TRIGGERED/final ones can't fire again
                continue
            self._try(f"cancel orphan oco {sym}", self.broker.cancel_oco, s["id"])
            st, err = self._try(f"orphan oco status {sym}", self.broker.oco_status, s["id"])
            if err is not None or not st["final"]:
                clean = False
            else:
                self._alert(f"Cancelled a leftover OCO on {sym} ({s['id']})")
        return clean

    def _oco_supported(self) -> bool:
        try:
            return bool(getattr(self.broker, "supports_oco", lambda: True)())
        except Exception:
            return True

    def _settle(self, leg: Dict):
        """P&L and per-executed-order charges from the recorded fills."""
        long = leg["direction"] == "long"
        fills = [f for f in leg.get("exit_fills") or [] if f.get("qty") and f.get("price")]
        qty = sum(f["qty"] for f in fills)
        entry = leg.get("entry_fill")
        if not qty or not entry or leg.get("exit_unknown_qty") or leg.get("oco_fill_ambiguous"):
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
        return any(l.get("status") in ACTIVE_STATES or
                   (l.get("status") == "stuck" and l.get("stuck_reason") == "positions")
                   for l in self.legs.values())

    def tick(self):
        self._roll_day()
        if self.is_backtest():
            return
        if self.standby:
            if not self.acquire_lock():
                return
            self.standby = False
            self._load_ledger()                         # another process may have changed it
        if liquidation_requested(self.session_dir, self._today()):
            self.liquidate("liquidate requested")
            if not self.busy():
                clear_liquidation_request(self.session_dir)
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
                    self._resolve_protection(leg)
                    paper_closed = paper_open is not None and not leg.get("smoke") and leg["paper_trade_id"] not in paper_open
                    if paper_closed or (leg.get("smoke") and not self._smoke_running):
                        leg["status"] = "exiting"            # paper closed while we weren't looking
                        self._save_leg(leg)
                        self._advance_exit(leg)
                    elif (not leg.get("sl_order") and leg.get("stop") and not leg.get("protect_n")
                          and not leg.get("smoke")):
                        self._protect(leg)
                elif st == "exiting" or (st == "stuck" and leg.get("stuck_reason") == "positions"
                                         and not self._past_deadline()):
                    self._advance_exit(leg)
            except Exception as e:                           # one bad leg must not stop the others
                logger.exception(f"Live leg {leg.get('paper_trade_id')} step failed: {e}")

    def liquidate(self, reason: str = "liquidate") -> int:
        """Flatten everything this mirror owns; no new entries today."""
        self._roll_day()
        if self._standby_drop("liquidate"):
            return 0
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
            elif st in ("exiting", "stuck"):
                n += 1
        return n

    def prepare_shutdown(self, liquidate: bool = False, budget_s: float = 50.0):
        """Before the process exits. Liquidate requested: flatten (bounded).
        Plain stop/restart: let in-flight orders settle, then make sure every
        leg still holding shares has a live protective order."""
        if self.standby:
            return
        deadline = self._mono() + budget_s
        if liquidate:
            self.liquidate("liquidate requested (agent stopping)")
        while self.busy() and self._mono() < deadline:
            self._sleep(POLL_S)
            self._advance_all()
        if liquidate and not self.busy():
            clear_liquidation_request(self.session_dir)
        for leg in list(self.legs.values()):
            if leg.get("status") not in ("open", "exiting", "stuck") or self._held(leg) <= 0:
                continue
            for a in list(leg.get("exit_attempts") or []):        # no resting exit next to a fresh stop
                if not a.get("final") and a.get("order"):
                    a["cancel"] = True
            self._resolve_attempts(leg)
            if self._held(leg) <= 0 or self._protection_active(leg):
                continue
            if (leg.get("protect_kind") or "").endswith("_unknown"):
                continue
            if any(not a.get("final") for a in leg.get("exit_attempts") or []):
                self._alert(f"{leg['symbol']}: an exit order is still unresolved at shutdown — not re-protecting "
                            f"to avoid a double exit. Check the Groww app.")
                continue
            if leg.get("status") == "exiting":
                leg["status"] = "open"           # the restarted agent re-exits it (paper is closed)
            stop = leg.get("stop")
            if not stop:
                px = leg.get("entry_fill") or 0
                stop = px * (1 - FALLBACK_STOP_PCT) if leg["direction"] == "long" else px * (1 + FALLBACK_STOP_PCT)
            self._protect(leg, stop)
            self._save_leg(leg)
        self._alert(f"Agent stopping. Real positions: {self.open_legs_summary()}. "
                    f"Protective orders stay at Groww; MIS squares off at 15:20.")

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

    def _wait_view(self, sym: str, want: int, timeout_s: float = 10) -> Optional[Dict]:
        """Position view once both sources show `want` (positions can lag a fill)."""
        deadline = self._mono() + timeout_s
        view = self._position_view(sym)
        while (view is None or view["net"] != want or view["orders_net"] != want) and self._mono() < deadline:
            self._sleep(POLL_S)
            view = self._position_view(sym)
        return view

    def smoke_test(self, ticker: str = None) -> Dict:
        """1 share through every API shape the mirror relies on:
        BUY (limit) → lookup by reference → fill price sanity → positions and
        fills both +1 → OCO ±2.5% → modify both triggers → cancel to final →
        SELL 1 MARKET (limit fallback) → positions and fills both 0.
        Each step writes a ledger row first. Core failures stop real entries
        today (the share is still sold); an OCO-only failure means plain stops."""
        self._roll_day()
        if self._standby_drop("smoke test"):
            return {"passed": False, "reason": "standby"}
        ticker = ticker or os.environ.get(SMOKE_SYMBOL_ENV, DEFAULT_SMOKE_SYMBOL)
        if self.is_backtest():
            return {"passed": False, "reason": "backtest"}
        self._smoke_running = True
        try:
            return self._run_smoke(ticker)
        finally:
            self._smoke_running = False

    def _run_smoke(self, ticker: str) -> Dict:
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
            return self._smoke_result(False, [f"no live price / bad symbol for {ticker}"], [])
        core: List[str] = []
        oco_fail: List[str] = []
        tid = f"S{attempt}"
        leg = {"paper_trade_id": f"smoke{attempt}-{self._day}", "date": self._day, "ticker": ticker,
               "symbol": sym, "direction": "long", "paper_qty": 1, "paper_entry": price,
               "stop": None, "target": None, "filled_qty": 0, "entry_fill": None, "entry_order": None,
               "sl_order": None, "protect_kind": None, "protect_final": False,
               "exit_attempts": [], "exit_fills": [], "status": "pending", "smoke": True}
        bref = self._ref(tid, "B")
        r = self._start_entry(leg, 1, price, kind="smoke_buy", ref=bref)
        if leg["status"] == "rejected":
            return self._smoke_result(False, [f"BUY rejected [{r['category']}]: {r['error']}"], [])
        self._wait_entry(leg, ENTRY_TIMEOUT_S)
        if leg["status"] != "open":
            leg["exit_requested"] = True                 # a late fill still gets sold by the tick
            self._save_leg(leg)
            return self._smoke_result(False, [f"BUY {leg['status']}: {leg.get('note', '')}"], [])

        # Response shapes the read-only check can't see.
        self._write({"event": "smoke_step", "step": "lookup_by_reference", "ref": bref})
        st, err = self._try("smoke lookup", self.broker.order_by_reference, bref)
        if err is not None or not st or not st.get("id"):
            core.append(f"lookup by reference returned no groww_order_id ({error_text(err) if err else st})")
        elif leg.get("entry_order") and st["id"] != leg["entry_order"]:
            core.append(f"lookup by reference returned {st['id']}, expected {leg['entry_order']}")
        fill = leg.get("entry_fill") or 0
        if not fill or abs(fill / price - 1) > 0.05:
            core.append(f"average_fill_price {fill} vs LTP {price} (units?)")
        self._write({"event": "smoke_step", "step": "positions_after_buy"})
        view = self._wait_view(sym, 1)
        if view is None or view["net"] != 1 or view["orders_net"] != 1:
            core.append(f"positions after BUY: positions {view and view['net']}, fills {view and view['orders_net']} "
                        f"(expected +1) — position semantics untrusted")

        # OCO: create near the price, modify both triggers, cancel to a final state.
        oref = self._ref(tid, "O")
        tick = self._tick_size(ticker, price)
        self._write({"event": "smoke_step", "step": "oco_create", "ref": oref})
        sid, err = self._try("smoke oco", self.broker.create_oco, sym, 1, SELL, 1,
                             round_to_tick(price * (1 + SMOKE_OCO_PCT), tick, True),
                             round_to_tick(price * (1 - SMOKE_OCO_PCT), tick, False), oref)
        if err is not None and is_ambiguous(err):
            sid, _ = self._try("smoke find oco", self.broker.find_oco, sym, oref)
        if not sid:
            oco_fail.append(f"create returned no smart_order_id ({error_text(err) if err else 'none'})")
            if err is not None and is_ambiguous(err):
                leg.update(protect_kind="oco_unknown", protect_ref=oref)
        else:
            leg.update(sl_order=sid, protect_kind="oco", protect_ref=oref, protect_final=False,
                       protect_created_at=self._now().isoformat(), protect_qty=1)
            st0, e0 = self._try("smoke oco status", self.broker.oco_status, sid)
            if e0 is not None or st0["status"] not in KNOWN_OCO_STATUSES:
                oco_fail.append(f"unexpected OCO status field: {st0 and st0.get('raw')}")
            self._write({"event": "smoke_step", "step": "oco_modify", "id": sid})
            _, merr = self._try("smoke modify oco", self.broker.modify_oco, sid, 1,
                                round_to_tick(price * (1 + SMOKE_OCO_MODIFY_PCT), tick, True),
                                round_to_tick(price * (1 - SMOKE_OCO_MODIFY_PCT), tick, False))
            if merr is not None:
                oco_fail.append(f"modify failed: {error_text(merr)}")
            self._write({"event": "smoke_step", "step": "oco_cancel", "id": sid})
            final = None
            for _ in range(6):
                self._try("smoke cancel oco", self.broker.cancel_oco, sid)
                stx, _e = self._try("smoke oco status", self.broker.oco_status, sid)
                if stx and stx["final"]:
                    final = stx
                    break
                self._sleep(POLL_S)
            if final is None:
                oco_fail.append("OCO did not reach a final state after cancel")
            else:
                leg.update(protect_final=True, protect_status=final["status"], protect_fired=final["fired"])
                if final["fired"]:
                    oco_fail.append("OCO fired during the test")
        self.oco_ok = not oco_fail
        self._save_leg(leg)

        # SELL 1: MARKET first (tests MARKET acceptance), then the normal exit engine.
        leg["status"] = "exiting"
        self._save_leg(leg)
        if not self._protection_active(leg) and not (leg.get("protect_kind") or "").endswith("_unknown"):
            mref = self._ref(tid, "M")
            r = self._place(leg, "smoke_sell", mref, SELL, 1, MARKET)
            if r["id"] or r["ambiguous"]:
                leg["exit_attempts"].append({"n": 0, "ref": mref, "kind": "market", "qty": 1, "price": None,
                                             "final": False, "_t": self._mono(), "order": r["id"]})
            else:
                self.market_ok = False
                leg.setdefault("failures", []).append(
                    {"what": "smoke MARKET sell", "category": r["category"], "message": r["error"]})
            self._save_leg(leg)
        self._advance_exit(leg)
        self._wait_exit(leg, 60)
        if not any(f.get("source") == "market" for f in leg["exit_fills"]):
            self.market_ok = False
        if leg["status"] != "closed":
            core.append(f"could not close the 1-share test position ({leg['status']})")
        else:
            view = self._wait_view(sym, 0)
            if view is None or view["net"] != 0 or view["orders_net"] != 0:
                core.append(f"positions after SELL: positions {view and view['net']}, fills "
                            f"{view and view['orders_net']} (expected 0)")
        return self._smoke_result(not core, core, oco_fail)

    def _smoke_result(self, passed: bool, core: List[str], oco_fail: List[str]) -> Dict:
        self._smoke = (self._day, passed)
        reason = "; ".join(core) if core else (
            f"ok (MARKET {'ok' if self.market_ok else 'not accepted — limit exits only'}; "
            f"OCO {'ok' if not oco_fail else 'disabled — plain stops: ' + '; '.join(oco_fail)})")
        self._write({"event": "smoke", "passed": passed, "reason": reason, "core_failures": core,
                     "oco_failures": oco_fail, "market_ok": self.market_ok, "oco_ok": self.oco_ok})
        if passed:
            self._alert(f"Smoke test passed: {reason}. Real orders are ON for today.")
        else:
            self.disable_for_today(f"smoke test failed: {reason}")
        return {"passed": passed, "reason": reason, "market_ok": self.market_ok, "oco_ok": self.oco_ok,
                "core_failures": core, "oco_failures": oco_fail}

    # ── Reconciliation ─────────────────────────────────────

    def reconcile(self, force: bool = False):
        """Resolve pending legs, compare Groww's net MIS positions with what
        the ledger owns, and sweep orphan protective orders (throttled)."""
        self._roll_day()
        if self.is_backtest() or self._standby_drop("reconcile"):
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
            actual = None
        except Exception:
            actual = None
        expected: Dict[str, int] = {}
        for l in self.legs.values():
            if l.get("status") in ("open", "exiting", "stuck"):
                q = self._held(l)
                expected[l["symbol"]] = expected.get(l["symbol"], 0) + (q if l["direction"] == "long" else -q)
        for sym in traded:
            busy = any(l.get("symbol") == sym and l.get("status") in ACTIVE_STATES for l in self.legs.values())
            if actual is not None and not busy:
                want, got = expected.get(sym, 0), actual.get(sym, 0)
                if want != got and (sym, got) not in self._mismatch_alerted:
                    self._mismatch_alerted.add((sym, got))
                    self._write({"event": "mismatch", "symbol": sym, "expected": want, "actual": got})
                    self._alert(f"Position mismatch {sym}: Groww {got:+d}, ledger expects {want:+d}")
            owns = any(l.get("symbol") == sym and l.get("status") in EXPOSURE_STATES for l in self.legs.values())
            if not owns:
                self._sweep_symbol(sym)                    # orphan protective orders of ours

    # ── Status / end-of-day report ─────────────────────────

    def open_legs_summary(self) -> str:
        legs = [l for l in list(self.legs.values()) if l.get("status") in EXPOSURE_STATES]
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
                if row.get("date") == self._day and row.get("event") == "error":
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
        legs = [l for l in list(self.legs.values()) if not l.get("smoke")]
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
            flag = " [OCO fill ambiguous]" if l.get("oco_fill_ambiguous") else ""
            lines.append(
                f"{head} x{q}: in {fmt(pe, '₹{:,.2f}')}→{fmt(rf, '₹{:,.2f}')} ({fmt(s_in, '{:+.1f}bps')}), "
                f"out {fmt(px, '₹{:,.2f}')}→{fmt(xf, '₹{:,.2f}')} ({fmt(s_out, '{:+.1f}bps')}), "
                f"paper {fmt(paper_pnl, '₹{:+,.2f}')} vs real net {fmt(l.get('real_pnl'), '₹{:+,.2f}')} "
                f"(charges {fmt(l.get('charges'), '₹{:,.2f}')}){flag}")
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
                lines.append(f"  [{f.get('category')}] {f.get('where') or ''}: {f.get('message') or ''}")
        return "\n".join(lines)

    def send_eod_report(self) -> str:
        text = self.eod_report()
        self._write({"event": "eod_report", "text": text})
        self._alert(text)
        return text


# ── Worker thread (actor) ─────────────────────────────────

class LiveWorker:
    """Single thread that owns every broker call. Started only by the agent's
    run_loop; before start() (one-shot / status paths) events are dropped.
    synchronous=True runs events inline (tests)."""

    BUSY_TICK_S = 2.0
    IDLE_TICK_S = 15.0

    def __init__(self, mirror: LiveMirror, synchronous: bool = False):
        self.mirror = mirror
        self.synchronous = synchronous
        self._q: "queue.Queue" = queue.Queue()
        self._stop = threading.Event()
        self._cv = threading.Condition()
        self._pending = 0                    # queued + running events
        self._thread = None
        self.started = False

    def start(self):
        if self.synchronous or self._thread is not None:
            return
        if not self.mirror.acquire_lock():
            self.mirror.standby = True
            self.mirror._alert("Another process holds the live lock — real orders on standby until it's free")
        self._thread = threading.Thread(target=self._loop, name="live-mirror", daemon=True)
        self.started = True
        self._thread.start()

    def submit(self, method: str, *args):
        if self.synchronous:
            self._run(method, args)
            return
        if not self.started or self._stop.is_set():
            logger.warning(f"Live mirror not running in this process — {method} dropped (paper only)")
            return
        with self._cv:
            self._pending += 1
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
                ev = self._q.get(timeout=max(0.05, wait - (time.monotonic() - last_tick)))
            except queue.Empty:
                ev = None
            if ev is not None:
                try:
                    self._run(*ev)
                finally:
                    with self._cv:
                        self._pending -= 1
                        self._cv.notify_all()
            if self._stop.is_set():
                break
            if ev is None or time.monotonic() - last_tick >= self.BUSY_TICK_S:
                self._run("tick", ())
                last_tick = time.monotonic()

    def drain(self, timeout: float) -> bool:
        """Wait until every queued event has run."""
        if self.synchronous or self._thread is None:
            return True
        with self._cv:
            return self._cv.wait_for(lambda: self._pending == 0, timeout)

    def shutdown(self, liquidate: bool = False, timeout: float = 60.0) -> bool:
        """SIGTERM path: finish queued events, then prepare_shutdown (flatten if
        a liquidation was requested, else keep every position protected)."""
        if self.synchronous:
            self._run("prepare_shutdown", (liquidate,))
            return True
        if self._thread is None:
            return True
        with self._cv:
            self._pending += 1
        self._q.put(("prepare_shutdown", (liquidate, max(5.0, timeout - 10))))
        done = self.drain(timeout)
        self.stop()
        return done

    def stop(self, join_s: float = 10.0):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=join_s)
            if self._thread.is_alive():
                logger.error("Live worker still mid-call — keeping the live lock until the process exits")
                return
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


def liquidate_from_dashboard(session, *, mirror: LiveMirror = None, wait_lock_s: float = 90.0,
                             background: bool = True, sleep=time.sleep) -> Optional[Dict]:
    """Kill switch for the real side. The dashboard has already written the
    dated request and SIGTERMed the agent, whose shutdown flattens (≤60s) and
    releases the lock. Here: wait for the lock, then flatten whatever is left,
    retrying until flat or the market closes."""
    if mirror is None and not ledger_has_exposure(session.session_dir):
        return None
    mirror = mirror or build_mirror(session, exit_only=True)

    def run():
        waited = 0.0
        while not mirror.acquire_lock():
            if waited >= wait_lock_s:
                mirror._alert("Liquidate: couldn't get the live lock — the agent should flatten on its next "
                              "tick (request file left in place). Check the Groww app.")
                return {"live": "requested_from_agent"}
            sleep(1.0)
            waited += 1.0
        try:
            mirror._load_ledger()
            n = mirror.liquidate("manual liquidation from the dashboard")
            while mirror.busy() and mirror._in_market() and not mirror._past_deadline():
                sleep(LiveWorker.BUSY_TICK_S)
                mirror._advance_all()
            if not mirror.busy():
                clear_liquidation_request(mirror.session_dir)
            else:
                mirror._alert(f"Liquidation incomplete: {mirror.open_legs_summary()}")
            return {"live": f"flattened {n} legs", "open": mirror.open_legs_summary()}
        finally:
            mirror.release_lock()
    if background:
        threading.Thread(target=run, name="live-liquidate", daemon=True).start()
        return {"live": "flattening in the background"}
    return run()
