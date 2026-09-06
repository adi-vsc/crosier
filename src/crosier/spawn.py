"""Hand a check to a detached worker and return immediately.

A hook runs in front of the user: Claude Code waits for it before the turn
starts. A headless `claude -p` call inline is ten to sixty seconds of frozen
terminal, which trains the user to Ctrl+C the pipeline. So nothing is awaited
here. The hook writes a job, walks away, and reads the answer on a later hook
invocation — by which point the check has cost the user no wall-clock time.
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

from crosier.paths import HOME_ENV, crosier_home, worker_marker_path

WORKER_SCRIPT = Path(__file__).resolve().parent / "worker.py"


def marker_path(session_id: str) -> Path:
    return worker_marker_path(session_id)


def worker_is_active(session_id: str, deadline: int) -> bool:
    """Is a check for this session already in flight?

    One in flight at a time, always. Without this a stuck session spawns a
    fresh reviewer every turn and pays for a queue of near-identical verdicts
    about the same loop.

    The marker is trusted only for as long as the worker's own deadline: the
    worker kills itself at that point, so a marker older than that describes a
    process that is already gone.
    """
    path = marker_path(session_id)
    try:
        age = time.time() - path.stat().st_mtime
    except OSError:
        return False
    if age > deadline:
        try:
            path.unlink()
        except OSError:
            pass
        return False
    return True


def _detach_kwargs() -> dict:
    if os.name == "nt":
        return {"creationflags": subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW}
    return {"start_new_session": True}


def spawn_worker(session_id: str, job: dict) -> bool:
    """Start the worker and hand it the job on stdin. Never waits for output.

    The busy marker is written only once the job has been delivered: a marker
    left behind by a failed hand-off would block every check for the session
    until the deadline passed.
    """
    path = marker_path(session_id)
    env = dict(os.environ)
    env[HOME_ENV] = str(crosier_home())
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        proc = subprocess.Popen(
            [sys.executable, str(WORKER_SCRIPT)],
            stdin=subprocess.PIPE,
            # The hook's own stdout is what Claude Code injects as context —
            # a stray line from a background process must never reach it.
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            env=env,
            **_detach_kwargs(),
        )
    except (OSError, ValueError):
        return False
    try:
        proc.stdin.write(json.dumps(job).encode("utf-8"))
        proc.stdin.close()
        path.write_text(str(proc.pid), encoding="utf-8")
    except (OSError, ValueError):
        return False
    return True
