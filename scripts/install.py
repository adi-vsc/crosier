"""Fallback installer: merge Crosier's hook entries directly into a Claude
Code settings.json, for users installing outside a plugin marketplace flow."""

import json
import subprocess
import sys
from pathlib import Path

"""The hook parses a transcript and spawns a detached worker, so its own
runtime is milliseconds. A generous timeout here would only ever mean the user
staring at a frozen prompt because something went wrong."""
HOOK_TIMEOUT_SECONDS = 10

"""Stop is the exception: with `stop_gate` on it runs a reviewer call in the
hook, capped at pipeline.GATE_CALL_TIMEOUT_CAP, plus up to 15s of kill and drain.
With the gate off the hook still returns in milliseconds, so the longer bound
costs nothing unless something is actually wrong."""
STOP_HOOK_TIMEOUT_SECONDS = 90

HOOK_EVENTS = ("UserPromptSubmit", "PostToolBatch", "Stop", "PreCompact")

MIN_VERSION = (3, 11)  # tomllib; below it the hook runs with defaults only

_VERSION_PROBE = "import sys; print('%d.%d' % sys.version_info[:2])"


def _hook_entry(command: str, timeout: int = HOOK_TIMEOUT_SECONDS) -> dict:
    return {"hooks": [{"type": "command", "command": command, "timeout": timeout}]}


def _probe(candidate: list) -> tuple | None:
    try:
        result = subprocess.run(candidate + ["-c", _VERSION_PROBE], capture_output=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    try:
        major, minor = result.stdout.decode().strip().split(".")
        return (int(major), int(minor))
    except ValueError:
        return None


def _resolve_python() -> str:
    """Probe for a working Python interpreter, preferring one new enough.

    ``python3`` resolves to a non-functional Microsoft Store alias stub on
    many stock Windows installs (python.org installs ship ``python.exe``
    only), and ``python`` there is often an older interpreter without
    ``tomllib``. Try ``python3``, ``python``, then the ``py -3`` launcher;
    take the first that meets MIN_VERSION, else the first that runs at all.
    """
    fallback = None
    for candidate in (["python3"], ["python"], ["py", "-3"]):
        version = _probe(candidate)
        if version is None:
            continue
        if version >= MIN_VERSION:
            return " ".join(candidate)
        fallback = fallback or " ".join(candidate)
    return fallback or "python3"


def merge_hooks_into_settings(settings_path: Path, plugin_root: Path) -> None:
    settings_path = Path(settings_path)
    if settings_path.exists():
        data = json.loads(settings_path.read_text(encoding="utf-8") or "{}")
    else:
        data = {}

    data.setdefault("hooks", {})

    python_cmd = _resolve_python()
    plugin_root_posix = Path(plugin_root).as_posix()
    command = f'{python_cmd} "{plugin_root_posix}/hooks/crosier_hook.py"'

    for event in HOOK_EVENTS:
        existing = data["hooks"].setdefault(event, [])
        already_present = any(
            command == h.get("command")
            for entry in existing
            for h in entry.get("hooks", [])
        )
        if not already_present:
            timeout = STOP_HOOK_TIMEOUT_SECONDS if event == "Stop" else HOOK_TIMEOUT_SECONDS
            existing.append(_hook_entry(command, timeout))

    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def main() -> int:
    home_settings = Path.home() / ".claude" / "settings.json"
    plugin_root = Path(__file__).resolve().parent.parent
    merge_hooks_into_settings(home_settings, plugin_root)
    print(f"Crosier hooks registered in {home_settings}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
