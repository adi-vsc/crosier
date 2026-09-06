"""Per-session state, persisted to disk between hook invocations (each hook
call is a fresh process, so nothing survives in memory between turns)."""

import json
import os
from dataclasses import asdict, dataclass, field

from crosier.paths import state_path

__all__ = ["SessionState", "load_state", "save_state", "state_path"]


@dataclass
class SessionState:
    # Where the last successful review ended: the next excerpt starts here.
    last_line_index: int = 0
    # Where counting ended: every line before this has fed the trigger
    # counters exactly once, whichever hook event saw it first.
    counted_line_index: int = 0
    total_turns: int = 0
    # Model calls (tool batches) are the unit drift accrues in: every one is a
    # chance to build on the model's own previous output. User turns are far
    # too coarse — autonomous sessions run hundreds of calls per prompt.
    calls_since_check: int = 0
    turns_since_check: int = 0
    chars_since_check: int = 0
    tokens_at_last_check: int = 0
    recent_tool_calls: list = field(default_factory=list)
    checks_run: int = 0
    consecutive_failures: int = 0
    # The last concern shown to the agent. Carried into the next review so a
    # concern the agent already answered is not raised again as if new.
    last_flag: str | None = None
    disabled_for_session: bool = False


def load_state(session_id: str) -> SessionState:
    path = state_path(session_id)
    if not path.exists():
        return SessionState()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return SessionState()
    if not isinstance(data, dict):
        return SessionState()
    known_fields = set(SessionState.__dataclass_fields__)
    filtered = {k: v for k, v in data.items() if k in known_fields}
    try:
        return SessionState(**filtered)
    except TypeError:
        return SessionState()


def save_state(session_id: str, state: SessionState) -> None:
    path = state_path(session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(path.name + ".tmp")
    tmp_path.write_text(json.dumps(asdict(state)), encoding="utf-8")
    os.replace(tmp_path, path)
