"""Where Crosier keeps its own files.

Under the user's Claude directory, never inside the project: an install-and-
forget plugin that drops an untracked `.crosier/` into every repository it runs
in gets uninstalled. Session ids are UUIDs, so nothing needs the project to
scope them. `CROSIER_HOME` overrides the location (tests, odd setups); the
hook passes it through to the worker so both sides agree.
"""

import os
from pathlib import Path

HOME_ENV = "CROSIER_HOME"


def crosier_home() -> Path:
    override = os.environ.get(HOME_ENV)
    if override:
        return Path(override)
    return Path.home() / ".claude" / "crosier"


def safe_id(session_id: str) -> str:
    return "".join(c for c in session_id if c.isalnum() or c in "-_") or "unknown"


def state_path(session_id: str) -> Path:
    return crosier_home() / "state" / f"{safe_id(session_id)}.json"


def result_path(session_id: str) -> Path:
    return crosier_home() / "results" / f"{safe_id(session_id)}.json"


def worker_marker_path(session_id: str) -> Path:
    return crosier_home() / "workers" / f"{safe_id(session_id)}.pid"


def errors_log_path() -> Path:
    return crosier_home() / "errors.log"
