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
