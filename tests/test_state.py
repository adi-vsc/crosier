# tests/test_state.py
from pathlib import Path
from unittest.mock import patch

from crosier.state import SessionState, load_state, save_state, _state_path


def test_load_state_returns_fresh_defaults_when_no_file(tmp_path: Path):
    state = load_state(tmp_path, "session-abc")
    assert state == SessionState()


def test_save_then_load_roundtrips(tmp_path: Path):
    state = SessionState(
        last_line_index=12,
        total_turns=5,
        turns_since_check=5,
        chars_since_check=4000,
        recent_tool_calls=["Read:111", "Edit:222"],
        consecutive_failures=1,
        disabled_for_session=False,
    )
    save_state(tmp_path, "session-abc", state)
    loaded = load_state(tmp_path, "session-abc")
    assert loaded == state


def test_state_is_isolated_per_session_id(tmp_path: Path):
    save_state(tmp_path, "session-a", SessionState(total_turns=3))
    save_state(tmp_path, "session-b", SessionState(total_turns=9))
    assert load_state(tmp_path, "session-a").total_turns == 3
    assert load_state(tmp_path, "session-b").total_turns == 9


def test_save_state_leaves_no_temp_file_behind(tmp_path: Path):
    save_state(tmp_path, "session-abc", SessionState(total_turns=1))
    path = _state_path(tmp_path, "session-abc")
    tmp_path_file = path.with_name(path.name + ".tmp")
    assert path.exists()
    assert not tmp_path_file.exists()


def test_save_state_writes_via_atomic_replace(tmp_path: Path):
    path = _state_path(tmp_path, "session-abc")
    with patch("crosier.state.os.replace") as mock_replace:
        save_state(tmp_path, "session-abc", SessionState(total_turns=1))
    mock_replace.assert_called_once()
    src, dst = mock_replace.call_args[0]
    assert Path(dst) == path
    assert Path(src) == path.with_name(path.name + ".tmp")
