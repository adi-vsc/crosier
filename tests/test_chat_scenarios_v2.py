"""Chat benchmark v2 scenarios: the due map, the turn count and the reference
must agree with the hidden tests, or per-turn scoring measures the harness."""

import ast
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "benchmark" / "chat"))

from reference import REFERENCE_V2  # noqa: E402
from scenarios_v2 import SCENARIOS_V2  # noqa: E402

IDS = [s["id"] for s in SCENARIOS_V2]


def _test_names(source: str) -> set:
    return {n.name for n in ast.parse(source).body if isinstance(n, ast.FunctionDef) and n.name.startswith("test_")}


@pytest.mark.parametrize("scenario", SCENARIOS_V2, ids=IDS)
def test_scenario_has_24_turns(scenario):
    assert len(scenario["turns"]) == 24


@pytest.mark.parametrize("scenario", SCENARIOS_V2, ids=IDS)
def test_due_map_names_exactly_the_hidden_tests(scenario):
    assert set(scenario["due"]) == _test_names(scenario["hidden_test"])


@pytest.mark.parametrize("scenario", SCENARIOS_V2, ids=IDS)
def test_every_due_turn_is_a_real_turn(scenario):
    assert all(1 <= turn <= len(scenario["turns"]) for turn in scenario["due"].values())


@pytest.mark.parametrize("scenario", SCENARIOS_V2, ids=IDS)
def test_reference_passes_every_hidden_test(scenario, tmp_path):
    (tmp_path / scenario["module"]).write_text(REFERENCE_V2[scenario["id"]], encoding="utf-8")
    hidden = tmp_path / "_hidden"
    hidden.mkdir()
    (hidden / "test_hidden.py").write_text(scenario["hidden_test"], encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "_hidden", "-q", "--no-header", "-p", "no:cacheprovider"],
        cwd=tmp_path, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
    )
    assert proc.returncode == 0, proc.stdout[-2000:]
