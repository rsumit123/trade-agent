"""Audit g2: scanner phase-1 batch robustness + non-equity instrument filter."""
import json
import logging

import pytest

from agent import premarket_scanner as ps
from agent.premarket_scanner import PreMarketScanner, _is_tradable_equity


# ── Fix 2: instrument filter ──────────────────────────────

@pytest.mark.parametrize("sym,name", [
    ("RELIANCE", "RELIANCE INDUSTRIES"), ("BAJAJ-AUTO", "BAJAJ AUTO"), ("M&M", "MAHINDRA & MAHINDRA"),
    ("J&KBANK", "J & K BANK"), ("NAM-INDIA", "NIPPON L I A M"), ("HCL-INSYS", "HCL INFOSYSTEMS"),
    ("BOSCH-HCIL", "BOSCH HOME COMFRT IND"), ("UMIYA-MRO", "UMIYA BUILDCON"), ("BDR-IT", "BDR BUILDCON"),
    ("PRANAV", "PRANAV CONSTRUCTIONS"), ("MODIS", "MODIS NAVNIRMAN"),
])
def test_real_equities_kept(sym, name):
    assert _is_tradable_equity(sym, name)


@pytest.mark.parametrize("sym,name", [
    ("ROCKPP-E1", "ROCKINGDCE RS.5 PPD UP"), ("ATLPP-E1", "ATL RE.0.50 PPD UP"), ("JAYKAYPP-E1", "JAYKAY RE.0.50 PPD UP"),
    ("ICICISINAV", "ICICISILVE NAV"), ("KOTLIQINAV", "KOTAKMAMC - KOTLIQINAV"), ("HDFCSIINAV-RL", ""),
    ("MHLSRG-SF", ""), ("MORARJEE-BZ", "MORARJEE TEXTILES"), ("CAPINVIT-IV", "CAPITAL INFRA TRUST - INVIT"),
    ("KRT-RR", "KNOWLEDGE REALTY TRUST - REIT"), ("SGBSEP28VI-GB", "2.50%GOLDBONDS2028SR-VI"),
    ("AAFS28A-N0", ""), ("MOTHERSON-D1", ""), ("KLBRENG-B", "KILBURN ENGINEERING"), ("PERFECT-SZ", "X"),
    ("ELECTCAST-W1", "ELECTCAST WARRANTS"), ("SCL310524A-ZK", ""),
])
def test_non_equities_dropped(sym, name):
    assert not _is_tradable_equity(sym, name)


def test_filter_applied_on_cache_load(tmp_path, monkeypatch):
    cache = tmp_path / "inst.json"
    cache.write_text(json.dumps({"date": ps.datetime.now().isoformat(), "instruments": [
        {"tradingsymbol": "RELIANCE", "name": "RELIANCE INDUSTRIES"},
        {"tradingsymbol": "ROCKPP-E1", "name": "ROCKINGDCE RS.5 PPD UP"},
        {"tradingsymbol": "ICICISINAV", "name": "ICICISILVE NAV"},
        {"tradingsymbol": "BAJAJ-AUTO", "name": "BAJAJ AUTO"},
    ]}))
    monkeypatch.setattr(ps, "INSTRUMENT_CACHE", cache)
    got = PreMarketScanner(None)._ensure_instrument_cache()
    assert [i["tradingsymbol"] for i in got] == ["RELIANCE", "BAJAJ-AUTO"]
    assert len(json.loads(cache.read_text())["instruments"]) == 4  # load never rewrites the file


# ── Fix 1: batch fetch ────────────────────────────────────

def sym(i):
    return "AB" + chr(65 + i // 26) + chr(65 + i % 26)  # no digits: the filter drops digit-led symbols


BAD_SET = {f"NSE:{sym(5)}", f"NSE:{sym(17)}", f"NSE:{sym(33)}"}


class CloudflareKite:
    """Rejects any request containing the whole offending combination (like the real WAF)."""
    def __init__(self):
        self.calls = []

    def ohlc(self, syms):
        self.calls.append(len(syms))
        if BAD_SET <= set(syms):
            raise Exception("Unknown Content-Type (text/html) with response: Just a moment...")
        return {s: {"ohlc": {"open": 1, "high": 1, "low": 1, "close": 1}, "last_price": 1} for s in syms}


@pytest.fixture
def fast(monkeypatch):
    monkeypatch.setattr(ps._time, "sleep", lambda *_: None)


def _setup(monkeypatch, tmp_path, n):
    cache = tmp_path / "inst.json"
    cache.write_text(json.dumps({"date": ps.datetime.now().isoformat(), "instruments": [
        {"tradingsymbol": sym(i), "name": sym(i)} for i in range(n)]}))
    monkeypatch.setattr(ps, "INSTRUMENT_CACHE", cache)


def test_batch_with_blocked_combination_recovers_all(fast, monkeypatch, tmp_path):
    monkeypatch.setattr(ps, "OHLC_BATCH_SIZE", 40)
    _setup(monkeypatch, tmp_path, 80)
    scanner = PreMarketScanner(CloudflareKite())
    got = scanner._fetch_ohlc_batch(1, [f"NSE:{sym(i)}" for i in range(40)])
    assert len(got) == 40


def test_scan_phase1_logs_missing_loudly(fast, monkeypatch, tmp_path, caplog):
    monkeypatch.setattr(ps, "OHLC_BATCH_SIZE", 10)
    _setup(monkeypatch, tmp_path, 10)

    class Dead:
        def ohlc(self, syms):
            raise Exception("Just a moment")
    with caplog.at_level(logging.WARNING, logger=ps.logger.name):
        PreMarketScanner(Dead()).scan_phase1()
    assert any("Phase 1 incomplete" in r.getMessage() for r in caplog.records)
    assert any("batch 1" in r.getMessage() and r.levelno == logging.WARNING for r in caplog.records)


def test_dead_session_bisect_is_bounded(fast):
    class Dead:
        calls = 0

        def ohlc(self, syms):
            Dead.calls += 1
            raise Exception("Just a moment")
    got = PreMarketScanner(Dead())._fetch_ohlc_batch(1, [f"NSE:{sym(i)}" for i in range(500)])
    assert got == {} and Dead.calls < 200


def test_scan_phase1_all_symbols_present(fast, monkeypatch, tmp_path, caplog):
    monkeypatch.setattr(ps, "OHLC_BATCH_SIZE", 40)
    _setup(monkeypatch, tmp_path, 80)
    k = CloudflareKite()
    with caplog.at_level(logging.WARNING, logger=ps.logger.name):
        PreMarketScanner(k).scan_phase1()
    assert not any("incomplete" in r.getMessage() for r in caplog.records)
