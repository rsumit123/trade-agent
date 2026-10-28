"""Setup tags + entry context on every new position, and the per-setup summary."""
import json
from datetime import datetime
from types import SimpleNamespace as NS

import agent.trade_context as tc
from agent.config import AgentConfig
from agent.portfolio import Portfolio
from agent.risk_manager import RiskManager
from agent.runner import TradingAgent
from agent.session import SessionConfig
from agent.web_research import LLMWebSearchTool

QUOTE = {"last_price": 103.0, "volume": 2_000_000, "average_price": 102.0,
         "ohlc": {"open": 101.0, "high": 103.5, "low": 100.5, "close": 100.0}}
MARKET = {"indices": [
    {"name": "NIFTY 50", "chg_prev_pct": 0.8, "chg_open_pct": 0.5, "range_pos_pct": 75, "trend_pct": 0.1},
    {"name": "BANK NIFTY", "chg_open_pct": 0.3},
    {"name": "INDIA VIX", "last": 14.2, "chg_prev_pct": -2.0}],
    "breadth": {"up_open": 38, "down_open": 12}}


def test_build_measures_stock_and_market_at_entry():
    ctx = tc.build(QUOTE, {"atr_14": 2.0, "current_price": 103.0, "vol_ratio": 1.8, "rsi_14": 61},
                   MARKET, datetime(2026, 10, 29, 9, 50), sector="IT")
    assert ctx["time_bucket"] == "open" and ctx["time"] == "09:50"
    assert (ctx["gap_pct"], ctx["from_open_pct"], ctx["change_pct"]) == (1.0, 1.98, 3.0)
    assert ctx["vs_vwap_atr"] == 0.5 and ctx["atr_pct"] == 1.94
    assert ctx["liquidity"] == "low" and ctx["turnover_cr"] == 20.4
    assert ctx["regime"] == "trend_up" and ctx["rs_vs_nifty"] == 1.48
    assert ctx["vix"] == 14.2 and ctx["breadth_up_open"] == 38 and ctx["sector"] == "IT"


def test_build_survives_missing_inputs():
    ctx = tc.build(None, None, None, datetime(2026, 10, 29, 14, 0))
    assert ctx == {"time": "14:00", "time_bucket": "late"}


def test_buckets_and_regime():
    assert tc.time_bucket(datetime(2026, 1, 1, 11, 0)) == "midday"
    assert [tc.liquidity_bucket(x) for x in (500, 50, 5, None)] == ["high", "mid", "low", None]
    assert tc.regime(-0.6, 20) == "trend_down" and tc.regime(0.6, 50) == "range"
    assert tc.regime(None, 50) is None


def test_setup_normalisation():
    assert tc.normalise_setup("Gap_Fade") == "gap_fade"
    assert tc.normalise_setup(None) == "untagged"
    assert tc.normalise_setup("my secret pattern") == "other"


def test_charges_match_groww_ballpark():
    assert 24 <= tc.est_intraday_charges(10_000, 10_050) <= 30


def test_trade_tool_offers_the_setup_list():
    prop = LLMWebSearchTool.get_trade_tool()["input_schema"]["properties"]["setup"]
    assert prop["enum"] == list(tc.SETUPS) and "gap_fade" in prop["description"]


def test_portfolio_persists_setup_and_context(tmp_path):
    pf = Portfolio(str(tmp_path / "t.db"), 100_000)
    pf.execute_short("A.NS", 1, 100.0, setup="vwap_cross", entry_context='{"regime": "range"}')
    t = pf.get_open_positions()[0]
    assert t.setup == "vwap_cross" and json.loads(t.entry_context) == {"regime": "range"}


class _Kite:
    def quote(self, syms):
        return {s: QUOTE for s in syms}


class _Market:
    kite = _Kite()

    def get_current_prices(self, tickers=None):
        return {t: 103.0 for t in tickers}

    def get_stock_context(self, ticker, include_vwap=False):
        return {"atr_14": 2.0, "current_price": 103.0, "vol_ratio": 1.8}

    def _call_with_retry(self, label, fn):
        return fn(self.kite)

    def _kite_symbol(self, t):
        return "NSE:" + t.replace(".NS", "")


def _agent(tmp_path, monkeypatch):
    monkeypatch.setattr(tc, "datetime", NS(now=lambda tz=None: datetime(2026, 10, 29, 10, 0)), raising=False)
    sc = SessionConfig(session_id="t", market="nse-intraday")
    sc.resolve_defaults()
    cfg = AgentConfig.from_session(sc)
    a = TradingAgent.__new__(TradingAgent)
    a.config, a.preset, a.session = cfg, cfg._market_preset, None
    a.portfolio = Portfolio(str(tmp_path / "t.db"), 500_000)
    a.risk_manager = RiskManager(cfg, a.portfolio)
    a.risk_manager.check_intraday_close = lambda now=None: False
    a.market_data = _Market()
    a.learner = NS(write_trade_log=lambda *x, **k: None)
    a._auto_exits, a._is_backtest, a._last_market_ctx = [], False, MARKET
    return a


def test_buy_and_short_record_setup_and_context(tmp_path, monkeypatch):
    a = _agent(tmp_path, monkeypatch)
    r = a._execute_trade({"action": "BUY", "ticker": "A.NS", "quantity": 10, "reason": "r",
                          "conviction": 4, "trade_type": "intraday", "setup": "orb_breakout"})
    assert r["success"] and r["setup"] == "orb_breakout"
    r2 = a._execute_trade({"action": "SHORT", "ticker": "B.NS", "quantity": 10, "reason": "r", "conviction": 4})
    assert r2["success"] and r2["setup"] == "untagged"
    by_ticker = {t.ticker: t for t in a.portfolio.get_open_positions()}
    ctx = json.loads(by_ticker["A.NS"].entry_context)
    assert ctx["regime"] == "trend_up" and ctx["gap_pct"] == 1.0 and "vs_vwap_atr" in ctx
    assert by_ticker["B.NS"].setup == "untagged" and by_ticker["B.NS"].entry_context


def test_summarise_groups_and_nets_out_charges():
    trades = [
        {"setup": "gap_fade", "direction": "short", "quantity": 100, "entry_price": 100.0,
         "exit_price": 98.0, "pnl": 200.0, "exit_type": "target_hit",
         "entry_context": json.dumps({"regime": "trend_down"})},
        {"setup": "gap_fade", "direction": "short", "quantity": 100, "entry_price": 100.0,
         "exit_price": 99.95, "pnl": 5.0, "exit_type": "manual",
         "entry_context": json.dumps({"regime": "range"})},
        {"setup": None, "direction": "long", "quantity": 1, "entry_price": 10.0,
         "exit_price": 11.0, "pnl": 1.0},
        {"setup": "gap_fade", "direction": "short", "quantity": 1, "entry_price": 1.0,
         "exit_price": None, "pnl": None},                      # still open: ignored
    ]
    rows = tc.summarise(trades)
    gf = next(r for r in rows if r["setup"] == "gap_fade")
    assert gf["n"] == 2 and gf["win_rate"] == 100.0 and gf["net_win_rate"] == 50.0   # +₹5 scratch nets negative
    assert gf["gross"] == 205.0 and gf["net"] < 205.0 and gf["exits"] == {"target_hit": 1, "manual": 1}
    assert any(r["setup"] == "untagged" for r in rows)
    split = tc.summarise(trades, by="regime")
    assert {r["regime"] for r in split if r["setup"] == "gap_fade"} == {"trend_down", "range"}
