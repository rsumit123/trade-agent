from types import SimpleNamespace

import agent.trade_alerts as ta


def test_open_message_has_levels_and_reason():
    msg = ta.format_trade(
        "nse_opus", "₹",
        {"conviction": 4, "reason": "Gap-up holding VWAP."},
        {"success": True, "action": "BUY", "trade_id": 10, "ticker": "GOODLUCK.NS",
         "quantity": 80, "price": 585.85, "stop_price": 544.13, "target_price": 641.47},
    )
    assert "OPEN LONG #10 GOODLUCK.NS" in msg
    assert "80 @ ₹585.85 (₹46,868.00) · conviction 4" in msg
    assert "stop ₹544.13 · target ₹641.47" in msg
    assert msg.endswith("Gap-up holding VWAP.")


def test_close_and_rejected_trades():
    msg = ta.format_trade("nse_opus", "₹", {"reason": "Lost VWAP"},
                          {"success": True, "action": "COVER", "trade_id": 9,
                           "ticker": "SHRIRAMFIN.NS", "exit_price": 955.0, "pnl": 815.0})
    assert msg.startswith("✅ nse_opus: CLOSE (COVER) #9") and "P&L ₹815.00" in msg
    assert ta.format_trade("nse_opus", "₹", {}, {"success": False, "error": "too big"}) is None


def test_levels_message_shows_old_and_new():
    msg = ta.format_levels("nse_opus", "₹", {"stop_price": 572, "reason": "below VWAP"},
                           {"success": True, "trade_id": 10, "ticker": "GOODLUCK.NS",
                            "stop_price": 572.0, "target_price": 641.47},
                           old_stop=544.13, old_target=641.47)
    assert "stop ₹544.13 → ₹572.00" in msg and "target" not in msg.split("\n")[1]


def test_auto_exit_message():
    trade = SimpleNamespace(id=8, ticker="TRF.NS", direction="long", entry_price=239.1,
                            exit_price=234.5, pnl=-1104.0, exit_type="stop_hit")
    assert ta.format_auto_exit("nse_opus", "₹", trade).startswith("🛑 STOP HIT nse_opus: #8 TRF.NS")


def test_send_only_when_marker_present(tmp_path, monkeypatch):
    sent = []
    monkeypatch.setattr(ta, "send_telegram", sent.append)
    monkeypatch.setattr(ta.threading, "Thread",
                        lambda target, args, daemon: SimpleNamespace(start=lambda: target(*args)))
    ta.send(tmp_path, "hello")
    assert sent == []
    (tmp_path / ta.MARKER_FILE).touch()
    ta.send(tmp_path, "hello")
    assert sent == ["hello"]
