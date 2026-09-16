"""Tests for the zero-token test-result-vs-claim contradiction check."""

from crosier.mechanical import (
    TestOutcome,
    contradiction_reason,
    latest_test_outcome,
    parse_test_summary,
)


def _assistant(*, bash_id: str, command: str) -> dict:
    return {
        "message": {
            "role": "assistant",
            "content": [
                {"type": "tool_use", "id": bash_id, "name": "Bash", "input": {"command": command}}
            ],
        }
    }


def _result(*, bash_id: str, output: str, is_error: bool = False) -> dict:
    return {
        "message": {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": bash_id, "content": output, "is_error": is_error}
            ],
        }
    }


def _result_blocks(*, bash_id: str, output: str, is_error: bool = False) -> dict:
    """A tool_result whose content is a list of text blocks instead of a string."""
    return {
        "message": {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": bash_id,
                    "content": [{"type": "text", "text": output}],
                    "is_error": is_error,
                }
            ],
        }
    }


def _edit(path: str) -> dict:
    return {
        "message": {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": "e1", "name": "Edit", "input": {"file_path": path}}],
        }
    }


# --- parse_test_summary: pytest ---------------------------------------------


def test_pytest_failed_and_passed():
    out = "2 failed, 48 passed in 3.45s"
    result = parse_test_summary("pytest", out)
    assert result == TestOutcome("pytest", 48, 2, None, False, "2 failed, 48 passed in 3.45s")


def test_pytest_all_passed():
    out = "48 passed in 1.02s"
    result = parse_test_summary("pytest", out)
    assert result.ok is True
    assert result.passed == 48
    assert result.failed is None


def test_pytest_error():
    out = "5 error in 1.02s"
    result = parse_test_summary("pytest", out)
    assert result.ok is False
    assert result.errors == 5
    assert result.runner == "pytest"


def test_pytest_no_tests_ran():
    out = "no tests ran in 0.01s"
    result = parse_test_summary("pytest -k nomatch", out)
    assert result.ok is False
    assert result.runner == "pytest"
    assert result.summary_line == "no tests ran in 0.01s"


def test_pytest_wrapped_in_equals_bars():
    out = (
        "collecting ... \n"
        "test_foo.py::test_a PASSED\n"
        "test_foo.py::test_b FAILED\n"
        "=================== 2 failed, 48 passed in 3.45s ===================\n"
    )
    result = parse_test_summary("pytest -q", out)
    assert result.ok is False
    assert result.failed == 2
    assert result.passed == 48
    assert result.summary_line == "2 failed, 48 passed in 3.45s"


def test_pytest_skipped_does_not_fail():
    out = "3 passed, 1 skipped in 0.5s"
    result = parse_test_summary("pytest", out)
    assert result.ok is True
    assert result.passed == 3


# --- parse_test_summary: unittest --------------------------------------------


def test_unittest_ok():
    out = "----------------------------------------------------------------------\nRan 12 tests in 0.045s\n\nOK\n"
    result = parse_test_summary("python -m unittest", out)
    assert result.ok is True
    assert result.runner == "unittest"


def test_unittest_failed_failures():
    out = "Ran 12 tests in 0.045s\n\nFAILED (failures=3)\n"
    result = parse_test_summary("python -m unittest", out)
    assert result.ok is False
    assert result.failed == 3
    assert result.errors is None


def test_unittest_failed_failures_and_errors():
    out = "Ran 12 tests in 0.045s\n\nFAILED (failures=1, errors=2)\n"
    result = parse_test_summary("python -m unittest", out)
    assert result.ok is False
    assert result.failed == 1
    assert result.errors == 2


def test_unittest_bare_ok_without_ran_line_is_unparseable():
    # "OK" with no "Ran N tests" line preceding it is not distinctive enough
    # to trust — some other tool printed it.
    out = "Build finished.\nOK\n"
    result = parse_test_summary("python -m unittest", out)
    assert result is None


# --- parse_test_summary: jest / vitest ---------------------------------------


def test_jest_failed_and_passed():
    out = "Tests:       2 failed, 48 passed, 50 total\nTime:        3.2s\n"
    result = parse_test_summary("npx jest", out)
    assert result.ok is False
    assert result.failed == 2
    assert result.passed == 48
    assert result.runner == "jest"


def test_vitest_all_passed():
    out = "Tests:       50 passed, 50 total\n"
    result = parse_test_summary("npx vitest run", out)
    assert result.ok is True
    assert result.runner == "vitest"


# --- parse_test_summary: cargo -----------------------------------------------


def test_cargo_failed():
    out = "test result: FAILED. 3 passed; 2 failed; 0 ignored; 0 measured; 0 filtered out; finished in 0.01s"
    result = parse_test_summary("cargo test", out)
    assert result.ok is False
    assert result.passed == 3
    assert result.failed == 2
    assert result.runner == "cargo"


def test_cargo_ok():
    out = "test result: ok. 5 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out; finished in 0.00s"
    result = parse_test_summary("cargo test", out)
    assert result.ok is True
    assert result.passed == 5
    assert result.failed == 0


# --- parse_test_summary: go ---------------------------------------------------


def test_go_ok():
    out = "ok  \texample.com/pkg\t0.003s\n"
    result = parse_test_summary("go test ./...", out)
    assert result.ok is True
    assert result.runner == "go"


def test_go_fail():
    out = "--- FAIL: TestFoo (0.00s)\n    foo_test.go:10: assertion failed\nFAIL\nexit status 1\nFAIL\texample.com/pkg\t0.003s\n"
    result = parse_test_summary("go test ./...", out)
    assert result.ok is False
    assert result.runner == "go"
    assert result.failed == 1


# --- parse_test_summary: npm fallback -----------------------------------------


def test_npm_err_fallback_not_ok():
    out = "npm ERR! Test failed. See above for more details.\n"
    result = parse_test_summary("npm test", out)
    assert result.ok is False
    assert result.runner == "npm"
    assert result.passed is None
    assert result.failed is None


def test_npm_exit_status_fallback():
    out = "Exit status 1\n"
    result = parse_test_summary("npm test", out)
    assert result.ok is False
    assert result.runner == "npm"


# --- parse_test_summary: never guesses ----------------------------------------


def test_unparseable_output_returns_none():
    assert parse_test_summary("pytest", "collecting tests...\nsome random noise\n") is None


def test_empty_output_returns_none():
    assert parse_test_summary("pytest", "") is None


def test_unrelated_command_with_ok_word_not_mistaken_for_unittest():
    # A generic command that happens to print "OK" must not be read as a test
    # result — no "Ran N tests" line, no pytest/jest/cargo/go shape.
    assert parse_test_summary("curl example.com", "HTTP/1.1 200 OK\n") is None


def test_parse_test_summary_never_raises_on_bad_input():
    assert parse_test_summary(None, None) is None
    assert parse_test_summary(123, {"not": "a string"}) is None


# --- latest_test_outcome ------------------------------------------------------


def test_latest_test_outcome_run_then_fix_later_run_wins():
    turn = [
        _assistant(bash_id="t1", command="pytest"),
        _result(bash_id="t1", output="2 failed, 48 passed in 3.45s"),
        _assistant(bash_id="t2", command="pytest"),
        _result(bash_id="t2", output="50 passed in 3.50s"),
    ]
    found = latest_test_outcome(turn)
    assert found is not None
    outcome, locator = found
    assert outcome.ok is True
    assert outcome.passed == 50
    assert locator["command"] == "pytest"
    assert locator["tool_use_id"] == "t2"


def test_latest_test_outcome_no_bash_calls_returns_none():
    turn = [
        {"message": {"role": "assistant", "content": [{"type": "text", "text": "All done."}]}},
    ]
    assert latest_test_outcome(turn) is None


def test_latest_test_outcome_unparseable_run_returns_none():
    turn = [
        _assistant(bash_id="t1", command="pytest"),
        _result(bash_id="t1", output="some unrelated noise, not a summary"),
    ]
    assert latest_test_outcome(turn) is None


def test_latest_test_outcome_is_error_still_counts():
    turn = [
        _assistant(bash_id="t1", command="pytest"),
        _result(bash_id="t1", output="2 failed, 48 passed in 3.45s", is_error=True),
    ]
    found = latest_test_outcome(turn)
    assert found is not None
    outcome, _ = found
    assert outcome.ok is False


def test_latest_test_outcome_content_as_block_list():
    turn = [
        _assistant(bash_id="t1", command="pytest"),
        _result_blocks(bash_id="t1", output="48 passed in 1.02s"),
    ]
    found = latest_test_outcome(turn)
    assert found is not None
    outcome, _ = found
    assert outcome.ok is True


# --- contradiction_reason ------------------------------------------------------


def test_contradiction_blocks_on_explicit_claim_with_failure():
    turn = [
        _assistant(bash_id="t1", command="pytest"),
        _result(bash_id="t1", output="2 failed, 48 passed in 3.45s"),
    ]
    reason = contradiction_reason("All tests pass now, the fix is complete.", turn)
    assert reason is not None
    assert "2 failed, 48 passed in 3.45s" in reason
    assert len(reason) <= 400


def test_contradiction_none_when_no_test_run():
    turn = [
        {"message": {"role": "assistant", "content": [{"type": "text", "text": "Done."}]}},
    ]
    assert contradiction_reason("The work is done.", turn) is None


def test_contradiction_none_when_latest_run_passed():
    turn = [
        _assistant(bash_id="t1", command="pytest"),
        _result(bash_id="t1", output="2 failed, 48 passed in 3.45s"),
        _assistant(bash_id="t2", command="pytest"),
        _result(bash_id="t2", output="50 passed in 1.00s"),
    ]
    assert contradiction_reason("All tests pass.", turn) is None


def test_contradiction_none_when_answer_is_honest_about_failure():
    turn = [
        _assistant(bash_id="t1", command="pytest"),
        _result(bash_id="t1", output="2 failed, 48 passed in 3.45s"),
    ]
    reason = contradiction_reason(
        "48 tests pass; 2 remaining failures are pre-existing and unrelated to this change.", turn
    )
    assert reason is None


def test_contradiction_none_when_answer_makes_no_claim():
    turn = [
        _assistant(bash_id="t1", command="pytest"),
        _result(bash_id="t1", output="2 failed, 48 passed in 3.45s"),
    ]
    assert contradiction_reason("Investigating the failure further.", turn) is None


def test_contradiction_none_on_unparseable_output():
    turn = [
        _assistant(bash_id="t1", command="pytest"),
        _result(bash_id="t1", output="garbled output that matches nothing"),
    ]
    assert contradiction_reason("All tests pass.", turn) is None


def test_contradiction_never_raises_on_malformed_entries():
    assert contradiction_reason("tests pass", [None, 1, "x", {}]) is None
    assert contradiction_reason(None, None) is None


# --- contradiction_reason: narrowed success-claim wording (spec change) ------


def test_contradiction_none_on_generic_done_claim_alone():
    # "done"/"complete"/"fixed"/"ready" say nothing about test results and
    # must not trigger a block on their own.
    turn = [
        _assistant(bash_id="t1", command="pytest"),
        _result(bash_id="t1", output="2 failed, 48 passed in 3.45s"),
    ]
    assert contradiction_reason("The refactor is done.", turn) is None
    assert contradiction_reason("This is complete and ready to merge.", turn) is None
    assert contradiction_reason("Fixed the typo in the docstring.", turn) is None


def test_contradiction_blocks_on_verified_claim():
    turn = [
        _assistant(bash_id="t1", command="pytest"),
        _result(bash_id="t1", output="2 failed, 48 passed in 3.45s"),
    ]
    reason = contradiction_reason("Verified the change end to end.", turn)
    assert reason is not None


def test_contradiction_blocks_on_suite_is_green():
    turn = [
        _assistant(bash_id="t1", command="pytest"),
        _result(bash_id="t1", output="2 failed, 48 passed in 3.45s"),
    ]
    assert contradiction_reason("The suite is green.", turn) is not None


def test_contradiction_works_only_counts_with_test_context():
    turn = [
        _assistant(bash_id="t1", command="pytest"),
        _result(bash_id="t1", output="2 failed, 48 passed in 3.45s"),
    ]
    # "works" with no test/suite mention in the same sentence: not a claim.
    assert contradiction_reason("The dashboard works now.", turn) is None
    # "works" sharing a sentence with "tests": a claim.
    assert contradiction_reason("The tests all work now.", turn) is not None


# --- contradiction_reason: path-scoping guard (spec change) -------------------


def test_contradiction_skipped_when_failing_run_targets_other_files():
    turn = [
        _edit("src/pkg_a/foo.py"),
        _assistant(bash_id="t1", command="pytest tests/pkg_b/test_bar.py"),
        _result(bash_id="t1", output="1 failed, 5 passed in 1.00s"),
    ]
    reason = contradiction_reason("All tests pass. See src/pkg_a/foo.py.", turn)
    assert reason is None


def test_contradiction_still_blocks_when_targets_overlap_edited_files():
    turn = [
        _edit("tests/pkg_b/test_bar.py"),
        _assistant(bash_id="t1", command="pytest tests/pkg_b/test_bar.py"),
        _result(bash_id="t1", output="1 failed, 5 passed in 1.00s"),
    ]
    reason = contradiction_reason("All tests pass.", turn)
    assert reason is not None


def test_contradiction_multi_target_command_is_a_known_risk_no_skip():
    # Known risk documented in mechanical.py's _targets_other_files: a
    # command with two file targets where only one (a/foo_test.py) overlaps
    # the edited files still counts as "overlaps", even when the answer's
    # own file mention names only the other target (b/bar_test.py), which a
    # more precise per-target scoping might have let through unblocked.
    turn = [
        _edit("a/foo_test.py"),
        _assistant(bash_id="t1", command="pytest a/foo_test.py b/bar_test.py"),
        _result(bash_id="t1", output="1 failed, 5 passed in 1.00s"),
    ]
    reason = contradiction_reason("All tests pass. See b/bar_test.py.", turn)
    assert reason is not None


def test_contradiction_ambiguous_scoping_does_not_skip():
    # The command's target can't be decoded from the command string (no
    # path-like token) — ambiguity means the rule doesn't apply, so a
    # positive claim still blocks.
    turn = [
        _edit("src/foo.py"),
        _assistant(bash_id="t1", command="make test"),
        _result(bash_id="t1", output="1 failed, 5 passed in 1.00s"),
    ]
    reason = contradiction_reason("All tests pass.", turn)
    assert reason is not None


# --- contradiction_reason: red-then-edit / TDD (spec change) ------------------


def test_contradiction_none_on_tdd_red_phase_answer():
    turn = [
        _assistant(bash_id="t1", command="pytest tests/test_new_feature.py"),
        _result(bash_id="t1", output="1 failed, 0 passed in 0.10s"),
    ]
    reason = contradiction_reason(
        "Wrote the failing test first, per TDD. Implementation is next.", turn
    )
    assert reason is None


def test_contradiction_blocks_red_then_edit_with_explicit_unverified_claim():
    # Failing run, then an edit, with no rerun — the agent may not claim the
    # rerun implicitly, but an explicit "tests pass" claim still blocks.
    turn = [
        _assistant(bash_id="t1", command="pytest"),
        _result(bash_id="t1", output="1 failed, 5 passed in 1.00s"),
        _edit("src/foo.py"),
    ]
    reason = contradiction_reason("Fixed the bug, tests pass now.", turn)
    assert reason is not None


def test_contradiction_none_red_then_edit_with_vague_claim():
    turn = [
        _assistant(bash_id="t1", command="pytest"),
        _result(bash_id="t1", output="1 failed, 5 passed in 1.00s"),
        _edit("src/foo.py"),
    ]
    reason = contradiction_reason("Fixed the bug.", turn)
    assert reason is None
