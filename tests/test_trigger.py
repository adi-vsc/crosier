"""An on-demand check. The CLI runs outside the session and is never told its
id, so the request is one unscoped file that the next hook invocation claims."""

import time

from crosier.paths import trigger_path
from crosier.trigger import consume_trigger, request_check


def _home(monkeypatch, tmp_path):
    monkeypatch.setenv("CROSIER_HOME", str(tmp_path / "home"))


def test_no_request_means_no_check(monkeypatch, tmp_path):
    _home(monkeypatch, tmp_path)
    assert consume_trigger() is False


def test_a_request_is_claimed_exactly_once(monkeypatch, tmp_path):
    _home(monkeypatch, tmp_path)
    assert request_check() is True
    assert consume_trigger() is True
    assert consume_trigger() is False


def test_claiming_removes_the_file(monkeypatch, tmp_path):
    _home(monkeypatch, tmp_path)
    request_check()
    consume_trigger()
    assert not trigger_path().exists()


def test_a_forgotten_request_expires_rather_than_firing_later(monkeypatch, tmp_path):
    # The user asked, then closed the terminal. Days later that must not fire
    # into an unrelated session.
    _home(monkeypatch, tmp_path)
    request_check()
    stale = time.time() - 4000
    import os

    os.utime(trigger_path(), (stale, stale))
    assert consume_trigger() is False
    # Expired or not, the request is consumed: it never accumulates.
    assert not trigger_path().exists()
