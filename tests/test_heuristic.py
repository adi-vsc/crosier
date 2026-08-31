# tests/test_heuristic.py
from crosier.config import CrosierConfig
from crosier.heuristic import should_escalate, tool_repetition_rate
from crosier.state import SessionState


def test_repetition_rate_empty_is_zero():
    assert tool_repetition_rate([]) == 0.0


def test_repetition_rate_no_repeats_is_zero():
    assert tool_repetition_rate(["Read:1", "Edit:2", "Bash:3"]) == 0.0


def test_repetition_rate_counts_repeats_in_last_ten():
    calls = ["Bash:1"] * 4 + ["Read:2", "Edit:3"]
    # window of 6, "Bash:1" repeated 4 times -> 3 "extra" repeats / 6
    assert tool_repetition_rate(calls) == 3 / 6


def test_no_escalation_below_all_thresholds():
    state = SessionState(turns_since_check=1, chars_since_check=100, recent_tool_calls=["Read:1"])
    config = CrosierConfig()
    assert should_escalate(state, config) is False


def test_escalates_on_turn_threshold():
    state = SessionState(turns_since_check=20)
    config = CrosierConfig(turn_threshold=20)
    assert should_escalate(state, config) is True


def test_escalates_on_char_threshold():
    config = CrosierConfig(token_threshold=1000)
    state = SessionState(chars_since_check=4000)  # 1000 tokens * 4 chars/token
    assert should_escalate(state, config) is True


def test_escalates_on_repetition_threshold():
    config = CrosierConfig(repetition_threshold=0.4)
    state = SessionState(recent_tool_calls=["Bash:1"] * 5)
    assert should_escalate(state, config) is True


def test_forced_escalates_regardless_of_thresholds():
    state = SessionState()
    config = CrosierConfig()
    assert should_escalate(state, config, forced=True) is True
