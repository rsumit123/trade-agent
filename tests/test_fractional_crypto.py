"""Crypto agents can trade fractions of a coin; stocks stay whole shares."""
from agent.config import AgentConfig
from agent.decision_engine import _get_tool_defs
from agent.market_presets import get_preset
from agent.portfolio import Portfolio
from agent.runner import TradingAgent


def _qty_schema(market):
    cfg = AgentConfig(starting_capital=10_000, _market_preset=get_preset(market))
    [trade] = [t for t in _get_tool_defs(cfg, is_backtest=True) if t["name"] == "place_trade"]
    return trade["input_schema"]["properties"]["quantity"]


def test_trade_tool_accepts_fractions_only_for_crypto():
    assert _qty_schema("crypto")["type"] == "number"
    assert _qty_schema("nse")["type"] == "integer"


def _max_qty(market, price):
    a = TradingAgent.__new__(TradingAgent)
    a.preset = get_preset(market)
    a.config = AgentConfig(starting_capital=10_000, _market_preset=a.preset)
    rows = [{"ticker": "X", "current_price": price}]
    a._enrich_with_affordability(rows, {"cash": 10_000, "total_value": 10_000})
    return rows[0]["max_qty"]


def test_btc_max_qty_is_not_zero():
    # One BTC costs more than the whole budget; int() used to make this 0,
    # so the five BTC agents never traded.
    q = _max_qty("crypto", 110_000.0)
    assert 0 < q < 1
    assert q * 110_000.0 <= 10_000 * AgentConfig().max_position_pct + 1e-6


def test_stock_max_qty_stays_whole():
    assert isinstance(_max_qty("nse", 333.0), int)


def test_fractional_position_round_trips(tmp_path):
    pf = Portfolio(str(tmp_path / "t.db"), 10_000)
    pf.execute_buy("BTC-USD", 0.015, 100_000.0, "swing")
    [pos] = pf.get_open_positions()
    assert pos.quantity == 0.015
    pf.execute_sell(pos.id, 102_000.0)
    assert round(pf.get_closed_trades()[0].pnl, 2) == 30.0
