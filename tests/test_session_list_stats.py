import sqlite3

import agent.session
import dashboard.app as app


def _session_with_trades(tmp_path, monkeypatch):
    monkeypatch.setattr(agent.session, "SESSIONS_DIR", tmp_path)
    monkeypatch.setattr(app, "_is_agent_running", lambda sid: {"running": True, "pid": 42})
    (tmp_path / "s1").mkdir()
    con = sqlite3.connect(tmp_path / "s1" / "trades.db")
    con.execute("create table trades (status text, pnl real, entry_time text)")
    con.execute("create table daily_snapshots (date text, total_value real, daily_pnl real)")
    con.executemany("insert into trades values (?,?,?)", [
        ("closed", 912.0, "2026-10-13T04:06"), ("closed", -408.0, "2026-10-12T04:06"),
        ("open", None, "2026-10-13T05:00"),
    ])
    con.commit()
    con.close()
    return {"session_id": "s1", "starting_capital": 500_000}


def test_list_stats_include_portfolio(tmp_path, monkeypatch):
    s = _session_with_trades(tmp_path, monkeypatch)
    app._attach_list_stats(s)
    assert s["total_trades"] == 2 and s["win_rate"] == 50.0
    assert s["portfolio"]["total_return"] == 504.0
    assert s["portfolio"]["open_positions"] == 1


def test_admin_list_has_same_stats_as_user_list(tmp_path, monkeypatch):
    # The admin list used to omit "portfolio", so every card showed "--" for return.
    s = _session_with_trades(tmp_path, monkeypatch)
    monkeypatch.setattr(agent.session, "list_sessions", lambda: [dict(s)])
    [admin] = app.get_admin_sessions({})
    assert admin["portfolio"]["total_return"] == 504.0
    assert admin["is_running"] is True
