# tests/test_pending.py
import time

import pytest

from crosier.pending import clear_result, is_stale, read_result, result_path, write_result


@pytest.fixture(autouse=True)
def _home(monkeypatch, tmp_path):
    monkeypatch.setenv("CROSIER_HOME", str(tmp_path))
    return tmp_path


def test_write_then_read_round_trips():
    write_result("s1", {"ok": True, "verdict": {"status": "proceed"}})
    assert read_result("s1")["ok"] is True


def test_read_missing_result_is_none():
    assert read_result("nope") is None


def test_read_corrupt_result_is_none():
    path = result_path("s2")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")
    assert read_result("s2") is None


def test_clear_removes_the_result():
    write_result("s3", {"ok": True})
    clear_result("s3")
    assert read_result("s3") is None


def test_clear_is_safe_when_nothing_is_there():
    clear_result("s4")


def test_session_id_is_sanitized_into_the_filename(_home):
    write_result("../../escape", {"ok": True})
    assert result_path("../../escape").parent == _home / "results"


def test_fresh_result_is_not_stale():
    result = {"transcript_index": 100, "created_at": time.time()}
    assert is_stale(result, current_line_count=105, line_limit=40) is False


def test_result_is_stale_once_the_session_has_moved_on():
    # 60 transcript lines happened while the reviewer was thinking: it is
    # critiquing a file the agent has already finished with.
    result = {"transcript_index": 100, "created_at": time.time()}
    assert is_stale(result, current_line_count=160, line_limit=40) is True


def test_an_idle_session_does_not_make_a_result_stale():
    # The agent stopped and the user walked away for an hour. Nothing moved,
    # so the verdict still describes the session exactly. The old age-based
    # rule discarded every verdict that waited for the user to come back.
    result = {"transcript_index": 100, "created_at": time.time() - 100_000}
    assert is_stale(result, current_line_count=101, line_limit=40) is False


def test_missing_fields_do_not_make_a_result_stale():
    assert is_stale({}, current_line_count=10_000, line_limit=40) is False
