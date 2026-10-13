"""NSE agents review once after the close, even though they run all day."""
from datetime import datetime
from types import SimpleNamespace as NS
from zoneinfo import ZoneInfo

from agent.config import AgentConfig
from agent.market_presets import get_preset
from agent.portfolio import Portfolio
from agent.risk_manager import RiskManager
from agent.runner import TradingAgent

IST = ZoneInfo("Asia/Kolkata")


def _agent(tmp_path):
    cfg = AgentConfig(starting_capital=500_000, _market_preset=get_preset("nse"))
    a = TradingAgent.__new__(TradingAgent)
    a.config = cfg
    a.portfolio = Portfolio(str(tmp_path / "t.db"), 500_000)
    a.risk_manager = RiskManager(cfg, a.portfolio)
    a.session = NS(session_dir=tmp_path)
    a._last_review_date = None
    a.market = {"open": True}
    a.market_data = NS(market_preset=get_preset("nse"), is_market_open=lambda: a.market["open"])
    a.reviews = []
    a.run_daily_review = lambda: a.reviews.append(1)
    return a


def at(h, m, day=13):
    return datetime(2026, 10, day, h, m, tzinfo=IST)   # 2026-10-13 is a Tuesday


def test_review_runs_once_after_close_for_an_agent_running_all_day(tmp_path):
    a = _agent(tmp_path)
    a.market["open"] = False
    a._maybe_run_daily_review(at(8, 0))          # pre-market: nothing to review
    a.market["open"] = True
    for h, m in [(9, 15), (12, 0), (15, 20)]:     # loops during the session
        a._maybe_run_daily_review(at(h, m))
    assert a.reviews == []
    a.market["open"] = False
    a._maybe_run_daily_review(at(15, 31))
    a._maybe_run_daily_review(at(15, 32))
    assert a.reviews == [1]


def test_restart_after_review_does_not_repeat_it(tmp_path):
    a = _agent(tmp_path)
    a.market["open"] = False
    a._maybe_run_daily_review(at(15, 40))
    b = _agent(tmp_path)                          # same session dir, fresh process
    b.market["open"] = False
    b._maybe_run_daily_review(at(16, 0))
    assert a.reviews == [1] and b.reviews == []
    b._maybe_run_daily_review(at(15, 40, day=14))  # next trading day
    assert b.reviews == [1]


def test_no_review_on_weekends(tmp_path):
    a = _agent(tmp_path)
    a.market["open"] = False
    a._maybe_run_daily_review(at(16, 0, day=17))  # Saturday
    assert a.reviews == []
