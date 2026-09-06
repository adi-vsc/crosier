# tests/test_errors.py
from crosier.errors import MAX_CONSECUTIVE_FAILURES, record_failure, should_disable


def test_record_failure_creates_log_and_appends(monkeypatch, tmp_path):
    monkeypatch.setenv("CROSIER_HOME", str(tmp_path))
    record_failure("first failure")
    record_failure("second failure")
    content = (tmp_path / "errors.log").read_text(encoding="utf-8")
    assert "first failure" in content
    assert "second failure" in content
    assert content.count("\n") == 2


def test_record_failure_never_raises_when_the_log_is_unwritable(monkeypatch, tmp_path):
    monkeypatch.setenv("CROSIER_HOME", str(tmp_path / "a-file"))
    (tmp_path / "a-file").write_text("not a directory", encoding="utf-8")
    record_failure("ignored")


def test_should_disable_after_max_consecutive_failures():
    assert should_disable(MAX_CONSECUTIVE_FAILURES - 1) is False
    assert should_disable(MAX_CONSECUTIVE_FAILURES) is True
