"""Per-session state, persisted to disk between hook invocations (each hook
call is a fresh process, so nothing survives in memory between turns)."""

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class SessionState:
    last_line_index: int = 0
    total_turns: int = 0
    turns_since_check: int = 0
    chars_since_check: int = 0
    recent_tool_calls: list = field(default_factory=list)
    consecutive_failures: int = 0
    disabled_for_session: bool = False


def _state_path(project_root: Path, session_id: str) -> Path:
    safe_id = "".join(c for c in session_id if c.isalnum() or c in "-_") or "unknown"
    return Path(project_root) / ".crosier" / "state" / f"{safe_id}.json"


def load_state(project_root: Path, session_id: str) -> SessionState:
    path = _state_path(project_root, session_id)
    if not path.exists():
        return SessionState()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return SessionState()
    known_fields = {f for f in SessionState.__dataclass_fields__}
    filtered = {k: v for k, v in data.items() if k in known_fields}
    return SessionState(**filtered)


def save_state(project_root: Path, session_id: str, state: SessionState) -> None:
    path = _state_path(project_root, session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(path.name + ".tmp")
    tmp_path.write_text(json.dumps(asdict(state)), encoding="utf-8")
    os.replace(tmp_path, path)
