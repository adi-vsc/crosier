# tests/test_state.py
from pathlib import Path
from unittest.mock import patch

import pytest

from crosier.state import SessionState, load_state, save_state, state_path


@pytest.fixture(autouse=True)
def _home(monkeypatch, tmp_path):
    monkeypatch.setenv("CROSIER_HOME", str(tmp_path))
    return tmp_path


def test_load_state_returns_fresh_defaults_when_no_file():
    assert load_state("session-abc") == SessionState()


def test_save_then_load_roundtrips():
    state = SessionState(
        last_line_index=12,
        total_turns=5,
        consecutive_failures=1,
        last_flag="the endpoint is idempotent",
        disabled_for_session=False,
    )
    save_state("session-abc", state)
    assert load_state("session-abc") == state


def test_unknown_fields_from_an_older_version_are_ignored():
    path = state_path("session-old")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"total_turns": 3, "retired_field": 1}', encoding="utf-8")
    assert load_state("session-old").total_turns == 3


def test_state_is_isolated_per_session_id():
    save_state("session-a", SessionState(total_turns=3))
    save_state("session-b", SessionState(total_turns=9))
    assert load_state("session-a").total_turns == 3
    assert load_state("session-b").total_turns == 9


def test_save_state_leaves_no_temp_file_behind():
    save_state("session-abc", SessionState(total_turns=1))
    path = state_path("session-abc")
    assert path.exists()
    assert not path.with_name(path.name + ".tmp").exists()


def test_save_state_writes_via_atomic_replace():
    path = state_path("session-abc")
    with patch("crosier.state.os.replace") as mock_replace:
        save_state("session-abc", SessionState(total_turns=1))
    mock_replace.assert_called_once()
    src, dst = mock_replace.call_args[0]
    assert Path(dst) == path
    assert Path(src) == path.with_name(path.name + ".tmp")
