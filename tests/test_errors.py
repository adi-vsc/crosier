# tests/test_errors.py
from pathlib import Path

from crosier.errors import MAX_CONSECUTIVE_FAILURES, record_failure, should_disable


def test_record_failure_creates_log_and_appends(tmp_path: Path):
    record_failure(tmp_path, "first failure")
    record_failure(tmp_path, "second failure")
    log_path = tmp_path / ".crosier" / "errors.log"
    content = log_path.read_text(encoding="utf-8")
    assert "first failure" in content
    assert "second failure" in content
    assert content.count("\n") == 2


def test_should_disable_false_below_max():
    assert should_disable(MAX_CONSECUTIVE_FAILURES - 1) is False


def test_should_disable_true_at_max():
    assert should_disable(MAX_CONSECUTIVE_FAILURES) is True


def test_should_disable_true_above_max():
    assert should_disable(MAX_CONSECUTIVE_FAILURES + 5) is True
