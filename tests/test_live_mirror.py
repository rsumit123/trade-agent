"""Real-money mirror on Groww: every paper event becomes a real order, safely.

The fake broker is adversarial on purpose: lost replies, failed status reads,
cancels that hang, partial EXECUTED fills, OCOs firing mid-exit, ambiguous
position signs. Scenarios marked [B*]/[M*] reproduce the review findings on
commit 1f5c864 and fail on that code."""
import json
import os
import stat
import sys
import types
from dataclasses import replace
from datetime import datetime
from types import SimpleNamespace as NS
from zoneinfo import ZoneInfo

import pytest

import agent.live_mirror as lm
from agent.config import AgentConfig
from agent.groww_broker import (GrowwBroker, GrowwInstruments, classify_error, is_ambiguous,
                                is_auth_error, norm_order)
from agent.live_mirror import (LiveMirror, LivePortfolio, LiveWorker, band_tick, live_enabled_for,
                               order_charges, round_to_tick)
from agent.market_presets import get_preset
from agent.portfolio import Portfolio
from agent.risk_manager import RiskManager
from agent.runner import TradingAgent

IST = ZoneInfo("Asia/Kolkata")


# ── Fakes ─────────────────────────────────────────────────

class GErr(Exception):
    """growwapi GrowwAPIException-shaped."""

    def __init__(self, msg, code="GA001"):
        super().__init__(msg)
        self.msg, self.code = msg, code


class FakeGrowwClient:
    """growwapi.GrowwAPI-shaped and adversarial. `script` holds per-order
    behaviours consumed in placement order:
      'fill' | ('fill', px) | ('partial', n) [OPEN, n filled] | ('exec_partial', n)
      [EXECUTED but n < qty] | 'open' | 'reject' | ('reject_msg', msg) |
      'reject_status' | 'auth' | ('after', how, exc) [accepted, then the reply
      is lost: exc in 'timeout' | 'json' | 'conn'] | 'timeout_before'.
    Unknown strings leave the order OPEN. Unscripted: SL orders rest
    (TRIGGER_PENDING), everything else fills at its limit (MARKET at `ltp`)."""

    def __init__(self, ltp=100.0, oco=False):
        self.ltp = ltp
        self.orders, self.smart = {}, {}
        self.calls, self.script, self.oco_script = [], [], []
        self.n = 0
        self.status_fail = 0             # next N order reads raise ConnectionError
        self.cancel_mode = "ok"          # ok | stuck | raise
        self.oco_cancel_mode = "ok"
        self.qty_mode = "signed"         # signed | abs | only_signed | disagree
        self.upper = self.lower = None
        self.pos_offset = {}             # symbol → qty added to positions only (lag / misread)
        self.clock = lambda: NOW
        self.mis_balance = 1_000_000.0
        self.modify_mode = "ok"          # ok | fail | ignored
        if oco:
            self.create_smart_order = self._create_smart_order
            self.modify_smart_order = self._modify_smart_order
            self.cancel_smart_order = self._cancel_smart_order
            self.get_smart_order = self._get_smart_order
            self.get_smart_order_list = self._get_smart_order_list

    # orders
    def _next(self, order_type):
        if self.script:
            return self.script.pop(0)
        return "open" if order_type == "SL" else "fill"

    def place_order(self, timeout=None, **kw):
        self.calls.append(("place", kw))
        how = self._next(kw["order_type"])
        lost = None
        if isinstance(how, tuple) and how[0] == "after":
            how, lost = how[1], how[2]
        if how == "reject":
            raise GErr("RMS: MIS product not allowed for this scrip")
        if isinstance(how, tuple) and how[0] == "reject_msg":
            raise GErr(how[1])
        if how == "auth":
            raise GErr("Invalid access token", code="GA005")
        if how == "timeout_before":
            raise TimeoutError("read timed out")
        if how == "failed_sync":                     # live: no exception, FAILED in the response
            self.n += 1
            oid = f"G{self.n}"
            self.orders[oid] = {"groww_order_id": oid, "order_reference_id": kw.get("order_reference_id"),
                                "trading_symbol": kw["trading_symbol"], "quantity": kw["quantity"],
                                "transaction_type": kw["transaction_type"], "order_type": kw["order_type"],
                                "price": kw.get("price"), "trigger_price": kw.get("trigger_price"),
                                "product": kw.get("product"), "created_at": self.ts(), "filled_quantity": 0,
                                "average_fill_price": 0.0, "order_status": "FAILED",
                                "remark": "Choose price in multiples of the tick size."}
            return {"groww_order_id": None, "order_status": "FAILED",
                    "order_reference_id": kw.get("order_reference_id"),
                    "remark": "Choose price in multiples of the tick size."}
        self.n += 1
        oid = f"G{self.n}"
        o = {"groww_order_id": oid, "order_reference_id": kw.get("order_reference_id"),
             "trading_symbol": kw["trading_symbol"], "quantity": kw["quantity"],
             "transaction_type": kw["transaction_type"], "order_type": kw["order_type"],
             "price": kw.get("price"), "trigger_price": kw.get("trigger_price"), "product": kw.get("product"),
             "created_at": self.ts(),
             "filled_quantity": 0, "average_fill_price": 0.0, "order_status": "OPEN", "remark": ""}
        px = kw.get("price") if kw["order_type"] != "MARKET" else self.ltp
        if how == "fill" or (isinstance(how, tuple) and how[0] == "fill"):
            o.update(order_status="EXECUTED", filled_quantity=kw["quantity"],
                     average_fill_price=how[1] if isinstance(how, tuple) else px)
        elif isinstance(how, tuple) and how[0] == "partial":
            o.update(filled_quantity=how[1], average_fill_price=px)
        elif isinstance(how, tuple) and how[0] == "exec_partial":
            o.update(order_status="EXECUTED", filled_quantity=how[1], average_fill_price=px)
        elif how == "reject_status":
            o.update(order_status="REJECTED", remark="Insufficient margin available")
        elif how == "open" and kw["order_type"] == "SL":
            o.update(order_status="TRIGGER_PENDING")
        elif isinstance(how, tuple) and how[0] == "reject_async":   # NEW now, REJECTED on the next read
            o.update(order_status="NEW", _next=("REJECTED", how[1]))
        self.orders[oid] = o
        if lost == "timeout":
            raise TimeoutError("read timed out")
        if lost == "json":
            raise json.JSONDecodeError("Expecting value", "<html>502</html>", 0)
        if lost == "conn":
            raise ConnectionError("connection reset")
        return {"groww_order_id": oid, "order_status": "NEW", "order_reference_id": kw.get("order_reference_id"),
                "remark": None}

    def _settle(self, o):
        nxt = o.pop("_next", None)
        if nxt:
            o["order_status"] = nxt[0]
            if len(nxt) > 1 and nxt[1] is not None:
                o["remark"] = nxt[1]
        return o

    def get_order_detail(self, **kw):
        if self.status_fail:
            self.status_fail -= 1
            raise ConnectionError("reset")
        if kw["groww_order_id"] not in self.orders:
            raise GErr("Order details are available only for orders placed today.", "GA004")
        return {k: v for k, v in self._settle(self.orders[kw["groww_order_id"]]).items() if not k.startswith("_")}

    def get_order_status_by_reference(self, segment=None, order_reference_id=None, timeout=None):
        for o in self.orders.values():
            if o.get("order_reference_id") == order_reference_id:
                self._settle(o)
                return {"groww_order_id": o["groww_order_id"], "order_status": o["order_status"],
                        "remark": o.get("remark"), "filled_quantity": o["filled_quantity"],
                        "reference_id": o["order_reference_id"]}       # live: "reference_id"
        raise GErr("Order status is available only for orders placed today. Please check your order "
                   "reference id.", code="GA004")

    PAGE = 25                         # the server's page size; the SDK never sends page_size

    def get_order_list(self, page=0, segment=None, timeout=None):
        rows = list(self.orders.values())[page * self.PAGE:(page + 1) * self.PAGE]
        return {"order_list": [{k: v for k, v in r.items() if not k.startswith("_")} for r in rows]}

    def add_manual(self, sym, qty, price=100.0, order_type="MARKET", status="EXECUTED", ref=None):
        """An order the user placed in the Groww app (no reference of ours)."""
        self.n += 1
        oid = f"G{self.n}"
        filled = abs(qty) if status == "EXECUTED" else 0
        self.orders[oid] = {"groww_order_id": oid, "order_reference_id": ref, "trading_symbol": sym,
                            "quantity": abs(qty), "transaction_type": "BUY" if qty > 0 else "SELL",
                            "order_type": order_type, "product": "MIS", "created_at": self.ts(),
                            "filled_quantity": filled, "average_fill_price": price if filled else None,
                            "order_status": status, "remark": "", "trigger_price": None, "price": price}
        return oid

    def cancel_order(self, groww_order_id, segment, timeout=None):
        self.calls.append(("cancel", groww_order_id))
        o = self.orders[groww_order_id]
        if self.cancel_mode == "raise":
            raise GErr("GA000 internal error", code="GA000")
        self._settle(o)
        if o["order_status"] in ("REJECTED", "FAILED") or (o["order_status"] == "EXECUTED"
                                                           and o["filled_quantity"] >= o["quantity"]):
            raise GErr("Cancellation not allowed", "GA001")                # live
        if o["order_status"] == "CANCELLED":                                 # live: no error
            return {"groww_order_id": groww_order_id, "order_status": "CANCELLATION_REQUESTED"}
        if self.cancel_mode == "stuck":
            o["order_status"] = "CANCELLATION_REQUESTED"
        else:
            o["order_status"] = "CANCELLED"
        return {"groww_order_id": groww_order_id, "order_status": "CANCELLATION_REQUESTED"}

    def modify_order(self, timeout=None, **kw):
        self.calls.append(("modify", kw))
        o = self.orders[kw["groww_order_id"]]
        if self.modify_mode == "fail":
            raise GErr("Order already terminated", "GA001")
        if o["order_type"] == "SL" and kw.get("price") is None:
            raise GErr("Order was rejected due to invalid limit price.", "GA001")      # live
        o.update(price=kw.get("price"), trigger_price=kw.get("trigger_price"), order_status="MODIFICATION_REQUESTED")
        if self.modify_mode != "ignored":                    # "ignored": accepted but never applied
            o["_next"] = ("TRIGGER_PENDING" if o["order_type"] in ("SL", "SL_M") else "APPROVED", None)
        else:
            o.update(trigger_price=o.get("_orig_trigger"), _next=("TRIGGER_PENDING", None))
        return {"groww_order_id": kw["groww_order_id"], "order_status": "MODIFICATION_REQUESTED"}

    def fire_stop(self, oid, price):
        o = self.orders[oid]
        o.update(order_status="EXECUTED", filled_quantity=o["quantity"], average_fill_price=price)

    # smart orders (OCO)
    def ts(self):
        """Groww's created_at: naive IST, millis sometimes 2 digits."""
        t = self.clock()
        return t.strftime("%Y-%m-%dT%H:%M:%S.") + f"{t.microsecond // 10000:02d}"

    def _create_smart_order(self, timeout=None, **kw):
        """Real shape: the response carries smart_order_id; the stop leg rests at
        once as an SL_M order with a MISOCO_ reference (the target leg doesn't)."""
        self.calls.append(("oco_create", kw))
        how = self.oco_script.pop(0) if self.oco_script else "ok"
        if how == "reject":
            raise GErr("OCO not allowed for this instrument")
        sid = f"oco_{len(self.smart) + 1}"
        trig = lambda leg: {"price": None, "triggerPrice": kw[leg]["trigger_price"], "orderType": None}
        self.n += 1
        child = f"G{self.n}"
        self.orders[child] = {
            "groww_order_id": child, "order_reference_id": f"MISOCO_MIS_CASH_OCO_STOP_LOSS-{sid}x{self.n:026d}",
            "trading_symbol": kw["trading_symbol"], "quantity": kw["quantity"], "product": "MIS",
            "transaction_type": kw["transaction_type"], "order_type": "SL_M", "order_status": "TRIGGER_PENDING",
            "trigger_price": float(kw["stop_loss"]["trigger_price"]) + 4.6,   # live: child trigger ≠ OCO's
            "price": 0.0, "filled_quantity": 0, "average_fill_price": 0.0, "remark": "", "created_at": self.ts()}
        self.smart[sid] = {"smart_order_id": sid, "trading_symbol": kw["trading_symbol"], "exchange": "NSE",
                           "quantity": kw["quantity"], "product_type": "MIS", "duration": "DAY",
                           "status": "ACTIVE", "is_cancellation_allowed": True, "is_modification_allowed": True,
                           "created_at": self.ts(), "expire_at": None, "triggered_at": None,
                           "updated_at": self.ts(), "target": trig("target"), "stop_loss": trig("stop_loss"),
                           "smart_order_type": "OCO", "_side": kw["transaction_type"], "_stop_child": child}
        if how == "timeout_after":
            raise TimeoutError("read timed out")
        return {"smart_order_id": sid, "status": "ACTIVE"}

    def _modify_smart_order(self, timeout=None, **kw):
        self.calls.append(("oco_modify", kw))
        s = self.smart[kw["smart_order_id"]]
        for leg in ("target", "stop_loss"):
            if kw.get(leg):
                s[leg]["triggerPrice"] = kw[leg].get("trigger_price", kw[leg].get("triggerPrice"))
        if kw.get("quantity"):
            s["quantity"] = kw["quantity"]
        return {"smart_order_id": kw["smart_order_id"], "status": s["status"]}

    def _cancel_smart_order(self, segment=None, smart_order_type=None, smart_order_id=None, timeout=None):
        self.calls.append(("oco_cancel", smart_order_id))
        s = self.smart[smart_order_id]
        if s["status"] == "ACTIVE" and self.oco_cancel_mode != "stuck":
            s["status"] = "CANCELLED"
            kid = self.orders.get(s["_stop_child"])
            if kid and kid["order_status"] == "TRIGGER_PENDING":
                kid["order_status"] = "CANCELLED"                  # Groww removes its legs
        return {"smart_order_id": smart_order_id, "status": s["status"]}

    def _public(self, sm):
        return {k: (dict(v) if isinstance(v, dict) else v) for k, v in sm.items() if not k.startswith("_")}

    def _get_smart_order(self, segment=None, smart_order_type=None, smart_order_id=None, timeout=None):
        return self._public(self.smart[smart_order_id])

    def _get_smart_order_list(self, timeout=None, **kw):
        return {"orders": [self._public(sm) for sm in self.smart.values()]}

    def fire_oco(self, sid, leg="stop_loss", price=None):
        """A leg fires: the stop leg's resting SL_M executes, or (target) a new
        MISOCO target order executes and the stop leg is cancelled."""
        s = self.smart[sid]
        s.update(status="COMPLETED", triggered_at=self.ts())
        px = price or float(s[leg]["triggerPrice"])
        stop_kid = self.orders[s["_stop_child"]]
        if leg == "stop_loss":
            stop_kid.update(order_status="EXECUTED", filled_quantity=stop_kid["quantity"], average_fill_price=px)
            return stop_kid["groww_order_id"]
        stop_kid["order_status"] = "CANCELLED"
        self.n += 1
        oid = f"G{self.n}"
        self.orders[oid] = {"groww_order_id": oid, "order_reference_id": f"MISOCO_MIS_CASH_OCO_TARGET-{self.n:030d}",
                            "product": "MIS", "created_at": self.ts(),
                            "trading_symbol": s["trading_symbol"], "quantity": s["quantity"],
                            "transaction_type": s["_side"], "order_type": "MARKET",
                            "filled_quantity": s["quantity"], "order_status": "EXECUTED",
                            "average_fill_price": px, "remark": ""}
        return oid

    def stop_child(self, sid):
        return self.smart[sid]["_stop_child"]

    def get_available_margin_details(self, timeout=None):
        return {"clear_cash": self.mis_balance, "net_margin_used": 0.0, "brokerage_and_charges": 0.0,
                "equity_margin_details": {"mis_balance_available": self.mis_balance,
                                          "cnc_balance_available": self.mis_balance,
                                          "mis_margin_used": 0.0, "net_equity_margin_used": 0.0},
                "fno_margin_details": {}, "commodity_margin_details": {}}

    # positions / quotes
    def net(self, sym):
        q = 0
        for o in self.orders.values():
            if o["trading_symbol"] == sym:
                q += o["filled_quantity"] * (1 if o["transaction_type"] == "BUY" else -1)
        return q

    def get_positions_for_user(self, segment=None, timeout=None):
        syms = {o["trading_symbol"] for o in self.orders.values()} | set(self.pos_offset)
        rows = []
        for s in sorted(syms):
            net = self.net(s) + self.pos_offset.get(s, 0)
            credit, debit = (net, 0) if net >= 0 else (0, -net)
            row = {"trading_symbol": s, "exchange": "NSE", "product": "MIS"}
            if self.qty_mode == "signed":
                row.update(quantity=net, credit_quantity=credit, debit_quantity=debit)
            elif self.qty_mode == "abs":
                row.update(quantity=abs(net), credit_quantity=credit, debit_quantity=debit)
            elif self.qty_mode == "only_signed":
                row.update(quantity=net)
            elif self.qty_mode == "disagree":
                row.update(quantity=net + 3, credit_quantity=credit, debit_quantity=debit)
            rows.append(row)
        return {"positions": rows}

    def get_quote(self, trading_symbol=None, exchange=None, segment=None, timeout=None):
        return {"last_price": self.ltp, "upper_circuit_limit": self.upper, "lower_circuit_limit": self.lower}

    def placed(self, order_type=None, side=None):
        return [kw for c, kw in self.calls if c == "place"
                and (order_type is None or kw["order_type"] == order_type)
                and (side is None or kw["transaction_type"] == side)]

    def ccalls(self, name):
        return [kw for c, kw in self.calls if c == name]


class FakeMD:
    def __init__(self, prices=None, tick=None):
        self.prices, self.tick = prices or {}, tick

    def tick_size(self, ticker):
        return self.tick

    def get_current_prices(self, tickers=None):
        return {t: self.prices[t] for t in (tickers or []) if t in self.prices}

    def is_market_open(self):
        return True


CSV = """exchange,exchange_token,trading_symbol,groww_symbol,name,instrument_type,segment,series,isin,tick_size,freeze_quantity,is_reserved,buy_allowed,sell_allowed,internal_trading_symbol,is_intraday
NSE,1,SBIN,NSE-SBIN,SBI,EQ,CASH,EQ,X,0.05,0,0,1,1,SBIN,1
NSE,2,X,NSE-X,X,EQ,CASH,EQ,X,0.05,0,0,1,1,X,1
NSE,3,ITC,NSE-ITC,ITC,EQ,CASH,EQ,X,0.05,0,0,1,1,ITC,1
NSE,4,SEJALLTD,NSE-SEJAL,Sejal,EQ,CASH,EQ,X,0.01,0,0,1,1,SEJALLTD,0
NSE,5,NOSHORT,NSE-NS,NoShort,EQ,CASH,EQ,X,0.05,0,0,1,0,NOSHORT,1
NSE,6,MRF,NSE-MRF,MRF,EQ,CASH,EQ,X,5.0,0,0,1,1,MRF,1
NSE,7,SBIN,BSE-SBIN,SBI,EQ,CASH,EQ,X,0.05,0,0,1,1,SBIN,0
"""
CSV = CSV.replace("NSE,7,SBIN,BSE", "BSE,7,SBIN,BSE")

NOW = datetime(2026, 10, 16, 10, 0, tzinfo=IST)     # a Friday


@pytest.fixture
def env(tmp_path):
    client = FakeGrowwClient(ltp=940.0)
    clock = {"t": 0.0, "now": NOW}
    client.clock = lambda: clock["now"]
    alerts = []
    md = FakeMD({"SBIN.NS": 940.0, "ITC.NS": 400.0})

    def make(smoke=True, oco=None, instruments=None, **kw):
        m = LiveMirror(GrowwBroker(client=client, token_path=tmp_path / "tok"), "nse_opus_live", tmp_path / "s",
                       market_data=md, instruments=instruments, alert=alerts.append, now=lambda: clock["now"],
                       sleep=lambda s: clock.__setitem__("t", clock["t"] + s),
                       monotonic=lambda: clock["t"], **kw)
        if smoke:
            m._smoke = (m._today(), True)
        return m

    mirror = make()
    pf = LivePortfolio(str(tmp_path / "t.db"), 500_000, mirror)

    def later(s):
        clock["t"] += s
    return NS(client=client, mirror=mirror, pf=pf, alerts=alerts, md=md, make=make, tmp=tmp_path,
              clock=clock, later=later)


def _ledger(env):
    return [json.loads(l) for l in (env.tmp / "s" / "live_orders.jsonl").read_text().splitlines()]


def _leg(env, t, m=None):
    return (m or env.mirror).legs[str(t.id)]


def _buy(env, qty=10, stop=930.0, target=None, ticker="SBIN.NS", price=940.0, pf=None):
    return (pf or env.pf).execute_buy(ticker, qty, price, "intraday", stop_price=stop, target_price=target)


# ── Entries ───────────────────────────────────────────────

def test_long_entry_fills_and_places_protective_stop(env):
    t = _buy(env)
    entry, stop = env.client.placed()
    assert entry["transaction_type"] == "BUY" and entry["order_type"] == "LIMIT" and entry["product"] == "MIS"
    assert entry["trading_symbol"] == "SBIN" and entry["quantity"] == 10 and entry["price"] == 942.85
    assert entry["order_reference_id"] == f"261016{env.mirror._salt}-{t.id}-E1"
    assert stop["order_type"] == "SL" and stop["transaction_type"] == "SELL" and stop["trigger_price"] == 930.0
    leg = _leg(env, t)
    assert leg["status"] == "open" and leg["filled_qty"] == 10 and leg["entry_fill"] == 942.85


def test_reference_ids_follow_groww_format(env):
    t = _buy(env)
    for kw in env.client.placed():
        ref = kw["order_reference_id"]
        assert 8 <= len(ref) <= 20 and ref.count("-") <= 2 and ref.replace("-", "").isalnum()
    assert len({kw["order_reference_id"] for kw in env.client.placed()}) == len(env.client.placed())


def test_pending_leg_is_written_before_the_order_is_sent(env):          # [M5]
    seen = []
    orig = env.client.place_order

    def spy(timeout=None, **kw):
        legs = [r for r in _ledger(env) if r["event"] == "leg"]
        seen.append(legs[-1]["leg"]["status"] if legs else None)
        return orig(timeout=timeout, **kw)
    env.client.place_order = spy
    _buy(env)
    assert seen[0] == "pending"


def test_short_entry_sells_below_and_stop_buys_above(env):
    env.pf.execute_short("SBIN.NS", 5, 940.0, stop_price=950.0, target_price=920.0)
    entry, stop = env.client.placed()
    assert entry["transaction_type"] == "SELL" and entry["price"] == 937.15
    assert stop["transaction_type"] == "BUY" and stop["trigger_price"] == 950.0


def test_partial_fill_cancels_rest_and_protects_only_the_fill(env):
    env.client.script = [("partial", 4)]
    t = _buy(env)
    env.later(31)
    env.mirror.tick()
    leg = _leg(env, t)
    assert leg["status"] == "open" and leg["filled_qty"] == 4
    assert env.client.placed("SL")[0]["quantity"] == 4


def test_executed_with_partial_fill_is_not_final(env):                  # [M3]
    env.client.script = [("exec_partial", 6)]
    t = _buy(env)
    assert _leg(env, t)["status"] == "entry_working"                     # EXECUTED 6/10 is not final
    env.later(31)
    env.mirror.tick()
    assert ("cancel", "G1") in env.client.calls
    assert _leg(env, t)["status"] == "open" and _leg(env, t)["filled_qty"] == 6


def test_unfilled_entry_is_missed_only_after_a_final_cancel(env):
    env.client.script = ["open"]
    t = _buy(env)
    assert _leg(env, t)["status"] == "entry_working"
    env.later(31)
    env.mirror.tick()
    assert _leg(env, t)["status"] == "missed"
    n = len(env.client.calls)
    env.pf.update_levels(t.id, stop_price=935.0)
    env.pf.execute_sell(t.id, 950.0, "exit")
    assert len(env.client.calls) == n and env.pf.get_open_positions() == []


def test_status_read_failure_is_unknown_not_missed(env):                # [B1] repro A
    env.client.status_fail = 1
    t = _buy(env)
    assert _leg(env, t)["status"] == "unknown"
    assert any("UNKNOWN" in a for a in env.alerts)
    env.mirror.tick()
    leg = _leg(env, t)
    assert leg["status"] == "open" and leg["filled_qty"] == 10 and leg["sl_order"]


def test_failed_entry_cancel_keeps_resolving_and_later_fill_is_tracked(env):   # [B4] repro D
    env.client.script = ["x"]                      # stays OPEN
    env.client.cancel_mode = "raise"
    t = _buy(env)
    env.later(31)
    env.mirror.tick()
    assert _leg(env, t)["status"] in ("entry_working", "unknown")         # never "missed"
    env.client.orders["G1"].update(order_status="EXECUTED", filled_quantity=10, average_fill_price=940.5)
    env.mirror.tick()
    assert _leg(env, t)["status"] == "open" and _leg(env, t)["entry_fill"] == 940.5
    env.mirror.reconcile(force=True)
    assert not env.mirror._mismatch_alerted


def test_entry_reply_lost_after_acceptance_resolves_by_reference(env):   # [B2]
    env.client.script = [("after", "fill", "json")]
    t = _buy(env)
    assert len(env.client.placed("LIMIT")) == 1
    assert _leg(env, t)["status"] == "open" and _leg(env, t)["entry_order"] == "G1"


def test_entry_never_reached_groww_is_missed_only_after_30s_of_lookups(env):
    env.client.script = ["timeout_before"]
    t = _buy(env)
    for _ in range(3):
        env.later(5)
        env.mirror.tick()
    assert _leg(env, t)["status"] == "entry_working"            # 3 lookups but only 15s
    for _ in range(4):
        env.later(5)
        env.mirror.tick()
    assert _leg(env, t)["status"] == "missed" and len(env.client.placed()) == 1


def test_definitive_rejection_is_classified_and_recorded(env):
    env.client.script = ["reject"]
    t = _buy(env)
    leg = _leg(env, t)
    assert leg["status"] == "rejected" and leg["category"] == "not_intraday_allowed"
    assert len(env.pf.get_open_positions()) == 1
    err = [r for r in _ledger(env) if r.get("event") == "order" and r.get("status") == "rejected"][0]
    assert err["category"] == "not_intraday_allowed" and "MIS product not allowed" in err["message"]


# ── Exits ─────────────────────────────────────────────────

def test_exit_cancels_stop_then_sells(env):
    t = _buy(env)
    env.client.ltp = 950.0
    env.pf.execute_sell(t.id, 950.0, "take profit")
    assert ("cancel", "G2") in env.client.calls
    sell = env.client.placed("LIMIT", "SELL")[0]
    assert sell["quantity"] == 10 and sell["price"] == round_to_tick(950 * 0.995, 0.05, up=False)
    assert _leg(env, t)["status"] == "closed" and env.client.net("SBIN") == 0


def test_exit_reply_lost_does_not_duplicate_or_flip(env):               # [B2] repro B
    t = _buy(env)
    env.client.script = [("after", "fill", "timeout")]
    env.pf.execute_sell(t.id, 950.0, "exit")
    assert len(env.client.placed("LIMIT", "SELL")) == 1
    assert _leg(env, t)["status"] == "closed" and env.client.net("SBIN") == 0


def test_exit_waits_while_stop_cancel_is_pending_then_records_stop_fill(env):   # [B3] repro C
    t = _buy(env)
    env.client.cancel_mode = "stuck"
    env.pf.execute_sell(t.id, 931.0, "stop", exit_type="stop_hit")
    assert env.client.placed("LIMIT", "SELL") == [] and _leg(env, t)["status"] == "exiting"
    env.client.fire_stop("G2", 929.5)
    env.mirror.tick()
    leg = _leg(env, t)
    assert leg["status"] == "closed" and leg["exit_fill"] == 929.5
    assert env.client.placed("LIMIT", "SELL") == [] and env.client.net("SBIN") == 0


def test_stop_already_filled_means_no_second_exit(env):
    t = _buy(env)
    env.client.fire_stop("G2", 929.5)
    env.pf.execute_sell(t.id, 929.0, "stop", exit_type="stop_hit")
    assert env.client.placed("LIMIT", "SELL") == [] and _leg(env, t)["exit_fill"] == 929.5
    assert env.client.net("SBIN") == 0


def test_exit_is_capped_at_what_we_own(env):                              # cross-cutting
    t = _buy(env)
    env.client.script = [("partial", 4), "fill"]   # the first exit only fills 4
    env.pf.execute_sell(t.id, 950.0, "exit")
    env.later(11)
    env.mirror.tick()
    sells = env.client.placed("LIMIT", "SELL")
    assert [k["quantity"] for k in sells] == [10, 6] and env.client.net("SBIN") == 0


@pytest.mark.parametrize("manual,our_sell,final_net", [
    (-4, 6, 0),        # the user sold 4 of ours in the app: we sell only the 6 left
    (-10, None, 0),    # the user squared off all of it: we sell nothing (B1)
    (-15, None, -5),   # ...and went short 5 on top: theirs stays untouched
    (15, 10, 15),      # the user bought their own 15: we sell our 10 only
])
def test_manual_trades_after_entry_never_cause_a_second_exit(env, manual, our_sell, final_net):
    t = _buy(env)
    env.client.add_manual("SBIN", manual)
    env.pf.execute_sell(t.id, 950.0, "exit")
    sells = [k["quantity"] for k in env.client.placed("LIMIT", "SELL")]
    assert sells == ([our_sell] if our_sell else [])
    assert _leg(env, t)["status"] == "closed" and env.client.net("SBIN") == final_net


def test_exit_reprices_then_market_across_ticks(env):                   # [M4]
    t = _buy(env)
    env.client.script = ["open", "open", ("partial", 3), "fill"]
    env.client.ltp = 948.0
    env.pf.execute_sell(t.id, 950.0, "exit")
    for _ in range(4):
        env.later(11)
        env.mirror.tick()
    exits = env.client.placed(side="SELL")[1:]       # skip the protective SL
    assert [e["order_type"] for e in exits] == ["LIMIT", "LIMIT", "LIMIT", "MARKET"]
    assert [e["quantity"] for e in exits] == [10, 10, 10, 7]
    assert exits[0]["price"] > exits[1]["price"] > exits[2]["price"]
    assert _leg(env, t)["status"] == "closed" and env.client.net("SBIN") == 0


def test_exit_stuck_at_deadline_alerts_loudly(env):
    t = _buy(env)
    env.mirror.market_ok = False
    env.client.script = ["open"] * 10
    env.pf.execute_sell(t.id, 950.0, "exit")
    env.clock["now"] = NOW.replace(hour=15, minute=19, second=5)
    for _ in range(5):
        env.later(11)
        env.mirror.tick()
    assert _leg(env, t)["status"] == "stuck" and any("15:19" in a for a in env.alerts)


def test_kill_switch_still_lets_exits_out(env):
    t = _buy(env)
    (env.tmp / "s" / "LIVE_KILL").write_text("")
    env.pf.execute_sell(t.id, 950.0, "exit")
    assert _leg(env, t)["status"] == "closed"


def test_paper_exit_during_working_entry_exits_once_filled(env):
    env.client.script = ["open"]
    t = _buy(env)
    env.pf.execute_sell(t.id, 941.0, "changed mind")
    assert _leg(env, t)["exit_requested"]
    env.client.orders["G1"].update(order_status="EXECUTED", filled_quantity=10, average_fill_price=941.0)
    env.mirror.tick()
    assert _leg(env, t)["status"] == "closed" and env.client.net("SBIN") == 0


# ── OCO ───────────────────────────────────────────────────

@pytest.fixture
def oco(env, monkeypatch):
    """The experimental OCO path (off in v1 unless GROWW_ENABLE_OCO=1)."""
    monkeypatch.setenv("GROWW_ENABLE_OCO", "1")
    env.mirror.oco_ok = True
    env.client.create_smart_order = env.client._create_smart_order
    env.client.modify_smart_order = env.client._modify_smart_order
    env.client.cancel_smart_order = env.client._cancel_smart_order
    env.client.get_smart_order = env.client._get_smart_order
    env.client.get_smart_order_list = env.client._get_smart_order_list
    return env


def test_oco_protects_with_market_target_and_sl_m_stop(oco):
    t = _buy(oco, stop=930.0, target=960.0)
    (kw,) = oco.client.ccalls("oco_create")
    assert kw["smart_order_type"] == "OCO" and kw["product_type"] == "MIS" and kw["segment"] == "CASH"
    assert kw["transaction_type"] == "SELL" and kw["quantity"] == 10 and kw["net_position_quantity"] == 10
    assert kw["target"] == {"trigger_price": "960.00", "order_type": "MARKET", "price": None}
    assert kw["stop_loss"] == {"trigger_price": "930.00", "order_type": "SL_M", "price": None}
    assert _leg(oco, t)["protect_kind"] == "oco" and oco.client.placed("SL") == []


def test_short_oco_buys_back_with_negative_net(oco):
    oco.pf.execute_short("SBIN.NS", 5, 940.0, stop_price=950.0, target_price=920.0)
    (kw,) = oco.client.ccalls("oco_create")
    assert kw["transaction_type"] == "BUY" and kw["net_position_quantity"] == -5


def test_update_levels_replaces_the_oco_and_never_modifies_it(oco):           # live: OCO modify is broken
    t = _buy(oco, stop=930.0, target=960.0)
    oco.pf.update_levels(t.id, stop_price=936.0, target_price=965.0)
    assert oco.client.ccalls("oco_modify") == []
    assert oco.client.smart["oco_1"]["status"] == "CANCELLED" and _leg(oco, t)["sl_order"] == "oco_2"
    kw = oco.client.ccalls("oco_create")[-1]
    assert kw["stop_loss"]["trigger_price"] == "936.00" and kw["target"]["trigger_price"] == "965.00"
    oco.pf.update_levels(t.id, stop_price=936.0)                     # unchanged → nothing sent
    assert len(oco.client.ccalls("oco_create")) == 2


def test_oco_fired_before_exit_is_recorded_without_a_second_exit(oco):
    t = _buy(oco, stop=930.0, target=960.0)
    oco.client.fire_oco("oco_1", "target", price=960.4)
    oco.pf.execute_sell(t.id, 960.0, "target", exit_type="target_hit")
    leg = _leg(oco, t)
    assert oco.client.placed(side="SELL") == [] and leg["status"] == "closed"
    assert leg["exit_fill"] == 960.4 and leg["exit_fills"][0]["source"] == "oco"
    assert oco.client.net("SBIN") == 0


def test_oco_triggering_between_cancel_and_exit_does_not_flip(oco):
    t = _buy(oco, stop=930.0, target=960.0)
    oco.client.oco_cancel_mode = "stuck"
    oco.pf.execute_sell(t.id, 931.0, "stop", exit_type="stop_hit")
    assert _leg(oco, t)["status"] == "exiting" and oco.client.placed(side="SELL") == []
    oco.client.fire_oco("oco_1", "stop_loss", price=929.8)
    oco.mirror.tick()
    assert _leg(oco, t)["status"] == "closed" and oco.client.net("SBIN") == 0
    assert oco.client.placed(side="SELL") == []


def test_oco_failure_falls_back_to_plain_stop(oco):
    oco.client.oco_script = ["reject"]
    t = _buy(oco, stop=930.0, target=960.0)
    assert _leg(oco, t)["protect_kind"] == "sl" and len(oco.client.placed("SL")) == 1
    assert any("plain stop" in a for a in oco.alerts)


def test_oco_reply_lost_is_found_not_duplicated(oco):
    oco.client.oco_script = ["timeout_after"]
    t = _buy(oco, stop=930.0, target=960.0)
    assert _leg(oco, t)["sl_order"] == "oco_1" and len(oco.client.smart) == 1 and oco.client.placed("SL") == []


# ── Guards / eligibility ──────────────────────────────────

def _session(sid="nse_opus_live", broker="groww", backtest=False):
    return NS(session_id=sid, live_broker=broker, backtest_mode=backtest)


def test_live_enabled_needs_all_three_gates(monkeypatch):
    monkeypatch.setenv("GROWW_LIVE_SESSIONS", "nse_opus_live, nse_opus")
    assert live_enabled_for(_session()) is True
    assert live_enabled_for(_session(sid="nse_opus")) is False
    assert live_enabled_for(_session(broker=None)) is False
    assert live_enabled_for(_session(backtest=True)) is False
    monkeypatch.delenv("GROWW_LIVE_SESSIONS")
    assert live_enabled_for(_session()) is False


@pytest.fixture
def inst(tmp_path):
    return GrowwInstruments(path=tmp_path / "inst.csv", fetch=lambda: CSV, now=lambda: NOW)


def test_not_intraday_allowed_is_blocked_without_an_api_call(env, inst):
    m = env.make(instruments=inst)
    pf = LivePortfolio(str(env.tmp / "t2.db"), 500_000, m)
    t = _buy(env, ticker="SEJALLTD.NS", price=120.0, stop=118.0, pf=pf)
    assert m.legs[str(t.id)]["status"] == "blocked" and m.legs[str(t.id)]["category"] == "not_intraday_allowed"
    assert env.client.calls == [] and any("not_intraday_allowed" in a for a in env.alerts)


def test_short_blocked_when_sell_not_allowed(env, inst):
    m = env.make(instruments=inst)
    pf = LivePortfolio(str(env.tmp / "t2.db"), 500_000, m)
    t = pf.execute_short("NOSHORT.NS", 5, 100.0, stop_price=102.0)
    assert m.legs[str(t.id)]["category"] == "sell_not_allowed" and env.client.calls == []


def test_instrument_csv_is_cached_daily_and_nse_cash_only(inst, tmp_path):
    calls = []
    i = GrowwInstruments(path=tmp_path / "c.csv", fetch=lambda: calls.append(1) or CSV, now=lambda: NOW)
    assert i.info("SBIN")["is_intraday"] is True                  # the BSE row (is_intraday=0) is ignored
    assert i.info("SEJALLTD")["is_intraday"] is False and i.info("SEJALLTD")["tick_size"] == 0.01
    j = GrowwInstruments(path=tmp_path / "c.csv", fetch=lambda: calls.append(1) or CSV, now=lambda: NOW)
    assert j.info("ITC") and len(calls) == 1                         # second reader uses today's file


def test_tick_size_prefers_groww_then_kite_then_band(env, inst):
    m = env.make(instruments=inst)
    assert m._tick_size("MRF.NS", 130000) == 5.0
    env.md.tick = 0.05
    assert m._tick_size("UNLISTED.NS", 1500) == 0.05
    env.md.tick = None
    assert m._tick_size("UNLISTED.NS", 1500) == 0.10 and band_tick(200) == 0.01 and band_tick(6000) == 0.5


def test_kill_switch_exposure_loss_and_order_caps(env, monkeypatch):
    (env.tmp / "s").mkdir(exist_ok=True)
    (env.tmp / "s" / "LIVE_KILL").write_text("")
    t = _buy(env)
    assert _leg(env, t)["note"] == "kill switch"
    (env.tmp / "s" / "LIVE_KILL").unlink()
    _buy(env, qty=40)
    t2 = _buy(env, qty=15)
    assert "exposure" in _leg(env, t2)["note"]
    env.mirror._realized_today = -1500.0
    assert "daily real loss" in _leg(env, _buy(env, qty=1))["note"]
    env.mirror._realized_today = 0.0
    monkeypatch.setattr(lm, "MAX_ORDERS_PER_DAY", 4)
    assert "order cap" in _leg(env, _buy(env, qty=1))["note"]


def test_backtest_and_non_nse_and_no_smoke(env):
    env.mirror.is_backtest = lambda: True
    _buy(env, qty=1)
    env.mirror.is_backtest = lambda: False
    t = _buy(env, ticker="RELIANCE.BO", price=1000.0, qty=1)
    assert "not an NSE equity" in _leg(env, t)["note"]
    m = env.make(smoke=False)
    pf = LivePortfolio(str(env.tmp / "t2.db"), 500_000, m)
    assert "smoke test" in m.legs[str(_buy(env, qty=1, pf=pf).id)]["note"]
    assert env.client.calls == []


# ── Auth / failures ───────────────────────────────────────

def test_body_level_auth_error_disables_entries(env, monkeypatch):       # [M1]
    env.client.script = ["auth", "auth"]
    monkeypatch.setattr(GrowwBroker, "_login", lambda self: env.client)
    t = _buy(env)
    assert _leg(env, t)["status"] == "rejected" and _leg(env, t)["category"] == "auth"
    assert "auth" in env.mirror.disabled_reason().lower()
    assert _leg(env, _buy(env, qty=1))["status"] == "skipped"
    assert env.make().disabled_reason()                                     # survives a restart
    env.clock["now"] = datetime(2026, 10, 19, 10, 0, tzinfo=IST)
    assert env.make().disabled_reason() is None


def test_three_consecutive_failures_disable_entries_but_exits_continue(env):   # [M1]
    t = _buy(env)
    env.client.status_fail = 3
    for _ in range(3):
        env.mirror._try("probe", env.mirror.broker.order_status, "G1")
    assert "in a row" in env.mirror.disabled_reason()
    env.pf.execute_sell(t.id, 950.0, "exit")
    assert _leg(env, t)["status"] == "closed"


def test_classification():
    assert classify_error(GErr("Invalid access token", "GA005")) == "auth"
    assert classify_error(GErr("Request from IP not whitelisted")) == "ip_not_whitelisted"
    assert classify_error(GErr("Insufficient margin")) == "insufficient_margin"
    assert classify_error(GErr("Price is outside the circuit limit")) == "circuit_limit"
    assert classify_error(GErr("Price must be a multiple of tick size")) == "invalid_price_tick"
    assert classify_error(GErr("Market is closed")) == "market_closed"
    assert classify_error(GErr("Too many requests", "429")) == "rate_limited"
    assert classify_error(GErr("MIS not allowed for this scrip")) == "not_intraday_allowed"
    assert classify_error(GErr("something odd")) == "unknown"
    assert is_auth_error(GErr("Invalid access token", "GA005")) and not is_auth_error(GErr("Insufficient margin"))
    assert is_ambiguous(TimeoutError()) and is_ambiguous(json.JSONDecodeError("x", "y", 0))
    assert is_ambiguous(GErr("bad gateway", "502")) and not is_ambiguous(GErr("Insufficient margin"))


# ── Positions ─────────────────────────────────────────────

def test_unsigned_quantity_that_disagrees_with_credit_debit_is_refused(env):    # [M2]
    env.client.qty_mode = "abs"
    env.pf.execute_short("SBIN.NS", 5, 940.0, stop_price=950.0)
    with pytest.raises(lm.PositionAmbiguous):
        env.mirror.broker.net_position("SBIN")


def test_signed_quantity_and_cumulative_credit_debit_from_the_probe():          # live probe S6/END
    after_short = {"trading_symbol": "ITC", "quantity": -1, "credit_quantity": 1, "debit_quantity": 2,
                   "product": "MIS", "net_price": 264.4}
    after_cover = {**after_short, "quantity": 0, "credit_quantity": 2}
    assert GrowwBroker._net(after_short) == -1 and GrowwBroker._net(after_cover) == 0


def test_position_sign_disagreement_refuses_to_act(env):                  # [M2]
    t = _buy(env)
    env.client.qty_mode = "disagree"
    env.pf.execute_sell(t.id, 950.0, "exit")
    assert env.client.placed("LIMIT", "SELL") == [] and _leg(env, t)["status"] == "exiting"
    assert any("disagree" in a for a in env.alerts)


# ── Restart / exit-only / liquidate ───────────────────────

def test_restart_mid_entry_resolves_by_reference(env):                    # [M5]
    orig = env.client.place_order

    def crash_after_send(timeout=None, **kw):
        orig(timeout=timeout, **kw)
        raise SystemExit("process killed")
    env.client.place_order = crash_after_send
    with pytest.raises(SystemExit):
        env.mirror.on_entry(NS(id=77, ticker="SBIN.NS", direction="long", quantity=10, entry_price=940.0,
                               stop_price=930.0, target_price=None))
    env.client.place_order = orig
    m2 = env.make()
    assert m2.legs["77"]["status"] == "pending"
    m2.tick()
    assert m2.legs["77"]["status"] == "open" and len(env.client.placed("LIMIT")) == 1


def test_restart_mid_exit_resolves_and_finishes(env):
    t = _buy(env)
    env.client.script = [("after", "open", "timeout")]
    env.pf.execute_sell(t.id, 950.0, "exit")
    m2 = env.make()
    assert m2.legs[str(t.id)]["status"] == "exiting"
    env.client.orders["G3"].update(order_status="EXECUTED", filled_quantity=10, average_fill_price=948.0)
    m2.tick()
    assert m2.legs[str(t.id)]["status"] == "closed" and len(env.client.placed("LIMIT", "SELL")) == 1


def test_open_leg_with_closed_paper_trade_is_exited_on_startup(env):
    t = _buy(env)
    Portfolio(env.pf.db_path, 500_000).execute_sell(t.id, 950.0, "closed while the agent was down")
    db = env.pf.db_path
    m2 = env.make(paper_open_ids=lambda: {str(p.id) for p in Portfolio(db, 0).get_open_positions()})
    m2.tick()
    assert m2.legs[str(t.id)]["status"] == "closed" and env.client.net("SBIN") == 0


def test_runner_builds_exit_only_mirror_when_gates_fail_but_positions_open(env, tmp_path, monkeypatch):   # [M7]
    t = _buy(env)
    monkeypatch.delenv("GROWW_LIVE_SESSIONS", raising=False)
    monkeypatch.setattr(lm, "ledger_has_exposure", lambda d, today=None: True)
    a = TradingAgent.__new__(TradingAgent)
    a.config = AgentConfig(starting_capital=50_000, db_path=str(tmp_path / "r.db"))
    a.market_data = FakeMD()
    a.session = NS(session_id="nse_opus_live", live_broker=None, backtest_mode=False, session_dir=env.tmp / "s")
    p = a._make_portfolio()
    assert isinstance(p, LivePortfolio) and a.live_mirror.exit_only
    assert "exit-only" in a.live_mirror._entry_block("SBIN.NS", 1, 940.0)


def test_runner_builds_live_portfolio_only_when_gated(tmp_path, monkeypatch):
    monkeypatch.setenv("GROWW_LIVE_SESSIONS", "nse_opus_live")
    a = TradingAgent.__new__(TradingAgent)
    a.config = AgentConfig(starting_capital=50_000, db_path=str(tmp_path / "t.db"))
    a.market_data = FakeMD()
    a.session = NS(session_id="nse_opus_live", live_broker="groww", backtest_mode=False, session_dir=tmp_path / "s")
    assert isinstance(a._make_portfolio(), LivePortfolio) and a.live_worker is not None
    a.session = NS(session_id="nse_opus", live_broker="groww", backtest_mode=False, session_dir=tmp_path / "o")
    a.live_mirror = None
    p = a._make_portfolio()
    assert type(p) is Portfolio and a.live_worker is None


def test_dashboard_liquidate_flattens_real_positions(env):                 # [B5]
    t = _buy(env)
    t2 = env.pf.execute_short("ITC.NS", 20, 400.0, stop_price=405.0)
    m = env.make(exit_only=True)
    res = lm.liquidate_from_dashboard(NS(session_dir=env.tmp / "s"), mirror=m, background=False,
                                      sleep=lambda s: None)
    assert res["live"] == "flattened 2 legs"
    assert m.legs[str(t.id)]["status"] == "closed" and m.legs[str(t2.id)]["status"] == "closed"
    assert env.client.net("SBIN") == 0 and env.client.net("ITC") == 0
    assert "manual liquidation" in m.disabled_reason()


def test_dashboard_liquidate_waits_for_the_lock_then_flattens(env):      # [Major 6]
    t = _buy(env)
    assert env.mirror.acquire_lock()                                        # the agent, still exiting
    other = env.make(exit_only=True)
    waits = []

    def sleep(s):
        waits.append(s)
        if len(waits) == 5:
            env.mirror.release_lock()                                       # agent exits
    res = lm.liquidate_from_dashboard(NS(session_dir=env.tmp / "s"), mirror=other, wait_lock_s=60,
                                      background=False, sleep=sleep)
    assert res["live"] == "flattened 1 legs" and other.legs[str(t.id)]["status"] == "closed"
    assert env.client.net("SBIN") == 0


def test_unclaimed_liquidation_request_is_acted_on_by_the_agent(env):
    t = _buy(env)
    lm.request_liquidation(env.tmp / "s", today="2026-10-16")
    env.mirror.tick()
    assert _leg(env, t)["status"] == "closed" and not (env.tmp / "s" / "LIVE_LIQUIDATE").exists()


def test_stale_liquidation_request_from_another_day_is_ignored_and_cleared(env):
    t = _buy(env)
    lm.request_liquidation(env.tmp / "s", today="2026-10-15")
    env.mirror.tick()
    assert _leg(env, t)["status"] == "open" and not (env.tmp / "s" / "LIVE_LIQUIDATE").exists()


def test_process_lock_is_exclusive(env):
    a, b = env.make(), env.make()
    assert a.acquire_lock() and not b.acquire_lock()
    a.release_lock()
    assert b.acquire_lock()
    b.release_lock()


# ── Runner paths through the wrapper ──────────────────────

def _agent(env):
    cfg = AgentConfig(starting_capital=500_000, _market_preset=get_preset("nse-intraday"))
    a = TradingAgent.__new__(TradingAgent)
    a.config, a.preset = cfg, get_preset("nse-intraday")
    a.portfolio = env.pf
    a.risk_manager = RiskManager(cfg, a.portfolio)
    a.learner = NS(write_trade_log=lambda *x, **k: None)
    a.engine = NS(client=None)
    a._auto_exits, a._is_backtest = [], False
    a.live_mirror = a.live_worker = None
    a._seconds_to_forced_close = lambda: None
    return a


def test_between_cycle_stop_sweep_is_mirrored(env):
    a = _agent(env)
    t = _buy(env)
    a.market_data = NS(is_market_open=lambda: True, get_current_prices=lambda tickers=None: {"SBIN.NS": 929.0})
    a.risk_manager.check_intraday_close = lambda now=None: False
    a._monitor_positions()
    assert _leg(env, t)["status"] == "closed"


def test_forced_close_places_all_exits(env):
    a = _agent(env)
    t1 = env.pf.execute_short("SBIN.NS", 5, 940.0, stop_price=950.0)
    t2 = _buy(env, ticker="ITC.NS", qty=10, price=400.0, stop=395.0)
    env.client.script = ["open", "open"]                                     # exits rest at first
    a._close_intraday_positions({"SBIN.NS": 938.0, "ITC.NS": 401.0})
    assert len(env.client.placed("LIMIT", "BUY")) >= 1 and len(env.client.placed("LIMIT", "SELL")) >= 1
    env.later(11)
    env.mirror.tick()
    assert _leg(env, t1)["exit_type"] == "forced_close"
    assert _leg(env, t1)["status"] == "closed" and _leg(env, t2)["status"] == "closed"


def test_forced_close_is_1510_for_the_live_session():
    from agent.session import SessionConfig
    sc = SessionConfig(session_id="nse_opus_live", market="nse-intraday", intraday_close_buffer_min=20)
    sc.resolve_defaults()
    cfg = AgentConfig.from_session(sc)
    rm = RiskManager(cfg, None)
    at = rm.forced_close_time(datetime(2026, 10, 16, 12, 0, tzinfo=IST))
    assert (at.hour, at.minute) == (15, 10)
    sc2 = SessionConfig(session_id="nse_opus", market="nse-intraday")
    sc2.resolve_defaults()
    at2 = RiskManager(AgentConfig.from_session(sc2), None).forced_close_time(datetime(2026, 10, 16, 12, 0, tzinfo=IST))
    assert (at2.hour, at2.minute) == (15, 15)


def test_mis_only_universe_filter_applies_to_the_live_session_only(env, inst):
    a = TradingAgent.__new__(TradingAgent)
    a.live_mirror = env.make(instruments=inst)
    a.session = NS(live_universe_filter="mis_only")
    assert a._apply_live_universe_filter(["SBIN.NS", "SEJALLTD.NS", "ITC.NS"]) == ["SBIN.NS", "ITC.NS"]
    a.session = NS(live_universe_filter=None)
    assert a._apply_live_universe_filter(["SEJALLTD.NS"]) == ["SEJALLTD.NS"]


# ── Worker thread ─────────────────────────────────────────

def test_worker_runs_broker_calls_off_the_agent_thread(env):            # [M8]
    import threading
    threads = []
    orig = env.client.place_order

    def spy(timeout=None, **kw):
        threads.append(threading.current_thread().name)
        return orig(timeout=timeout, **kw)
    env.client.place_order = spy
    w = LiveWorker(env.mirror)
    w.BUSY_TICK_S = w.IDLE_TICK_S = 0.05
    w.start()
    try:
        pf = LivePortfolio(str(env.tmp / "w.db"), 500_000, w)
        t = _buy(env, pf=pf)
        assert w.drain(5)
        assert threads and all(n == "live-mirror" for n in threads)
        assert _leg(env, t)["status"] == "open"
    finally:
        w.stop()


# ── Smoke test ────────────────────────────────────────────

def test_smoke_test_v1_sequence(env):                                           # v1: plain SL
    m = env.make(smoke=False)
    env.client.ltp = 264.45
    r = m.smoke_test("ITC.NS")
    assert r["passed"] and r["market_ok"] and r["oco_ok"] is False, r["reason"]
    assert [kw["order_type"] for kw in env.client.placed()] == ["LIMIT", "SL", "MARKET"]
    sl = env.client.placed("SL")[0]
    assert sl["trigger_price"] == 257.8 and sl["price"] == 255.2                 # 2.5% below, limit 1% beyond
    (mod,) = env.client.ccalls("modify")
    assert mod["price"] is not None and mod["trigger_price"] == 256.5           # both price and trigger
    assert env.client.ccalls("oco_create") == [] and env.client.net("ITC") == 0
    steps = [x["step"] for x in _ledger(env) if x.get("event") == "smoke_step"]
    assert steps == ["lookup_by_reference", "lookup_unknown_reference", "positions_after_buy",
                     "sl_place", "sl_modify", "sl_cancel"]
    sl_id = next(o for o in env.client.orders.values() if o["order_type"] == "SL")
    assert sl_id["order_status"] == "CANCELLED"
    assert env.make(smoke=False).smoke_done_today()


def test_smoke_oco_unsupported_still_passes_with_plain_stop_fallback(env):
    m = env.make(smoke=False)
    env.client.ltp = 400.0
    r = m.smoke_test("ITC.NS")
    assert r["passed"] and r["oco_ok"] is False


def test_smoke_market_rejected_falls_back_to_limit(env):
    m = env.make(smoke=False)
    env.client.ltp = 400.0
    env.client.script = ["fill", "open", "reject", "fill"]                      # buy, SL, MARKET, limit
    r = m.smoke_test("ITC.NS")
    assert r["passed"] and r["market_ok"] is False and env.client.net("ITC") == 0


def test_failed_smoke_buy_disables_the_day(env):
    m = env.make(smoke=False)
    env.client.script = ["reject"]
    r = m.smoke_test("ITC.NS")
    assert not r["passed"] and m.disabled_reason().startswith("smoke test failed")


def test_market_ok_resets_next_day_and_ocos_stay_off(env):
    env.mirror.market_ok = False
    env.clock["now"] = datetime(2026, 10, 19, 10, 0, tzinfo=IST)
    env.mirror._roll_day()
    assert env.mirror.market_ok and env.mirror.oco_ok is False


# ── Circuit limits ────────────────────────────────────────

def test_entry_limit_is_clamped_to_the_upper_circuit(env):
    env.client.upper = 941.0
    _buy(env)
    assert env.client.placed("LIMIT")[0]["price"] == 941.0


def test_long_at_lower_circuit_alerts_cant_exit(env):
    t = _buy(env)
    env.client.lower = env.client.ltp = 900.0
    env.client.script = ["open"]
    env.pf.execute_sell(t.id, 900.0, "stop")
    assert any("lower circuit" in a and "can't exit" in a for a in env.alerts)


# ── Reconcile / report / charges / token ──────────────────

def test_reconcile_alerts_on_mismatch_once(env):
    _buy(env)
    env.client.pos_offset["SBIN"] = -3                     # Groww's position disagrees with our fills
    env.mirror.reconcile(force=True)
    env.mirror.reconcile(force=True)
    assert sum("mismatch" in a for a in env.alerts) == 1


def test_charges_match_hand_computed_schedule():
    assert order_charges("BUY", 10_000) == 12.47
    assert order_charges("SELL", 10_000) == 14.67
    assert order_charges("BUY", 3_000) == 6.10
    assert order_charges("BUY", 50_000) == pytest.approx(20 + 1.485 + 0.05 + 1.5 + 0.05 + 0.18 * 21.535, abs=0.01)


def test_charges_are_per_executed_order_across_split_fills(env):
    t = _buy(env)
    env.client.script = [("partial", 3), "fill"]
    env.client.ltp = 950.0
    env.pf.execute_sell(t.id, 950.0, "exit")
    env.later(11)
    env.mirror.tick()
    leg = _leg(env, t)
    assert leg["status"] == "closed" and len(leg["exit_fills"]) == 2
    expected = order_charges("BUY", 9428.5) + sum(order_charges("SELL", f["qty"] * f["price"])
                                                  for f in leg["exit_fills"])
    assert leg["charges"] == pytest.approx(expected, abs=0.01)


def test_eod_report_compares_paper_and_real_and_lists_failures(env):
    t = _buy(env)
    env.client.script = [("fill", 948.0)]
    env.pf.execute_sell(t.id, 950.0, "exit")
    env.client.script = ["reject"]
    _buy(env, ticker="ITC.NS", qty=10, price=400.0, stop=395.0)
    leg = _leg(env, t)
    assert leg["real_gross"] == pytest.approx((948.0 - 942.85) * 10)
    text = env.mirror.send_eod_report()
    assert "paper ₹+100.00" in text and "+30.3bps" in text and "rejected 1" in text
    assert "FAILURES: not_intraday_allowed 1" in text and "MIS product not allowed" in text


def test_eod_report_survives_missing_fields_and_flags_stuck(env):         # [M9]
    env.mirror.legs["9"] = {"paper_trade_id": "9", "status": "closed", "direction": "long"}
    env.mirror.legs["10"] = {"paper_trade_id": "10", "status": "stuck", "symbol": "SBIN", "direction": "short",
                             "filled_qty": 5, "exit_fills": []}
    text = env.mirror.eod_report()
    assert "NEEDS ATTENTION" in text and "stuck" in text and "#9" in text


def test_token_is_cached_for_the_ist_day_with_0600_perms(tmp_path, monkeypatch):
    calls = []

    class GrowwAPI:
        def __init__(self, token):
            raise AssertionError("constructor fetches the changelog with no timeout; must not be called")

        @staticmethod
        def get_access_token(api_key, totp=None, secret=None):
            calls.append((api_key, totp))
            return f"tok{len(calls)}"
    fake = types.ModuleType("growwapi")
    fake.GrowwAPI = GrowwAPI
    monkeypatch.setitem(sys.modules, "growwapi", fake)
    otp = types.ModuleType("pyotp")
    otp.TOTP = lambda secret: NS(now=lambda: "123456")
    monkeypatch.setitem(sys.modules, "pyotp", otp)
    day = {"now": NOW}
    mk = lambda: GrowwBroker(api_key="K", totp_secret="S", token_path=tmp_path / "tok.json", now=lambda: day["now"])
    assert mk().client.token == "tok1" and mk().client.token == "tok1" and calls == [("K", "123456")]
    assert stat.S_IMODE(os.stat(tmp_path / "tok.json").st_mode) == 0o600
    day["now"] = datetime(2026, 10, 17, 9, 0, tzinfo=IST)
    assert mk().client.token == "tok2"


def test_login_without_keys_is_an_auth_error(tmp_path, monkeypatch):
    from agent.groww_broker import GrowwAuthError
    fake = types.ModuleType("growwapi")
    fake.GrowwAPI = type("GrowwAPI", (), {})
    monkeypatch.setitem(sys.modules, "growwapi", fake)
    with pytest.raises(GrowwAuthError):
        GrowwBroker(api_key="", totp_secret="", token_path=tmp_path / "t.json").client


def test_norm_order_finality():
    assert norm_order({"order_status": "EXECUTED", "quantity": 10, "filled_quantity": 10})["final"]
    assert not norm_order({"order_status": "EXECUTED", "quantity": 10, "filled_quantity": 6})["final"]
    assert norm_order({"order_status": "CANCELLED", "quantity": 10, "filled_quantity": 6})["final"]
    assert not norm_order({"order_status": "CANCELLATION_REQUESTED", "quantity": 10})["final"]


def test_kite_market_data_exposes_tick_size(monkeypatch, tmp_path):
    import agent.kite_data as kd
    from tests.test_watchlist_indicators import FakeKite
    monkeypatch.setattr(kd, "DAILY_CACHE_DIR", tmp_path / "daily")

    class TickKite(FakeKite):
        def instruments(self, exchange):
            return [{"tradingsymbol": "SBIN", "instrument_token": 1, "tick_size": 0.05},
                    {"tradingsymbol": "IDEA", "instrument_token": 2, "tick_size": 0.01}]
    m = kd.KiteMarketData(["SBIN.NS"], kite_client=TickKite({}, {}))
    assert m.tick_size("SBIN.NS") == 0.05 and m.tick_size("IDEA") == 0.01 and m.tick_size("XYZ.NS") is None
    assert round_to_tick(9.876, 0.01, up=True) == 9.88 and round_to_tick(942.82, 0.05, up=True) == 942.85


def test_dashboard_liquidate_skips_paper_only_sessions(tmp_path, monkeypatch):
    import dashboard.app as app
    monkeypatch.delenv("GROWW_LIVE_SESSIONS", raising=False)
    sess = NS(session_id="nse_opus", live_broker=None, backtest_mode=False, session_dir=tmp_path)
    assert app._liquidate_live(sess) is None and app._liquidate_live(None) is None


def _sigterm(env, a):
    import signal
    old = signal.getsignal(signal.SIGTERM)
    try:
        a._start_live()
        handler = signal.getsignal(signal.SIGTERM)
        with pytest.raises(SystemExit):
            handler(signal.SIGTERM, None)
    finally:
        signal.signal(signal.SIGTERM, old)


def _term_agent(env):
    a = TradingAgent.__new__(TradingAgent)
    a.live_worker = LiveWorker(env.mirror, synchronous=True)
    a.session = NS(session_dir=env.tmp / "s")
    return a


def test_sigterm_plain_stop_keeps_positions_protected(env, monkeypatch):     # [Major 6]
    monkeypatch.setattr(lm, "_ist_today", lambda: "2026-10-16")
    t = _buy(env)
    env.mirror.market_ok = False
    env.client.script = ["open"] * 30                                        # exit orders keep resting
    env.pf.execute_sell(t.id, 950.0, "exit")
    assert _leg(env, t)["protect_final"] and env.client.net("SBIN") == 10    # stop cancelled, exit resting
    _sigterm(env, _term_agent(env))
    leg = _leg(env, t)
    assert leg["status"] == "open" and leg["sl_order"] and not leg["protect_final"]   # re-protected
    resting_exits = [o for o in env.client.orders.values()
                     if o["order_type"] == "LIMIT" and o["transaction_type"] == "SELL" and o["order_status"] == "OPEN"]
    assert resting_exits == []                                               # no exit next to the fresh stop
    assert any("Agent stopping" in x and "SBIN long 10" in x for x in env.alerts)


def test_sigterm_with_liquidation_request_flattens(env, monkeypatch):
    monkeypatch.setattr(lm, "_ist_today", lambda: "2026-10-16")
    t = _buy(env)
    lm.request_liquidation(env.tmp / "s", today="2026-10-16")
    _sigterm(env, _term_agent(env))
    assert _leg(env, t)["status"] == "closed" and env.client.net("SBIN") == 0


# ── Re-review blockers (repro2.py / repro3.py on c95397b) ─────────────────

def _no_live_protection(client, sym):
    sl = [o for o in client.orders.values() if o["trading_symbol"] == sym and o["order_type"] in ("SL", "SL_M")
          and o["order_status"] in ("OPEN", "TRIGGER_PENDING")]
    oco = [s for s in client.smart.values() if s["trading_symbol"] == sym and s["status"] == "ACTIVE"]
    return not sl and not oco


def test_unresolved_oco_blocks_closing_until_found_and_cancelled(oco):           # [N1] repro E
    oco.client.oco_script = ["timeout_after"]
    lister = oco.client.get_smart_order_list
    oco.client.get_smart_order_list = lambda **kw: {"smart_orders": []}          # Groww can't show it yet
    t = _buy(oco, stop=930.0, target=960.0)
    assert _leg(oco, t)["protect_kind"] == "oco_unknown"
    oco.pf.execute_sell(t.id, 950.0, "exit")
    for _ in range(5):
        oco.later(5)
        oco.mirror.tick()
    assert oco.client.net("SBIN") == 0 and _leg(oco, t)["status"] == "exiting"   # never "closed" yet
    assert any("unknown" in a for a in oco.alerts)
    oco.client.get_smart_order_list = lister                                     # it shows up
    oco.mirror.tick()
    assert _leg(oco, t)["status"] == "closed" and _no_live_protection(oco.client, "SBIN")


def test_unknown_plain_stop_is_resolved_by_reference_and_cancelled(env):         # [N1] repro F
    env.client.script = ["fill", ("after", "open", "timeout")]
    orig, orig_list = env.client.get_order_status_by_reference, env.client.get_order_list
    boom = lambda **kw: (_ for _ in ()).throw(ConnectionError("reset"))
    env.client.get_order_status_by_reference = boom
    real_place = env.client.place_order

    def place(timeout=None, **kw):                     # the order list goes down right after the stop is sent
        try:
            return real_place(timeout=timeout, **kw)
        finally:
            if kw["order_type"] == "SL":
                env.client.get_order_list = boom
    env.client.place_order = place
    t = _buy(env)
    assert _leg(env, t)["protect_kind"] == "sl_unknown"
    env.client.get_order_status_by_reference, env.client.get_order_list = orig, orig_list
    env.client.place_order = real_place
    env.pf.execute_sell(t.id, 950.0, "exit")
    env.mirror.tick()
    assert _leg(env, t)["status"] == "closed" and _no_live_protection(env.client, "SBIN")
    assert env.client.net("SBIN") == 0


def test_close_sweeps_a_leftover_stop_of_ours_but_not_the_users(env):          # [N1]
    t = _buy(env)
    ghost = env.client.add_manual("SBIN", -10, order_type="SL", status="TRIGGER_PENDING",
                                  ref=env.mirror._ref(t.id, "P", 9))             # ours, forgotten
    users = env.client.add_manual("SBIN", -5, order_type="SL", status="TRIGGER_PENDING", ref="MYOWNSTOP1")
    env.pf.execute_sell(t.id, 950.0, "exit")
    assert _leg(env, t)["status"] == "closed"
    assert env.client.orders[ghost]["order_status"] == "CANCELLED"
    assert env.client.orders[users]["order_status"] == "TRIGGER_PENDING"


def test_periodic_orphan_sweep_cancels_our_stop_on_a_flat_symbol(env):          # [N1]
    t = _buy(env)
    env.pf.execute_sell(t.id, 950.0, "exit")
    ghost = env.client.add_manual("SBIN", -10, order_type="SL", status="TRIGGER_PENDING",
                                  ref=env.mirror._ref(77, "P", 1))
    env.mirror.reconcile(force=True)
    assert env.client.orders[ghost]["order_status"] == "CANCELLED"
    assert any("leftover protective stop" in a for a in env.alerts)


def test_oco_triggered_without_its_order_waits_then_exits(oco):                  # [Major 1] repro G
    t = _buy(oco, stop=930.0, target=960.0)
    oco.client.smart["oco_1"]["status"] = "TRIGGERED"
    oco.client.orders[oco.client.stop_child("oco_1")]["order_status"] = "CANCELLED"   # Groww removed it
    oco.pf.execute_sell(t.id, 950.0, "exit")
    assert oco.client.placed(side="SELL") == [] and _leg(oco, t)["status"] == "exiting"
    for _ in range(4):
        oco.later(6)
        oco.mirror.tick()
    assert _leg(oco, t)["status"] == "closed" and oco.client.net("SBIN") == 0
    assert len(oco.client.placed("LIMIT", "SELL")) == 1


def test_triggered_oco_child_is_never_cancelled_we_wait_for_it(oco):          # live check
    t = _buy(oco, stop=930.0, target=960.0)
    child = oco.client.stop_child("oco_1")
    oco.client.orders[child]["order_status"] = "OPEN"                           # triggered, working
    oco.client.smart["oco_1"]["status"] = "TRIGGERED"
    oco.pf.execute_sell(t.id, 931.0, "stop")
    for _ in range(5):
        oco.later(6)
        oco.mirror.tick()
    assert ("cancel", child) not in oco.client.calls and oco.client.placed(side="SELL") == []
    assert _leg(oco, t)["status"] == "exiting" and any("OCO's own order" in a for a in oco.alerts)
    oco.client.fire_stop(child, 929.0)                                           # it fills
    oco.mirror.tick()
    assert _leg(oco, t)["status"] == "closed" and _leg(oco, t)["exit_fill"] == 929.0
    assert oco.client.net("SBIN") == 0


def test_entry_lookup_lag_is_resolved_from_the_order_list(env):                 # [Major 3] repro H
    env.client.script = [("after", "fill", "timeout")]
    env.client.get_order_status_by_reference = lambda **kw: (_ for _ in ()).throw(GErr("nf", "404"))
    for i in range(30):                                                          # push it to page 2
        env.client.add_manual("ITC", 1 if i % 2 else -1)
    t = _buy(env)
    for _ in range(8):
        env.later(2)
        env.mirror.tick()
    assert _leg(env, t)["status"] == "open" and env.client.net("SBIN") == 10


def test_standby_mirror_drops_events_and_never_touches_the_broker(env):        # [N2] repro I
    t = _buy(env)
    standby = env.make()
    standby.standby = True
    env.client.script = ["open"]
    env.pf.execute_sell(t.id, 950.0, "exit")
    n = len(env.client.calls)
    standby.on_exit(replace(t, exit_price=950.0, exit_type="manual"))
    standby.on_levels(t)
    standby.on_entry(t)
    assert len(env.client.calls) == n
    env.client.orders["G3"].update(order_status="EXECUTED", filled_quantity=10, average_fill_price=949.0)
    env.mirror.tick()
    assert env.client.net("SBIN") == 0


def test_unstarted_worker_drops_events(env):                                     # [N2]
    w = LiveWorker(env.mirror)
    pf = LivePortfolio(str(env.tmp / "u.db"), 500_000, w)
    _buy(env, pf=pf)
    assert env.client.calls == []


def test_runner_never_takes_the_lock_outside_the_loop(tmp_path, monkeypatch):    # [N2]
    monkeypatch.setenv("GROWW_LIVE_SESSIONS", "nse_opus_live")
    a = TradingAgent.__new__(TradingAgent)
    a.config = AgentConfig(starting_capital=50_000, db_path=str(tmp_path / "t.db"))
    a.market_data = FakeMD()
    a.session = NS(session_id="nse_opus_live", live_broker="groww", backtest_mode=False, session_dir=tmp_path / "s")
    a._make_portfolio()
    assert a.live_worker.started is False
    probe = LiveMirror(None, "nse_opus_live", tmp_path / "s", alert=lambda s: None)
    assert probe.acquire_lock()
    probe.release_lock()


class Inverted(FakeGrowwClient):
    """credit = sold / debit = bought, quantity unsigned (a plausible misreading)."""

    def get_positions_for_user(self, segment=None, timeout=None):
        rows = super().get_positions_for_user(segment)["positions"]
        for r in rows:
            r["credit_quantity"], r["debit_quantity"] = r["debit_quantity"], r["credit_quantity"]
            r["quantity"] = abs(r["quantity"])
        return {"positions": rows}


def test_inverted_position_semantics_fail_the_smoke_test_and_still_sell(tmp_path):   # [N3] repro3
    c = Inverted(ltp=400.0, oco=True)
    alerts, clk = [], {"t": 0.0}
    m = LiveMirror(GrowwBroker(client=c, token_path=tmp_path / "tok"), "nse_opus_live", tmp_path,
                   FakeMD({"ITC.NS": 400.0}), alert=alerts.append, now=lambda: NOW,
                   sleep=lambda s: clk.__setitem__("t", clk["t"] + s), monotonic=lambda: clk["t"])
    r = m.smoke_test("ITC.NS")
    assert not r["passed"] and "position semantics untrusted" in r["reason"]
    assert c.net("ITC") == 0 and m.disabled_reason()
    t = NS(id=11, ticker="ITC.NS", direction="long", quantity=10, entry_price=400.0, stop_price=395.0,
           target_price=410.0, exit_price=400.0, exit_type="x")
    m.on_entry(t)
    assert m.legs["11"]["status"] == "skipped" and c.net("ITC") == 0


def test_position_lag_after_exit_fill_never_double_exits(env):                 # [N3]
    t = _buy(env)
    orig = env.client.place_order

    def lagging(timeout=None, **kw):
        r = orig(timeout=timeout, **kw)
        if kw["transaction_type"] == "SELL" and kw["order_type"] == "LIMIT":
            env.client.pos_offset["SBIN"] = 10                               # positions still show +10
        return r
    env.client.place_order = lagging
    env.pf.execute_sell(t.id, 950.0, "exit")
    for _ in range(3):
        env.later(3)
        env.mirror.tick()
    assert len(env.client.placed("LIMIT", "SELL")) == 1 and _leg(env, t)["status"] == "exiting"
    env.client.pos_offset.clear()
    env.mirror.tick()
    assert _leg(env, t)["status"] == "closed" and env.client.net("SBIN") == 0


def test_disagreement_with_flat_positions_goes_stuck_but_places_nothing(env):   # [N3 + Major 1]
    t = _buy(env)
    env.mirror.market_ok = False
    env.client.script = ["open"] * 10
    env.pf.execute_sell(t.id, 950.0, "exit")
    env.client.pos_offset["SBIN"] = -10                                      # positions flat, fills +10
    for _ in range(6):
        env.later(6)
        env.mirror.tick()
    leg = _leg(env, t)
    assert leg["status"] == "stuck" and "positions untrusted" in env.mirror.disabled_reason()
    assert len(env.client.placed("SL")) == 1                                 # no blind re-protection
    assert any("not re-protecting" in a for a in env.alerts)


def test_persistent_disagreement_goes_stuck_reprotects_and_disables(env):      # [N3]
    t = _buy(env)
    env.mirror.market_ok = False
    env.client.script = ["open"] * 10                                        # exits rest
    env.pf.execute_sell(t.id, 950.0, "exit")                                 # stop cancelled, exit resting
    env.client.pos_offset["SBIN"] = 5                                        # positions 15, fills 10
    for _ in range(6):
        env.later(6)
        env.mirror.tick()
    leg = _leg(env, t)
    assert leg["status"] == "stuck" and leg["stuck_reason"] == "positions"
    assert leg["sl_order"] and not leg["protect_final"]                      # re-protected
    assert "positions untrusted" in env.mirror.disabled_reason()
    assert env.client.net("SBIN") == 10 and len(env.client.placed("SL")) == 2
    env.client.pos_offset.clear()                                            # reads agree again
    env.client.script = []
    for _ in range(3):
        env.later(11)
        env.mirror.tick()
    assert _leg(env, t)["status"] == "closed" and env.client.net("SBIN") == 0


def test_position_read_failure_keeps_protection(env):                          # [Major 2]
    t = _buy(env)
    env.client.get_positions_for_user = lambda **kw: (_ for _ in ()).throw(ConnectionError("reset"))
    env.pf.execute_sell(t.id, 950.0, "exit")
    assert ("cancel", "G2") not in env.client.calls and _leg(env, t)["status"] == "exiting"
    assert env.client.orders["G2"]["order_status"] == "TRIGGER_PENDING"


@pytest.mark.parametrize("exc,amb", [
    (TimeoutError(), True), (OSError("ssl"), True), (ConnectionError(), True),
    (GErr("internal", "GA000"), True), (GErr("internal", "GA003"), True), (GErr("bad gw", "502"), True),
    (json.JSONDecodeError("x", "y", 0), True), (KeyError("payload"), True),
    (GErr("Insufficient margin", "GA001"), False), (GErr("bad request", "400"), False),
    (GErr("too many", "429"), False), (AttributeError("no method"), False),
])
def test_only_explicit_rejections_are_definitive(exc, amb):                    # [N4]
    assert is_ambiguous(exc) is amb


def test_internal_error_after_accept_is_looked_up_not_retried(env):            # [N4]
    t = _buy(env)
    orig = env.client.place_order

    def internal(timeout=None, **kw):
        orig(timeout=timeout, **kw)
        raise GErr("Something went wrong", "GA000")
    env.client.place_order = internal
    env.pf.execute_sell(t.id, 950.0, "exit")
    assert len(env.client.placed("LIMIT", "SELL")) == 1
    assert _leg(env, t)["status"] == "closed" and env.client.net("SBIN") == 0


def test_triggers_are_tick_rounded_toward_safety(oco):                          # [Major 4]
    _buy(oco, stop=930.03, target=960.02)
    kw = oco.client.ccalls("oco_create")[0]
    assert kw["stop_loss"]["trigger_price"] == "930.00" and kw["target"]["trigger_price"] == "960.05"
    oco.pf.execute_short("SBIN.NS", 5, 940.0, stop_price=950.02, target_price=920.04)
    kw = oco.client.ccalls("oco_create")[1]
    assert kw["stop_loss"]["trigger_price"] == "950.05" and kw["target"]["trigger_price"] == "920.00"


def test_smoke_never_touches_ocos_even_if_enabled(oco):
    m = oco.make(smoke=False)
    oco.client.ltp = 400.0
    r = m.smoke_test("ITC.NS")
    assert r["passed"] and oco.client.ccalls("oco_create") == [] and oco.client.ccalls("oco_modify") == []


def test_smoke_validates_reference_lookup_and_fill_units(env):                  # [Major 5]
    m = env.make(smoke=False)
    env.client.ltp = 400.0
    env.client.get_order_status_by_reference = lambda **kw: {"order_status": "EXECUTED"}   # no id
    r = m.smoke_test("ITC.NS")
    assert not r["passed"] and "groww_order_id" in r["reason"] and env.client.net("ITC") == 0


def test_smoke_flags_fill_price_in_wrong_units(env):
    m = env.make(smoke=False)
    env.client.ltp = 400.0
    env.client.script = [("fill", 40000.0)]                                     # paise?
    r = m.smoke_test("ITC.NS")
    assert not r["passed"] and "units" in r["reason"]


def test_smoke_fails_when_the_sl_modify_does_not_verify(env):
    m = env.make(smoke=False)
    env.client.ltp = 400.0
    env.client.modify_mode = "fail"                                             # → replace path
    r = m.smoke_test("ITC.NS")
    assert not r["passed"] and "didn't verify" in r["reason"] and env.client.net("ITC") == 0
    assert len(env.client.placed("SL")) == 2                                    # cancelled + replaced


def test_smoke_buy_that_fills_late_is_sold(env):                                # [minor]
    m = env.make(smoke=False)
    env.client.ltp = 400.0
    env.client.script = ["open"]
    env.client.cancel_mode = "stuck"
    r = m.smoke_test("ITC.NS")
    assert not r["passed"]
    env.client.cancel_mode = "ok"
    buy = env.client.placed("LIMIT", "BUY")[0]["order_reference_id"]
    o = next(o for o in env.client.orders.values() if o["order_reference_id"] == buy)
    o.update(order_status="EXECUTED", filled_quantity=1, average_fill_price=400.5)
    for _ in range(3):
        env.later(11)
        m.tick()
    assert env.client.net("ITC") == 0


def test_orders_are_counted_once_on_replay(env):                                # [minor]
    _buy(env)
    assert env.make()._orders_today == len(env.client.placed()) == env.mirror._orders_today


def test_oco_fill_attribution_ignores_older_and_manual_orders(oco):            # [minor]
    t = _buy(oco, stop=930.0, target=960.0)
    oco.client.add_manual("SBIN", -10, price=900.0)                             # user's sell, not an OCO leg
    oco.client.fire_oco("oco_1", "target", price=960.4)
    oco.pf.execute_sell(t.id, 960.0, "target")
    leg = _leg(oco, t)
    assert leg["exit_fill"] == 960.4 and leg["real_pnl"] is not None


def test_oco_target_fill_is_attributed_and_users_orders_ignored(oco):         # [minor]
    t = _buy(oco, stop=930.0, target=960.0)
    oco.client.fire_oco("oco_1", "target", price=960.4)
    oco.client.add_manual("SBIN", -10, price=961.0)                             # user's own round trip
    oco.client.add_manual("SBIN", 10, price=961.0)
    oco.pf.execute_sell(t.id, 960.0, "target")
    leg = _leg(oco, t)
    assert leg["status"] == "closed" and leg["exit_fill"] == 960.4 and len(leg["exit_fills"]) == 1
    assert oco.client.placed(side="SELL") == []


def test_entry_for_an_already_closed_paper_trade_is_dropped(env):              # [minor]
    m = env.make(paper_open_ids=lambda: set())
    m.on_entry(NS(id=5, ticker="SBIN.NS", direction="long", quantity=1, entry_price=940.0,
                  stop_price=930.0, target_price=None))
    assert m.legs["5"]["status"] == "skipped" and env.client.calls == []


def test_worker_stop_keeps_the_lock_while_a_call_is_running(env):              # [minor]
    import threading
    w = LiveWorker(env.mirror)
    assert env.mirror.acquire_lock()
    gate = threading.Event()
    w._thread = threading.Thread(target=gate.wait, daemon=True)
    w._thread.start()
    w.stop(join_s=0.05)
    assert env.mirror._lock_fh is not None                                      # still held
    gate.set()
    w._thread.join()
    env.mirror.release_lock()


def test_drain_waits_for_the_running_event(env):                                # [minor]
    import time as _t
    done = []
    env.mirror.slow = lambda: (_t.sleep(0.2), done.append(1))
    w = LiveWorker(env.mirror)
    w.BUSY_TICK_S = w.IDLE_TICK_S = 0.05
    w.start()
    try:
        w.submit("slow")
        assert w.drain(5) and done == [1]
    finally:
        w.stop()


def test_instrument_download_happens_outside_the_lock(tmp_path):                # [minor]
    seen = []
    holder = {}

    def fetch():
        seen.append(holder["i"]._lock.locked())
        return CSV
    holder["i"] = GrowwInstruments(path=tmp_path / "i.csv", fetch=fetch, now=lambda: NOW)
    assert holder["i"].load() and seen == [False]


def test_duplicate_csv_symbols_prefer_series_eq(tmp_path):                       # [minor]
    csv_text = CSV + "NSE,8,DUPE,NSE-D1,D,EQ,CASH,BE,X,0.01,0,0,1,1,DUPE,0\n" \
                     "NSE,9,DUPE,NSE-D2,D,EQ,CASH,EQ,X,0.05,0,0,1,1,DUPE,1\n"
    i = GrowwInstruments(path=tmp_path / "d.csv", fetch=lambda: csv_text, now=lambda: NOW)
    assert i.info("DUPE")["is_intraday"] is True and i.info("DUPE")["tick_size"] == 0.05


def test_login_timeout_never_uses_a_late_token(tmp_path, monkeypatch):          # [minor]
    import time as _t
    import agent.groww_broker as gb
    from agent.groww_broker import GrowwAuthError

    class GrowwAPI:
        @staticmethod
        def get_access_token(api_key, totp=None, secret=None):
            _t.sleep(0.3)
            return "late"
    fake = types.ModuleType("growwapi")
    fake.GrowwAPI = GrowwAPI
    monkeypatch.setitem(sys.modules, "growwapi", fake)
    otp = types.ModuleType("pyotp")
    otp.TOTP = lambda secret: NS(now=lambda: "123456")
    monkeypatch.setitem(sys.modules, "pyotp", otp)
    monkeypatch.setattr(gb, "LOGIN_TIMEOUT_S", 0.05)
    b = GrowwBroker(api_key="K", totp_secret="S", token_path=tmp_path / "tok.json", now=lambda: NOW)
    with pytest.raises(GrowwAuthError, match="timed out"):
        b.client
    _t.sleep(0.4)
    assert not (tmp_path / "tok.json").exists()


def test_order_list_pages_until_nothing_new(env):                               # [minor]
    for i in range(60):
        env.client.add_manual("ITC", 1)
    assert len(env.mirror.broker.order_list()) == 60
    assert env.mirror.broker.orders_net("ITC") == 60


# ── Round 3 (repro4.py on a636bc9) ─────────────────────────

def _ga004(client):
    def lookup(segment=None, order_reference_id=None, timeout=None):
        for o in client.orders.values():
            if o.get("order_reference_id") == order_reference_id:
                return {"groww_order_id": o["groww_order_id"]}
        raise GErr("Order not found", code="GA004")
    client.get_order_status_by_reference = lookup


def test_ga004_not_found_lets_a_never_placed_exit_move_on(env):                 # [P] repro4 P
    _ga004(env.client)
    t = _buy(env)
    env.client.script = ["timeout_before"]
    env.pf.execute_sell(t.id, 950.0, "exit")
    for _ in range(25):
        env.later(2)
        env.mirror.tick()
    leg = _leg(env, t)
    assert leg["status"] == "closed" and env.client.net("SBIN") == 0
    assert leg["exit_attempts"][0]["final"] and "never placed" in leg["exit_attempts"][0]["note"]


@pytest.mark.parametrize("exc", [GErr("Order not found", "GA004"), GErr("x", "404"),
                                 GErr("No order exists with this reference", "GA001")])
def test_not_found_variants_read_as_missing(env, exc):
    env.client.get_order_status_by_reference = lambda **kw: (_ for _ in ()).throw(exc)
    assert env.mirror.broker.order_by_reference("261016-1-X9") is None


def test_lookup_error_falls_back_to_the_order_list(env):                        # [P b]
    t = _buy(env)
    env.client.get_order_status_by_reference = lambda **kw: (_ for _ in ()).throw(ConnectionError("reset"))
    env.client.script = [("after", "fill", "timeout")]
    env.pf.execute_sell(t.id, 950.0, "exit")
    assert _leg(env, t)["status"] == "closed" and len(env.client.placed("LIMIT", "SELL")) == 1


def test_unresolvable_exit_reprotects_after_30s(env):                            # [P d]
    t = _buy(env)
    env.client.get_order_status_by_reference = lambda **kw: (_ for _ in ()).throw(ConnectionError("reset"))
    env.client.script = ["timeout_before"]
    env.pf.execute_sell(t.id, 950.0, "exit")                                   # stop cancelled, exit lost
    for _ in range(2):
        env.later(10)
        env.mirror.tick()
    assert len(env.client.placed("SL")) == 1                                    # < 30s: still waiting
    for _ in range(2):
        env.later(10)
        env.mirror.tick()
    assert any("unresolved" in a for a in env.alerts)
    assert len(env.client.placed("SL")) == 2 and env.client.placed("SL")[-1]["quantity"] == 10
    assert len(env.client.placed("LIMIT", "SELL")) == 1                          # no blind second exit


def test_deadline_fires_even_while_an_exit_is_unresolved(env):                  # [P d]
    t = _buy(env)
    env.client.get_order_status_by_reference = lambda **kw: (_ for _ in ()).throw(ConnectionError("reset"))
    env.client.script = ["timeout_before"]
    env.pf.execute_sell(t.id, 950.0, "exit")
    env.clock["now"] = NOW.replace(hour=15, minute=19, second=10)
    env.mirror.tick()
    assert _leg(env, t)["status"] == "stuck" and any("15:19" in a for a in env.alerts)


def test_smoke_unknown_reference_lookup_must_be_not_found(env):                  # [P c]
    m = env.make(smoke=False)
    env.client.ltp = 400.0
    real = env.client.get_order_status_by_reference

    def lookup(segment=None, order_reference_id=None, timeout=None):
        if order_reference_id.endswith("-Z1"):
            raise GErr("Something went wrong", "GA000")
        return real(segment=segment, order_reference_id=order_reference_id)
    env.client.get_order_status_by_reference = lookup
    r = m.smoke_test("ITC.NS")
    assert not r["passed"] and "lookup semantics untrusted" in r["reason"] and env.client.net("ITC") == 0


def test_smoke_passes_with_ga004_not_found(oco):
    _ga004(oco.client)
    m = oco.make(smoke=False)
    oco.client.ltp = 400.0
    assert m.smoke_test("ITC.NS")["passed"]


def test_oco_fired_child_missing_from_order_list_closes_without_reprotect(oco):  # [Major 1] repro4 Q
    t = _buy(oco, stop=930.0, target=960.0)
    oco.client.smart["oco_1"]["status"] = "COMPLETED"
    oco.client.orders.pop(oco.client.stop_child("oco_1"))                      # child filled, not listed
    oco.client.pos_offset["SBIN"] = -10
    oco.pf.execute_sell(t.id, 950.0, "exit")
    assert _leg(oco, t)["status"] == "exiting"                                 # one flat read is not enough
    oco.later(2)
    oco.mirror.tick()
    leg = _leg(oco, t)
    assert leg["status"] == "closed" and leg["closed_by_oco_unconfirmed"] and leg["real_pnl"] is None
    assert len(oco.client.ccalls("oco_create")) == 1 and oco.client.placed(side="SELL") == []
    assert oco.mirror.disabled_reason() is None


def test_oco_firing_during_cancel_closes_without_reprotect(oco):                 # [Major 1] repro4 Q2
    t = _buy(oco, stop=930.0, target=960.0)

    def fire_on_cancel(segment=None, smart_order_type=None, smart_order_id=None, timeout=None):
        oco.client.smart[smart_order_id]["status"] = "COMPLETED"
        oco.client.orders.pop(oco.client.stop_child(smart_order_id), None)
        oco.client.pos_offset["SBIN"] = -10
        return {"smart_order_id": smart_order_id, "status": "COMPLETED"}
    oco.client.cancel_smart_order = fire_on_cancel
    oco.pf.execute_sell(t.id, 931.0, "stop")
    for _ in range(5):
        oco.later(4)
        oco.mirror.tick()
    leg = _leg(oco, t)
    assert leg["status"] == "closed" and len(oco.client.ccalls("oco_create")) == 1
    assert oco.client.placed(side="SELL") == [] and oco.client.net("SBIN") + oco.client.pos_offset["SBIN"] == 0


def test_shutdown_reprotects_only_what_groww_shows(env, monkeypatch):            # [Major 1]
    monkeypatch.setattr(lm, "_ist_today", lambda: "2026-10-16")
    t = _buy(env)
    env.mirror.market_ok = False
    env.client.script = ["open"] * 30
    env.pf.execute_sell(t.id, 950.0, "exit")
    env.client.pos_offset["SBIN"] = -6                                           # Groww shows only 4
    env.mirror.prepare_shutdown(budget_s=5)
    assert env.client.placed("SL")[-1]["quantity"] == 4


def test_shutdown_places_nothing_when_groww_is_flat(env):
    t = _buy(env)
    env.mirror.market_ok = False
    env.client.script = ["open"] * 30
    env.pf.execute_sell(t.id, 950.0, "exit")
    env.client.pos_offset["SBIN"] = -10                                          # Groww shows flat
    env.mirror.prepare_shutdown(budget_s=5)
    assert len(env.client.placed("SL")) == 1


def test_smoke_fails_when_the_protective_sl_is_rejected(env):
    m = env.make(smoke=False)
    env.client.ltp = 400.0
    env.client.script = ["fill", ("reject_async", "RMS:Rule: Assigned basket  for entity account-X")]
    r = m.smoke_test("ITC.NS")
    assert not r["passed"] and "protective SL not accepted [not_intraday_allowed]" in r["reason"]
    assert env.client.net("ITC") == 0


def test_smoke_requires_order_type_labels(env):                                 # [Major 2]
    m = env.make(smoke=False)
    env.client.ltp = 400.0
    real = env.client.get_order_list
    env.client.get_order_list = lambda **kw: {"order_list": [{**r, "order_type": "L"} for r in real(**kw)["order_list"]]}
    r = m.smoke_test("ITC.NS")
    assert not r["passed"] and "order_type" in r["reason"]


def test_force_plain_sl_switch(oco, monkeypatch):                                # [Major 2]
    monkeypatch.setenv("GROWW_FORCE_PLAIN_SL", "1")
    m = oco.make()
    pf = LivePortfolio(str(oco.tmp / "f.db"), 500_000, m)
    _buy(oco, stop=930.0, target=960.0, pf=pf)
    assert oco.client.ccalls("oco_create") == [] and len(oco.client.placed("SL")) == 1
    m2 = oco.make(smoke=False)
    oco.client.ltp = 400.0
    r = m2.smoke_test("ITC.NS")
    assert r["oco_ok"] is False and oco.client.ccalls("oco_create") == []


def test_triggered_oco_never_cancels_the_users_order(oco):                       # [minor]
    t = _buy(oco, stop=930.0, target=960.0)
    users = oco.client.add_manual("SBIN", -10, order_type="LIMIT", status="OPEN")   # user's resting sell
    oco.client.smart["oco_1"]["status"] = "TRIGGERED"
    oco.pf.execute_sell(t.id, 950.0, "exit")
    for _ in range(4):
        oco.later(6)
        oco.mirror.tick()
    assert ("cancel", users) not in oco.client.calls
    assert oco.client.orders[users]["order_status"] == "OPEN"


def test_sweep_spares_another_legs_inflight_oco_child(oco):                      # [minor]
    a = _buy(oco, stop=930.0, target=960.0)
    b = _buy(oco, stop=931.0, target=961.0, qty=5)
    pref = _leg(oco, b)["protect_ref"]
    kid = oco.client.add_manual("SBIN", -5, order_type="SL_M", status="OPEN", ref=pref + "C")
    oco.pf.execute_sell(a.id, 950.0, "exit")
    assert _leg(oco, a)["status"] == "closed"
    assert oco.client.orders[kid]["order_status"] == "OPEN"


def test_flat_reads_must_be_two_seconds_apart(oco):                              # final review
    t = _buy(oco, stop=930.0, target=960.0)
    oco.client.smart["oco_1"]["status"] = "COMPLETED"
    oco.client.orders.pop(oco.client.stop_child("oco_1"))
    oco.client.pos_offset["SBIN"] = -10
    oco.pf.execute_sell(t.id, 950.0, "exit")
    for _ in range(3):
        oco.later(0.5)                                                          # reads 0.5s apart
        oco.mirror.tick()
    assert _leg(oco, t)["status"] == "exiting"
    oco.later(1)                                                                # now ≥2s after the first
    oco.mirror.tick()
    assert _leg(oco, t)["status"] == "closed" and _leg(oco, t)["closed_by_oco_unconfirmed"]


def test_a_non_flat_read_resets_the_flat_confirmation(oco):
    t = _buy(oco, stop=930.0, target=960.0)
    oco.client.smart["oco_1"]["status"] = "COMPLETED"
    oco.client.orders.pop(oco.client.stop_child("oco_1"))
    oco.client.pos_offset["SBIN"] = -10
    oco.pf.execute_sell(t.id, 950.0, "exit")
    oco.client.pos_offset["SBIN"] = -5                                          # positions show 5 again
    oco.later(3)
    oco.mirror.tick()
    assert _leg(oco, t)["status"] in ("exiting", "stuck") and "_flat_n" not in _leg(oco, t)


# ── Real Groww payloads from the read-only live check (2026-10-15), ids anonymised ──

REAL_POSITION_BHEL = {"trading_symbol": "BHEL", "segment": "CASH", "credit_quantity": 0, "credit_price": 0.0,
                      "debit_quantity": 200, "debit_price": 444.0, "carry_forward_credit_quantity": 0,
                      "carry_forward_credit_price": 0.0, "carry_forward_debit_quantity": 0,
                      "carry_forward_debit_price": 0.0, "exchange": "NSE", "symbol_isin": "INE257A01026",
                      "quantity": -200, "product": "MIS", "net_carry_forward_quantity": 0, "net_price": 444.0,
                      "net_carry_forward_price": 0.0, "realised_pnl": 0.0}
REAL_ORDER_ROW = {"groww_order_id": "GMK0000000000000001", "trading_symbol": "BHEL", "isin": "INE257A01026",
                  "order_status": "TRIGGER_PENDING", "quantity": 200, "price": 0.0, "trigger_price": 456.0,
                  "filled_quantity": 0, "remaining_quantity": 200, "average_fill_price": 0.0, "amo_status": "NA",
                  "validity": "DAY", "exchange": "NSE", "order_type": "SL_M", "transaction_type": "BUY",
                  "segment": "CASH", "product": "MIS", "created_at": "2026-10-15T10:05:20.44",
                  "exchange_time": "2026-10-15T10:05:20", "trade_date": "2026-10-15",
                  "order_reference_id": "MISOCO_MIS_CASH_OCO_STOP_LOSS-" + "a" * 30, "remark": ""}
REAL_PRIMARY_ROW = {**REAL_ORDER_ROW, "groww_order_id": "GMK0000000000000000", "order_status": "EXECUTED",
                    "order_type": "MARKET", "transaction_type": "SELL", "trigger_price": 0.0,
                    "filled_quantity": 200, "remaining_quantity": 0, "average_fill_price": 444.0,
                    "created_at": "2026-10-15T10:05:20.517",
                    "order_reference_id": "MISOCO_MIS_CASH_OCO_PRIMARY-" + "b" * 30}
REAL_SMART_ORDER = {"smart_order_id": "VRay28rN0g" + "c" * 20, "trading_symbol": "BHEL", "exchange": "NSE",
                    "quantity": 200, "product_type": "MIS", "duration": "DAY", "status": "ACTIVE",
                    "is_cancellation_allowed": True, "is_modification_allowed": True,
                    "cancellation_not_allowed_reason": None, "modification_not_allowed_reason": None,
                    "created_at": "2026-10-15T10:05:20.49", "expire_at": "2026-10-15T15:30:00",
                    "triggered_at": None, "updated_at": "2026-10-15T10:05:20.517",
                    "target": {"price": None, "triggerPrice": "430.00", "orderType": None},
                    "stop_loss": {"price": None, "triggerPrice": "451.40", "orderType": None},
                    "smart_order_type": "OCO"}
REAL_MARGIN = {"clear_cash": 50210.5, "net_margin_used": 17760.0, "brokerage_and_charges": 0.0,
               "equity_margin_details": {"mis_balance_available": 32450.5, "cnc_balance_available": 32450.5,
                                         "mis_margin_used": 17760.0, "net_equity_margin_used": 17760.0},
               "fno_margin_details": {}, "commodity_margin_details": {}}


def test_real_position_payload_short_is_negative():
    assert GrowwBroker._net(REAL_POSITION_BHEL) == -200


def test_real_order_row_parses():
    o = norm_order(REAL_ORDER_ROW)
    assert o["avg_price"] is None and not o["final"] and o["order_type"] == "SL_M" and o["product"] == "MIS"
    from agent.groww_broker import parse_ts
    t = parse_ts(o["created_at"])
    assert (t.hour, t.minute, t.microsecond, t.utcoffset().total_seconds()) == (10, 5, 440000, 19800)


def test_real_smart_order_list_shape():
    from agent.groww_broker import norm_smart_order
    c = FakeGrowwClient()
    c.get_smart_order_list = lambda **kw: {"orders": [REAL_SMART_ORDER]}
    (r,) = GrowwBroker(client=c).smart_orders("BHEL")
    assert r["id"] == REAL_SMART_ORDER["smart_order_id"] and r["product"] == "MIS" and r["status"] == "ACTIVE"
    assert (r["target_trigger"], r["stop_trigger"]) == ("430.00", "451.40")
    assert norm_smart_order({"target": {"trigger_price": "1.5"}})["target_trigger"] == "1.50"   # either case


def test_real_margin_payload():
    c = FakeGrowwClient()
    c.get_available_margin_details = lambda timeout=None: REAL_MARGIN
    assert GrowwBroker(client=c).mis_balance_available() == 32450.5


def test_real_ga004_not_found_messages():
    from agent.groww_broker import is_not_found
    assert is_not_found(GErr("Order status is available only for orders placed today. Please check your "
                             "order reference id.", "GA004"))
    assert is_not_found(GErr("Order details are available only for orders placed today.", "GA004"))


def _bhel_manual_short(client):
    """The user's own BHEL MIS short 200 with its own OCO (as seen live)."""
    client.orders[REAL_PRIMARY_ROW["groww_order_id"]] = dict(REAL_PRIMARY_ROW)
    client.orders[REAL_ORDER_ROW["groww_order_id"]] = dict(REAL_ORDER_ROW)
    client.smart[REAL_SMART_ORDER["smart_order_id"]] = {**REAL_SMART_ORDER, "_side": "BUY",
                                                         "_stop_child": REAL_ORDER_ROW["groww_order_id"]}


def test_manual_bhel_short_blocks_mirror_entries_in_bhel_only(oco, inst):       # live check 4c
    _bhel_manual_short(oco.client)
    m = oco.make()
    pf = LivePortfolio(str(oco.tmp / "b.db"), 500_000, m)
    t = pf.execute_buy("BHEL.NS", 10, 445.0, "intraday", stop_price=440.0, target_price=455.0)
    leg = m.legs[str(t.id)]
    assert leg["status"] == "blocked" and leg["category"] == "manual_position_conflict"
    assert any("manual_position_conflict" in a for a in oco.alerts)
    assert oco.client.placed() == []
    t2 = _buy(oco, stop=930.0, target=960.0, pf=pf)                              # SBIN is fine
    assert m.legs[str(t2.id)]["status"] == "open"


def test_users_oco_and_child_are_never_touched(oco):                              # live check 4a
    _bhel_manual_short(oco.client)
    t = _buy(oco, stop=930.0, target=960.0)
    oco.pf.execute_sell(t.id, 950.0, "exit")
    oco.mirror.reconcile(force=True)
    sid = REAL_SMART_ORDER["smart_order_id"]
    assert oco.client.smart[sid]["status"] == "ACTIVE"
    assert oco.client.orders[REAL_ORDER_ROW["groww_order_id"]]["order_status"] == "TRIGGER_PENDING"
    assert all(c[1] != sid for c in oco.client.calls if c[0] == "oco_cancel")
    assert _leg(oco, t)["status"] == "closed"


def test_users_oco_in_our_symbol_is_not_ours_to_sweep(oco):                     # live check 2
    t = _buy(oco, stop=930.0, target=960.0)
    user_oco = {**REAL_SMART_ORDER, "smart_order_id": "USEROCO" + "d" * 23, "trading_symbol": "SBIN",
                "quantity": 10, "_side": "SELL", "_stop_child": None,
                "target": {"price": None, "triggerPrice": "960.00", "orderType": None},
                "stop_loss": {"price": None, "triggerPrice": "930.00", "orderType": None}}
    oco.client.smart[user_oco["smart_order_id"]] = user_oco                       # same fingerprint even
    oco.pf.execute_sell(t.id, 950.0, "exit")
    oco.mirror.reconcile(force=True)
    assert oco.client.smart[user_oco["smart_order_id"]]["status"] == "ACTIVE"


def test_ambiguous_oco_with_two_fingerprint_matches_stays_unknown(oco):          # live check 2
    oco.client.oco_script = ["timeout_after"]
    real_create = oco.client.create_smart_order

    def create_twice(timeout=None, **kw):                                        # a twin appears too
        try:
            real_create(timeout=timeout, **kw)
        except TimeoutError:
            pass
        oco.client.oco_script = ["timeout_after"]
        return real_create(timeout=timeout, **kw)
    oco.client.create_smart_order = create_twice
    t = _buy(oco, stop=930.0, target=960.0)
    assert _leg(oco, t)["protect_kind"] == "oco_unknown"
    assert any("can't tell which" in a for a in oco.alerts)


def test_entry_limited_by_mis_balance(env):                                      # live check 4d
    env.client.mis_balance = 20_000.0
    t = _buy(env)                                                                # ₹9,400 fits
    assert _leg(env, t)["status"] == "open"
    t2 = _buy(env, qty=25)                                                       # ₹23,500 > ₹20,000
    assert _leg(env, t2)["status"] == "skipped" and "MIS balance" in _leg(env, t2)["note"]


def test_oco_stop_child_with_a_different_trigger_is_still_ours(oco):            # live check 3
    t = _buy(oco, stop=930.0, target=960.0)
    child = oco.client.stop_child("oco_1")
    assert oco.client.orders[child]["trigger_price"] != 930.0                    # as seen live
    view = oco.mirror._position_view("SBIN")
    assert view["net"] == 10 and view["orders_net"] == 10 and not view["foreign"]
    oco.client.fire_stop(child, 929.0)
    oco.pf.execute_sell(t.id, 929.0, "stop")
    assert _leg(oco, t)["status"] == "closed" and _leg(oco, t)["exit_fill"] == 929.0


def test_owned_net_excludes_manual_fills(env):                                   # live check 4b
    t = _buy(env)
    env.client.add_manual("SBIN", -25)
    view = env.mirror._position_view("SBIN")
    assert view["raw"] == -15 and view["net"] == 10 and view["orders_net"] == 10 and view["foreign"]
    env.mirror.reconcile(force=True)
    assert not any("mismatch" in a for a in env.alerts)
    assert _leg(env, t)["status"] == "closed" and _leg(env, t)["closed_outside_mirror"]   # B1


# ── Real 1-share probe (Groww, 2026-10-15) ────────────────

def test_sl_rejected_async_with_16448_is_retried_with_a_narrow_limit(env):     # probe S2b
    env.client.script = ["fill", ("reject_async", "16448 : Difference between limit price and trigger "
                                                  "price is beyond permissible range"), "open"]
    t = _buy(env, stop=930.0)
    sls = env.client.placed("SL")
    assert len(sls) == 2 and sls[0]["price"] == 920.7 and sls[1]["price"] == 925.35   # 1% then 0.5%
    leg = _leg(env, t)
    assert leg["sl_order"] and leg["protect_kind"] == "sl" and leg["protect_status"] == "TRIGGER_PENDING"
    assert any(r.get("category") == "invalid_sl_range" for r in _ledger(env) if r.get("event") == "error")


def test_sl_rejected_for_another_reason_alerts_and_relies_on_the_paper_sweep(env):
    env.client.script = ["fill", ("reject_async", "RMS:Margin Exceeds,Required:247740.10, Available:114999.09")]
    t = _buy(env, stop=930.0)
    leg = _leg(env, t)
    assert leg["sl_order"] is None and leg["protect_kind"] == "rejected" and len(env.client.placed("SL")) == 1
    assert any("NO protective stop" in a and "insufficient_margin" in a for a in env.alerts)
    env.pf.execute_sell(t.id, 929.0, "stop", exit_type="stop_hit")             # the paper sweep exits
    assert _leg(env, t)["status"] == "closed" and env.client.net("SBIN") == 0


def test_level_change_modifies_sl_with_price_and_trigger_and_verifies(env):    # probe S2
    t = _buy(env, stop=930.0)
    env.pf.update_levels(t.id, stop_price=935.0)
    (mod,) = env.client.ccalls("modify")
    assert mod["trigger_price"] == 935.0 and mod["price"] == 925.65
    o = env.client.orders[_leg(env, t)["sl_order"]]
    assert o["order_status"] == "TRIGGER_PENDING" and o["trigger_price"] == 935.0
    assert _leg(env, t)["protect_stop"] == 935.0 and len(env.client.placed("SL")) == 1


def test_level_change_that_fails_replaces_the_sl(env):
    t = _buy(env, stop=930.0)
    old = _leg(env, t)["sl_order"]
    env.client.modify_mode = "fail"
    env.pf.update_levels(t.id, stop_price=935.0)
    assert env.client.orders[old]["order_status"] == "CANCELLED"
    new = _leg(env, t)["sl_order"]
    assert new != old and env.client.orders[new]["trigger_price"] == 935.0
    assert env.client.orders[new]["order_status"] == "TRIGGER_PENDING"


def test_level_change_not_applied_is_detected_and_replaced(env):
    t = _buy(env, stop=930.0)
    old = _leg(env, t)["sl_order"]
    env.client.orders[old]["_orig_trigger"] = 930.0
    env.client.modify_mode = "ignored"                                          # accepted, never applied
    env.pf.update_levels(t.id, stop_price=935.0)
    new = _leg(env, t)["sl_order"]
    assert new != old and env.client.orders[old]["order_status"] == "CANCELLED"
    assert env.client.orders[new]["trigger_price"] == 935.0


def test_synchronous_failed_place_is_a_definitive_rejection(env):              # probe R2
    env.client.script = ["failed_sync"]
    t = _buy(env)
    leg = _leg(env, t)
    assert leg["status"] == "rejected" and leg["category"] == "invalid_price_tick"
    assert len(env.client.placed()) == 1                                        # no lookup-and-retry loop


def test_cancelling_a_cancelled_order_is_decided_by_the_read(env):             # probe S2 "cancel again"
    t = _buy(env)
    sl = _leg(env, t)["sl_order"]
    env.client.orders[sl]["order_status"] = "CANCELLED"                         # gone already
    env.pf.execute_sell(t.id, 950.0, "exit")
    assert _leg(env, t)["status"] == "closed" and env.client.net("SBIN") == 0


@pytest.mark.parametrize("remark,cat", [
    ("RMS:Rule: Assigned basket  for entity account-4407512387 across exchange across segment across product ",
     "not_intraday_allowed"),
    ("RMS:Rule: Check circuit limit including square off order exceeds : Circuit breach, Order Price :234.35,  "
     "Low Price Range:235.35 High Price Range:287.65  for entity account-4407512387", "circuit_limit"),
    ("RMS:Margin Exceeds,Required:247740.10, Available:114999.09 for entity account-4407512387",
     "insufficient_margin"),
    ("Choose price in multiples of the tick size.", "invalid_price_tick"),
    ("16448 : Difference between limit price and trigger price is beyond permissible range", "invalid_sl_range"),
])
def test_probe_remarks_classify(remark, cat):                                    # probe R1-R3, M1, S2b
    assert classify_error(remark) == cat


def test_circuit_band_is_parsed_and_used_to_clamp(env):                         # probe R3
    from agent.groww_broker import circuit_band
    rem = ("RMS:Rule: Check circuit limit including square off order exceeds : Circuit breach, Order Price "
           ":234.35,  Low Price Range:235.35 High Price Range:287.65  for entity account-X")
    assert circuit_band(rem) == (235.35, 287.65)
    env.client.script = [("reject_async", rem)]
    env.client.ltp = None
    env.md.prices["ITC.NS"] = 250.0
    _buy(env, ticker="ITC.NS", qty=1, price=250.0, stop=245.0)                  # entry rejected: circuit
    env.later(31)
    env.mirror.tick()
    assert env.mirror._quote("ITC")["upper"] == 287.65


def test_probe_lookup_shapes(env):                                               # probe S1
    t = _buy(env)
    st = env.mirror.broker.order_by_reference(_leg(env, t)["entry_ref"])
    assert st["id"] == _leg(env, t)["entry_order"] and st["status"] == "EXECUTED"
    assert env.mirror.broker.order_by_reference("261016-Z-Z1") is None          # GA004


def test_growwapi_imports_against_installed_deps():                               # Docker: --no-deps install
    gw = pytest.importorskip("growwapi")
    assert hasattr(gw.GrowwAPI, "place_order") and hasattr(gw.GrowwAPI, "get_order_status_by_reference")


# ── e2e script guards ─────────────────────────────────────

def _e2e():
    import importlib.util
    from pathlib import Path
    path = Path(__file__).resolve().parent.parent / "scripts" / "live_mirror_e2e.py"
    spec = importlib.util.spec_from_file_location("live_mirror_e2e", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize("argv,msg", [
    (["--yes-real-orders", "--smoke", "--symbol", "RELIANCE"], "not in"),
    (["--smoke"], "--yes-real-orders"),
    (["--yes-real-orders"], "exactly one"),
])
def test_e2e_script_guards(monkeypatch, argv, msg):
    mod = _e2e()
    monkeypatch.setattr(sys, "argv", ["live_mirror_e2e.py"] + argv)
    with pytest.raises(SystemExit) as e:
        mod.main()
    assert msg in str(e.value)
    assert mod.QTY == 1 and mod.ALLOWED_SYMBOLS == {"ITC"}



# ── B1 / M1 / M2 (repro6 on abdbc8e) ──────────────────────

def _live_sls(client, sym="SBIN"):
    return [o for o in client.orders.values() if o["trading_symbol"] == sym and o["order_type"] == "SL"
            and o["order_status"] == "TRIGGER_PENDING"]


@pytest.mark.parametrize("manual", [-10, -5])
def test_manual_square_off_never_triggers_a_second_exit(env, manual):           # [B1] repro6
    t = _buy(env)
    env.client.add_manual("SBIN", manual)                                        # user sells in the app
    env.pf.execute_sell(t.id, 950.0, "exit")
    for _ in range(5):
        env.later(2)
        env.mirror.tick()
    leg = _leg(env, t)
    assert env.client.net("SBIN") == 0 and leg["status"] == "closed" and leg["closed_outside_mirror"]
    assert _live_sls(env.client) == [] and leg["real_pnl"] is None
    assert "closed_outside_mirror" in leg["note"]


def test_full_manual_close_while_open_closes_the_leg_and_cancels_the_sl(env):   # [B1]
    t = _buy(env)
    env.client.add_manual("SBIN", -10)
    env.later(61)
    env.mirror.tick()
    leg = _leg(env, t)
    assert leg["status"] == "closed" and leg["closed_outside_mirror"] and _live_sls(env.client) == []
    assert any("closed outside the mirror" in a for a in env.alerts)
    env.pf.execute_sell(t.id, 950.0, "paper exit later")                         # paper catches up: nothing sent
    assert env.client.placed("LIMIT", "SELL") == [] and env.client.net("SBIN") == 0


def test_partial_manual_close_while_open_shrinks_the_sl(env):                  # [B1]
    t = _buy(env)
    env.client.add_manual("SBIN", -4)
    env.later(61)
    env.mirror.tick()
    leg = _leg(env, t)
    (sl,) = _live_sls(env.client)
    assert sl["quantity"] == 6 and leg["status"] == "open" and env.mirror._held(leg) == 6
    env.pf.execute_sell(t.id, 950.0, "exit")
    assert [k["quantity"] for k in env.client.placed("LIMIT", "SELL")] == [6] and env.client.net("SBIN") == 0


def test_restart_replay_after_a_manual_close(env):                              # [B1]
    t = _buy(env)
    env.client.add_manual("SBIN", -10)                                           # while the agent was down
    m2 = env.make()
    m2.tick()
    assert m2.legs[str(t.id)]["status"] == "closed" and _live_sls(env.client) == []
    pf2 = LivePortfolio(env.pf.db_path, 500_000, m2)
    pf2.execute_sell(t.id, 950.0, "exit")
    assert env.client.net("SBIN") == 0 and env.client.placed("LIMIT", "SELL") == []


def test_dead_stop_is_detected_and_reprotected(env):                            # [M1]
    t = _buy(env)
    sl = _leg(env, t)["sl_order"]
    env.client.orders[sl].update(order_status="CANCELLED")                       # cancelled in the app
    env.later(61)
    env.mirror.tick()
    leg = _leg(env, t)
    assert leg["sl_order"] and leg["sl_order"] != sl and len(_live_sls(env.client)) == 1
    assert any("CANCELLED at Groww — re-protecting" in a for a in env.alerts)


def test_executed_stop_is_recorded_as_the_exit(env):                            # [M1]
    t = _buy(env)
    env.client.fire_stop(_leg(env, t)["sl_order"], 929.0)                       # stop hit at Groww first
    env.later(61)
    env.mirror.tick()
    leg = _leg(env, t)
    assert leg["status"] == "closed" and leg["exit_fill"] == 929.0 and leg["real_pnl"] is not None
    env.pf.execute_sell(t.id, 928.0, "paper stop")                               # paper sweep later: no-op
    assert env.client.placed("LIMIT", "SELL") == [] and env.client.net("SBIN") == 0


def test_stop_partly_filled_during_a_move_is_replaced_for_the_rest(env):        # [M2]
    t = _buy(env)
    sl = _leg(env, t)["sl_order"]
    env.client.modify_mode = "fail"
    real_cancel = env.client.cancel_order

    def partial_then_cancel(groww_order_id, segment, timeout=None):
        o = env.client.orders[groww_order_id]
        if groww_order_id == sl:
            o.update(filled_quantity=4, average_fill_price=929.0)                # 4 filled while moving
        return real_cancel(groww_order_id, segment, timeout)
    env.client.cancel_order = partial_then_cancel
    env.pf.update_levels(t.id, stop_price=935.0)
    leg = _leg(env, t)
    new = leg["sl_order"]
    assert new and new != sl and env.client.orders[new]["quantity"] == 6
    assert env.client.orders[new]["order_status"] == "TRIGGER_PENDING" and not leg["protect_final"]
    assert env.mirror._held(leg) == 6


def test_smoke_sell_alert_has_no_fake_paper_price(env):                         # [minor]
    m = env.make(smoke=False)
    env.client.ltp = 400.0
    m.smoke_test("ITC.NS")
    closes = [a for a in env.alerts if "closed @" in a]
    assert closes and all("paper ₹0.00" not in a for a in closes)



# ── Reference salt / GA007 (real e2e re-run on 5268b82) ───

def test_reference_salt_is_persisted_and_replayed(env):
    t = _buy(env)
    salt = env.mirror._salt
    assert salt and len(salt) == 3 and env.client.placed()[0]["order_reference_id"].startswith(f"261016{salt}-")
    m2 = env.make()
    assert m2._salt == salt and m2._ref(t.id, "E") == env.mirror._ref(t.id, "E")
    assert m2.legs[str(t.id)]["entry_ref"] == m2._ref(t.id, "E")


def test_two_ledgers_on_the_same_day_get_different_refs(tmp_path, monkeypatch):
    import secrets
    chars = iter("k7xq2p")                                    # deterministic "random" salts
    monkeypatch.setattr(secrets, "choice", lambda alphabet: next(chars))
    c = FakeGrowwClient()
    mk = lambda d: LiveMirror(GrowwBroker(client=c), "nse_opus_live", tmp_path / d, alert=lambda s: None,
                              now=lambda: NOW)
    first, second = mk("run1"), mk("run2")
    assert first._ref(1, "E") == "261016k7x-1-E1" and second._ref(1, "E") == "261016q2p-1-E1"


def test_reference_length_worst_case(env):
    ref = env.mirror._ref(123456789012, "X", 99)
    assert len(ref) <= 20 and ref.count("-") == 2 and ref.replace("-", "").isalnum()
    assert env.mirror._ref("smoke12-2026-10-16", "B", 1).count("-") == 2


def _ga007_on_next_place(client, existing_kw=None):
    real = client.place_order

    def place(timeout=None, **kw):
        if existing_kw is not None:                        # the order already exists at Groww
            real(timeout=timeout, **{**kw, **existing_kw})
            client.place_order = real
        raise GErr("Duplicate order. Order with this reference id already exists.", "GA007")
    client.place_order = place


def test_ga007_adopts_our_own_earlier_order(env):
    _ga007_on_next_place(env.client, existing_kw={})        # same symbol/side/qty, just created
    t = _buy(env)
    leg = _leg(env, t)
    assert leg["status"] == "open" and leg["entry_order"] == "G1" and len(env.client.orders) == 2   # + SL
    assert any(r.get("note") == "GA007 adopted" for r in _ledger(env) if r.get("event") == "order")


def test_ga007_never_adopts_a_foreign_order(env):
    _ga007_on_next_place(env.client, existing_kw={"quantity": 3})   # someone else's order, other qty
    t = _buy(env)
    leg = _leg(env, t)
    assert leg["status"] == "rejected" and leg["category"] == "duplicate_reference"
    assert any("NOT adopting" in a for a in env.alerts)
    assert env.client.net("SBIN") == 3                                            # theirs, untouched
