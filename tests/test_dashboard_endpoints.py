import dashboard.app as app
from agent.portfolio import Portfolio


class FakeData:
    watchlist = ["RELIANCE.NS"]

    def get_current_prices(self, tickers=None):
        return {t: 110.0 for t in (tickers or self.watchlist)}


class FakeRisk:
    def __init__(self, pf):
        self.pf = pf

    def get_risk_status(self, prices):
        return self.pf.get_portfolio_summary(prices)


def _components(tmp_path):
    pf = Portfolio(str(tmp_path / "t.db"), 500_000)
    pf.execute_buy("HCLTECH.NS", 10, 100.0, "intraday")  # not in the watchlist
    return {"portfolio": pf, "market_data": FakeData(), "risk_manager": FakeRisk(pf)}


def test_portfolio_endpoint_prices_holdings_outside_watchlist(tmp_path, monkeypatch):
    # /api/portfolio and /api/risk only priced the watchlist, so scanner
    # holdings showed entry price = current price and zero P&L.
    c = _components(tmp_path)
    monkeypatch.setattr(app, "_get_components", lambda sid=None: c)
    h = app.get_portfolio(session=None, user={})["holdings"][0]
    assert h["current_price"] == 110.0 and h["unrealized_pnl"] == 100.0


def test_risk_endpoint_prices_holdings_outside_watchlist(tmp_path, monkeypatch):
    c = _components(tmp_path)
    monkeypatch.setattr(app, "_get_components", lambda sid=None: c)
    assert app.get_risk_status(session=None, user={})["holdings"][0]["unrealized_pnl"] == 100.0


def test_dashboard_performance_is_live_phase_and_uncapped(tmp_path, monkeypatch):
    # The bundle's performance came from the learner: last 100 closed trades of
    # any phase, so hero/Insights disagreed with the list card and /api/performance.
    import sqlite3
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    pf = Portfolio(str(tmp_path / "t.db"), 500_000)
    for _ in range(120):
        t = pf.execute_buy("AAA.NS", 1, 100.0, "intraday")
        pf.execute_sell(t.id, 101.0)
    with sqlite3.connect(pf.db_path) as conn:  # first 10 = pre-Go-Live backtest trades
        conn.execute("UPDATE trades SET entry_time = '2020-01-01T00:00:00' WHERE id <= 10")
    sc = MagicMock()
    sc.session_dir = tmp_path
    sc.live_started_at = "2021-01-01T00:00:00"
    sc.resolve_universe.return_value = ("discovery", None)
    c = {
        "portfolio": pf, "market_data": FakeData(), "risk_manager": FakeRisk(pf),
        "learner": SimpleNamespace(get_performance_stats=lambda: {"total_trades": 100}),
        "session": sc, "config": MagicMock(watchlist=[]), "preset": MagicMock(),
    }
    monkeypatch.setattr(app, "_get_components", lambda sid=None: c)
    monkeypatch.setattr(app, "_ensure_session_access", lambda sid, user: sc)
    monkeypatch.setattr(app, "_is_agent_running", lambda sid: {"running": False, "pid": None})
    assert app.get_dashboard("s1", user={})["performance"]["total_trades"] == 110
