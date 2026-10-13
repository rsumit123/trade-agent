"""EMA(50) and the EMA trend must be available, or the prompt's trend signal is dead."""
import numpy as np
import pandas as pd

import agent.market_data as md


class _FakeTicker:
    requested = {}

    def __init__(self, ticker):
        pass

    def history(self, period, interval):
        _FakeTicker.requested = {"period": period, "interval": interval}
        days = int(period.rstrip("d"))
        idx = pd.date_range(end="2026-10-12", periods=days, freq="D")
        close = pd.Series(np.linspace(100, 150, days), index=idx)    # steady uptrend
        return pd.DataFrame({"Open": close, "High": close + 1, "Low": close - 1,
                             "Close": close, "Volume": 1000.0})


def test_stock_context_has_ema50_and_trend(monkeypatch):
    monkeypatch.setattr(md.yf, "Ticker", _FakeTicker)
    ctx = md.MarketData(["BTC-USD"]).get_stock_context("BTC-USD", include_vwap=False)
    assert int(_FakeTicker.requested["period"].rstrip("d")) >= 60
    assert ctx["ema_50"] is not None
    assert ctx["ema_trend"] == "bullish"


def test_current_price_single_ticker_multiindex(monkeypatch):
    # yfinance now returns ("Close", ticker) columns even for one ticker; this
    # used to raise "truth value of a Series is ambiguous" on every BTC cycle.
    idx = pd.date_range(end="2026-10-13 03:00", periods=3, freq="min")
    cols = pd.MultiIndex.from_product([["Close", "Volume"], ["BTC-USD"]])
    data = pd.DataFrame([[82700.0, 1], [82710.0, 1], [np.nan, 1]], index=idx, columns=cols)
    monkeypatch.setattr(md.yf, "download", lambda *a, **k: data)
    monkeypatch.setattr(md.yf, "Ticker", lambda t: (_ for _ in ()).throw(AssertionError("fallback used")))
    assert md.MarketData(["BTC-USD"]).get_current_prices() == {"BTC-USD": 82710.0}


def test_prices_incl_positions_quotes_holdings_outside_watchlist(tmp_path):
    # Scanner sessions hold stocks the dashboard's watchlist doesn't contain;
    # they used to be valued at entry price (₹0 P&L).
    from agent.market_data import prices_incl_positions
    from agent.portfolio import Portfolio

    class FakeData:
        watchlist = ["RELIANCE.NS"]
        def get_current_prices(self, tickers=None):
            self.asked = tickers
            return {t: 100.0 for t in (tickers or self.watchlist)}

    pf = Portfolio(str(tmp_path / "t.db"), 500_000)
    data = FakeData()
    assert prices_incl_positions(data, pf) == {"RELIANCE.NS": 100.0}
    pf.execute_buy("HCLTECH.NS", 10, 90.0, "intraday")
    prices = prices_incl_positions(data, pf)
    assert data.asked == ["RELIANCE.NS", "HCLTECH.NS"]
    assert pf.get_portfolio_summary(prices)["holdings"][0]["unrealized_pnl"] == 100.0
