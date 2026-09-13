"""Chat benchmark v2 runner: the pure pieces that decide what a number means."""

import sys
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "benchmark" / "chat"))

from run_chat_v2 import arm_toml, parse_junit, reviewer_by_turn  # noqa: E402
from scoring import turn_rows  # noqa: E402

from crosier.config import load_config  # noqa: E402

JUNIT = """<?xml version="1.0" encoding="utf-8"?>
<testsuites><testsuite name="pytest">
  <testcase classname="_hidden.test_hidden" name="test_ok" time="0.01"/>
  <testcase classname="_hidden.test_hidden" name="test_bad" time="0.01"><failure message="x">x</failure></testcase>
  <testcase classname="_hidden.test_hidden" name="test_err" time="0.01"><error message="x">x</error></testcase>
  <testcase classname="_hidden.test_hidden" name="test_skip" time="0.01"><skipped message="x">x</skipped></testcase>
</testsuite></testsuites>"""


def test_crosier_arms_share_the_budget_and_differ_only_in_the_gate(tmp_path):
    configs = {}
    for arm in ("async", "gate"):
        workdir = tmp_path / arm
        workdir.mkdir()
        (workdir / ".crosier.toml").write_text(arm_toml(arm), encoding="utf-8")
        configs[arm] = load_config(workdir)
    assert configs["gate"].stop_gate is True
    assert configs["async"].stop_gate is False
    assert configs["gate"].max_checks_per_session == configs["async"].max_checks_per_session > 12


def test_the_off_arm_still_writes_a_config_so_the_workdirs_match():
    assert tomllib.loads(arm_toml("off"))["crosier"]["stop_gate"] is False


def test_reviewer_model_is_written_when_given(tmp_path):
    (tmp_path / ".crosier.toml").write_text(arm_toml("gate", reviewer_model="haiku"), encoding="utf-8")
    assert load_config(tmp_path).verdict_model == "haiku"


def test_junit_counts_only_a_clean_testcase_as_passed():
    assert parse_junit(JUNIT) == {"test_ok": True, "test_bad": False, "test_err": False, "test_skip": False}


def test_unreadable_junit_is_no_results_not_a_crash():
    # scoring treats a due test missing from the results as failing.
    assert parse_junit("") == {}
    assert parse_junit("<not xml") == {}


def test_snapshots_are_tested_and_scored_end_to_end(tmp_path, monkeypatch):
    # The offline half of a session, no API: snapshot the seed at turn 1 and
    # the reference at turn 2, then run the hidden tests against each commit.
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "benchmark" / "chat" / "snapshot_plugin" / "hooks"))
    import run_chat_v2
    import snapshot_hook
    from reference import REFERENCE_V2
    from scenarios_v2 import SCENARIOS_V2

    scenario = SCENARIOS_V2[0]
    work, snap = tmp_path / "work", tmp_path / "snap"
    work.mkdir()
    snap.mkdir()
    stops = []
    for turn, body in ((1, scenario["seed"]["retry_util.py"]), (2, REFERENCE_V2[scenario["id"]])):
        (work / "retry_util.py").write_text(body, encoding="utf-8")
        monkeypatch.setenv("BENCH_TURN", str(turn))
        stops.append(snapshot_hook.snapshot({"cwd": str(work), "last_assistant_message": "Done."}, snap))
    monkeypatch.setattr(run_chat_v2.run_chat, "PYTEST_ARGV", [sys.executable])

    results = run_chat_v2._test_snapshots(snap, [s["sha"] for s in stops], scenario)
    rows = turn_rows(stops, results, scenario["due"], [])
    assert len(results[stops[1]["sha"]]) == len(scenario["due"])
    assert (rows[0]["draft_bad"], rows[0]["draft_class"]) == (True, "loud")
    assert rows[1]["draft_bad"] is False


def test_reviewer_cost_is_charged_to_the_turn_that_dispatched_the_check_stale_included():
    journal = [
        {"turn": 3, "delivered_turn": 4, "input_tokens": 100, "output_tokens": 10, "cost_usd": 0.01, "stop_gate": False},
        {"turn": 4, "delivered_turn": 4, "input_tokens": 50, "output_tokens": 5, "cost_usd": 0.02, "stop_gate": True},
        {"turn": 4, "delivered_turn": 4, "status": "stale"},
    ]
    by_turn = reviewer_by_turn(journal)
    assert by_turn[3] == {"checks": 1, "gate_checks": 0, "input_tokens": 100, "output_tokens": 10, "cost_usd": 0.01}
    assert by_turn[4]["checks"] == 2
    assert by_turn[4]["gate_checks"] == 1
    assert by_turn[4]["input_tokens"] == 50
