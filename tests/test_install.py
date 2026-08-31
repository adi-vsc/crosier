# tests/test_install.py
import json
import subprocess
from pathlib import Path

import pytest

from scripts.install import merge_hooks_into_settings, _resolve_python


@pytest.fixture(autouse=True)
def _fixed_python(monkeypatch):
    # Pin the resolved interpreter so merge tests are deterministic and don't
    # spawn real subprocesses; interpreter resolution itself is tested below.
    monkeypatch.setattr("scripts.install._resolve_python", lambda: "python3")


def test_merge_into_empty_settings(tmp_path: Path):
    settings_path = tmp_path / "settings.json"
    settings_path.write_text("{}", encoding="utf-8")
    merge_hooks_into_settings(settings_path, plugin_root=Path("/plugins/crosier"))
    data = json.loads(settings_path.read_text(encoding="utf-8"))
    assert "UserPromptSubmit" in data["hooks"]
    assert "PreCompact" in data["hooks"]
    command = data["hooks"]["UserPromptSubmit"][0]["hooks"][0]["command"]
    assert "user_prompt_submit.py" in command
    assert "/plugins/crosier" in command


def test_merge_preserves_existing_unrelated_hooks(tmp_path: Path):
    settings_path = tmp_path / "settings.json"
    settings_path.write_text(
        json.dumps({"hooks": {"SessionStart": [{"hooks": [{"type": "command", "command": "echo hi"}]}]}}),
        encoding="utf-8",
    )
    merge_hooks_into_settings(settings_path, plugin_root=Path("/plugins/crosier"))
    data = json.loads(settings_path.read_text(encoding="utf-8"))
    assert data["hooks"]["SessionStart"][0]["hooks"][0]["command"] == "echo hi"
    assert "UserPromptSubmit" in data["hooks"]


def test_merge_is_idempotent(tmp_path: Path):
    settings_path = tmp_path / "settings.json"
    settings_path.write_text("{}", encoding="utf-8")
    merge_hooks_into_settings(settings_path, plugin_root=Path("/plugins/crosier"))
    merge_hooks_into_settings(settings_path, plugin_root=Path("/plugins/crosier"))
    data = json.loads(settings_path.read_text(encoding="utf-8"))
    assert len(data["hooks"]["UserPromptSubmit"]) == 1


def test_merge_writes_timeout_on_hook_entries(tmp_path: Path):
    settings_path = tmp_path / "settings.json"
    settings_path.write_text("{}", encoding="utf-8")
    merge_hooks_into_settings(settings_path, plugin_root=Path("/plugins/crosier"))
    data = json.loads(settings_path.read_text(encoding="utf-8"))
    assert data["hooks"]["UserPromptSubmit"][0]["hooks"][0]["timeout"] == 100
    assert data["hooks"]["PreCompact"][0]["hooks"][0]["timeout"] == 100


def test_resolve_python_falls_back_when_python3_is_broken(monkeypatch):
    def fake_run(cmd, **kwargs):
        returncode = 9009 if cmd[0] == "python3" else 0
        return subprocess.CompletedProcess(cmd, returncode=returncode)

    monkeypatch.setattr("scripts.install.subprocess.run", fake_run)
    assert _resolve_python() == "python"


def test_resolve_python_prefers_python3_when_it_works(monkeypatch):
    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, returncode=0)

    monkeypatch.setattr("scripts.install.subprocess.run", fake_run)
    assert _resolve_python() == "python3"


def test_resolve_python_falls_back_to_py_launcher(monkeypatch):
    def fake_run(cmd, **kwargs):
        if cmd[:2] == ["py", "-3"]:
            return subprocess.CompletedProcess(cmd, returncode=0)
        return subprocess.CompletedProcess(cmd, returncode=9009)

    monkeypatch.setattr("scripts.install.subprocess.run", fake_run)
    assert _resolve_python() == "py -3"
