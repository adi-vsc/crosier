"""A check the user asked for, instead of one a threshold asked for.

`crosier check` runs outside the session and cannot learn its id, so the
request is a single unscoped file that the next hook invocation in any session
claims and deletes. Claiming is a delete-then-act, so two sessions racing for
one trigger produce one check, not two.

A forced check still goes through `dispatch`, so the session budget and the
one-worker-in-flight rule hold: this skips the threshold, not the guards.
"""

import time

from crosier.paths import trigger_path

__all__ = ["consume_trigger", "request_check"]

# A trigger the session never picked up (the user asked, then walked away and
# closed the terminal) must not fire days later into an unrelated session.
TRIGGER_TTL_SECONDS = 900


def request_check() -> bool:
    path = trigger_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(time.time()), encoding="utf-8")
    except OSError:
        return False
    return True


def consume_trigger(ttl: int = TRIGGER_TTL_SECONDS) -> bool:
    """Claim a pending request. True means this invocation owns the check."""
    path = trigger_path()
    try:
        age = time.time() - path.stat().st_mtime
    except OSError:
        return False
    try:
        path.unlink()
    except OSError:
        # Someone else claimed it between the stat and the unlink.
        return False
    return age <= ttl
