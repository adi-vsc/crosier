#!/usr/bin/env python3
"""Detached background worker: runs one reviewer call and writes the result
where the next hook invocation will find it.

Nothing supervises this process. The session that spawned it may be force-quit
a second later, and no parent will ever reap it — so it supervises itself. A
watchdog thread kills the headless call it started and then the worker, at a
hard deadline, whatever state it is in. That bound is the reason a background
check cannot leave a resident `claude` behind.

The job payload carries the excerpt itself rather than a transcript path: the
hook has already read and sliced the file, and by the time this process runs
that file may have been rewritten by a compaction.
"""

import json
import os
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from crosier.claude_cli import kill_active  # noqa: E402
from crosier.pending import write_result  # noqa: E402
from crosier.verdict import generate_verdict  # noqa: E402


def _self_destruct() -> None:
    kill_active()
    # No cleanup, no atexit, no flush: the point is that this cannot be
    # blocked by whatever the main thread is stuck on.
    os._exit(1)


def arm_watchdog(deadline: int) -> threading.Timer:
    timer = threading.Timer(deadline, _self_destruct)
    timer.daemon = True
    timer.start()
    return timer


def run_job(job: dict) -> dict:
    excerpt = job.get("excerpt", "")
    verdict = (
        generate_verdict(
            excerpt,
            model=job.get("verdict_model", "sonnet"),
            timeout=job.get("call_timeout", 45),
            previous_flag=job.get("previous_flag"),
        )
        if excerpt.strip()
        else None
    )
    result = {
        "ok": verdict is not None,
        "verdict": verdict,
        "turn_number": job.get("turn_number", 0),
        "transcript_index": job.get("transcript_index", 0),
        "context_tokens": job.get("context_tokens"),
        "created_at": time.time(),
    }
    if verdict is None:
        result["error"] = (
            "empty excerpt" if not excerpt.strip() else "verdict failed or returned unparseable output"
        )
    return result


def main() -> int:
    try:
        job = json.load(sys.stdin)
    except (json.JSONDecodeError, OSError):
        return 0
    if not isinstance(job, dict):
        return 0

    marker = job.get("marker_path")
    arm_watchdog(int(job.get("worker_deadline", 120)))
    try:
        result = run_job(job)
        write_result(job.get("session_id", "unknown"), result)
    except (OSError, KeyError, ValueError):
        return 1
    finally:
        if marker:
            try:
                Path(marker).unlink()
            except OSError:
                pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
