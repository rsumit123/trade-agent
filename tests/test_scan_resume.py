"""A restarted agent resumes today's pre-market picks instead of re-scanning."""
from datetime import date
from types import SimpleNamespace as NS

import pytest

import agent.premarket_scanner as pms
import agent.session as sess
from agent.config import AgentConfig
from agent.market_presets import get_preset
from agent.runner import TradingAgent


@pytest.fixture
def agent(tmp_path, monkeypatch):
    sc = sess.SessionConfig(session_id="s1", market="nse-intraday")
    monkeypatch.setattr(type(sc), "session_dir", property(lambda self: tmp_path))
    monkeypatch.setattr(sess, "load_session", lambda sid: sc)
    a = TradingAgent.__new__(TradingAgent)
    a.config = AgentConfig(watchlist=["DEFAULT.NS"], _market_preset=get_preset("nse-intraday"))
    a.preset = get_preset("nse-intraday")
    a.session = sc
    a.market_data = NS(kite=object(), watchlist=["DEFAULT.NS"])
    return a


def test_restart_resumes_todays_picks(agent, monkeypatch):
    agent.session.write_scan_picks(["TRF.NS", "GOODLUCK.NS"], "## Pre-Market Picks — 2")
    monkeypatch.setattr(pms, "PreMarketScanner", lambda *a, **k: pytest.fail("re-scanned"))
    agent._maybe_run_premarket_scan()
    assert agent.market_data.watchlist == ["TRF.NS", "GOODLUCK.NS"]
    assert agent._premarket_summary == "## Pre-Market Picks — 2"


def test_yesterdays_picks_are_not_reused(agent):
    agent.session.write_scan_picks(["OLD.NS"])
    path = agent.session.session_dir / "scan_picks.json"
    path.write_text(path.read_text().replace(date.today().isoformat(), "2000-01-01"))
    assert agent.session.read_scan_picks(date.today().isoformat()) is None


def _scanner_spy(monkeypatch, calls):
    class Spy:
        def __init__(self, *a, **k):
            pass

        def scan(self, **k):
            calls.append(k)
            return [{"ticker": "HCLTECH.NS", "ltp": 1500.0, "gap_pct": -0.5, "llm_direction": "short"}]
    monkeypatch.setattr(pms, "PreMarketScanner", Spy)


def test_no_scan_before_the_open(agent, monkeypatch):
    # At 08:30 Kite still quotes yesterday's candle, so the "gap" is yesterday's
    # open vs close: HCLTECH's +8.4% day on 10-13 was ranked a -6.1% gap-down.
    calls = []
    _scanner_spy(monkeypatch, calls)
    agent.market_data.is_market_open = lambda: False
    agent._maybe_run_premarket_scan()
    assert calls == [] and agent.market_data.watchlist == ["DEFAULT.NS"]

    agent.market_data.is_market_open = lambda: True        # 09:15: scan, once
    agent._maybe_run_premarket_scan()
    agent._maybe_run_premarket_scan()
    assert len(calls) == 1 and agent.market_data.watchlist == ["HCLTECH.NS"]
