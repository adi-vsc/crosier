# tests/test_errors.py
from crosier.errors import (
    COOLDOWN_REVIEWS,
    MAX_CONSECUTIVE_FAILURES,
    MAX_TOTAL_FAILURES,
    record_failure,
    should_cooldown,
    should_disable,
    should_hard_stop,
)


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


def test_should_cooldown_at_the_same_threshold_as_the_old_should_disable():
    # should_cooldown is the new name for this threshold: pipeline.py's own
    # permanent disable still fires at should_disable's threshold, and the
    # hook converts that into a cooldown at the same point.
    assert should_cooldown(MAX_CONSECUTIVE_FAILURES - 1) is False
    assert should_cooldown(MAX_CONSECUTIVE_FAILURES) is True


def test_should_hard_stop_after_max_total_failures():
    assert should_hard_stop(MAX_TOTAL_FAILURES - 1) is False
    assert should_hard_stop(MAX_TOTAL_FAILURES) is True


def test_cooldown_and_hard_stop_thresholds_are_sane():
    # A hard stop must take more than one cooldown cycle to reach, or the
    # cooldown never gets a chance to matter.
    assert MAX_TOTAL_FAILURES > MAX_CONSECUTIVE_FAILURES
    assert COOLDOWN_REVIEWS > 0
