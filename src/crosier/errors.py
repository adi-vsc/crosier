"""Failure logging and backoff. A hook must fail open — this module never
raises into the caller; it only records and decides whether to stop trying
for the rest of the session."""

from datetime import datetime, timezone

from crosier.paths import errors_log_path

MAX_CONSECUTIVE_FAILURES = 2
# A cooldown skips this many review opportunities (Stops where a review would
# otherwise have run) rather than disabling the gate for the rest of the
# session outright: two failures in a row are often a transient timeout (a
# slow reviewer call against the Stop hook's deadline), not a broken install.
# Counted in review opportunities, not turns, so a session where most Stops
# are not risky enough to review does not quietly serve out its whole
# cooldown on turns that were never going to be gated anyway.
COOLDOWN_REVIEWS = 5
# Hard stop only after failures keep recurring across cooldowns: three
# cooldown-triggering cycles (2 failures each) in one session.
MAX_TOTAL_FAILURES = 6


def record_failure(message: str) -> None:
    try:
        log_path = errors_log_path()
        log_path.parent.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(timezone.utc).isoformat()
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"{timestamp} {message}\n")
    except OSError:
        pass


def should_disable(consecutive_failures: int) -> bool:
    # Kept for callers still on the old permanent-disable behaviour. New
    # callers should read this as "start a cooldown" (should_cooldown) and
    # additionally check should_hard_stop before disabling permanently.
    return consecutive_failures >= MAX_CONSECUTIVE_FAILURES


def should_cooldown(consecutive_failures: int) -> bool:
    return consecutive_failures >= MAX_CONSECUTIVE_FAILURES


def should_hard_stop(total_failures: int) -> bool:
    return total_failures >= MAX_TOTAL_FAILURES
