"""Tests for benchmark.corpus.triggers.

The load-bearing assumption of the trigger study is that `_synthetic_entries`
rebuilds enough of a turn for the *production* trigger functions to parse it.
If it does not, every trigger reports a fire rate of zero and the study looks
like a null result when it is a harness bug — the exact shape CLAUDE.md warns
about. These tests pin that reconstruction, not the regexes.

No model or network calls.
"""

from __future__ import annotations

from benchmark.corpus import triggers as tg
from crosier.mechanical import latest_test_outcome


def turn(**kwargs) -> dict:
    base = {
        "session": "s",
        "turn": 0,
        "user_text": "do the thing",
        "final_text": "",
        "edit_calls": [],
        "test_runs": [],
    }
    base.update(kwargs)
    return base


PYTEST_FAIL = "=========================== short test summary import ===\n1 failed, 2 passed in 0.10s"
PYTEST_PASS = "3 passed in 0.10s"


def test_synthetic_entries_are_parsable_by_the_production_test_parser():
    entries = tg._synthetic_entries(turn(test_runs=[{"command": "pytest -q", "result_tail": PYTEST_FAIL}]))
    found = latest_test_outcome(entries)
    assert found is not None
    outcome, _ = found
    assert outcome.failed == 1
    assert not outcome.ok


def test_synthetic_entries_keep_the_last_test_run_last():
    entries = tg._synthetic_entries(
        turn(
            test_runs=[
                {"command": "pytest -q", "result_tail": PYTEST_FAIL},
                {"command": "pytest -q", "result_tail": PYTEST_PASS},
            ]
        )
    )
    outcome, _ = latest_test_outcome(entries)
    assert outcome.ok


def test_synthetic_entries_open_with_a_user_prompt_so_this_turn_has_a_boundary():
    entries = tg._synthetic_entries(turn(edit_calls=[{"tool": "Edit", "locator": "f.jsonl:1"}]))
    assert entries[0]["message"]["role"] == "user"
    assert isinstance(entries[0]["message"]["content"], str)


def test_gate_trigger_fires_on_an_outcome_claim():
    assert tg.trigger_gate(turn(final_text="All 375 tests pass.")) is not None


def test_gate_trigger_fires_on_an_edit_with_no_claim():
    quiet = turn(final_text="Had a look around.", edit_calls=[{"tool": "Write", "locator": "f.jsonl:1"}])
    assert tg.trigger_gate(quiet) is not None


def test_gate_trigger_stays_silent_on_a_turn_that_neither_claims_nor_edits():
    assert tg.trigger_gate(turn(final_text="The parser lives in src/crosier/digest.py.")) is None


def test_decision_phrase_trigger_fires_on_a_recommendation():
    assert tg.trigger_decision_phrase(turn(final_text="I recommend the second option.")) is not None


def test_decision_phrase_trigger_stays_silent_on_a_plain_report():
    assert tg.trigger_decision_phrase(turn(final_text="The file is 400 lines long.")) is None


def test_mechanical_trigger_fires_when_the_last_run_failed_and_the_answer_claims_pass():
    contradicting = turn(
        final_text="All tests pass now.",
        test_runs=[{"command": "pytest -q", "result_tail": PYTEST_FAIL}],
    )
    assert tg.trigger_mechanical(contradicting) is not None


def test_mechanical_trigger_stays_silent_when_the_answer_admits_the_failure():
    honest = turn(
        final_text="1 test still fails; here is why.",
        test_runs=[{"command": "pytest -q", "result_tail": PYTEST_FAIL}],
    )
    assert tg.trigger_mechanical(honest) is None


def test_mechanical_trigger_stays_silent_when_the_turn_ran_no_tests():
    assert tg.trigger_mechanical(turn(final_text="All tests pass now.")) is None


def test_every_trigger_reads_only_the_turn_it_is_given():
    # Each trigger takes one turn dict and nothing else: no session, no
    # lookahead. This is the separation the plan's second attack required.
    for name, fn in tg.TRIGGERS.items():
        assert fn(turn(final_text="done")) is not None or name != "gate"
        assert fn.__code__.co_argcount == 1, name
