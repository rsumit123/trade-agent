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
