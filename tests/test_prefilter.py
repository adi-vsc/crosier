"""What a window could possibly be flagged for, decided without a model call."""

import pytest

from crosier.prefilter import ALWAYS_LIVE, live_modes, surface_report
from crosier.verdict import CATEGORIES

GOAL = "[goal] Fix the failing auth test.\n\n"


def test_every_mode_it_reports_is_a_real_rubric_category():
    excerpt = GOAL + "[assistant] All 48 tests pass now."
    assert live_modes(excerpt) <= set(CATEGORIES)


def test_the_goal_line_alone_keeps_off_goal_live():
    # build_excerpt pins the goal into every excerpt, so the one mode whose
    # evidence may be the goal itself can never be filtered out.
    assert live_modes(GOAL.strip()) == set(ALWAYS_LIVE)


def test_a_verification_claim_in_assistant_text_wakes_unverified_claim():
    excerpt = GOAL + "[assistant] I ran the suite and all 48 tests pass."
    assert "unverified_claim" in live_modes(excerpt)


def test_a_verification_word_in_a_tool_result_does_not_wake_it():
    # The mode is about what the agent asserted. A passing run quoted from a
    # result is the evidence, not the claim.
    excerpt = GOAL + "[tool_result #3 | 40 chars]\n48 passed in 3.06s"
    assert "unverified_claim" not in live_modes(excerpt)


def test_a_repeated_identical_call_wakes_loop():
    call = "[tool_use #{} Bash] {{'command': 'pytest -q'}}"
    excerpt = GOAL + call.format(4) + "\n\n" + call.format(5)
    assert "loop" in live_modes(excerpt)


def test_two_different_calls_do_not_wake_loop():
    excerpt = (GOAL + "[tool_use #4 Bash] {'command': 'pytest -q'}\n\n"
               "[tool_use #5 Bash] {'command': 'ruff check'}")
    assert "loop" not in live_modes(excerpt)


def test_a_user_correction_wakes_ignored_correction():
    excerpt = GOAL + "[user] No, do not touch the config file.\n\n[assistant] Understood."
    assert "ignored_correction" in live_modes(excerpt)


def test_a_user_turn_with_no_correction_does_not_wake_it():
    excerpt = GOAL + "[user] Thanks, carry on with the next file."
    assert "ignored_correction" not in live_modes(excerpt)


def test_prose_with_no_calls_wakes_padding_and_research_collapse():
    excerpt = GOAL + "[assistant] " + ("Here is the plan for the refactor. " * 20)
    live = live_modes(excerpt)
    assert "padding" in live
    assert "research_collapse" in live


def test_work_with_calls_and_little_prose_wakes_neither():
    excerpt = (GOAL + "[assistant] Reading it.\n\n"
               "[tool_use #4 Read] {'path': 'a.py'}\n\n"
               "[tool_result #4 | 12 chars]\nx = 1\n\n"
               "[tool_use #5 Read] {'path': 'b.py'}\n\n"
               "[tool_result #5 | 12 chars]\ny = 2")
    live = live_modes(excerpt)
    assert "padding" not in live
    assert "research_collapse" not in live


def test_an_empty_excerpt_wakes_nothing():
    assert live_modes("") == set()


@pytest.mark.parametrize("mode", ALWAYS_LIVE)
def test_always_live_modes_are_real_categories(mode):
    assert mode in CATEGORIES


def test_surface_report_explains_each_live_mode():
    excerpt = GOAL + "[assistant] All 48 tests pass."
    report = surface_report(excerpt)
    assert set(report) == live_modes(excerpt)
    assert all(isinstance(v, str) and v for v in report.values())


def test_a_bug_declared_resolved_wakes_unverified_claim():
    excerpt = GOAL + "[assistant] I've fixed the crash and the bug is resolved."
    assert "unverified_claim" in live_modes(excerpt)


def test_stated_recall_wakes_research_collapse_even_beside_a_call():
    # The mode is acting on a remembered fact where a check was available, so
    # a window that also edits a file is exactly where it shows up.
    excerpt = (GOAL + "[assistant] The retry() helper takes (fn, attempts, delay), "
               "I recall its signature, so I will call it without checking.\n\n"
               "[tool_use #1 Edit] {'file_path': 'main.py'}")
    assert "research_collapse" in live_modes(excerpt)


def test_a_short_re_plan_with_no_calls_still_wakes_padding():
    excerpt = (GOAL + "[assistant] Here is my plan: analyze, design, then structure.\n\n"
               "[assistant] To recap the plan: step one analyze, step two design. "
               "Let me restate the approach before proceeding.")
    assert "padding" in live_modes(excerpt)
