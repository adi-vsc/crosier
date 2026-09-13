"""The chat benchmark's draft capture: a Stop hook that commits the working tree
into a git dir the agent never sees, one commit per Stop, so a blocked draft and
its revision can both be scored. Real git, temp dirs, no API."""

import importlib.util
import io
import json
import subprocess
import sys
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parent.parent / "benchmark" / "chat" / "snapshot_plugin" / "hooks" / "snapshot_hook.py"


def _load():
    spec = importlib.util.spec_from_file_location("snapshot_hook", HOOK)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def env(tmp_path, monkeypatch):
    work = tmp_path / "work"
    work.mkdir()
    snap = tmp_path / "snap"
    monkeypatch.setenv("BENCH_SNAPSHOT_DIR", str(snap))
    monkeypatch.setenv("BENCH_TURN", "3")
    return work, snap


def _fire(module, monkeypatch, work, **extra):
    payload = {"hook_event_name": "Stop", "cwd": str(work), "session_id": "s1", **extra}
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    assert module.main() == 0


def _records(snap):
    return [json.loads(line) for line in (snap / "stops.jsonl").read_text(encoding="utf-8").splitlines()]


def _show(snap, sha, path):
    return subprocess.run(
        ["git", "--git-dir", str(snap / "git"), "show", f"{sha}:{path}"], capture_output=True, text=True
    ).stdout


def test_each_stop_commits_the_tree_and_records_the_answer(env, monkeypatch):
    work, snap = env
    module = _load()
    (work / "mod.py").write_text("v = 1\n", encoding="utf-8")
    _fire(module, monkeypatch, work, stop_hook_active=False, last_assistant_message="draft")
    (work / "mod.py").write_text("v = 2\n", encoding="utf-8")
    _fire(module, monkeypatch, work, stop_hook_active=True, last_assistant_message="revised")

    first, second = _records(snap)
    assert (first["turn"], first["stop_hook_active"], first["last_assistant_message"]) == (3, False, "draft")
    assert (second["turn"], second["stop_hook_active"], second["last_assistant_message"]) == (3, True, "revised")
    assert _show(snap, first["sha"], "mod.py") == "v = 1\n"
    assert _show(snap, second["sha"], "mod.py") == "v = 2\n"
    assert first["torn"] is False


def test_the_agents_own_repo_and_the_oracle_stay_out_of_the_snapshot(env, monkeypatch):
    work, snap = env
    module = _load()
    subprocess.run(["git", "init", "-q"], cwd=work, check=True)
    (work / "_hidden").mkdir()
    (work / "_hidden" / "test_hidden.py").write_text("secret", encoding="utf-8")
    (work / ".crosier_home").mkdir()
    (work / ".crosier_home" / "state.json").write_text("{}", encoding="utf-8")
    (work / "mod.py").write_text("v = 1\n", encoding="utf-8")
    _fire(module, monkeypatch, work, stop_hook_active=False, last_assistant_message="x")

    sha = _records(snap)[0]["sha"]
    listed = subprocess.run(
        ["git", "--git-dir", str(snap / "git"), "ls-tree", "-r", "--name-only", sha], capture_output=True, text=True
    ).stdout.split()
    assert listed == ["mod.py"]
    # The agent's repo has no commits from the hook.
    log = subprocess.run(["git", "log", "--oneline"], cwd=work, capture_output=True, text=True)
    assert log.stdout == ""


def test_without_a_snapshot_dir_the_hook_does_nothing(tmp_path, monkeypatch):
    monkeypatch.delenv("BENCH_SNAPSHOT_DIR", raising=False)
    module = _load()
    work = tmp_path / "work"
    work.mkdir()
    _fire(module, monkeypatch, work, stop_hook_active=False, last_assistant_message="x")
    assert list(tmp_path.iterdir()) == [work]


def test_a_git_failure_never_fails_the_turn(env, monkeypatch):
    work, snap = env
    module = _load()
    monkeypatch.setattr(module, "GIT", "git-that-does-not-exist")
    _fire(module, monkeypatch, work, stop_hook_active=False, last_assistant_message="x")
