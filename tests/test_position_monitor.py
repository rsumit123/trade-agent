"""Stops/targets fire between decision cycles, and the agent is told about it next cycle."""
from types import SimpleNamespace as NS

import pytest

import agent.runner as runner_mod
from agent.config import AgentConfig
from agent.market_presets import get_preset
from agent.portfolio import Portfolio
from agent.risk_manager import RiskManager
from agent.runner import TradingAgent


class _Market:
    def __init__(self, prices=None, open_=True, fail=False):
        self.prices, self.open_, self.fail, self.calls = prices or {}, open_, fail, []

    def is_market_open(self):
        return self.open_

    def get_current_prices(self, tickers=None):
        self.calls.append(tickers)
        if self.fail:
            raise RuntimeError("429 Too many requests")
        return {t: self.prices[t] for t in (tickers or []) if t in self.prices}


class _Learner:
    def __init__(self):
        self.logged = []

    def write_trade_log(self, trade, llm_client=None):
        self.logged.append(trade)


@pytest.fixture
def agent(tmp_path):
    cfg = AgentConfig(starting_capital=500_000, _market_preset=get_preset("nse"))
    a = TradingAgent.__new__(TradingAgent)
    a.config = cfg
    a.preset = get_preset("nse")
    a.portfolio = Portfolio(str(tmp_path / "t.db"), 500_000)
    a.risk_manager = RiskManager(cfg, a.portfolio)
    a.learner = _Learner()
    a.engine = NS(client=None)
    a._auto_exits = []
    a._is_backtest = False
    return a


def _short_sbin(agent):
    agent.portfolio.execute_short("SBIN.NS", 105, 939.05, reason="breakdown",
                                  stop_price=943.8, target_price=933.5)


def test_short_stop_fires_between_cycles(agent):
    _short_sbin(agent)
    agent.market_data = _Market({"SBIN.NS": 943.9})       # the 12:18 touch
    agent._monitor_positions()

    assert agent.portfolio.get_open_positions() == []
    (trade,) = agent.learner.logged
    assert trade.exit_type == "stop_hit" and trade.exit_price == 943.9
    assert agent.market_data.calls == [["SBIN.NS"]]        # only held tickers are quoted
    (note,) = agent._auto_exits
    assert "SBIN.NS short" in note and "STOPPED OUT" in note and "₹943.90" in note and "IST" in note


def test_long_target_fires_between_cycles(agent):
    agent.portfolio.execute_buy("ASHOKLEY.NS", 600, 147.41, "intraday", stop_price=146.8, target_price=148.0)
    agent.market_data = _Market({"ASHOKLEY.NS": 148.05})
    agent._monitor_positions()
    assert agent.learner.logged[0].exit_type == "target_hit"
    assert "hit its TARGET" in agent._auto_exits[0]


def test_no_exit_inside_levels(agent):
    _short_sbin(agent)
    agent.market_data = _Market({"SBIN.NS": 941.0})
    agent._monitor_positions()
    assert len(agent.portfolio.get_open_positions()) == 1 and agent._auto_exits == []


@pytest.mark.parametrize("market", [
    _Market({"SBIN.NS": 950.0}, open_=False),              # market closed
    _Market(fail=True),                                     # quote rate-limited / failing
    _Market({}),                                            # no price returned
])
def test_skips_minute_without_raising(agent, market):
    _short_sbin(agent)
    agent.market_data = market
    agent._monitor_positions()                              # must not raise
    assert len(agent.portfolio.get_open_positions()) == 1


def test_no_quotes_without_open_positions(agent):
    agent.market_data = _Market({"SBIN.NS": 950.0})
    agent._monitor_positions()
    assert agent.market_data.calls == []


def test_sleep_checks_every_minute_until_next_cycle(agent, monkeypatch):
    clock = {"t": 0.0}
    monkeypatch.setattr(runner_mod.time, "monotonic", lambda: clock["t"])
    monkeypatch.setattr(runner_mod.time, "sleep", lambda s: clock.__setitem__("t", clock["t"] + s))
    monkeypatch.setattr(runner_mod.random, "uniform", lambda a, b: 0)
    checks = []
    monkeypatch.setattr(agent, "_monitor_positions", lambda: checks.append(clock["t"]))

    agent._sleep_monitoring_positions(10 * 60)

    assert clock["t"] == 600
    assert checks == [60.0 * i for i in range(1, 10)]       # not at 600: the cycle itself sweeps
