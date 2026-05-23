import json
from pathlib import Path
import agent.sector_data as sd


SAMPLE_CSV = (
    "Company Name,Industry,Symbol,Series,ISIN Code\n"
    "Tata Steel Limited,Metals & Mining,TATASTEEL,EQ,INE081A01020\n"
    "JSW Steel Limited,Metals & Mining,JSWSTEEL,EQ,INE019A01038\n"
)


def test_parse_csv_extracts_ns_tickers():
    assert sd.parse_constituents_csv(SAMPLE_CSV) == ["TATASTEEL.NS", "JSWSTEEL.NS"]


def test_parse_csv_ignores_blank_and_malformed_rows():
    csv = SAMPLE_CSV + "\n,,,,\nGarbage line without commas\n"
    assert sd.parse_constituents_csv(csv) == ["TATASTEEL.NS", "JSWSTEEL.NS"]


def test_parse_csv_skips_dummy_placeholder_symbols():
    csv = SAMPLE_CSV + "Vedanta Dummy,Metals,DUMMYVEDL1,EQ,INE000000000\n"
    assert sd.parse_constituents_csv(csv) == ["TATASTEEL.NS", "JSWSTEEL.NS"]


def test_load_cache_returns_sectors_when_fresh(tmp_path, monkeypatch):
    cache = tmp_path / "_nse_sectors.json"
    cache.write_text(json.dumps({
        "fetched_at": "2999-01-01T00:00:00",  # far future → never stale
        "sectors": {"Metals": ["TATASTEEL.NS"]},
    }))
    monkeypatch.setattr(sd, "SECTOR_CACHE", cache)
    # Force-fail the network path so we know the cache was used
    monkeypatch.setattr(sd, "_refresh_sectors", lambda: {})
    assert sd.get_nse_sectors() == {"Metals": ["TATASTEEL.NS"]}


def test_refresh_failure_falls_back_to_stale_cache(tmp_path, monkeypatch):
    cache = tmp_path / "_nse_sectors.json"
    cache.write_text(json.dumps({
        "fetched_at": "2000-01-01T00:00:00",  # stale → triggers refresh
        "sectors": {"Metals": ["TATASTEEL.NS"]},
    }))
    monkeypatch.setattr(sd, "SECTOR_CACHE", cache)
    monkeypatch.setattr(sd, "_fetch_index_csv", lambda slug: None)  # all fetches fail
    # stale cache exists → returned despite refresh producing nothing usable
    assert sd.get_nse_sectors() == {"Metals": ["TATASTEEL.NS"]}


def test_no_cache_and_failed_fetch_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(sd, "SECTOR_CACHE", tmp_path / "_nse_sectors.json")
    monkeypatch.setattr(sd, "_fetch_index_csv", lambda slug: None)
    assert sd.get_nse_sectors() == {}
