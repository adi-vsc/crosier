"""The single place Crosier shells out to `claude -p`.

Every headless call goes through here, so a call that hits its own timeout
kills the whole CLI process tree it started; otherwise the headless `claude`
(and its node children) is left resident.
"""

import json
import os
import signal
import subprocess
import tempfile
from pathlib import Path

# One bad call must not be able to run up a bill on its own.
MAX_BUDGET_USD = 0.50

def _spawn_kwargs() -> dict:
    """Put the child in its own process group so the whole tree can be killed.

    `claude` is a launcher: killing the process we spawned does not reliably
    take its children with it, so we need a group/tree handle, not a pid.
    """
    if os.name == "nt":
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


def _kill_tree(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                capture_output=True,
                timeout=10,
            )
        else:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (OSError, subprocess.SubprocessError):
        try:
            proc.kill()
        except OSError:
            pass


def _neutral_cwd() -> str:
    """A directory with no CLAUDE.md, no .claude/, no project settings.

    `claude` discovers CLAUDE.md files and `.claude/settings.local.json` hooks
    from its working directory. The reviewer must not inherit the project's:
    measured in a project dir, a "2+2" prompt cost 30,906 input tokens.
    """
    path = Path(tempfile.gettempdir()) / "crosier-reviewer"
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError:
        return tempfile.gettempdir()
    return str(path)


def run_claude(
    system_prompt: str,
    stdin_text: str,
    model: str,
    timeout: int,
    json_schema: dict | None = None,
    max_budget_usd: float = MAX_BUDGET_USD,
    effort: str | None = None,
) -> dict | None:
    """Run one isolated headless `claude -p` call.

    Returns the parsed result envelope (`result`, `structured_output` when a
    schema was given, and what the call spent), or None on any failure -
    callers treat None as "no result", never as an exception. Instructions
    travel in the system prompt, data on stdin; the user turn is a one-line
    pointer to the data.

    `effort` is the reviewer's thinking budget. Left None the CLI picks, and
    measured on a cap-sized excerpt that default spent 8,322 output tokens over
    93.0s, which is past the worker deadline that is supposed to bound it. The
    level belongs to the caller.
    """
    argv = [
        "claude",
        "-p",
        "The material to review is on stdin. Follow the system prompt exactly.",
        "--model",
        model,
        "--system-prompt",
        system_prompt,
        "--output-format",
        "json",
        # Nothing of the user's setup: no CLAUDE.md, no hooks, no plugins, no
        # MCP servers, no tools, no saved session. This is what "zero-context"
        # actually requires, and it is also what makes the call cheap.
        "--setting-sources",
        "",
        "--strict-mcp-config",
        "--tools",
        "",
        "--no-session-persistence",
        "--max-budget-usd",
        str(max_budget_usd),
    ]
    if effort is not None:
        argv += ["--effort", effort]
    if json_schema is not None:
        argv += ["--json-schema", json.dumps(json_schema)]
    try:
        proc = subprocess.Popen(
            argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            # text=True alone encodes stdin with the locale codec (cp1252 on a
            # stock Windows install), which silently truncates the payload at
            # the first em dash, arrow or curly quote.
            encoding="utf-8",
            errors="replace",
            cwd=_neutral_cwd(),
            **_spawn_kwargs(),
        )
    except (FileNotFoundError, OSError):
        return None

    try:
        stdout, _ = proc.communicate(stdin_text, timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_tree(proc)
        try:
            proc.communicate(timeout=5)
        except (subprocess.SubprocessError, OSError):
            pass
        return None
    except OSError:
        _kill_tree(proc)
        return None

    if proc.returncode != 0:
        return None
    try:
        envelope = json.loads((stdout or "").strip() or "null")
    except json.JSONDecodeError:
        return None
    if not isinstance(envelope, dict) or envelope.get("is_error"):
        return None
    result = envelope.get("result")
    if not isinstance(result, str):
        return None
    structured = envelope.get("structured_output")
    return {
        "result": result.strip(),
        "structured_output": structured if isinstance(structured, dict) else None,
        **_spend(envelope),
    }


def _spend(envelope: dict) -> dict:
    """What this call cost, for the journal and for budget tuning.

    Input is reported as one number because the three buckets are billed at
    different rates but all of them are context the reviewer had to be given:
    cached reads are the stable rubric, cache writes are the excerpt, which
    differs every check and is therefore never read back.
    """
    usage = envelope.get("usage")
    usage = usage if isinstance(usage, dict) else {}

    def count(key: str) -> int:
        value = usage.get(key)
        return value if isinstance(value, int) else 0

    cost = envelope.get("total_cost_usd")
    return {
        "input_tokens": (
            count("input_tokens")
            + count("cache_creation_input_tokens")
            + count("cache_read_input_tokens")
        ),
        "cached_input_tokens": count("cache_read_input_tokens"),
        "output_tokens": count("output_tokens"),
        "cost_usd": float(cost) if isinstance(cost, (int, float)) else 0.0,
    }
