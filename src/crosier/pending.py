"""The result file a detached worker leaves behind for the next hook run.

This is the entire interface between the two processes. The worker never
touches SessionState — the hook stays the only writer — so a worker that dies
mid-flight can corrupt nothing, and a worker whose answer arrived too late can
simply be dropped on read.
"""

import json
import os

from crosier.paths import result_path

__all__ = ["clear_result", "is_stale", "read_result", "result_path", "write_result"]


def write_result(session_id: str, result: dict) -> None:
    path = result_path(session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(path.name + ".tmp")
    tmp_path.write_text(json.dumps(result), encoding="utf-8")
    # The hook may read this at any moment; a rename is the only way it sees
    # either the whole file or none of it.
    os.replace(tmp_path, path)


def read_result(session_id: str) -> dict | None:
    path = result_path(session_id)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    return data if isinstance(data, dict) else None


def clear_result(session_id: str) -> None:
    try:
        result_path(session_id).unlink()
    except OSError:
        pass


def is_stale(result: dict, current_line_count: int, line_limit: int) -> bool:
    """Has the session moved on past what this verdict was about?

    The worker read a slice of a session that kept running while it thought.
    By the time the answer lands the agent may have finished that file, taken
    the user's correction, or fixed the bug being critiqued. Announcing then
    is worse than staying quiet: it describes a session that no longer exists.

    Movement is measured in transcript lines, and only in lines. Wall-clock
    age is not movement: a session the user walked away from for an hour is
    exactly the session the verdict describes, and discarding on age alone
    threw away real verdicts on every idle turn.
    """
    index = result.get("transcript_index")
    return isinstance(index, int) and current_line_count - index > line_limit
