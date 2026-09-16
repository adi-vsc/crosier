"""Tests for benchmark/chat/base_rate.py: Wilson interval, power calc, and
parsing of the v1/v2 chat-benchmark result shapes."""

import json
import math
import sys
from pathlib import Path
from statistics import NormalDist

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "benchmark" / "chat"))

import base_rate  # noqa: E402


# ---------------------------------------------------------------------------
# wilson_interval
# ---------------------------------------------------------------------------

def test_wilson_interval_zero_n_is_degenerate():
    result = base_rate.wilson_interval(0, 0)
    assert result == {"phat": 0.0, "lower": 0.0, "upper": 0.0, "k": 0, "n": 0}


def test_wilson_interval_matches_hand_computation():
    # k=2, n=38 (our actual pooled v2 figures), 95% Wilson score interval,
    # computed independently by the textbook formula (Wilson 1927):
    # center = (phat + z^2/2n) / (1 + z^2/n)
    # margin = z*sqrt(phat*(1-phat)/n + z^2/4n^2) / (1 + z^2/n)
    k, n = 2, 38
    z = NormalDist().inv_cdf(0.975)
    phat = k / n
    denom = 1 + z * z / n
    center = (phat + z * z / (2 * n)) / denom
    margin = z * math.sqrt(phat * (1 - phat) / n + z * z / (4 * n * n)) / denom
    expected_lower, expected_upper = center - margin, center + margin

    result = base_rate.wilson_interval(k, n)
    assert result["phat"] == k / n
    assert math.isclose(result["lower"], expected_lower, rel_tol=1e-9)
    assert math.isclose(result["upper"], expected_upper, rel_tol=1e-9)
    assert result["lower"] < phat < result["upper"]


def test_wilson_interval_bounds_stay_in_unit_range():
    result = base_rate.wilson_interval(0, 10)
    assert math.isclose(result["lower"], 0.0, abs_tol=1e-9)
    result = base_rate.wilson_interval(10, 10)
    assert result["upper"] == 1.0


# ---------------------------------------------------------------------------
# power_two_proportion
# ---------------------------------------------------------------------------

def test_power_two_proportion_matches_hand_computation():
    # p1=0.5, p2=0.75, alpha=0.05 (two-sided), power=0.8 - a standard
    # normal-approximation (Fleiss) sample-size example, computed by hand:
    # n = (z_a*sqrt(2*pbar*qbar) + z_b*sqrt(p1*q1+p2*q2))^2 / (p1-p2)^2
    nd = NormalDist()
    z_a, z_b = nd.inv_cdf(0.975), nd.inv_cdf(0.8)
    pbar = 0.625
    term1 = z_a * math.sqrt(2 * pbar * (1 - pbar))
    term2 = z_b * math.sqrt(0.5 * 0.5 + 0.75 * 0.25)
    expected_n = math.ceil(((term1 + term2) ** 2) / (0.25 ** 2))

    assert base_rate.power_two_proportion(0.5, 0.75) == expected_n
    assert expected_n == 58  # known value for this textbook case


def test_power_two_proportion_smaller_effect_needs_more_n():
    small_effect = base_rate.power_two_proportion(0.10, 0.09)
    large_effect = base_rate.power_two_proportion(0.10, 0.02)
    assert small_effect > large_effect


def test_power_two_proportion_rejects_equal_rates():
    try:
        base_rate.power_two_proportion(0.1, 0.1)
        assert False, "expected ValueError"
    except ValueError:
        pass


# ---------------------------------------------------------------------------
# design_effect / sessions_needed
# ---------------------------------------------------------------------------

def test_design_effect_icc_zero_is_one():
    assert base_rate.design_effect(cluster_size=24, icc=0.0) == 1.0


def test_design_effect_scales_with_icc_and_cluster_size():
    assert base_rate.design_effect(cluster_size=24, icc=0.1) == 1 + 23 * 0.1


def test_sessions_needed_icc_zero_divides_by_cluster_size():
    # 240 turns needed, 24 turns/session, no clustering penalty -> 10 sessions
    assert base_rate.sessions_needed(turns_per_arm=240, turns_per_session=24, icc=0.0) == 10


def test_sessions_needed_grows_with_icc():
    low = base_rate.sessions_needed(turns_per_arm=240, turns_per_session=24, icc=0.0)
    high = base_rate.sessions_needed(turns_per_arm=240, turns_per_session=24, icc=0.1)
    assert high > low


# ---------------------------------------------------------------------------
# parsing: minimal v1 fixture
# ---------------------------------------------------------------------------

def _write_v1_fixture(tmp_path: Path) -> Path:
    data = {
        "agent_model": "haiku",
        "repeats": 1,
        "records": [
            {"scenario": "retry-backoff", "arm": "off", "repeat": 1,
             "all_pass": True, "cost_usd": 0.30, "turns_completed": 10},
            {"scenario": "retry-backoff", "arm": "off", "repeat": 2,
             "all_pass": False, "cost_usd": 0.34, "turns_completed": 10},
            {"scenario": "retry-backoff", "arm": "on", "repeat": 1,
             "all_pass": True, "cost_usd": 0.40, "turns_completed": 10},
        ],
    }
    path = tmp_path / "chat_results_fixture.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_parse_v1_file_counts_bad_sessions(tmp_path):
    parsed = base_rate.parse_v1_file(_write_v1_fixture(tmp_path))
    assert parsed["agent_model"] == "haiku"
    off = parsed["by_key"][("retry-backoff", "off")]
    assert off["n"] == 2
    assert off["bad"] == 1  # one all_pass=False
    on = parsed["by_key"][("retry-backoff", "on")]
    assert on["n"] == 1
    assert on["bad"] == 0


def test_parse_v1_file_missing_path_returns_none(tmp_path):
    assert base_rate.parse_v1_file(tmp_path / "does_not_exist.json") is None


def test_v1_summary_reports_pass_fraction(tmp_path):
    fixture = _write_v1_fixture(tmp_path)
    table = base_rate.v1_summary(files=[fixture])
    off_row = next(r for r in table if r["arm"] == "off")
    assert off_row["pass"] == "1/2"
    assert off_row["n"] == 2
    assert off_row["bad"] == 1


def test_v1_off_pool_aggregates_across_files(tmp_path):
    fixture = _write_v1_fixture(tmp_path)
    pool = base_rate.v1_off_pool(files=[fixture])
    assert pool["n"] == 2
    assert pool["bad"] == 1


# ---------------------------------------------------------------------------
# parsing: minimal v2 fixture
# ---------------------------------------------------------------------------

def _write_v2_fixture(tmp_path: Path) -> Path:
    def row(turn, bad, cls):
        return {
            "turn": turn,
            "draft_bad": bad,
            "final_bad": bad,
            "draft_class": cls,
            "final_class": cls,
        }

    data = {
        "agent_model": "haiku",
        "reviewer_model": "haiku",
        "records": [
            {
                "scenario": "retry-backoff-24", "arm": "off", "repeat": 1,
                "turns_completed": 4, "agent_cost_usd": 0.80,
                "rows": [row(1, False, "good"), row(2, False, "good"),
                         row(3, False, "good"), row(4, False, "good")],
            },
            {
                "scenario": "retry-backoff-24", "arm": "on", "repeat": 1,
                "turns_completed": 4, "agent_cost_usd": 0.90,
                "rows": [row(1, False, "good"), row(2, False, "good"),
                         row(3, True, "loud"), row(4, True, "silent")],
            },
        ],
    }
    path = tmp_path / "chat_v2_results_fixture.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_parse_v2_file_groups_rows_by_arm(tmp_path):
    parsed = base_rate.parse_v2_file(_write_v2_fixture(tmp_path))
    assert len(parsed["by_arm"]["off"]["rows"]) == 4
    assert len(parsed["by_arm"]["on"]["rows"]) == 4


def test_v2_summary_counts_bad_and_classes(tmp_path):
    summary = base_rate.v2_summary(_write_v2_fixture(tmp_path))
    assert summary["off"]["final_bad"] == "0/4"
    assert summary["on"]["final_bad"] == "2/4"
    assert summary["on"]["loud_turns"] == [3]
    assert summary["on"]["silent_turns"] == [4]


def test_v2_summary_ungated_excludes_gated_on_turns(tmp_path):
    # GATE_DISABLED_AFTER_TURN=2: on-arm turns 1-2 are gated (excluded),
    # turns 3-4 are ungated (gate disabled), matching turns 3 and 4 above.
    summary = base_rate.v2_summary(_write_v2_fixture(tmp_path))
    assert summary["on"]["ungated_turns"] == 2
    assert summary["on"]["ungated_bad"] == 2  # both turn 3 and turn 4 are bad
    assert summary["off"]["ungated_turns"] == 4  # off is never gated
    assert summary["off"]["ungated_bad"] == 0


def test_pooled_turn_rate_combines_off_and_ungated_on(tmp_path):
    v2 = base_rate.v2_summary(_write_v2_fixture(tmp_path))
    pooled = base_rate.pooled_turn_rate(v2)
    # off: 4 turns, 0 bad. on ungated: 2 turns, 2 bad. pooled: 2/6.
    assert pooled["n"] == 6
    assert pooled["bad"] == 2
    assert math.isclose(pooled["phat"], 2 / 6)


# ---------------------------------------------------------------------------
# power_estimate / cost_estimate: sanity on the report glue
# ---------------------------------------------------------------------------

def test_power_estimate_zero_rate_reports_error_not_a_crash():
    result = base_rate.power_estimate(0.0)
    assert "error" in result


def test_power_estimate_positive_rate_returns_sessions_for_both_iccs():
    result = base_rate.power_estimate(0.10, turns_per_session=24)
    assert result["sessions_per_arm_icc_0.1"] >= result["sessions_per_arm_icc_0.0"]
    assert result["turns_per_arm"] > 0


def test_cost_estimate_doubles_for_both_arms(tmp_path):
    v2 = {"off": {"agent_cost_usd": 0.80}}
    cost = base_rate.cost_estimate(sessions_per_arm=5, v2=v2)
    assert cost["total_sessions_both_arms"] == 10
    assert math.isclose(cost["estimated_total_usd"], 0.80 * 10)


def test_prepared_command_reflects_missing_arms_flag():
    result = base_rate.prepared_command(repeats=3)
    assert "run_chat.py" in result["command"]
    assert "--arms" not in result["command"]  # run_chat.py has no such flag
    assert "no --arms flag" in result["limitation"]
    assert result["sessions"] == len(base_rate.V1_SCENARIO_IDS) * 3 * 2
