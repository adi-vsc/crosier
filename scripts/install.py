"""Fallback installer: merge Crosier's hook entries directly into a Claude
Code settings.json, for users installing outside a plugin marketplace flow."""

import json
import subprocess
import sys
from pathlib import Path


def _hook_entry(command: str) -> dict:
    return {"hooks": [{"type": "command", "command": command, "timeout": 100}]}


def _resolve_python() -> str:
    """Probe for a working Python interpreter.

    ``python3`` resolves to a non-functional Microsoft Store alias stub on
    many stock Windows installs (python.org installs ship ``python.exe``
    only), so we can't hardcode it. Try ``python3``, then ``python``, then
    the Windows ``py -3`` launcher, and use whichever actually runs.
    """
    for candidate in (["python3"], ["python"], ["py", "-3"]):
        try:
            result = subprocess.run(candidate + ["--version"], capture_output=True, timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            continue
        if result.returncode == 0:
            return " ".join(candidate)
    return "python3"


def merge_hooks_into_settings(settings_path: Path, plugin_root: Path) -> None:
    settings_path = Path(settings_path)
    if settings_path.exists():
        data = json.loads(settings_path.read_text(encoding="utf-8") or "{}")
    else:
        data = {}

    data.setdefault("hooks", {})

    python_cmd = _resolve_python()
    plugin_root_posix = Path(plugin_root).as_posix()
    ups_command = f'{python_cmd} "{plugin_root_posix}/hooks/user_prompt_submit.py"'
    precompact_command = f'{python_cmd} "{plugin_root_posix}/hooks/pre_compact.py"'

    for event, command in (("UserPromptSubmit", ups_command), ("PreCompact", precompact_command)):
        existing = data["hooks"].setdefault(event, [])
        already_present = any(
            command == h.get("command")
            for entry in existing
            for h in entry.get("hooks", [])
        )
        if not already_present:
            existing.append(_hook_entry(command))

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
