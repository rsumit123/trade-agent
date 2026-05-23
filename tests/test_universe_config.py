from agent.session import SessionConfig


def test_default_universe_is_discovery():
    sc = SessionConfig(session_id="t", market="nse")
    assert sc.resolve_universe() == ("discovery", None)


def test_nonempty_universe_is_fixed():
    sc = SessionConfig(session_id="t", market="nse", universe=["RELIANCE.NS"])
    assert sc.resolve_universe() == ("fixed", ["RELIANCE.NS"])


def test_empty_list_universe_is_discovery():
    sc = SessionConfig(session_id="t", market="nse", universe=[])
    assert sc.resolve_universe() == ("discovery", None)
