"""screen_stocks: whole-universe screener tool for opted-in Kite sessions."""
import json
from types import SimpleNamespace as NS

import pytest

import agent.screener as scr
from agent.config import AgentConfig
from agent.decision_engine import _get_tool_defs
from agent.runner import TradingAgent
from agent.session import SessionConfig


def _q(ltp, prev, opn=None, hi=None, lo=None, vwap=None, vol=1_000_000, upper=None):
    return {"last_price": ltp, "volume": vol, "average_price": vwap or ltp,
            "upper_circuit_limit": upper or prev * 1.2, "lower_circuit_limit": prev * 0.8,
            "ohlc": {"open": opn or prev, "high": hi or max(ltp, opn or prev),
                     "low": lo or min(ltp, opn or prev), "close": prev}}


def _rows():
    quotes = {
        "HOT": _q(113, 100, opn=105, hi=114, lo=104, vwap=110),       # +13%, extended
        "MILD": _q(103, 100, opn=101, hi=103.5, lo=100.5, vwap=102),   # +3%, above VWAP
        "WEAK": _q(97, 100, opn=99, hi=99.5, lo=96.5, vwap=98),        # -3%, below VWAP
        "TINY": _q(103, 100, vol=1000),                                # illiquid
        "PENNY": _q(20, 19),                                           # under ₹50
    }
    return [scr.row_from_quote(s, q) for s, q in quotes.items()]


def test_row_from_quote_computes_moves_vwap_and_turnover():
    r = scr.row_from_quote("MILD", _q(103, 100, opn=101, hi=103.5, lo=100.5, vwap=102))
    assert r["ticker"] == "MILD.NS"
    assert (r["change_pct"], r["gap_pct"], r["from_open_pct"]) == (3.0, 1.0, 1.98)
    assert r["range_pos"] == 83 and r["vs_vwap_pct"] == 0.98
    assert r["turnover_cr"] == 10.2  # 1M shares x ₹102 VWAP
    assert scr.row_from_quote("X", {"last_price": 0, "ohlc": {"close": 10}}) is None


def test_upper_circuit_is_flagged():
    assert scr.row_from_quote("UC", _q(120, 100, upper=120))["at_circuit"] == "upper"


def test_defaults_drop_illiquid_and_penny_stocks():
    tickers = [r["ticker"] for r in scr.filter_and_rank(_rows())]
    assert tickers == ["HOT.NS", "MILD.NS", "WEAK.NS"]  # sorted by turnover


def test_change_band_excludes_extended_movers():
    got = scr.filter_and_rank(_rows(), change_pct_min=1, change_pct_max=5, vs_vwap="above")
    assert [r["ticker"] for r in got] == ["MILD.NS"]


def test_below_vwap_and_ascending_sort_finds_weak_names():
    got = scr.filter_and_rank(_rows(), vs_vwap="below", sort_by="change_asc")
    assert [r["ticker"] for r in got] == ["WEAK.NS"]
    got = scr.filter_and_rank(_rows(), sort_by="change_asc")
    assert [r["ticker"] for r in got] == ["WEAK.NS", "MILD.NS", "HOT.NS"]


def test_sector_filter_matches_part_of_the_index_name():
    sectors = {"MILD.NS": ["IT"], "HOT.NS": ["Metal"], "WEAK.NS": ["Utilities", "PSU Bank"]}
    got = scr.filter_and_rank(_rows(), sectors, sector="it")
    assert [r["ticker"] for r in got] == ["MILD.NS"] and got[0]["sector"] == "IT"
    assert [r["ticker"] for r in scr.filter_and_rank(_rows(), sectors, sector="bank")] == ["WEAK.NS"]


class _Kite:
    def __init__(self, quotes, fail_first=0):
        self.quotes, self.calls, self.fail_first = quotes, 0, fail_first

    def quote(self, syms):
        self.calls += 1
        if self.calls <= self.fail_first:
            raise Exception("Unknown Content-Type (text/html) Just a moment")
        return {s: self.quotes[s.split(":")[1]] for s in syms if s.split(":")[1] in self.quotes}


def _screener(monkeypatch, tmp_path, kite, universe_filter=None):
    monkeypatch.setattr(scr, "SNAPSHOT_CACHE", tmp_path / "snap.json")
    monkeypatch.setattr(scr._time, "sleep", lambda s: None)
    md = NS(kite=kite, _call_with_retry=lambda label, fn: fn(kite))
    s = scr.StockScreener(md, universe_filter)
    s._syms = ["HOT", "MILD", "WEAK", "TINY", "PENNY"]
    s._sector_map = {}
    return s


def test_screen_returns_rows_and_caches_the_snapshot(monkeypatch, tmp_path):
    kite = _Kite({"HOT": _q(113, 100), "MILD": _q(103, 100)})
    s = _screener(monkeypatch, tmp_path, kite)
    out = s.screen(limit=5)
    assert [r["ticker"] for r in out["results"]] == ["HOT.NS", "MILD.NS"]
    assert out["universe_scanned"] == 2 and "incomplete" in out  # 3 of 5 had no data
    s.screen(change_pct_max=5)
    assert kite.calls == 1  # second screen reused the cached snapshot
    assert json.loads((tmp_path / "snap.json").read_text())["rows"]


def test_failed_batch_is_bisected_not_raised(monkeypatch, tmp_path):
    monkeypatch.setattr(scr, "QUOTE_BATCH_SIZE", 5)
    kite = _Kite({"HOT": _q(113, 100), "MILD": _q(103, 100)}, fail_first=2)
    out = _screener(monkeypatch, tmp_path, kite).screen()
    assert {r["ticker"] for r in out["results"]} == {"HOT.NS", "MILD.NS"}


def test_kite_down_returns_an_error_not_an_exception(monkeypatch, tmp_path):
    kite = _Kite({}, fail_first=10**6)
    out = _screener(monkeypatch, tmp_path, kite).screen()
    assert "error" in out


def test_live_session_only_sees_intraday_allowed_stocks(monkeypatch, tmp_path):
    kite = _Kite({"HOT": _q(113, 100), "MILD": _q(103, 100), "WEAK": _q(97, 100)})
    out = _screener(monkeypatch, tmp_path, kite,
                    universe_filter=lambda ts: [t for t in ts if t != "HOT.NS"]).screen()
    assert [r["ticker"] for r in out["results"]] == ["MILD.NS", "WEAK.NS"]


def _cfg(**session):
    sc = SessionConfig(session_id="t", market="nse-intraday", **session)
    sc.resolve_defaults()
    return AgentConfig.from_session(sc)


def _tool_names(cfg, is_backtest=False):
    return [t["name"] for t in _get_tool_defs(cfg, is_backtest=is_backtest)]


def test_tool_offered_only_to_opted_in_kite_sessions_outside_backtest():
    assert "screen_stocks" in _tool_names(_cfg(screener=True, data_source="kite"))
    assert "screen_stocks" not in _tool_names(_cfg(screener=True, data_source="kite"), is_backtest=True)
    assert "screen_stocks" not in _tool_names(_cfg(screener=True, data_source="yfinance"))
    assert "screen_stocks" not in _tool_names(_cfg(data_source="kite"))


def test_runner_refuses_the_tool_when_not_enabled():
    a = TradingAgent.__new__(TradingAgent)
    a.config = _cfg(data_source="kite")
    a.market_data = NS(kite=object())
    assert "error" in a.handle_tool_call("screen_stocks", {})


def test_screener_flag_survives_yaml_round_trip(tmp_path, monkeypatch):
    import agent.session as session_mod
    monkeypatch.setattr(session_mod, "SESSIONS_DIR", tmp_path)
    session_mod.save_session(SessionConfig(session_id="t", market="nse-intraday", screener=True))
    assert session_mod.load_session("t").screener is True
