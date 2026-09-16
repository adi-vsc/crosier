# tests/test_install.py
import json
import subprocess
from pathlib import Path

import pytest

from scripts.install import HOOK_EVENTS, _resolve_python, merge_hooks_into_settings


@pytest.fixture(autouse=True)
def _fixed_python(monkeypatch):
    # Pin the resolved interpreter so merge tests are deterministic and don't
    # spawn real subprocesses; interpreter resolution itself is tested below.
    monkeypatch.setattr("scripts.install._resolve_python", lambda: "python3")


def test_merge_registers_every_event_on_the_single_entrypoint(tmp_path: Path):
    settings_path = tmp_path / "settings.json"
    settings_path.write_text("{}", encoding="utf-8")
    merge_hooks_into_settings(settings_path, plugin_root=Path("/plugins/crosier"))
    data = json.loads(settings_path.read_text(encoding="utf-8"))
    assert set(HOOK_EVENTS) == {"Stop"}
    for event in HOOK_EVENTS:
        command = data["hooks"][event][0]["hooks"][0]["command"]
        assert "crosier_hook.py" in command
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
    assert "Stop" in data["hooks"]


def test_merge_is_idempotent(tmp_path: Path):
    settings_path = tmp_path / "settings.json"
    settings_path.write_text("{}", encoding="utf-8")
    merge_hooks_into_settings(settings_path, plugin_root=Path("/plugins/crosier"))
    merge_hooks_into_settings(settings_path, plugin_root=Path("/plugins/crosier"))
    data = json.loads(settings_path.read_text(encoding="utf-8"))
    assert len(data["hooks"]["Stop"]) == 1


def test_stop_timeout_outlasts_the_gate_call_in_both_install_paths(tmp_path: Path):
    # The Stop gate runs a reviewer call inside the hook. A hook Claude Code
    # cancels at its timeout has its output discarded, which fails open but also
    # throws the paid-for verdict away, so the hook timeout must cover the call
    # plus the kill-tree (10s) and drain (5s) run_claude may spend after it.
    from crosier.pipeline import GATE_CALL_TIMEOUT_CAP
    from scripts.install import STOP_HOOK_TIMEOUT_SECONDS

    assert STOP_HOOK_TIMEOUT_SECONDS >= GATE_CALL_TIMEOUT_CAP + 20
    settings_path = tmp_path / "settings.json"
    settings_path.write_text("{}", encoding="utf-8")
    merge_hooks_into_settings(settings_path, plugin_root=Path("/plugins/crosier"))
    data = json.loads(settings_path.read_text(encoding="utf-8"))
    assert data["hooks"]["Stop"][0]["hooks"][0]["timeout"] == STOP_HOOK_TIMEOUT_SECONDS
    manifest = json.loads((Path(__file__).resolve().parent.parent / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    assert manifest["hooks"]["Stop"][0]["hooks"][0]["timeout"] == STOP_HOOK_TIMEOUT_SECONDS


def _runner(versions: dict):
    """Fake `subprocess.run` for the version probe: `versions` maps the first
    argv token to the (major, minor) it reports, or None for a broken stub."""

    def fake_run(cmd, **kwargs):
        version = versions.get(cmd[0])
        if version is None:
            return subprocess.CompletedProcess(cmd, returncode=9009, stdout=b"")
        return subprocess.CompletedProcess(cmd, returncode=0, stdout=f"{version[0]}.{version[1]}".encode())

    return fake_run


def test_resolve_python_falls_back_when_python3_is_broken(monkeypatch):
    monkeypatch.setattr("scripts.install.subprocess.run", _runner({"python3": None, "python": (3, 12)}))
    assert _resolve_python() == "python"


def test_resolve_python_prefers_python3_when_it_works(monkeypatch):
    monkeypatch.setattr("scripts.install.subprocess.run", _runner({"python3": (3, 12), "python": (3, 12)}))
    assert _resolve_python() == "python3"


def test_resolve_python_falls_back_to_py_launcher(monkeypatch):
    monkeypatch.setattr("scripts.install.subprocess.run", _runner({"py": (3, 12)}))
    assert _resolve_python() == "py -3"


def test_resolve_python_prefers_an_interpreter_with_tomllib(monkeypatch):
    # On this machine `python` is 3.10 and `py -3` is 3.14. The hook runs on
    # 3.10 (config ignored) but should not be installed onto it when a newer
    # interpreter is one probe away.
    monkeypatch.setattr("scripts.install.subprocess.run", _runner({"python3": None, "python": (3, 10), "py": (3, 14)}))
    assert _resolve_python() == "py -3"


def test_resolve_python_settles_for_an_old_interpreter_when_nothing_newer_exists(monkeypatch):
    monkeypatch.setattr("scripts.install.subprocess.run", _runner({"python": (3, 10)}))
    assert _resolve_python() == "python"
