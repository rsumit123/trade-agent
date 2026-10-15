"""Real-money mirror on Groww: every paper event becomes a real order, safely."""
import json
import sys
import types
from datetime import datetime
from types import SimpleNamespace as NS
from zoneinfo import ZoneInfo

import pytest

import agent.live_mirror as lm
from agent.config import AgentConfig
from agent.groww_broker import GrowwAuthError, GrowwBroker
from agent.live_mirror import LiveMirror, LivePortfolio, live_enabled_for, order_charges, round_to_tick
from agent.market_presets import get_preset
from agent.portfolio import Portfolio
from agent.risk_manager import RiskManager
from agent.runner import TradingAgent

IST = ZoneInfo("Asia/Kolkata")


# ── Fakes ─────────────────────────────────────────────────

class AuthExc(Exception):
    code = "401"


class FakeGrowwClient:
    """growwapi.GrowwAPI-shaped. `script` holds per-order behaviours consumed in
    placement order: 'fill', ('fill', price), ('partial', n), 'open', 'reject',
    'reject_status', 'auth'. Unscripted: SL orders rest (TRIGGER_PENDING),
    everything else fills at its limit (or `ltp` for MARKET)."""

    def __init__(self, ltp=100.0):
        self.ltp = ltp
        self.orders = {}
        self.calls = []
        self.script = []
        self.n = 0

    def _next(self, order_type):
        if self.script:
            return self.script.pop(0)
        return "open" if order_type == "SL" else "fill"

    def place_order(self, timeout=None, **kw):
        self.calls.append(("place", kw))
        how = self._next(kw["order_type"])
        if how == "reject":
            raise RuntimeError("RMS: MIS not allowed for this scrip")
        if how == "auth":
            raise AuthExc("401")
        self.n += 1
        oid = f"G{self.n}"
        o = {"groww_order_id": oid, "trading_symbol": kw["trading_symbol"], "quantity": kw["quantity"],
             "transaction_type": kw["transaction_type"], "order_type": kw["order_type"],
             "price": kw.get("price"), "trigger_price": kw.get("trigger_price"),
             "filled_quantity": 0, "average_fill_price": None, "order_status": "OPEN"}
        fill_px = kw.get("price") if kw["order_type"] != "MARKET" else self.ltp
        if how == "fill" or (isinstance(how, tuple) and how[0] == "fill"):
            px = how[1] if isinstance(how, tuple) else fill_px
            o.update(order_status="EXECUTED", filled_quantity=kw["quantity"], average_fill_price=px)
        elif isinstance(how, tuple) and how[0] == "partial":
            o.update(filled_quantity=how[1], average_fill_price=fill_px)
        elif how == "reject_status":
            o.update(order_status="REJECTED", remark="Insufficient margin")
        elif how == "open" and kw["order_type"] == "SL":
            o.update(order_status="TRIGGER_PENDING")
        self.orders[oid] = o
        return {"groww_order_id": oid, "order_status": o["order_status"]}

    def get_order_detail(self, groww_order_id, segment, timeout=None):
        return dict(self.orders[groww_order_id])

    def cancel_order(self, groww_order_id, segment, timeout=None):
        self.calls.append(("cancel", groww_order_id))
        o = self.orders[groww_order_id]
        if o["order_status"] in ("EXECUTED", "REJECTED", "CANCELLED"):
            raise RuntimeError("order not open")
        o["order_status"] = "CANCELLED"
        return {"groww_order_id": groww_order_id, "order_status": "CANCELLATION_REQUESTED"}

    def modify_order(self, timeout=None, **kw):
        self.calls.append(("modify", kw))
        o = self.orders[kw["groww_order_id"]]
        o.update(price=kw.get("price"), trigger_price=kw.get("trigger_price"))
        return {"groww_order_id": kw["groww_order_id"], "order_status": "OPEN"}

    def fire_stop(self, oid, price):
        o = self.orders[oid]
        o.update(order_status="EXECUTED", filled_quantity=o["quantity"], average_fill_price=price)

    def get_positions_for_user(self, segment=None, timeout=None):
        net = {}
        for o in self.orders.values():
            q = o["filled_quantity"] * (1 if o["transaction_type"] == "BUY" else -1)
            net[o["trading_symbol"]] = net.get(o["trading_symbol"], 0) + q
        return {"positions": [{"trading_symbol": s, "quantity": q, "exchange": "NSE", "product": "MIS"}
                              for s, q in net.items()]}

    def placed(self, order_type=None):
        return [kw for c, kw in self.calls if c == "place" and (order_type is None or kw["order_type"] == order_type)]


class FakeMD:
    def __init__(self, prices=None, tick=0.05):
        self.prices, self.tick = prices or {}, tick

    def tick_size(self, ticker):
        return self.tick

    def get_current_prices(self, tickers=None):
        return {t: self.prices[t] for t in (tickers or []) if t in self.prices}

    def is_market_open(self):
        return True


NOW = datetime(2026, 10, 16, 10, 0, tzinfo=IST)


@pytest.fixture
def env(tmp_path):
    client = FakeGrowwClient()
    clock = {"t": 0.0}
    alerts = []
    md = FakeMD({"SBIN.NS": 940.0, "ITC.NS": 400.0})

    def make(smoke=True, now=NOW, **kw):
        m = LiveMirror(GrowwBroker(client=client), "nse_opus_live", tmp_path / "s", market_data=md,
                       alert=alerts.append, now=lambda: now,
                       sleep=lambda s: clock.__setitem__("t", clock["t"] + s),
                       monotonic=lambda: clock["t"], **kw)
        if smoke:
            m._smoke = (m._today(), True)
        return m

    mirror = make()
    pf = LivePortfolio(str(tmp_path / "t.db"), 500_000, mirror)
    return NS(client=client, mirror=mirror, pf=pf, alerts=alerts, md=md, make=make, tmp=tmp_path)


def _ledger(env):
    return [json.loads(l) for l in (env.tmp / "s" / "live_orders.jsonl").read_text().splitlines()]


# ── Entries ───────────────────────────────────────────────

def test_long_entry_fills_and_places_protective_stop(env):
    t = env.pf.execute_buy("SBIN.NS", 10, 940.0, "intraday", stop_price=930.0, target_price=960.0)
    entry, stop = env.client.placed()
    assert entry["transaction_type"] == "BUY" and entry["order_type"] == "LIMIT" and entry["product"] == "MIS"
    assert entry["trading_symbol"] == "SBIN" and entry["quantity"] == 10
    assert entry["price"] == round_to_tick(940 * 1.003, 0.05, up=True) == 942.85
    assert stop["order_type"] == "SL" and stop["transaction_type"] == "SELL"
    assert stop["trigger_price"] == 930.0 and stop["price"] == 920.7
    leg = env.mirror.legs[str(t.id)]
    assert leg["status"] == "open" and leg["filled_qty"] == 10 and leg["entry_fill"] == 942.85
    assert leg["sl_order"] == "G2"


def test_short_entry_sells_below_and_stop_buys_above(env):
    env.pf.execute_short("SBIN.NS", 5, 940.0, stop_price=950.0, target_price=920.0)
    entry, stop = env.client.placed()
    assert entry["transaction_type"] == "SELL" and entry["price"] == 937.15
    assert stop["transaction_type"] == "BUY" and stop["trigger_price"] == 950.0 and stop["price"] == 959.5


def test_partial_fill_keeps_filled_qty_and_stop_covers_only_that(env):
    env.client.script = [("partial", 4)]
    t = env.pf.execute_buy("SBIN.NS", 10, 940.0, "intraday", stop_price=930.0)
    leg = env.mirror.legs[str(t.id)]
    assert leg["status"] == "open" and leg["filled_qty"] == 4
    assert ("cancel", "G1") in env.client.calls
    assert env.client.placed("SL")[0]["quantity"] == 4
    assert any("PARTIAL 4/10" in a for a in env.alerts)


def test_unfilled_entry_is_missed_and_later_events_do_nothing(env):
    env.client.script = ["open"]
    t = env.pf.execute_buy("SBIN.NS", 10, 940.0, "intraday", stop_price=930.0)
    assert env.mirror.legs[str(t.id)]["status"] == "missed"
    n = len(env.client.calls)
    env.pf.update_levels(t.id, stop_price=935.0)
    env.pf.execute_sell(t.id, 950.0, "exit")
    assert len(env.client.calls) == n              # no stop, no exit order
    assert env.pf.get_open_positions() == []        # paper unaffected


def test_rejected_entry_is_recorded_and_paper_still_books(env):
    env.client.script = ["reject"]
    t = env.pf.execute_buy("SBIN.NS", 10, 940.0, "intraday", stop_price=930.0)
    assert t.id and env.mirror.legs[str(t.id)]["status"] == "rejected"
    assert len(env.pf.get_open_positions()) == 1
    err = [r for r in _ledger(env) if r.get("event") == "order"][0]
    assert err["status"] == "error" and "MIS not allowed" in err["error"]


# ── Exits ─────────────────────────────────────────────────

def test_exit_cancels_stop_then_sells(env):
    t = env.pf.execute_buy("SBIN.NS", 10, 940.0, "intraday", stop_price=930.0)
    env.pf.execute_sell(t.id, 950.0, "take profit")
    assert ("cancel", "G2") in env.client.calls
    exit_order = env.client.placed()[-1]
    assert exit_order["transaction_type"] == "SELL" and exit_order["quantity"] == 10
    assert exit_order["price"] == round_to_tick(950 * 0.995, 0.05, up=False)
    leg = env.mirror.legs[str(t.id)]
    assert leg["status"] == "closed" and leg["paper_exit"] == 950.0
    assert env.client.get_positions_for_user()["positions"][0]["quantity"] == 0


def test_stop_already_filled_at_broker_means_no_second_exit(env):
    t = env.pf.execute_buy("SBIN.NS", 10, 940.0, "intraday", stop_price=930.0)
    env.client.fire_stop("G2", 929.5)
    n_places = len(env.client.placed())
    env.pf.execute_sell(t.id, 929.0, "stop", exit_type="stop_hit")
    assert len(env.client.placed()) == n_places           # nothing new sent
    leg = env.mirror.legs[str(t.id)]
    assert leg["status"] == "closed" and leg["exit_fill"] == 929.5
    assert env.client.get_positions_for_user()["positions"][0]["quantity"] == 0   # not flipped short


def test_exit_escalates_then_market(env):
    t = env.pf.execute_buy("SBIN.NS", 10, 940.0, "intraday", stop_price=930.0)
    env.client.script = ["open", "open", ("partial", 3), "fill"]   # 3 limits, then MARKET
    env.client.ltp = 948.0
    env.pf.execute_sell(t.id, 950.0, "exit")
    exits = env.client.placed()[2:]
    assert [e["order_type"] for e in exits] == ["LIMIT", "LIMIT", "LIMIT", "MARKET"]
    assert [e["quantity"] for e in exits] == [10, 10, 10, 7]
    assert exits[0]["price"] > exits[1]["price"] > exits[2]["price"]
    leg = env.mirror.legs[str(t.id)]
    assert leg["status"] == "closed"
    assert leg["exit_fill"] == pytest.approx((3 * exits[2]["price"] + 7 * 948.0) / 10, abs=1e-3)


def test_exit_without_market_leaves_remaining_open_and_alerts(env):
    env.mirror.market_ok = False
    t = env.pf.execute_buy("SBIN.NS", 10, 940.0, "intraday", stop_price=930.0)
    env.client.script = ["open", "open", "open"]
    env.pf.execute_sell(t.id, 950.0, "exit")
    leg = env.mirror.legs[str(t.id)]
    assert leg["status"] == "open" and leg["filled_qty"] == 10
    assert any("STILL OPEN" in a for a in env.alerts)


def test_update_levels_modifies_broker_stop(env):
    t = env.pf.execute_buy("SBIN.NS", 10, 940.0, "intraday", stop_price=930.0)
    env.pf.update_levels(t.id, stop_price=936.0)
    (mod,) = [kw for c, kw in env.client.calls if c == "modify"]
    assert mod["groww_order_id"] == "G2" and mod["trigger_price"] == 936.0 and mod["quantity"] == 10
    assert mod["order_type"] == "SL"


# ── Guards ────────────────────────────────────────────────

def _session(sid="nse_opus_live", broker="groww", backtest=False):
    return NS(session_id=sid, live_broker=broker, backtest_mode=backtest)


def test_live_enabled_needs_all_three_gates(monkeypatch):
    monkeypatch.setenv("GROWW_LIVE_SESSIONS", "nse_opus_live, nse_opus")
    assert live_enabled_for(_session()) is True
    assert live_enabled_for(_session(sid="nse_opus")) is False         # env-listed, not hard-allowlisted
    assert live_enabled_for(_session(broker=None)) is False
    assert live_enabled_for(_session(backtest=True)) is False
    assert live_enabled_for(None) is False
    monkeypatch.delenv("GROWW_LIVE_SESSIONS")
    assert live_enabled_for(_session()) is False


def test_kill_switch_blocks_entries(env):
    (env.tmp / "s").mkdir(exist_ok=True)
    (env.tmp / "s" / "LIVE_KILL").write_text("")
    t = env.pf.execute_buy("SBIN.NS", 10, 940.0, "intraday", stop_price=930.0)
    assert env.client.calls == [] and env.mirror.legs[str(t.id)]["note"] == "kill switch"


def test_kill_switch_still_lets_exits_out(env):
    t = env.pf.execute_buy("SBIN.NS", 10, 940.0, "intraday", stop_price=930.0)
    (env.tmp / "s" / "LIVE_KILL").write_text("")
    env.pf.execute_sell(t.id, 950.0, "exit")
    assert env.mirror.legs[str(t.id)]["status"] == "closed"


def test_exposure_and_order_value_caps(env):
    env.pf.execute_buy("SBIN.NS", 40, 940.0, "intraday", stop_price=930.0)     # ₹37.7K real
    t = env.pf.execute_buy("SBIN.NS", 15, 940.0, "intraday", stop_price=930.0)  # +₹14.1K > ₹50K
    assert env.mirror.legs[str(t.id)]["status"] == "skipped" and "exposure" in env.mirror.legs[str(t.id)]["note"]
    t2 = env.pf.execute_buy("ITC.NS", 130, 400.0, "intraday")                     # ₹52K one order
    assert "order value" in env.mirror.legs[str(t2.id)]["note"]


def test_daily_loss_cap_blocks_new_entries(env):
    env.mirror._realized_today = -1500.0
    t = env.pf.execute_buy("SBIN.NS", 1, 940.0, "intraday")
    assert "daily real loss" in env.mirror.legs[str(t.id)]["note"]


def test_order_cap(env, monkeypatch):
    monkeypatch.setattr(lm, "MAX_ORDERS_PER_DAY", 3)
    env.pf.execute_buy("SBIN.NS", 1, 940.0, "intraday", stop_price=930.0)        # 2 orders
    t = env.pf.execute_buy("SBIN.NS", 1, 940.0, "intraday", stop_price=930.0)
    assert "order cap" in env.mirror.legs[str(t.id)]["note"]


def test_backtest_is_a_noop(env):
    env.mirror.is_backtest = lambda: True
    env.pf.execute_buy("SBIN.NS", 1, 940.0, "intraday")
    env.mirror.reconcile(force=True)
    assert env.client.calls == [] and env.alerts == []


def test_no_entries_before_smoke_test_passes(env):
    m = env.make(smoke=False)
    pf = LivePortfolio(str(env.tmp / "t2.db"), 500_000, m)
    t = pf.execute_buy("SBIN.NS", 1, 940.0, "intraday")
    assert "smoke test" in m.legs[str(t.id)]["note"]


def test_non_nse_ticker_refused(env):
    t = env.pf.execute_buy("RELIANCE.BO", 1, 1000.0, "intraday")
    assert "not an NSE equity" in env.mirror.legs[str(t.id)]["note"] and env.client.calls == []


# ── Auth / disable ────────────────────────────────────────

def test_auth_failure_disables_real_orders_for_the_day(env, monkeypatch):
    env.client.script = ["auth", "auth"]               # first call + retry after re-login
    env.mirror.broker._client = env.client
    monkeypatch.setattr(GrowwBroker, "_login", lambda self: env.client)
    t = env.pf.execute_buy("SBIN.NS", 10, 940.0, "intraday", stop_price=930.0)
    assert env.mirror.legs[str(t.id)]["status"] == "rejected"
    assert env.mirror.disabled_reason() and "auth" in env.mirror.disabled_reason().lower()
    assert any("DISABLED" in a for a in env.alerts)
    t2 = env.pf.execute_buy("SBIN.NS", 1, 940.0, "intraday")
    assert env.mirror.legs[str(t2.id)]["status"] == "skipped"
    # a restart the same day stays disabled; the next day starts fresh
    assert env.make().disabled_reason()
    assert env.make(now=datetime(2026, 10, 19, 10, 0, tzinfo=IST)).disabled_reason() is None


def test_login_without_keys_is_an_auth_error(tmp_path, monkeypatch):
    fake = types.ModuleType("growwapi")
    fake.GrowwAPI = type("GrowwAPI", (), {})
    monkeypatch.setitem(sys.modules, "growwapi", fake)
    b = GrowwBroker(api_key="", totp_secret="", token_path=tmp_path / "tok.json")
    with pytest.raises(GrowwAuthError):
        b.client


def test_token_is_cached_for_the_ist_day(tmp_path, monkeypatch):
    calls = []

    class GrowwAPI:
        def __init__(self, token):
            self.token = token

        @staticmethod
        def get_access_token(api_key, totp=None, secret=None):
            calls.append((api_key, totp))
            return f"tok{len(calls)}"
    fake = types.ModuleType("growwapi")
    fake.GrowwAPI = GrowwAPI
    monkeypatch.setitem(sys.modules, "growwapi", fake)
    otp = types.ModuleType("pyotp")
    otp.TOTP = lambda secret: NS(now=lambda: "123456" if secret == "JBSWY3DPEHPK3PXP" else "bad")
    monkeypatch.setitem(sys.modules, "pyotp", otp)
    day = {"now": NOW}
    mk = lambda: GrowwBroker(api_key="K", totp_secret="JBSWY3DPEHPK3PXP", token_path=tmp_path / "tok.json",
                             now=lambda: day["now"])
    assert mk().client.token == "tok1" and mk().client.token == "tok1" and len(calls) == 1
    assert len(calls[0][1]) == 6                               # a TOTP code, not the secret
    day["now"] = datetime(2026, 10, 17, 9, 0, tzinfo=IST)
    assert mk().client.token == "tok2"


# ── Smoke test ────────────────────────────────────────────

def test_smoke_test_buys_one_and_sells_market(env):
    m = env.make(smoke=False)
    env.client.ltp = 400.0
    r = m.smoke_test("ITC.NS")
    assert r["passed"] and m.market_ok
    buy, sell = env.client.placed()
    assert buy["quantity"] == 1 and buy["order_type"] == "LIMIT" and sell["order_type"] == "MARKET"
    assert m.smoke_done_today()
    assert env.make(smoke=False).smoke_done_today()            # persisted across restarts


def test_smoke_test_falls_back_to_limit_exit_when_market_rejected(env):
    m = env.make(smoke=False)
    env.client.script = ["fill", "reject", "fill"]
    r = m.smoke_test("ITC.NS")
    assert r["passed"] and m.market_ok is False
    assert [p["order_type"] for p in env.client.placed()] == ["LIMIT", "MARKET", "LIMIT"]


def test_failed_smoke_test_disables_the_day(env):
    m = env.make(smoke=False)
    env.client.script = ["open"]
    r = m.smoke_test("ITC.NS")
    assert not r["passed"] and m.disabled_reason().startswith("smoke test failed")


# ── Reconcile ─────────────────────────────────────────────

def test_reconcile_alerts_on_mismatch_once(env):
    env.pf.execute_buy("SBIN.NS", 10, 940.0, "intraday", stop_price=930.0)
    env.client.orders["G1"]["filled_quantity"] = 7          # Groww disagrees
    env.mirror.reconcile(force=True)
    env.mirror.reconcile(force=True)
    assert sum("mismatch" in a for a in env.alerts) == 1


def test_flattens_traded_symbols_after_forced_close(env):
    m = env.make(now=datetime(2026, 10, 16, 15, 17, tzinfo=IST),
                 forced_close_time=lambda now: now.replace(hour=15, minute=15, second=0))
    pf = LivePortfolio(str(env.tmp / "t3.db"), 500_000, m)
    pf.execute_buy("SBIN.NS", 10, 940.0, "intraday", stop_price=930.0)
    env.client.orders["G99"] = {"trading_symbol": "INFY", "quantity": 5, "filled_quantity": 5,
                                "transaction_type": "BUY", "order_status": "EXECUTED"}   # user's own trade
    m.reconcile(force=True)
    pos = {p["trading_symbol"]: p["quantity"] for p in env.client.get_positions_for_user()["positions"]}
    assert pos["SBIN"] == 0 and pos["INFY"] == 5            # never touches symbols we didn't trade


# ── Restart ───────────────────────────────────────────────

def test_restart_rebuilds_open_legs_from_ledger(env):
    t = env.pf.execute_buy("SBIN.NS", 10, 940.0, "intraday", stop_price=930.0)
    m2 = env.make()
    assert m2.legs[str(t.id)]["status"] == "open" and m2._orders_today == 2
    pf2 = LivePortfolio(env.pf.db_path, 500_000, m2)
    pf2.execute_sell(t.id, 950.0, "exit")
    assert m2.legs[str(t.id)]["status"] == "closed"


# ── Runner paths: stop sweep + forced close go through the wrapper ──

def _agent(env):
    cfg = AgentConfig(starting_capital=500_000, _market_preset=get_preset("nse-intraday"))
    a = TradingAgent.__new__(TradingAgent)
    a.config, a.preset = cfg, get_preset("nse-intraday")
    a.portfolio = env.pf
    a.risk_manager = RiskManager(cfg, a.portfolio)
    a.learner = NS(write_trade_log=lambda *x, **k: None)
    a.engine = NS(client=None)
    a._auto_exits, a._is_backtest = [], False
    a.live_mirror = None
    a._seconds_to_forced_close = lambda: None
    return a


def test_between_cycle_stop_sweep_is_mirrored(env):
    a = _agent(env)
    t = env.pf.execute_buy("SBIN.NS", 10, 940.0, "intraday", stop_price=930.0)
    a.market_data = NS(is_market_open=lambda: True,
                       get_current_prices=lambda tickers=None: {"SBIN.NS": 929.0})
    a.risk_manager.check_intraday_close = lambda now=None: False
    a._monitor_positions()
    assert env.mirror.legs[str(t.id)]["status"] == "closed"
    assert env.client.placed()[-1]["transaction_type"] == "SELL"


def test_forced_close_is_mirrored(env):
    a = _agent(env)
    t = env.pf.execute_short("SBIN.NS", 5, 940.0, stop_price=950.0)
    a._close_intraday_positions({"SBIN.NS": 938.0})
    assert env.mirror.legs[str(t.id)]["status"] == "closed"
    assert env.mirror.legs[str(t.id)]["exit_type"] == "forced_close"
    assert env.client.placed()[-1]["transaction_type"] == "BUY"


def test_runner_builds_live_portfolio_only_when_gated(tmp_path, monkeypatch):
    monkeypatch.setenv("GROWW_LIVE_SESSIONS", "nse_opus_live")
    a = TradingAgent.__new__(TradingAgent)
    a.config = AgentConfig(starting_capital=50_000, db_path=str(tmp_path / "t.db"))
    a.market_data = FakeMD()
    a.session = NS(session_id="nse_opus_live", live_broker="groww", backtest_mode=False,
                   session_dir=tmp_path / "s")
    assert isinstance(a._make_portfolio(), LivePortfolio) and a.live_mirror is not None
    a.session = NS(session_id="nse_opus", live_broker="groww", backtest_mode=False, session_dir=tmp_path)
    a.live_mirror = None
    p = a._make_portfolio()
    assert type(p) is Portfolio and a.live_mirror is None


# ── Charges + report ──────────────────────────────────────

def test_charges_match_hand_computed_schedule():
    # ₹10,000 buy: brokerage 10 + exch 0.297 + sebi 0.01 + stamp 0.30 + ipft 0.01 + GST 18% of 10.307
    assert order_charges("BUY", 10_000) == 12.47
    # ₹10,000 sell: brokerage 10 + STT 2.50 + exch 0.297 + sebi 0.01 + ipft 0.01 + GST 1.855
    assert order_charges("SELL", 10_000) == 14.67
    # ₹3,000 buy: 0.1% = ₹3 < ₹5 → min(₹5, 2.5%) = ₹5
    assert order_charges("BUY", 3_000) == 6.10
    # ₹50,000: brokerage capped at ₹20
    assert order_charges("BUY", 50_000) == pytest.approx(20 + 1.485 + 0.05 + 1.5 + 0.05 + 0.18 * 21.535, abs=0.01)


def test_eod_report_compares_paper_and_real(env):
    t = env.pf.execute_buy("SBIN.NS", 10, 940.0, "intraday", stop_price=930.0)
    env.client.script = [("fill", 948.0)]
    env.pf.execute_sell(t.id, 950.0, "exit")
    env.client.script = ["open"]
    env.pf.execute_buy("ITC.NS", 10, 400.0, "intraday")
    leg = env.mirror.legs[str(t.id)]
    gross = (948.0 - 942.85) * 10
    assert leg["real_gross"] == pytest.approx(gross)
    assert leg["real_pnl"] == pytest.approx(gross - order_charges("BUY", 9428.5) - order_charges("SELL", 9480.0), abs=0.01)
    text = env.mirror.send_eod_report()
    assert "paper ₹+100.00" in text and "missed 1" in text and "closed 1" in text
    assert "+30.3bps" in text                 # entry 942.85 vs 940 = 30.3bps worse
    assert any(r.get("event") == "eod_report" for r in _ledger(env))


def test_ledger_records_orders_and_legs(env):
    t = env.pf.execute_buy("SBIN.NS", 10, 940.0, "intraday", stop_price=930.0)
    rows = _ledger(env)
    orders = [r for r in rows if r["event"] == "order"]
    assert [o["leg"] for o in orders] == ["entry", "stop"]
    assert orders[0]["groww_order_id"] == "G1" and orders[0]["req_price"] == 942.85
    assert all(r["date"] == "2026-10-16" for r in rows)
    assert rows[-1]["leg"]["paper_trade_id"] == str(t.id)


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
