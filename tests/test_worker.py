# tests/test_worker.py
import io
import json
import time
from unittest.mock import patch

from crosier import worker
from crosier.pending import read_result
from crosier.worker import arm_watchdog, run_job

PROCEED = {"status": "proceed", "confidence": "high", "evidence_verified": True}


def _job(**overrides):
    job = {
        "project_root": "/tmp/x",
        "session_id": "s1",
        "excerpt": "[goal] some session text",
        "turn_number": 7,
        "transcript_index": 42,
        "context_tokens": 1234,
        "call_timeout": 45,
        "verdict_model": "sonnet",
    }
    job.update(overrides)
    return job


@patch("crosier.worker.generate_verdict", return_value=PROCEED)
def test_run_job_carries_the_dispatch_context_into_the_result(mock_verdict):
    result = run_job(_job())
    assert result["ok"] is True
    assert result["verdict"]["status"] == "proceed"
    # The hook needs these to decide whether the answer still describes the
    # session it is about to be announced into.
    assert result["turn_number"] == 7
    assert result["transcript_index"] == 42
    assert result["context_tokens"] == 1234
    assert result["created_at"] <= time.time()


@patch("crosier.worker.generate_verdict", return_value=PROCEED)
def test_run_job_makes_exactly_one_reviewer_call_on_the_excerpt(mock_verdict):
    # No summarising hop: the reviewer reads the mechanical excerpt itself.
    run_job(_job(previous_flag="an earlier concern"))
    mock_verdict.assert_called_once()
    args, kwargs = mock_verdict.call_args
    assert args[0] == "[goal] some session text"
    assert kwargs["model"] == "sonnet"
    assert kwargs["timeout"] == 45
    assert kwargs["previous_flag"] == "an earlier concern"


@patch("crosier.worker.generate_verdict", return_value=None)
def test_run_job_reports_an_unparseable_verdict(mock_verdict):
    result = run_job(_job())
    assert result["ok"] is False
    assert result["verdict"] is None
    assert "verdict" in result["error"]


def test_run_job_skips_the_call_on_an_empty_excerpt():
    with patch("crosier.worker.generate_verdict") as mock_verdict:
        result = run_job(_job(excerpt=""))
    assert result["ok"] is False
    mock_verdict.assert_not_called()


def test_watchdog_kills_the_headless_call_before_exiting():
    # Nothing supervises a detached worker, so the deadline has to be enforced
    # from the inside — and it has to take the `claude` process with it.
    calls = []
    with patch("crosier.worker.kill_active", side_effect=lambda: calls.append("kill")):
        with patch("crosier.worker.os._exit", side_effect=lambda code: calls.append(code)):
            timer = arm_watchdog(0)
            timer.join(2)
    assert calls == ["kill", 1]


def test_watchdog_is_a_daemon_so_it_cannot_hold_the_worker_open():
    timer = arm_watchdog(3600)
    try:
        assert timer.daemon is True
    finally:
        timer.cancel()


@patch("crosier.worker.generate_verdict", return_value=PROCEED)
def test_main_writes_the_result_and_releases_the_marker(mock_verdict, tmp_path, monkeypatch):
    marker = tmp_path / "workers" / "s1.pid"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("1234", encoding="utf-8")
    job = _job(project_root=str(tmp_path), marker_path=str(marker))
    monkeypatch.setenv("CROSIER_HOME", str(tmp_path))
    monkeypatch.setattr(worker.sys, "stdin", io.StringIO(json.dumps(job)))

    assert worker.main() == 0
    assert read_result("s1")["ok"] is True
    # Left behind, this would block every later check for the session.
    assert not marker.exists()


def test_main_ignores_a_malformed_job(monkeypatch):
    monkeypatch.setattr(worker.sys, "stdin", io.StringIO("not json"))
    assert worker.main() == 0
