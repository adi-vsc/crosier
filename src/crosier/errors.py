"""Failure logging and backoff. A hook must fail open — this module never
raises into the caller; it only records and decides whether to stop trying
for the rest of the session."""

from datetime import datetime, timezone
from pathlib import Path

MAX_CONSECUTIVE_FAILURES = 2


def record_failure(project_root: Path, message: str) -> None:
    log_path = Path(project_root) / ".crosier" / "errors.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).isoformat()
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(f"{timestamp} {message}\n")


def should_disable(consecutive_failures: int) -> bool:
    return consecutive_failures >= MAX_CONSECUTIVE_FAILURES
