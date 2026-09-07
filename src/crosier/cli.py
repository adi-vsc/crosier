"""crosier - the diagnostics and control surface.

Deliberately small. Crosier's job is to be invisible until it has something to
say, and the cost of that is a user who cannot tell it apart from a plugin that
failed to load. These three subcommands answer "is it on?", "what did it do?"
and "check now"; `CROSIER_DISABLED=1` answers "leave me alone". That is all of
it — this is a diagnostics surface, not a framework.

Everything here reads Crosier's own files under `CROSIER_HOME`. Nothing reads
the transcript, the project, or the repository — the isolation the reviewer
runs under is not weakened by a reporting tool that could see more than it can.
"""

import argparse
import sys
from pathlib import Path

from crosier.config import DISABLE_ENV, disabled_by_env, load_config
from crosier.journal import read_journal
from crosier.paths import crosier_home, errors_log_path
from crosier.state import load_state
from crosier.trigger import request_check

__all__ = ["main"]


def _latest_session() -> str | None:
    """The session whose state file was touched last.

    The CLI runs outside any session and is never told an id, so "the session
    you are in" is inferred from what wrote most recently. With one interactive
    session — the normal case — that is exactly right; with several, the report
    names the id it chose so a wrong guess is visible rather than silent.
    """
    directory = crosier_home() / "state"
    try:
        candidates = [p for p in directory.iterdir() if p.suffix == ".json"]
    except OSError:
        return None
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime).stem


def _describe(entry: dict) -> str:
    if entry.get("status") == "stale":
        return "verdict discarded - the session moved on before it came back"
    if entry.get("status") != "flag":
        return "no issues found"
    parts = [entry.get("category") or "other"]
    claim = entry.get("flagged_claim")
    if claim:
        parts.append(claim)
    tail = ": ".join(parts)
    if not entry.get("delivered_to_agent"):
        tail += "  [not shown to the agent: below the confidence gate]"
    return tail


def cmd_status(_args) -> int:
    config = load_config(Path.cwd())
    session = _latest_session()
    print(f"home:      {crosier_home()}")
    print(f"log:       {errors_log_path()}")
    if disabled_by_env():
        print(f"enabled:   no ({DISABLE_ENV} is set for this shell)")
    else:
        print(f"enabled:   {'yes' if config.enabled else 'no (.crosier.toml)'}")
    print(f"model:     {config.verdict_model}")
    if session is None:
        print("session:   none yet - no check has run on this machine")
        return 0
    state = load_state(session)
    print(f"session:   {session}")
    print(f"checks:    {state.checks_run}/{config.max_checks_per_session} used")
    if state.disabled_for_session:
        print("           backed off for this session after repeated errors")
    if state.checks_run == 0:
        remaining = max(0, config.first_check_call_threshold - state.calls_since_check)
    else:
        remaining = max(0, config.call_threshold - state.calls_since_check)
    print(f"next:      about {remaining} more model calls")
    entries = read_journal(session)
    print(f"last:      {_describe(entries[-1]) if entries else 'nothing yet'}")
    return 0


def cmd_report(_args) -> int:
    session = _latest_session()
    if session is None:
        print("No Crosier session found under " + str(crosier_home()))
        return 0
    config = load_config(Path.cwd())
    state = load_state(session)
    entries = read_journal(session)
    print(f"Crosier session report - {session}")
    print(f"  checks used:  {state.checks_run}/{config.max_checks_per_session}")
    print(f"  turns seen:   {state.total_turns}")
    if not entries:
        print("  no completed checks yet")
        return 0
    flagged = [e for e in entries if e.get("status") == "flag"]
    shown = [e for e in flagged if e.get("delivered_to_agent")]
    print(f"  verdicts in:  {len(entries)}  ({len(flagged)} flagged, {len(shown)} shown to the agent)")
    print()
    for entry in entries:
        context = entry.get("context_tokens")
        size = f"{context:,} ctx tokens" if isinstance(context, int) else "context size unknown"
        print(f"  turn {entry.get('turn', '?')}: {_describe(entry)}")
        detail = f"confidence={entry.get('confidence')}"
        if entry.get("status") == "flag":
            detail += f"  evidence_verified={entry.get('evidence_verified')}"
        print(f"    {detail}  {size}")
    return 0


def cmd_check(_args) -> int:
    if not request_check():
        print("Could not write the trigger file under " + str(crosier_home()), file=sys.stderr)
        return 1
    print("Direction check requested - it runs on the session's next hook event.")
    return 0


def main(argv: list | None = None) -> int:
    parser = argparse.ArgumentParser(prog="crosier", description=__doc__.splitlines()[0])
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("status", help="is it on, how much budget is left, what did it last say")
    subparsers.add_parser("report", help="what this session was checked for and what was flagged")
    subparsers.add_parser("check", help="request a direction check now instead of at a threshold")
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])
    handlers = {"status": cmd_status, "report": cmd_report, "check": cmd_check}
    handler = handlers.get(args.command)
    if handler is None:
        parser.print_help()
        return 0
    return handler(args)


if __name__ == "__main__":
    sys.exit(main())
