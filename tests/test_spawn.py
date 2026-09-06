# tests/test_spawn.py
import json
import os
import time
from unittest.mock import MagicMock, patch

import pytest

from crosier.spawn import marker_path, spawn_worker, worker_is_active


@pytest.fixture(autouse=True)
def _home(monkeypatch, tmp_path):
    monkeypatch.setenv("CROSIER_HOME", str(tmp_path))
    return tmp_path


def _fake_popen():
    proc = MagicMock()
    proc.pid = 1234
    proc.stdin = MagicMock()
    return proc


@patch("crosier.spawn.subprocess.Popen")
def test_spawn_writes_the_job_to_stdin_and_does_not_wait(mock_popen):
    proc = _fake_popen()
    mock_popen.return_value = proc
    assert spawn_worker("s1", {"excerpt": "text"}) is True
    written = proc.stdin.write.call_args.args[0]
    assert json.loads(written.decode("utf-8"))["excerpt"] == "text"
    # The whole point of the background worker: the hook must never join it.
    proc.wait.assert_not_called()
    proc.communicate.assert_not_called()


@patch("crosier.spawn.subprocess.Popen")
def test_spawn_never_lets_the_worker_write_to_the_hooks_stdout(mock_popen):
    # The hook's stdout is injected into the session as context; a stray line
    # from a detached process would land in the user's conversation.
    mock_popen.return_value = _fake_popen()
    spawn_worker("s2", {})
    kwargs = mock_popen.call_args.kwargs
    assert kwargs["stdout"] == kwargs["stderr"] == -3  # subprocess.DEVNULL


@patch("crosier.spawn.subprocess.Popen")
def test_spawn_detaches_the_child(mock_popen):
    mock_popen.return_value = _fake_popen()
    spawn_worker("s3", {})
    kwargs = mock_popen.call_args.kwargs
    key = "creationflags" if os.name == "nt" else "start_new_session"
    assert key in kwargs


@patch("crosier.spawn.subprocess.Popen")
def test_spawn_passes_the_home_through_to_the_worker(mock_popen):
    # The worker writes its result where the hook will look for it; the two
    # must agree even when the hook's home came from the environment.
    mock_popen.return_value = _fake_popen()
    spawn_worker("s3b", {})
    env = mock_popen.call_args.kwargs["env"]
    assert env["CROSIER_HOME"] == os.environ["CROSIER_HOME"]


@patch("crosier.spawn.subprocess.Popen", side_effect=OSError())
def test_spawn_reports_failure_instead_of_raising(mock_popen):
    assert spawn_worker("s4", {}) is False


@patch("crosier.spawn.subprocess.Popen")
def test_marker_is_only_written_once_the_job_is_delivered(mock_popen):
    # A marker left behind by a failed hand-off blocks every check for the
    # session until the deadline passes.
    proc = _fake_popen()
    proc.stdin.write.side_effect = OSError()
    mock_popen.return_value = proc
    assert spawn_worker("s4b", {}) is False
    assert not marker_path("s4b").exists()


@patch("crosier.spawn.subprocess.Popen")
def test_marker_marks_the_session_busy(mock_popen):
    mock_popen.return_value = _fake_popen()
    assert worker_is_active("s5", deadline=120) is False
    spawn_worker("s5", {})
    assert worker_is_active("s5", deadline=120) is True


@patch("crosier.spawn.subprocess.Popen")
def test_marker_older_than_the_deadline_is_ignored_and_removed(mock_popen):
    # The worker kills itself at its deadline, so a marker older than that
    # describes a process that no longer exists. Trusting it would wedge the
    # session's checks off for good.
    mock_popen.return_value = _fake_popen()
    spawn_worker("s6", {})
    path = marker_path("s6")
    stale = time.time() - 600
    os.utime(path, (stale, stale))
    assert worker_is_active("s6", deadline=120) is False
    assert not path.exists()
