# tests/test_heuristic.py
"""Drift accrues per model call, not per user prompt. Replaying 26 real
sessions through the prompt-counted triggers: 0 checks dispatched in 4 of 10
long sessions (some past 250k context tokens), 1 to 2 in the rest, because a
session with 6 user prompts never clears a 5-prompt cooldown. The unit here is
the model call (one tool batch), which is what the PostToolBatch hook counts."""

from crosier.config import CrosierConfig
from crosier.heuristic import (
    CHARS_PER_TOKEN,
    budget_exhausted,
    is_bulk_operation,
    should_escalate,
    tool_repetition_rate,
)
from crosier.state import SessionState

# Every non-turn trigger is gated behind the cooldown, so a state under test
# has to be past it before the signal itself can be observed.
PAST_COOLDOWN = CrosierConfig().min_calls_between_checks


def test_repetition_rate_empty_is_zero():
    assert tool_repetition_rate([]) == 0.0


def test_repetition_rate_no_repeats_is_zero():
    assert tool_repetition_rate(["Read:1", "Edit:2", "Bash:3"]) == 0.0


def test_repetition_rate_counts_repeats_in_full_window():
    calls = ["Bash:1"] * 4 + ["Read:2", "Edit:3", "Read:4", "Edit:5", "Read:6", "Edit:7"]
    # window of 10, "Bash:1" repeated 4 times -> 3 "extra" repeats / 10
    assert tool_repetition_rate(calls) == 3 / 10


def test_repetition_rate_is_zero_until_window_is_full():
    # Two identical calls right after a check resets the buffer is 1 repeat over
    # a window of 2 -> a rate of 0.5, which clears the default 0.4 threshold and
    # escalates on what is really just a file read twice.
    assert tool_repetition_rate(["Bash:1", "Bash:1"]) == 0.0


def test_repetition_rate_is_zero_for_short_window_of_all_duplicates():
    assert tool_repetition_rate(["Read:1"] * 9) == 0.0


def test_bulk_operation_detects_one_tool_over_many_targets():
    window = [f"Edit:{i}" for i in range(8)] + ["Read:x", "Bash:y"]
    assert is_bulk_operation(window) is True


def test_bulk_operation_is_false_for_one_tool_on_one_target():
    assert is_bulk_operation(["Read:same"] * 10) is False


def test_bulk_operation_is_false_without_a_dominant_tool():
    window = ["Read:1", "Edit:2", "Bash:3", "Glob:4", "Grep:5"] * 2
    assert is_bulk_operation(window) is False


def test_no_escalation_below_all_thresholds():
    state = SessionState(calls_since_check=1, chars_since_check=100, recent_tool_calls=["Read:1"])
    assert should_escalate(state, CrosierConfig()) is False


def test_escalates_on_call_threshold():
    # Steady state: checks_run > 0, so the ordinary threshold governs.
    config = CrosierConfig(call_threshold=30)
    assert should_escalate(SessionState(calls_since_check=30, checks_run=1), config) is True
    assert should_escalate(SessionState(calls_since_check=29, checks_run=1), config) is False


def test_first_check_of_a_session_fires_earlier():
    # The damage a long session never recovers from is committed early, so the
    # first check does not wait for the steady-state cadence.
    config = CrosierConfig(call_threshold=30, first_check_call_threshold=12)
    assert should_escalate(SessionState(calls_since_check=12, checks_run=0), config) is True
    assert should_escalate(SessionState(calls_since_check=11, checks_run=0), config) is False
    # And it applies only to the first: after one check, 12 is not enough.
    assert should_escalate(SessionState(calls_since_check=12, checks_run=1), config) is False


def test_early_first_check_still_obeys_the_cooldown():
    # A first_check threshold below the cooldown cannot fire below the cooldown.
    config = CrosierConfig(first_check_call_threshold=2, min_calls_between_checks=8)
    assert should_escalate(SessionState(calls_since_check=2, checks_run=0), config) is False
    assert should_escalate(SessionState(calls_since_check=8, checks_run=0), config) is True


def test_early_first_check_never_exceeds_the_ordinary_threshold():
    # A user who lowers call_threshold below the first-check default must not
    # get a *later* first check than their own setting.
    config = CrosierConfig(call_threshold=5, first_check_call_threshold=12,
                           min_calls_between_checks=0)
    assert should_escalate(SessionState(calls_since_check=5, checks_run=0), config) is True


def test_early_first_check_does_not_raise_the_budget():
    config = CrosierConfig(max_checks_per_session=2, first_check_call_threshold=1,
                           min_calls_between_checks=0)
    assert should_escalate(SessionState(calls_since_check=50, checks_run=2), config) is False


def test_escalates_on_turn_threshold_even_with_few_calls():
    # A chat-only session drifts too. The turn trigger is not gated by the
    # call cooldown, which exists to stop a stuck loop re-triggering, not to
    # silence a long conversation.
    config = CrosierConfig(turn_threshold=10)
    assert should_escalate(SessionState(turns_since_check=10, calls_since_check=0), config) is True


def test_escalates_on_char_threshold():
    config = CrosierConfig(token_threshold=1000)
    state = SessionState(calls_since_check=PAST_COOLDOWN, chars_since_check=4000)  # 1000 tokens * 4 chars
    assert should_escalate(state, config) is True


def test_escalates_on_repetition_threshold():
    config = CrosierConfig(repetition_threshold=0.4)
    state = SessionState(calls_since_check=PAST_COOLDOWN, recent_tool_calls=["Bash:1"] * 10)
    assert should_escalate(state, config) is True


def test_does_not_escalate_on_a_bulk_refactor():
    # Ten Edit calls in a row across ten files clears the repetition threshold
    # on tool name alone. It is a sweep, not a loop, and interrupting it is a
    # false positive on the single most common long-session workload.
    config = CrosierConfig(repetition_threshold=0.4)
    calls = ["Edit:a"] * 3 + ["Edit:b"] * 3 + ["Edit:c", "Edit:d", "Edit:e", "Edit:f"]
    assert tool_repetition_rate(calls) >= config.repetition_threshold
    state = SessionState(calls_since_check=PAST_COOLDOWN, recent_tool_calls=calls)
    assert should_escalate(state, config) is False


def test_escalates_on_real_context_growth():
    config = CrosierConfig(token_threshold=1000)
    state = SessionState(calls_since_check=PAST_COOLDOWN)
    assert should_escalate(state, config, context_growth=1000) is True


def test_real_context_growth_below_threshold_overrides_char_estimate():
    config = CrosierConfig(token_threshold=1000)
    state = SessionState(calls_since_check=PAST_COOLDOWN, chars_since_check=8000)
    assert should_escalate(state, config, context_growth=200) is False


def test_falls_back_to_char_estimate_when_context_growth_unknown():
    config = CrosierConfig(token_threshold=1000)
    state = SessionState(calls_since_check=PAST_COOLDOWN, chars_since_check=4000)
    assert should_escalate(state, config, context_growth=None) is True


def test_call_cooldown_suppresses_a_trigger_that_just_fired():
    # Without a cooldown a stuck session re-triggers on the same evidence every
    # call, and the checks meant to cut the waste double it instead.
    config = CrosierConfig(min_calls_between_checks=8)
    state = SessionState(calls_since_check=2, recent_tool_calls=["Bash:1"] * 10)
    assert should_escalate(state, config, context_growth=1_000_000) is False


def test_budget_stops_escalating_after_the_session_cap():
    config = CrosierConfig(max_checks_per_session=3)
    state = SessionState(calls_since_check=500, checks_run=3)
    assert budget_exhausted(state, config) is True
    assert should_escalate(state, config) is False


def test_chars_per_token_matches_what_a_digest_actually_costs():
    # Measured on a real 39,409-char excerpt billed by the API: 17,456 tokens
    # for the excerpt portion, so 2.26 chars per token. The old value of 4 was
    # calibrated on prose, but 89.5% of an excerpt is tool traffic - JSON
    # arguments and command output, which tokenize far denser than prose. At 4
    # the fallback token trigger needed 160,000 chars of growth to fire where
    # it meant to fire at 90,400.
    assert 2.0 <= CHARS_PER_TOKEN <= 2.5


def test_the_fallback_token_trigger_fires_on_measured_growth():
    config = CrosierConfig(token_threshold=40_000, min_calls_between_checks=0)
    state = SessionState()
    state.calls_since_check = 1
    state.chars_since_check = int(40_000 * 2.26) - 1
    assert not should_escalate(state, config)
    state.chars_since_check = int(40_000 * 2.5) + 1
    assert should_escalate(state, config)
