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
    # User turns, counted at the first Stop of each turn.
    total_turns: int = 0
    checks_run: int = 0
    consecutive_failures: int = 0
    # The last concern shown to the agent. Carried into the next review so a
    # concern the agent already answered is not raised again as if new.
    last_flag: str | None = None
    # A hard stop: the gate is off for the rest of the session. Set only after
    # MAX_TOTAL_FAILURES (see errors.py) -- a transient run of failures gets a
    # cooldown instead (cooldown_turns_left), not this.
    disabled_for_session: bool = False
    # A user who installs a plugin and then sees nothing for many turns has
    # no way to tell it from a plugin that did not install. One line, once.
    announced_activation: bool = False
    # Failures counted across the whole session, never reset by a success.
    # Compared against errors.MAX_TOTAL_FAILURES to decide a hard stop.
    total_failures: int = 0
    # Review opportunities left to skip -- decremented only on a Stop that
    # would otherwise have triggered a review (a risky answer, or an
    # unreadable transcript), not on every turn. Set to
    # errors.COOLDOWN_REVIEWS when consecutive_failures crosses
    # errors.MAX_CONSECUTIVE_FAILURES.
    cooldown_remaining_reviews: int = 0
    # The cooldown systemMessage is shown once per session, the first time it
    # backs off, so a session that cycles through several cooldowns is not
    # narrated every time. A hard stop still gets its own message.
    announced_backoff: bool = False


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
