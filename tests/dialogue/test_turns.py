from services.dialogue.turns import TurnRegistry


def test_turn_registry_rejects_parallel_turns_and_tracks_cancel():
    registry = TurnRegistry()
    turn_id = registry.begin("session-1", "request-1", turn_id="turn-1")
    assert turn_id == "turn-1"
    assert registry.is_active(turn_id)

    try:
        registry.begin("session-1", "request-2")
    except RuntimeError as exc:
        assert str(exc) == "session_busy"
    else:
        raise AssertionError("parallel turn should be rejected")

    assert registry.cancel(turn_id) is True
    assert registry.is_cancelled(turn_id)
    assert not registry.is_active(turn_id)

