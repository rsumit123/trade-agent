"""
Real-money mirror of a paper session on Groww.

Every paper event (entry, exit from any path, stop/target change) is replayed
as a real NSE intraday (MIS) order at the same quantity, so paper and real can
be compared trade by trade. Paper always books first and stays the record the
agent and learner see; nothing here ever raises into the agent loop.

Plan: docs/plans/2026-10-15-groww-live-mirror.md
"""

import json
import logging
import math
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional
from zoneinfo import ZoneInfo

from .groww_broker import BUY, SELL, LIMIT, MARKET, SL, GrowwAuthError, is_auth_error
from .portfolio import Portfolio, Trade

logger = logging.getLogger(__name__)

IST = ZoneInfo("Asia/Kolkata")

# Only these sessions can ever place real orders, and only if also listed in
# the GROWW_LIVE_SESSIONS env var and configured with live_broker: groww.
LIVE_SESSION_ALLOWLIST = {"nse_opus_live"}
KILL_SWITCH_FILE = "LIVE_KILL"
LEDGER_FILE = "live_orders.jsonl"

MAX_EXPOSURE = 50_000.0          # ₹, open real positions at entry value
MAX_ORDER_VALUE = 50_000.0       # ₹, one entry
DAILY_LOSS_CAP = 1_500.0         # ₹ realised real loss → no new real entries today
MAX_ORDERS_PER_DAY = 40

ENTRY_BUFFER = 0.003             # limit = paper price ± 0.3%
ENTRY_TIMEOUT_S = 30             # then cancel → missed / partial
POLL_S = 2
SL_LIMIT_PAD = 0.01              # SL-limit price = trigger ∓ 1%
EXIT_BUFFER = 0.005              # exit limit = LTP ∓ 0.5% × try
EXIT_TRIES = 3
EXIT_REPRICE_S = 10
MARKET_EXIT_WAIT_S = 10
RECONCILE_EVERY_S = 60
DEFAULT_TICK = 0.05
SMOKE_SYMBOL_ENV = "GROWW_SMOKE_SYMBOL"
DEFAULT_SMOKE_SYMBOL = "ITC.NS"


def live_enabled_for(session) -> bool:
    """All three gates: hard allowlist, env allowlist, session config."""
    if session is None or getattr(session, "backtest_mode", False):
        return False
    sid = getattr(session, "session_id", "")
    env = {s.strip() for s in os.environ.get("GROWW_LIVE_SESSIONS", "").split(",") if s.strip()}
    return (sid in LIVE_SESSION_ALLOWLIST and sid in env
            and (getattr(session, "live_broker", None) or "").lower() == "groww")


# ── Charges (Groww help page schedule, modelled — not from the API) ─────

def order_charges(side: str, value: float) -> float:
    """Charges for one executed NSE intraday order of `value` rupees."""
    value = abs(value)
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


def round_to_tick(price: float, tick: float, up: bool) -> float:
    tick = tick if tick and tick > 0 else DEFAULT_TICK
    n = price / tick
    n = math.ceil(n - 1e-9) if up else math.floor(n + 1e-9)
    return round(n * tick, 2)


def _symbol(ticker: str) -> Optional[str]:
    """'RELIANCE.NS' → 'RELIANCE'; None for anything that isn't an NSE equity."""
    if not ticker or not ticker.endswith(".NS"):
        return None
    sym = ticker[:-3]
    return sym if sym and all(c.isalnum() or c in "-&" for c in sym) else None


class LiveMirror:
    def __init__(self, broker, session_id: str, session_dir, market_data=None, *,
                 is_backtest: Callable[[], bool] = lambda: False,
                 alert: Callable[[str], object] = None,
                 now: Callable[[], datetime] = None,
                 sleep: Callable[[float], None] = time.sleep,
                 monotonic: Callable[[], float] = time.monotonic,
                 forced_close_time: Callable[[datetime], Optional[datetime]] = None):
        self.broker = broker
        self.session_id = session_id
        self.session_dir = Path(session_dir)
        self.market_data = market_data
        self.is_backtest = is_backtest
        if alert is None:
            from .health import send_telegram as alert
        self._alert_fn = alert
        self._now = now or (lambda: datetime.now(IST))
        self._sleep = sleep
        self._mono = monotonic
        self._forced_close_time = forced_close_time
        self.ledger_path = self.session_dir / LEDGER_FILE
        self.legs: Dict[str, Dict] = {}          # str(paper_trade_id) → leg
        self._day = None
        self._orders_today = 0
        self._realized_today = 0.0
        self._disabled: Optional[tuple] = None   # (IST date, reason)
        self._smoke: Optional[tuple] = None      # (IST date, passed)
        self.market_ok = True                    # MARKET orders accepted (smoke test)
        self._last_reconcile = None
        self._mismatch_alerted = set()
        self._flattened = set()
        self._load_ledger()

    # ── Day state / ledger ─────────────────────────────────

    def _today(self) -> str:
        return self._now().astimezone(IST).date().isoformat()

    def _roll_day(self):
        today = self._today()
        if self._day != today:
            self._day = today
            self._orders_today = 0
            self._realized_today = 0.0
            self._mismatch_alerted = set()
            self._flattened = set()
            self.legs = {k: v for k, v in self.legs.items() if v.get("date") == today}

    def _write(self, row: Dict):
        row = {"ts": self._now().isoformat(), "date": self._today(), **row}
        try:
            self.session_dir.mkdir(parents=True, exist_ok=True)
            with open(self.ledger_path, "a") as f:
                f.write(json.dumps(row, default=str) + "\n")
        except OSError as e:
            logger.warning(f"Live ledger write failed: {e}")

    def _save_leg(self, leg: Dict):
        self.legs[leg["paper_trade_id"]] = leg
        self._write({"event": "leg", "leg": dict(leg)})

    def _load_ledger(self):
        """Rebuild today's legs and counters so a mid-day restart still knows
        which real positions are open."""
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
                if row.get("market_ok") is False:
                    self.market_ok = False
        self._realized_today = sum(
            (l.get("real_pnl") or 0.0) for l in self.legs.values()
            if l.get("status") == "closed" and not l.get("smoke"))

    def _alert(self, text: str):
        try:
            self._alert_fn(f"[{self.session_id} LIVE] {text}")
        except Exception as e:
            logger.warning(f"Live alert failed: {e}")

    def disable_for_today(self, reason: str):
        self._roll_day()
        if self._disabled and self._disabled[0] == self._day:
            return
        self._disabled = (self._day, reason)
        self._write({"event": "disabled", "reason": reason})
        logger.error(f"🛑 Live mirror disabled for today: {reason}")
        self._alert(f"Real orders DISABLED for today: {reason}. Paper trading continues.")

    def disabled_reason(self) -> Optional[str]:
        self._roll_day()
        return self._disabled[1] if self._disabled and self._disabled[0] == self._day else None

    def _handle_error(self, where: str, exc: Exception):
        logger.error(f"Live {where} failed: {exc}")
        if isinstance(exc, GrowwAuthError) or is_auth_error(exc):
            self.disable_for_today(f"Groww auth/IP failure during {where}: {exc}")

    # ── Order helpers ──────────────────────────────────────

    def _tick(self, ticker: str) -> float:
        try:
            t = self.market_data.tick_size(ticker) if self.market_data is not None else None
            return float(t) if t and float(t) > 0 else DEFAULT_TICK
        except Exception:
            return DEFAULT_TICK

    def _ltp(self, ticker: str, fallback: float) -> float:
        try:
            p = self.market_data.get_current_prices([ticker]).get(ticker) if self.market_data else None
            return float(p) if p else fallback
        except Exception:
            return fallback

    def _place(self, leg: Dict, kind: str, symbol: str, side: str, qty: int,
               order_type: str, price: float = None, trigger: float = None) -> Optional[str]:
        self._orders_today += 1
        row = {"event": "order", "paper_trade_id": leg["paper_trade_id"], "leg": kind,
               "symbol": symbol, "side": side, "order_type": order_type, "req_qty": qty,
               "req_price": price, "trigger": trigger}
        try:
            oid = self.broker.place_order(symbol, side, qty, order_type, price=price,
                                          trigger_price=trigger)
        except Exception as e:
            self._write({**row, "status": "error", "error": str(e)})
            self._handle_error(f"{kind} order {side} {qty} {symbol}", e)
            return None
        self._write({**row, "groww_order_id": oid, "status": "placed"})
        return oid

    def _status(self, oid: str) -> Optional[Dict]:
        try:
            return self.broker.order_status(oid)
        except Exception as e:
            self._handle_error(f"status {oid}", e)
            return None

    def _wait(self, oid: str, timeout_s: float) -> Optional[Dict]:
        """Poll until filled/dead or timeout; returns the last status seen."""
        deadline = self._mono() + timeout_s
        st = self._status(oid)
        while st is not None and not (st["filled"] or st["dead"]) and self._mono() < deadline:
            self._sleep(POLL_S)
            st = self._status(oid)
        return st

    def _cancel_and_read(self, oid: str) -> Optional[Dict]:
        try:
            self.broker.cancel_order(oid)
        except Exception as e:
            # Usually "already executed/cancelled"; the status read below decides.
            logger.info(f"Cancel {oid}: {e}")
            if isinstance(e, GrowwAuthError) or is_auth_error(e):
                self._handle_error(f"cancel {oid}", e)
        st = self._status(oid)
        for _ in range(3):
            if st is None or st["filled"] or st["dead"]:
                break
            self._sleep(POLL_S)          # CANCELLATION_REQUESTED → settle
            st = self._status(oid)
        return st

    # ── Guards ─────────────────────────────────────────────

    def _open_exposure(self) -> float:
        return sum((l.get("filled_qty") or 0) * (l.get("entry_fill") or 0)
                   for l in self.legs.values() if l.get("status") == "open")

    def _entry_block(self, ticker: str, qty: int, price: float) -> Optional[str]:
        if self.is_backtest():
            return "backtest"
        if self.disabled_reason():
            return f"disabled: {self.disabled_reason()}"
        if (self.session_dir / KILL_SWITCH_FILE).exists():
            return "kill switch"
        if not (self._smoke and self._smoke[0] == self._day and self._smoke[1]):
            return "smoke test not passed today"
        if _symbol(ticker) is None:
            return f"not an NSE equity: {ticker}"
        value = qty * price
        if value > MAX_ORDER_VALUE:
            return f"order value ₹{value:,.0f} > ₹{MAX_ORDER_VALUE:,.0f}"
        if self._open_exposure() + value > MAX_EXPOSURE:
            return f"exposure ₹{self._open_exposure() + value:,.0f} > ₹{MAX_EXPOSURE:,.0f}"
        if self._realized_today <= -DAILY_LOSS_CAP:
            return f"daily real loss ₹{self._realized_today:,.0f} hit the cap"
        if self._orders_today + 2 > MAX_ORDERS_PER_DAY:      # entry + its stop
            return f"order cap {MAX_ORDERS_PER_DAY}/day reached"
        return None

    # ── Paper events ───────────────────────────────────────

    def on_entry(self, trade: Trade):
        self._roll_day()
        side_dir = (trade.direction or "long").lower()
        leg = {
            "paper_trade_id": str(trade.id), "date": self._day, "ticker": trade.ticker,
            "symbol": _symbol(trade.ticker), "direction": side_dir,
            "paper_qty": trade.quantity, "paper_entry": trade.entry_price,
            "stop": trade.stop_price, "filled_qty": 0, "entry_fill": None,
            "sl_order": None, "status": "pending", "entry_order": None,
        }
        block = self._entry_block(trade.ticker, trade.quantity, trade.entry_price)
        if block:
            leg.update(status="skipped", note=block)
            self._save_leg(leg)
            logger.warning(f"🚫 Live entry skipped for {trade.ticker} (paper #{trade.id}): {block}")
            if block not in ("backtest",):
                self._alert(f"Skipped real {side_dir.upper()} {trade.quantity} {trade.ticker}: {block}")
            return
        self._enter(leg, trade.quantity, trade.entry_price)

    def _enter(self, leg: Dict, qty: int, price: float, with_stop: bool = True):
        long = leg["direction"] == "long"
        side = BUY if long else SELL
        tick = self._tick(leg["ticker"])
        limit = round_to_tick(price * (1 + ENTRY_BUFFER) if long else price * (1 - ENTRY_BUFFER),
                              tick, up=long)
        oid = self._place(leg, "entry", leg["symbol"], side, qty, LIMIT, price=limit)
        leg["entry_order"] = oid
        leg["entry_limit"] = limit
        if not oid:
            leg.update(status="rejected", note="entry order not accepted")
            self._save_leg(leg)
            self._alert(f"Real {side} {qty} {leg['symbol']} NOT placed (see log)")
            return
        st = self._wait(oid, ENTRY_TIMEOUT_S)
        if st is not None and not (st["filled"] or st["dead"]):
            st = self._cancel_and_read(oid)
        filled = (st or {}).get("filled_qty") or 0
        if st and st["filled"] and not filled:
            filled = qty
        leg["filled_qty"] = int(filled)
        leg["entry_fill"] = (st or {}).get("avg_price") or (limit if filled else None)
        leg["entry_status"] = (st or {}).get("status")
        if filled <= 0:
            dead = st and st["dead"] and st["status"] != "CANCELLED"
            leg.update(status="rejected" if dead else "missed",
                       note=(st or {}).get("remark") or "no fill within timeout")
            self._save_leg(leg)
            self._alert(f"Real {side} {qty} {leg['symbol']} {leg['status'].upper()} "
                        f"(limit ₹{limit:,.2f}): {leg['note']}")
            return
        leg["status"] = "open"
        self._save_leg(leg)
        part = "" if filled == qty else f" (PARTIAL {filled}/{qty})"
        self._alert(f"Real {side} {filled} {leg['symbol']} filled @ ₹{leg['entry_fill']:,.2f}{part}; "
                    f"paper @ ₹{leg['paper_entry']:,.2f}")
        if with_stop and leg.get("stop"):
            self._place_stop(leg)

    def _stop_prices(self, leg: Dict, stop: float):
        long = leg["direction"] == "long"
        tick = self._tick(leg["ticker"])
        trigger = round_to_tick(stop, tick, up=not long)
        limit = round_to_tick(stop * (1 - SL_LIMIT_PAD) if long else stop * (1 + SL_LIMIT_PAD),
                              tick, up=not long)
        return trigger, limit

    def _place_stop(self, leg: Dict):
        long = leg["direction"] == "long"
        trigger, limit = self._stop_prices(leg, leg["stop"])
        oid = self._place(leg, "stop", leg["symbol"], SELL if long else BUY,
                          leg["filled_qty"], SL, price=limit, trigger=trigger)
        leg["sl_order"] = oid
        self._save_leg(leg)
        if not oid:
            self._alert(f"Protective stop for {leg['symbol']} NOT placed — position relies on the agent's monitor")

    def on_levels(self, trade: Trade):
        self._roll_day()
        leg = self.legs.get(str(trade.id))
        if not leg or leg.get("status") != "open" or trade.stop_price is None:
            return
        leg["stop"] = trade.stop_price
        if not leg.get("sl_order"):
            self._place_stop(leg)
            return
        trigger, limit = self._stop_prices(leg, trade.stop_price)
        try:
            self.broker.modify_order(leg["sl_order"], SL, leg["filled_qty"], price=limit,
                                     trigger_price=trigger)
            self._write({"event": "modify", "paper_trade_id": leg["paper_trade_id"],
                         "groww_order_id": leg["sl_order"], "trigger": trigger, "req_price": limit})
            self._save_leg(leg)
        except Exception as e:
            self._handle_error(f"modify stop {leg['symbol']}", e)
            self._alert(f"Could not move the real stop for {leg['symbol']} to ₹{trigger:,.2f}: {e}")

    def on_exit(self, trade: Trade):
        self._roll_day()
        leg = self.legs.get(str(trade.id))
        if not leg or leg.get("status") != "open":
            return
        leg["paper_exit"] = trade.exit_price
        leg["exit_type"] = trade.exit_type
        self._exit(leg, trade.exit_price)

    def _exit(self, leg: Dict, ref_price: float):
        long = leg["direction"] == "long"
        side = SELL if long else BUY
        held = int(leg["filled_qty"])
        fills: List[tuple] = []                       # (qty, price)

        # 1. The broker stop may already have fired: cancel it and read its fills
        #    before sending anything, or we'd exit twice and flip the position.
        if leg.get("sl_order"):
            st = self._cancel_and_read(leg["sl_order"])
            if st is None:
                # Can't tell whether the stop filled: don't risk a double exit.
                leg["note"] = "stop order state unknown at exit"
                self._save_leg(leg)
                self._alert(f"Exit of {leg['symbol']} paused: couldn't read stop order "
                            f"{leg['sl_order']}. Check Groww manually.")
                return
            if st["filled_qty"]:
                fills.append((st["filled_qty"], st["avg_price"] or leg.get("stop")))
            leg["sl_status"] = st["status"]
        remaining = held - sum(q for q, _ in fills)

        # 2. Aggressive limits, re-priced; then MARKET if the smoke test showed it works.
        tick = self._tick(leg["ticker"])
        ltp = ref_price
        for i in range(EXIT_TRIES):
            if remaining <= 0:
                break
            if i:
                ltp = self._ltp(leg["ticker"], ltp)
            pad = EXIT_BUFFER * (i + 1)
            limit = round_to_tick(ltp * (1 - pad) if long else ltp * (1 + pad), tick, up=not long)
            oid = self._place(leg, f"exit{i + 1}", leg["symbol"], side, remaining, LIMIT, price=limit)
            if not oid:
                if self.disabled_reason() and "auth" in self.disabled_reason().lower():
                    break
                continue
            st = self._wait(oid, EXIT_REPRICE_S)
            if st is not None and not (st["filled"] or st["dead"]):
                st = self._cancel_and_read(oid)
            got = (st or {}).get("filled_qty") or 0
            if st and st["filled"] and not got:
                got = remaining
            if got:
                fills.append((got, (st or {}).get("avg_price") or limit))
                remaining -= got
        if remaining > 0 and self.market_ok:
            oid = self._place(leg, "exit_market", leg["symbol"], side, remaining, MARKET)
            if oid:
                st = self._wait(oid, MARKET_EXIT_WAIT_S)
                got = (st or {}).get("filled_qty") or 0
                if st and st["filled"] and not got:
                    got = remaining
                if got:
                    fills.append((got, (st or {}).get("avg_price") or ltp))
                    remaining -= got

        closed_qty = held - max(remaining, 0)
        if closed_qty > 0:
            avg = sum(q * p for q, p in fills) / closed_qty
            gross = (avg - leg["entry_fill"]) * closed_qty if long else (leg["entry_fill"] - avg) * closed_qty
            charges = (order_charges(BUY if long else SELL, leg["entry_fill"] * closed_qty)
                       + order_charges(side, avg * closed_qty))
            leg["exit_fill"] = round(avg, 4)
            leg["real_gross"] = round(gross, 2)
            leg["charges"] = round(charges, 2)
            leg["real_pnl"] = round(gross - charges, 2)
        if remaining > 0:
            leg["filled_qty"] = remaining
            leg["sl_order"] = None
            leg["status"] = "open"
            leg["note"] = f"{remaining} still open after exit attempts"
            self._save_leg(leg)
            self._alert(f"⚠️ {remaining} {leg['symbol']} STILL OPEN on Groww after exit attempts — "
                        f"check the app (Groww squares off MIS at 15:20).")
            return
        leg["status"] = "closed"
        if not leg.get("smoke"):
            self._realized_today += leg.get("real_pnl") or 0.0
        self._save_leg(leg)
        self._alert(f"Real {side} {held} {leg['symbol']} closed @ ₹{leg['exit_fill']:,.2f} "
                    f"(paper ₹{(leg.get('paper_exit') or 0):,.2f}); real net ₹{leg['real_pnl']:+,.2f}")

    # ── Smoke test ─────────────────────────────────────────

    def smoke_done_today(self) -> bool:
        self._roll_day()
        return bool(self._smoke and self._smoke[0] == self._day)

    def smoke_test(self, ticker: str = None) -> Dict:
        """1-share BUY via the entry path, then SELL as MARKET (falling back to
        the limit exit path). Proves auth, static IP, MIS and both order types.
        Failure disables real orders for the day."""
        self._roll_day()
        ticker = ticker or os.environ.get(SMOKE_SYMBOL_ENV, DEFAULT_SMOKE_SYMBOL)
        if self.is_backtest() or self.disabled_reason():
            return {"passed": False, "reason": self.disabled_reason() or "backtest"}
        price = self._ltp(ticker, 0.0)
        if not price or _symbol(ticker) is None:
            return self._smoke_result(False, f"no live price / bad symbol for {ticker}", None)
        leg = {"paper_trade_id": f"smoke-{self._day}", "date": self._day, "ticker": ticker,
               "symbol": _symbol(ticker), "direction": "long", "paper_qty": 1,
               "paper_entry": price, "stop": None, "filled_qty": 0, "entry_fill": None,
               "sl_order": None, "status": "pending", "smoke": True}
        self._enter(leg, 1, price, with_stop=False)
        if leg["status"] != "open":
            return self._smoke_result(False, f"entry {leg['status']}: {leg.get('note', '')}", leg)
        # MARKET acceptance test on the exit.
        oid = self._place(leg, "exit_market", leg["symbol"], SELL, 1, MARKET)
        st = self._wait(oid, MARKET_EXIT_WAIT_S) if oid else None
        if st and (st["filled"] or st["filled_qty"]):
            leg.update(status="closed", exit_fill=st["avg_price"] or price,
                       paper_exit=price, exit_type="smoke")
            leg["real_pnl"] = round((leg["exit_fill"] - leg["entry_fill"])
                                    - order_charges(BUY, leg["entry_fill"])
                                    - order_charges(SELL, leg["exit_fill"]), 2)
            self._save_leg(leg)
            return self._smoke_result(True, "ok", leg)
        if oid and st is not None and not st["dead"]:
            st = self._cancel_and_read(oid)
            if st and st["filled_qty"]:
                leg.update(status="closed", exit_fill=st["avg_price"] or price, exit_type="smoke")
                self._save_leg(leg)
                return self._smoke_result(True, "ok (market order slow)", leg)
        self.market_ok = False
        logger.warning("Smoke test: MARKET exit not accepted/filled — exits will use limits only")
        self._exit(leg, self._ltp(ticker, price))
        if leg["status"] == "closed":
            return self._smoke_result(True, "ok (MARKET rejected; limit exits only)", leg)
        return self._smoke_result(False, "could not close the 1-share test position", leg)

    def _smoke_result(self, passed: bool, reason: str, leg: Optional[Dict]) -> Dict:
        self._smoke = (self._day, passed)
        self._write({"event": "smoke", "passed": passed, "reason": reason, "market_ok": self.market_ok})
        if passed:
            self._alert(f"Smoke test passed ({reason}). Real orders are ON for today.")
        else:
            self.disable_for_today(f"smoke test failed: {reason}")
        return {"passed": passed, "reason": reason, "leg": leg}

    # ── Reconciliation ─────────────────────────────────────

    def reconcile(self, force: bool = False):
        """Compare Groww's net MIS positions with our open legs (throttled).
        After the forced-close time, flatten anything still open in a symbol
        this mirror traded today. Never touches symbols we didn't trade."""
        self._roll_day()
        if self.is_backtest():
            return
        now_m = self._mono()
        if not force and self._last_reconcile is not None and now_m - self._last_reconcile < RECONCILE_EVERY_S:
            return
        traded_today = {l["symbol"] for l in self.legs.values()
                        if l.get("symbol") and (l.get("filled_qty") or l.get("status") in ("open", "closed"))}
        if not traded_today:
            return
        self._last_reconcile = now_m
        try:
            actual = self.broker.positions()
        except Exception as e:
            self._handle_error("positions", e)
            return
        expected: Dict[str, int] = {}
        for l in self.legs.values():
            if l.get("status") == "open":
                q = int(l.get("filled_qty") or 0)
                expected[l["symbol"]] = expected.get(l["symbol"], 0) + (q if l["direction"] == "long" else -q)
        for sym in traded_today:
            want, got = expected.get(sym, 0), actual.get(sym, 0)
            if want != got and (sym, got) not in self._mismatch_alerted:
                self._mismatch_alerted.add((sym, got))
                self._write({"event": "mismatch", "symbol": sym, "expected": want, "actual": got})
                self._alert(f"Position mismatch {sym}: Groww {got:+d}, expected {want:+d}")
        self._flatten_after_close(actual, traded_today)

    def _flatten_after_close(self, actual: Dict[str, int], traded_today):
        if self._forced_close_time is None:
            return
        now = self._now()
        close_at = self._forced_close_time(now)
        if close_at is None or (now - close_at).total_seconds() < 60:
            return
        for sym in traded_today:
            qty = actual.get(sym, 0)
            if not qty or sym in self._flattened:
                continue
            self._flattened.add(sym)
            leg = {"paper_trade_id": f"flatten-{sym}-{self._day}", "date": self._day,
                   "ticker": f"{sym}.NS", "symbol": sym, "direction": "long" if qty > 0 else "short",
                   "filled_qty": abs(qty), "entry_fill": None, "status": "open", "sl_order": None}
            self._alert(f"{sym}: {qty:+d} still open on Groww after the forced close — flattening")
            # Real P&L isn't knowable for this leg; record the exit orders only.
            open_legs = [l for l in self.legs.values() if l.get("symbol") == sym and l.get("status") == "open"]
            if open_legs and open_legs[0].get("entry_fill"):
                leg["entry_fill"] = open_legs[0]["entry_fill"]
            else:
                leg["entry_fill"] = self._ltp(leg["ticker"], 0.0) or 0.0
            for l in open_legs:
                if l.get("sl_order"):
                    self._cancel_and_read(l["sl_order"])
                    l["sl_order"] = None
            self._exit(leg, self._ltp(leg["ticker"], leg["entry_fill"]))
            if leg["status"] == "closed":
                for l in open_legs:
                    l.update(status="closed", note="flattened after forced close")
                    self._save_leg(l)

    # ── End-of-day report ──────────────────────────────────

    def eod_report(self) -> str:
        self._roll_day()
        legs = [l for l in self.legs.values() if not l.get("smoke") and not
                str(l.get("paper_trade_id", "")).startswith("flatten-")]
        if not legs:
            return f"{self.session_id} LIVE {self._day}: no paper trades today."
        lines = [f"{self.session_id} LIVE vs PAPER — {self._day}"]
        tot_paper = tot_real = tot_charges = 0.0
        slips = []
        counts: Dict[str, int] = {}
        for l in sorted(legs, key=lambda x: str(x["paper_trade_id"])):
            counts[l["status"]] = counts.get(l["status"], 0) + 1
            head = f"#{l['paper_trade_id']} {l['direction'].upper()} {l['ticker']}"
            if l["status"] != "closed":
                lines.append(f"{head}: {l['status']} {('— ' + l['note']) if l.get('note') else ''}".rstrip())
                continue
            q = l["filled_qty"]
            sign = 1 if l["direction"] == "long" else -1
            # slippage in bps, positive = real worse than paper
            s_in = sign * (l["entry_fill"] - l["paper_entry"]) / l["paper_entry"] * 1e4
            s_out = (-sign * (l["exit_fill"] - l["paper_exit"]) / l["paper_exit"] * 1e4
                     if l.get("paper_exit") else 0.0)
            paper_pnl = sign * ((l.get("paper_exit") or l["exit_fill"]) - l["paper_entry"]) * q
            slips += [s_in, s_out]
            tot_paper += paper_pnl
            tot_real += l["real_pnl"]
            tot_charges += l["charges"]
            lines.append(
                f"{head} x{q}: in ₹{l['paper_entry']:,.2f}→₹{l['entry_fill']:,.2f} ({s_in:+.1f}bps), "
                f"out ₹{(l.get('paper_exit') or 0):,.2f}→₹{l['exit_fill']:,.2f} ({s_out:+.1f}bps), "
                f"paper ₹{paper_pnl:+,.2f} vs real net ₹{l['real_pnl']:+,.2f} (charges ₹{l['charges']:,.2f})")
        avg_slip = sum(slips) / len(slips) if slips else 0.0
        lines.append(
            f"TOTAL (same qty): paper ₹{tot_paper:+,.2f} | real net ₹{tot_real:+,.2f} | "
            f"charges ₹{tot_charges:,.2f} (modelled) | avg slippage {avg_slip:+.1f}bps/side | "
            + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())))
        return "\n".join(lines)

    def send_eod_report(self) -> str:
        text = self.eod_report()
        self._write({"event": "eod_report", "text": text})
        self._alert(text)
        return text


class LivePortfolio(Portfolio):
    """Paper portfolio whose every booking is mirrored to real orders.
    Paper books first; the mirror never raises into the caller."""

    def __init__(self, db_path: str, starting_capital: float, mirror: LiveMirror):
        super().__init__(db_path, starting_capital)
        self.mirror = mirror

    def _mirror(self, method: str, trade):
        if trade is None:
            return
        try:
            getattr(self.mirror, method)(trade)
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
