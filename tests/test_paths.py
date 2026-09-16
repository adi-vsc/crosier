# tests/test_paths.py
"""Crosier's own files live under the user's Claude directory, not inside the
project. An install-and-forget plugin that drops an untracked `.crosier/` into
every repository it runs in gets uninstalled; and session ids are UUIDs, so
nothing needs the project to scope them."""

from pathlib import Path

from crosier.paths import crosier_home, errors_log_path, state_path


def test_home_defaults_under_the_users_claude_directory(monkeypatch):
    monkeypatch.delenv("CROSIER_HOME", raising=False)
    assert crosier_home() == Path.home() / ".claude" / "crosier"


def test_home_honours_the_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("CROSIER_HOME", str(tmp_path))
    assert crosier_home() == tmp_path


def test_paths_are_keyed_on_a_sanitized_session_id(monkeypatch, tmp_path):
    monkeypatch.setenv("CROSIER_HOME", str(tmp_path))
    assert state_path("../../escape") == tmp_path / "state" / "escape.json"
    assert errors_log_path() == tmp_path / "errors.log"


def test_empty_session_id_still_yields_a_filename(monkeypatch, tmp_path):
    monkeypatch.setenv("CROSIER_HOME", str(tmp_path))
    assert state_path("").name == "unknown.json"
