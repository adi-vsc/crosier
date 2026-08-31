# tests/test_install.py
import json
from pathlib import Path

from scripts.install import merge_hooks_into_settings


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
