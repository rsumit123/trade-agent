import dashboard.app as app


def test_categories_payload_shape(monkeypatch):
    monkeypatch.setattr(
        app, "get_nse_sectors",
        lambda: {"Metals": ["TATASTEEL.NS", "JSWSTEEL.NS"], "Auto": ["MARUTI.NS"]},
    )
    payload = app._categories_payload("nse")
    assert payload["source"] == "nse_sectoral_indices"
    names = {c["name"]: c for c in payload["categories"]}
    assert names["Metals"]["count"] == 2
    assert names["Metals"]["tickers"] == ["TATASTEEL.NS", "JSWSTEEL.NS"]
    assert names["Auto"]["count"] == 1


def test_categories_payload_non_nse_is_empty(monkeypatch):
    monkeypatch.setattr(app, "get_nse_sectors", lambda: {"Metals": ["TATASTEEL.NS"]})
    payload = app._categories_payload("crypto")
    assert payload["categories"] == []
