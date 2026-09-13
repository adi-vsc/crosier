"""A finished benchmark run has to say why the reviewer flagged, not only that
it did. Without the quote and the reason, the 2026-09-11 off_goal diagnosis could
only be made from the category."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "benchmark"))

import run  # noqa: E402

FLAG = {
    "status": "flag",
    "confidence": "medium",
    "category": "off_goal",
    "flagged_claim": "work drifted to the CLI",
    "reason": "the goal asks for a migration plan",
    "suggested_check": "re-read the goal",
    "evidence": "Give me a plan for the migration",
    "evidence_verified": True,
}


def test_run_case_keeps_the_evidence_and_reason(monkeypatch):
    monkeypatch.setattr(run, "generate_verdict", lambda *a, **k: dict(FLAG))
    out = run.run_case({"id": "c1", "excerpt": "x"}, repeats=1, model="sonnet", timeout=5)
    kept = out["runs"][0]
    assert kept["evidence"] == FLAG["evidence"]
    assert kept["reason"] == FLAG["reason"]
    assert kept["flagged_claim"] == FLAG["flagged_claim"]
