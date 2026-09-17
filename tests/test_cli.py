"""The diagnostics surface. It reads only Crosier's own files under
CROSIER_HOME — never the transcript, never the project — so a reporting tool
cannot become a way for anything to see more than the reviewer can."""

import pytest

from crosier import cli
from crosier.cli import main
from crosier.journal import record_check
from crosier.state import SessionState, save_state


@pytest.fixture(autouse=True)
def _home(monkeypatch, tmp_path):
    monkeypatch.setenv("CROSIER_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("CROSIER_DISABLED", raising=False)
    return tmp_path / "home"


def test_status_without_a_session_says_so(capsys):
    assert main(["status"]) == 0
    out = capsys.readouterr().out
    assert "none yet" in out
    assert "enabled:   no (parked by default" in out


def test_status_reports_budget_and_the_last_verdict(capsys):
    save_state("s1", SessionState(checks_run=3))
    record_check("s1", {"turn": 7, "status": "flag", "category": "unverified_claim",
                        "flagged_claim": "tests pass", "delivered_to_agent": True})
    assert main(["status"]) == 0
    out = capsys.readouterr().out
    assert "session:   s1" in out
    assert "checks:    3/12 used" in out
    assert "unverified_claim: tests pass" in out


def test_status_reports_the_env_kill_switch(monkeypatch, capsys):
    monkeypatch.setenv("CROSIER_DISABLED", "1")
    assert main(["status"]) == 0
    assert "enabled:   no (CROSIER_DISABLED is set" in capsys.readouterr().out


def test_status_picks_the_most_recently_written_session(capsys):
    save_state("older", SessionState(checks_run=1))
    save_state("newer", SessionState(checks_run=2))
    assert main(["status"]) == 0
    assert "session:   newer" in capsys.readouterr().out


def test_report_lists_every_check_and_marks_what_the_agent_saw(capsys):
    save_state("s1", SessionState(checks_run=2, total_turns=9))
    record_check("s1", {"turn": 3, "status": "proceed", "delivered_to_agent": False,
                        "confidence": "high", "context_tokens": 41000})
    record_check("s1", {"turn": 8, "status": "flag", "category": "loop",
                        "flagged_claim": "re-reading conf.yaml", "confidence": "low",
                        "delivered_to_agent": False})
    assert main(["report"]) == 0
    out = capsys.readouterr().out
    assert "checks used:  2/12" in out
    assert "turns seen:   9" in out
    assert "(1 flagged, 0 shown to the agent)" in out
    assert "turn 3: no issues found" in out
    assert "loop: re-reading conf.yaml" in out
    # A flag the confidence gate suppressed is reported as such, not as a
    # correction the agent ignored.
    assert "not shown to the agent" in out
    assert "41,000 ctx tokens" in out


def test_report_without_a_session_is_not_an_error(capsys):
    assert main(["report"]) == 0
    assert "No Crosier session found" in capsys.readouterr().out


def test_bare_invocation_prints_help_rather_than_failing(capsys):
    assert main([]) == 0
    assert "usage: crosier" in capsys.readouterr().out


def test_report_shows_what_the_reviewer_itself_spent(tmp_path, monkeypatch, capsys):
    # scope.md open question: a plugin whose whole pitch is saving a session
    # from wasted work has to be able to say what it cost to run.
    monkeypatch.setattr(cli, "_latest_session", lambda: "sess")
    monkeypatch.setattr(cli, "load_state", lambda _s: SessionState(checks_run=2, total_turns=9))
    monkeypatch.setattr(
        cli,
        "read_journal",
        lambda _s: [
            {"turn": 3, "status": "proceed", "confidence": "high",
             "input_tokens": 19843, "output_tokens": 115, "cost_usd": 0.0946},
            {"turn": 8, "status": "proceed", "confidence": "high",
             "input_tokens": 10000, "output_tokens": 200, "cost_usd": 0.05},
        ],
    )
    cli.cmd_report(None)
    out = capsys.readouterr().out
    assert "29,843" in out
    assert "315" in out
    assert "0.14" in out
