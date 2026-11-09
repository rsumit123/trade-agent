"""Intraday stop floor (min_stop_a5): N x the 5-minute ATR, never under 0.35% of price,
estimated from the daily ATR early in the session, never widens an existing stop."""
from types import SimpleNamespace as NS

import pandas as pd

import agent.trade_context as tc
from agent.config import AgentConfig
from agent.decision_engine import _build_trading_rules, _get_tool_defs
from agent.market_presets import get_preset
from agent.portfolio import Portfolio
from agent.risk_manager import RiskManager
from agent.runner import TradingAgent
from agent.session import SessionConfig
import agent.session as session_mod


def _cfg(**session):
    sc = SessionConfig(session_id="t", market="nse-intraday", llm_provider="anthropic", llm_model="m", **session)
    sc.resolve_defaults()
    return AgentConfig.from_session(sc)


def _bars(n, rng=0.5):
    """n 5-minute bars, each with high-low = rng and no gaps (true range = rng)."""
    return [{"high": 100 + rng / 2, "low": 100 - rng / 2, "close": 100.0} for _ in range(n)]


def test_five_min_atr():
    assert abs(tc.five_min_atr(_bars(20, 0.5)) - 0.5) < 1e-9
    assert tc.five_min_atr(_bars(5)) is None                      # too few bars
    assert abs(tc.five_min_atr(pd.DataFrame(_bars(20, 0.8))) - 0.8) < 1e-9   # DataFrame input
    assert tc.five_min_atr(None) is None


class _Market:
    def __init__(self, price, bars=None, daily_atr=None):
        self.price, self.bars, self.daily_atr = price, bars, daily_atr

    def get_current_prices(self, tickers=None):
        return {t: self.price for t in tickers}

    def get_stock_context(self, ticker, include_vwap=False):
        return {"atr_14": self.daily_atr} if self.daily_atr else {}

    def get_intraday_data(self, ticker, interval="5minute"):
        if self.bars is None:
            raise RuntimeError("no data")
        return pd.DataFrame(self.bars)


def _agent(tmp_path, market, **session):
    tmp_path.mkdir(exist_ok=True)
    cfg = _cfg(**session)
    a = TradingAgent.__new__(TradingAgent)
    a.config, a.preset, a.session = cfg, cfg._market_preset, None
    a.portfolio = Portfolio(str(tmp_path / "t.db"), 500_000)
    a.risk_manager = RiskManager(cfg, a.portfolio)
    a.market_data = market
    a._auto_exits, a._is_backtest = [], False
    return a


def test_floor_uses_the_five_minute_atr(tmp_path):
    a = _agent(tmp_path, _Market(500.0, _bars(20, 2.0)), min_stop_a5=3.0)         # ATR 2.0 -> floor 6.0 (1.2%)
    t = a.portfolio.execute_buy("X.NS", 10, 500.0, "intraday", stop_price=480.0, atr_14=12.0)
    r = a._update_levels({"trade_id": t.id, "stop_price": 497.0, "reason": "tight"})
    assert r["stop_price"] == 494.0 and "5-minute ATR" in r["note"]               # 500 - 3 x 2.0
    ok = a._update_levels({"trade_id": t.id, "stop_price": 492.0, "reason": "wide enough"})
    assert ok["stop_price"] == 492.0 and "note" not in ok


def test_floor_never_goes_below_0_35_percent_of_price(tmp_path):
    a = _agent(tmp_path, _Market(1000.0, _bars(20, 0.2)), min_stop_a5=3.0)         # 3 x 0.2 = 0.6, but 0.35% = 3.5
    t = a.portfolio.execute_short("X.NS", 5, 1000.0, stop_price=1020.0, atr_14=25.0)
    r = a._update_levels({"trade_id": t.id, "stop_price": 1002.0, "reason": "tight"})
    assert r["stop_price"] == 1003.5


def test_early_in_the_session_the_atr_is_estimated_from_the_daily_atr(tmp_path):
    a = _agent(tmp_path, _Market(500.0, _bars(3), daily_atr=12.0), min_stop_a5=3.0)  # 12/12 = 1.0 -> floor 3.0
    t = a.portfolio.execute_buy("X.NS", 10, 500.0, "intraday", stop_price=480.0, atr_14=12.0)
    r = a._update_levels({"trade_id": t.id, "stop_price": 499.0, "reason": "tight"})
    assert r["stop_price"] == 497.0 and "estimated" in r["note"]


def test_floor_never_widens_an_existing_stop(tmp_path):
    a = _agent(tmp_path, _Market(495.0, _bars(20, 2.0)), min_stop_a5=3.0)          # band edge 489, existing stop 490
    t = a.portfolio.execute_buy("X.NS", 10, 500.0, "intraday", stop_price=490.0, atr_14=12.0)
    r = a._update_levels({"trade_id": t.id, "stop_price": 493.0, "reason": "cut loss"})
    assert r["stop_price"] == 490.0 and "kept at 490.0" in r["note"]


def test_data_failure_leaves_the_requested_stop_alone(tmp_path):
    a = _agent(tmp_path, _Market(500.0, bars=None, daily_atr=None), min_stop_a5=3.0)
    t = a.portfolio.execute_buy("X.NS", 10, 500.0, "intraday", stop_price=480.0, atr_14=None)
    assert a._update_levels({"trade_id": t.id, "stop_price": 499.0, "reason": "r"})["stop_price"] == 499.0


def test_both_floors_the_wider_wins(tmp_path):
    a = _agent(tmp_path, _Market(500.0, _bars(20, 0.5)), min_stop_a5=3.0, min_stop_atr=0.5)   # a5 floor 1.75; daily 0.5 x 12 = 6
    t = a.portfolio.execute_buy("X.NS", 10, 500.0, "intraday", stop_price=480.0, atr_14=12.0)
    assert a._update_levels({"trade_id": t.id, "stop_price": 498.0, "reason": "r"})["stop_price"] == 494.0


def test_prompt_and_tool_describe_the_5_minute_floor():
    preset = get_preset("nse-intraday")
    rules = _build_trading_rules(preset, None, 3.0)
    assert "3.0× the 5-minute ATR" in rules and "1.5× the stop distance" in rules and "-0.3% to -0.5%" not in rules
    desc = next(t for t in _get_tool_defs(_cfg(min_stop_a5=3.0)) if t["name"] == "update_levels")["description"]
    assert "5-minute ATR" in desc
    assert "ATR" not in next(t for t in _get_tool_defs(_cfg()) if t["name"] == "update_levels")["description"]


def test_field_survives_yaml_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr(session_mod, "SESSIONS_DIR", tmp_path)
    session_mod.save_session(SessionConfig(session_id="t", market="nse-intraday", min_stop_a5=3.0))
    assert session_mod.load_session("t").min_stop_a5 == 3.0


def test_liquidity_uses_the_20_day_average_turnover():
    quote = {"last_price": 100.0, "volume": 1000, "average_price": 100.0,
             "ohlc": {"open": 100, "high": 101, "low": 99, "close": 100}}     # today so far: tiny turnover
    ctx = tc.build(quote, {"atr_14": 2.0, "current_price": 100.0, "avg_volume_20d": 40_000_000},
                   None, tc.datetime(2026, 11, 9, 9, 40))
    assert ctx["avg_turnover_cr"] == 400.0 and ctx["liquidity"] == "high" and ctx["turnover_cr"] < 1
    ctx2 = tc.build(quote, None, None, tc.datetime(2026, 11, 9, 9, 40))       # no history: falls back
    assert ctx2["liquidity"] == "low"
