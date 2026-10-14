"""Audit fixes in the runner: forced close, blocked/outage cycles, qty and trade_type validation, scan retry."""
from datetime import datetime
from types import SimpleNamespace as NS
from zoneinfo import ZoneInfo

import pytest

import agent.premarket_scanner as pms
import agent.runner as runner_mod
import agent.session as sess
from agent.config import AgentConfig
from agent.market_presets import get_preset
from agent.portfolio import Portfolio
from agent.risk_manager import RiskManager
from agent.runner import TradingAgent
from agent.web_research import LLMWebSearchTool


class _Market:
    def __init__(self, prices=None, rows=None, watchlist=("SBIN.NS",), open_=True):
        self.prices, self.rows, self.watchlist, self.open_ = prices or {}, rows or [], list(watchlist), open_

    def is_market_open(self):
        return self.open_

    def get_current_prices(self, tickers=None):
        return {t: self.prices[t] for t in (tickers or self.watchlist) if t in self.prices}

    def get_watchlist_summary(self):
        return self.rows

    def get_stock_context(self, ticker, include_vwap=False):
        return {}


class _Engine:
    client = None
    last_error = None
    last_usage = {"calls": 1}

    def __init__(self):
        self.calls = []

    def run_decision_loop(self, **kw):
        self.calls.append(kw)
        return []


class _Learner:
    def __init__(self):
        self.logged = []

    def write_trade_log(self, trade, llm_client=None):
        self.logged.append(trade)

    def get_learnings(self, max_chars=0):
        return ""


def _make(tmp_path, market_id="nse", market=None):
    preset = get_preset(market_id)
    cfg = AgentConfig(starting_capital=500_000, watchlist=["SBIN.NS"], _market_preset=preset)
    a = TradingAgent.__new__(TradingAgent)
    a.config, a.preset, a.session = cfg, preset, None
    a.portfolio = Portfolio(str(tmp_path / "t.db"), 500_000)
    a.risk_manager = RiskManager(cfg, a.portfolio)
    a.learner, a.engine = _Learner(), _Engine()
    a.researcher = NS(build_research_context=lambda wl: "news")
    a.market_data = market or _Market({"SBIN.NS": 940.0}, rows=[{"ticker": "SBIN.NS", "price": 940.0}])
    a._auto_exits, a._is_backtest = [], False
    return a


@pytest.fixture
def agent(tmp_path):
    a = _make(tmp_path)
    a.risk_manager.check_intraday_close = lambda now=None: False     # not wall-clock dependent
    return a


def _near_close(agent, monkeypatch, value=True):
    monkeypatch.setattr(agent.risk_manager, "check_intraday_close", lambda now=None: value)


# -- 1. forced close from the between-cycle monitor ---------------------------

def test_monitor_force_closes_intraday_after_cutoff(agent, monkeypatch):
    agent.portfolio.execute_buy("SBIN.NS", 10, 930.0, "intraday", stop_price=900, target_price=990)
    agent.portfolio.execute_short("TCS.NS", 5, 3000.0, stop_price=3100, target_price=2900)
    keep = agent.portfolio.execute_buy("INFY.NS", 5, 1500.0, "swing", stop_price=1400, target_price=1700)
    agent.market_data = _Market({"SBIN.NS": 940.0, "TCS.NS": 2990.0, "INFY.NS": 1510.0})
    _near_close(agent, monkeypatch)
    agent._monitor_positions()
    assert [p.id for p in agent.portfolio.get_open_positions()] == [keep.id]
    assert {tr.exit_type for tr in agent.learner.logged} == {"forced_close"}
    assert {tr.exit_price for tr in agent.learner.logged} == {940.0, 2990.0}
    assert len(agent._auto_exits) == 2


def test_monitor_does_not_force_close_before_cutoff(agent, monkeypatch):
    agent.portfolio.execute_buy("SBIN.NS", 10, 930.0, "intraday", stop_price=900, target_price=990)
    agent.market_data = _Market({"SBIN.NS": 940.0})
    _near_close(agent, monkeypatch, False)
    agent._monitor_positions()
    assert len(agent.portfolio.get_open_positions()) == 1


def test_forced_close_without_live_price_is_skipped_and_retried(agent, monkeypatch, caplog):
    agent.portfolio.execute_buy("SBIN.NS", 10, 930.0, "intraday", stop_price=900, target_price=990)
    agent.portfolio.execute_buy("TCS.NS", 1, 3000.0, "intraday", stop_price=2900, target_price=3100)
    _near_close(agent, monkeypatch)
    agent.market_data = _Market({"TCS.NS": 3010.0})          # SBIN quote missing
    with caplog.at_level("WARNING"):
        agent._monitor_positions()
    assert [p.ticker for p in agent.portfolio.get_open_positions()] == ["SBIN.NS"]   # never booked at entry
    assert "No live price for SBIN.NS" in caplog.text
    agent.market_data = _Market({"SBIN.NS": 938.0, "TCS.NS": 3010.0})
    agent._monitor_positions()                                # next tick
    assert agent.portfolio.get_open_positions() == []
    assert [tr.exit_price for tr in agent.learner.logged if tr.ticker == "SBIN.NS"] == [938.0]


def test_sleep_wakes_at_the_forced_close_time(agent, monkeypatch):
    clock = {"t": 0.0}
    monkeypatch.setattr(runner_mod.time, "monotonic", lambda: clock["t"])
    monkeypatch.setattr(runner_mod.time, "sleep", lambda s: clock.__setitem__("t", clock["t"] + s))
    monkeypatch.setattr(runner_mod.random, "uniform", lambda a, b: 0)
    agent._seconds_to_forced_close = lambda: (100.0 - clock["t"]) if clock["t"] < 100 else None
    checks = []
    monkeypatch.setattr(agent, "_monitor_positions", lambda: checks.append(clock["t"]))
    agent._sleep_monitoring_positions(300)
    assert 100.5 in checks                                    # woke just after the trigger, not at 120


def test_seconds_to_forced_close_uses_market_clock(agent, monkeypatch):
    fixed = {"now": datetime(2026, 10, 14, 15, 14, 0, tzinfo=ZoneInfo("Asia/Kolkata"))}

    class _DT(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed["now"]
    monkeypatch.setattr(runner_mod, "datetime", _DT)
    assert agent._seconds_to_forced_close() == 60.0
    fixed["now"] = fixed["now"].replace(minute=16)
    assert agent._seconds_to_forced_close() is None
    agent.config._market_preset = get_preset("crypto")
    assert agent._seconds_to_forced_close() is None


# -- 2/3. blocked day still sweeps; risk status is post-sweep -----------------

def test_blocked_day_still_force_closes_intraday(agent, monkeypatch):
    agent.portfolio.execute_buy("SBIN.NS", 100, 1000.0, "intraday", stop_price=900, target_price=1200)
    agent.config.daily_loss_limit_pct = 0.01                    # -6k realised below blows the 5k limit
    agent.market_data = _Market({"SBIN.NS": 940.0}, rows=[{"ticker": "SBIN.NS"}])
    monkeypatch.setattr(agent.risk_manager, "get_risk_status", lambda p=None: {"can_trade": False})
    _near_close(agent, monkeypatch, False)
    res = agent.run_once()                                      # blocked, not near close: no sweep hit
    assert res["status"] == "blocked" and len(agent.portfolio.get_open_positions()) == 1
    _near_close(agent, monkeypatch)
    agent.run_once()
    assert agent.portfolio.get_open_positions() == [] and agent.engine.calls == []


def test_blocked_day_still_runs_stop_sweep(agent):
    agent.config.daily_loss_limit_pct = 0.01
    agent.portfolio.execute_buy("SBIN.NS", 100, 1000.0, "swing", stop_price=950, target_price=1200)
    agent.market_data = _Market({"SBIN.NS": 940.0}, rows=[{"ticker": "SBIN.NS"}])
    res = agent.run_once()
    assert agent.portfolio.get_open_positions() == []
    assert res["status"] == "blocked" and agent.engine.calls == []


def test_risk_status_is_computed_after_the_sweep(agent):
    agent.portfolio.execute_buy("SBIN.NS", 100, 1000.0, "swing", stop_price=950, target_price=1200)
    agent.market_data = _Market({"SBIN.NS": 940.0}, rows=[{"ticker": "SBIN.NS"}])
    agent.config.daily_loss_limit_pct = 0.5                     # not blocked
    agent.run_once()
    (call,) = agent.engine.calls
    assert call["risk_status"]["open_positions"] == 0           # stop already booked
    assert call["risk_status"]["daily_pnl"] == pytest.approx(-6000)


# -- 8. data outage ------------------------------------------------------------

def test_outage_skips_llm_but_keeps_sweeping(agent, monkeypatch):
    agent.portfolio.execute_buy("SBIN.NS", 10, 930.0, "intraday", stop_price=900, target_price=990)
    agent.market_data = _Market({}, rows=[])
    res = agent.run_once()
    assert agent.engine.calls == [] and res["status"] == "no_data"
    assert len(agent.portfolio.get_open_positions()) == 1       # no price: left alone, not booked at entry
    # close window with a quote for the position but an empty watchlist summary: still flattened
    agent.market_data = _Market({"SBIN.NS": 935.0}, rows=[])
    _near_close(agent, monkeypatch)
    agent.run_once()
    assert agent.portfolio.get_open_positions() == [] and agent.engine.calls == []


def test_empty_summary_but_quotes_still_runs_llm(agent):
    agent.market_data = _Market({"SBIN.NS": 940.0}, rows=[])
    agent.run_once()
    assert len(agent.engine.calls) == 1


def test_normal_cycle_runs_llm(agent):
    assert agent.run_once()["status"] == "ok" and len(agent.engine.calls) == 1


# -- 4. intraday-only presets --------------------------------------------------

def test_intraday_only_preset_forces_intraday_trade_type(tmp_path):
    a = _make(tmp_path, "nse-intraday")
    a.risk_manager.check_intraday_close = lambda now=None: False
    for given in ({}, {"trade_type": "swing"}):
        res = a._execute_trade({"action": "BUY", "ticker": "SBIN.NS", "quantity": 10, "reason": "x", **given})
        assert res["success"], res
    assert {p.trade_type for p in a.portfolio.get_open_positions()} == {"intraday"}


def test_other_presets_keep_swing_default(agent):
    agent._execute_trade({"action": "BUY", "ticker": "SBIN.NS", "quantity": 10, "reason": "x"})
    assert agent.portfolio.get_open_positions()[0].trade_type == "swing"


def test_trade_tool_schema_matches_preset():
    def schema(p):
        return LLMWebSearchTool.get_trade_tool(p)["input_schema"]["properties"]["trade_type"]
    assert schema(get_preset("nse-intraday"))["enum"] == ["intraday"]
    assert schema(get_preset("nse"))["enum"] == ["intraday", "swing"]


# -- 5. quantity validation ----------------------------------------------------

@pytest.mark.parametrize("action", ["BUY", "SHORT"])
@pytest.mark.parametrize("qty", [0, -5, None, "ten", 2.5, True, float("nan")])
def test_bad_quantity_is_rejected(agent, action, qty):
    cash = agent.portfolio.get_cash()
    inp = {"action": action, "ticker": "SBIN.NS", "reason": "x"}
    if qty is not None:
        inp["quantity"] = qty
    res = agent._execute_trade(inp)
    assert res["success"] is False and "quantity" in res["error"].lower()
    assert agent.portfolio.get_cash() == cash and agent.portfolio.get_open_positions() == []


def test_integral_float_quantity_is_accepted_as_int(agent):
    res = agent._execute_trade({"action": "BUY", "ticker": "SBIN.NS", "quantity": 10.0, "reason": "x"})
    assert res["success"] and res["quantity"] == 10 and isinstance(res["quantity"], int)


def test_fractional_market_accepts_fractions(tmp_path):
    a = _make(tmp_path, "crypto", _Market({"BTC-USD": 100.0}))
    a.risk_manager.check_intraday_close = lambda now=None: False
    res = a._execute_trade({"action": "BUY", "ticker": "BTC-USD", "quantity": 0.015, "reason": "x"})
    assert res["success"] and res["quantity"] == 0.015


# -- 6/7. scan retry and labelling ---------------------------------------------

@pytest.fixture
def scan_agent(tmp_path, monkeypatch):
    sc = sess.SessionConfig(session_id="s1", market="nse-intraday")
    monkeypatch.setattr(type(sc), "session_dir", property(lambda self: tmp_path))
    monkeypatch.setattr(sess, "load_session", lambda sid: sc)
    a = TradingAgent.__new__(TradingAgent)
    a.config = AgentConfig(watchlist=["DEFAULT.NS"], _market_preset=get_preset("nse-intraday"))
    a.preset, a.session = get_preset("nse-intraday"), sc
    a.market_data = NS(kite=object(), watchlist=["DEFAULT.NS"], is_market_open=lambda: True)
    return a


def _scanner(monkeypatch, outcomes):
    calls = []

    class Spy:
        def __init__(self, *a, **k):
            pass

        def scan(self, **k):
            calls.append(1)
            o = outcomes[min(len(calls), len(outcomes)) - 1]
            if isinstance(o, Exception):
                raise o
            return o
    monkeypatch.setattr(pms, "PreMarketScanner", Spy)
    return calls


_PICK = [{"ticker": "HCLTECH.NS", "ltp": 1500.0, "gap_pct": -0.5, "llm_direction": "short"}]


def test_failed_scan_is_retried_then_succeeds(scan_agent, monkeypatch):
    calls = _scanner(monkeypatch, [RuntimeError("kite down"), [], _PICK])
    for _ in range(5):
        scan_agent._maybe_run_premarket_scan()
    assert len(calls) == 3 and scan_agent.market_data.watchlist == ["HCLTECH.NS"]


def test_scan_gives_up_after_three_attempts(scan_agent, monkeypatch):
    calls = _scanner(monkeypatch, [[]])
    for _ in range(6):
        scan_agent._maybe_run_premarket_scan()
    assert len(calls) == 3 and scan_agent.market_data.watchlist == ["DEFAULT.NS"]


def test_scan_summary_is_timestamped_and_survives_restart(scan_agent, monkeypatch):
    _scanner(monkeypatch, [_PICK])
    scan_agent._maybe_run_premarket_scan()
    summary = scan_agent._premarket_summary
    head = summary.splitlines()[0]
    assert "Pre-Market" not in head and " at " in head and "IST" in head
    assert "not live" in summary.splitlines()[1]
    # restart later in the day: the saved summary (with the original scan time) is reused
    scan_agent._scan_done_today = False
    scan_agent._premarket_summary = ""
    monkeypatch.setattr(pms, "PreMarketScanner", lambda *a, **k: pytest.fail("re-scanned"))
    scan_agent._maybe_run_premarket_scan()
    assert scan_agent._premarket_summary == summary
