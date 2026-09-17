"""crosier - the diagnostics and control surface.

Deliberately small. Crosier's job is to be invisible until it has something to
say, and the cost of that is a user who cannot tell it apart from a plugin that
failed to load. These two subcommands answer "is it on?" and "what did it do?";
`CROSIER_DISABLED=1` answers "leave me alone". That is all of it — this is a diagnostics surface, not a framework.

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
        print(f"enabled:   {'yes' if config.enabled else 'no (parked by default; set enabled = true in .crosier.toml)'}")
    print(f"model:     {config.verdict_model}")
    if session is None:
        print("session:   none yet - no check has run on this machine")
        return 0
    state = load_state(session)
    print(f"session:   {session}")
    print(f"checks:    {state.checks_run}/{config.max_checks_per_session} used")
    if state.disabled_for_session:
        print("           backed off for this session after repeated errors")
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
    _print_spend(entries)
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


def _total(entries: list, key: str):
    """Sum a spend field, ignoring checks that never reported one."""
    values = [e.get(key) for e in entries]
    return sum(v for v in values if isinstance(v, (int, float)))


def _print_spend(entries: list) -> None:
    """What watching this session cost it.

    A drift checker that hides its own bill is asking to be taken on faith.
    Older journal lines predate spend reporting and simply contribute nothing.
    """
    spent_in = _total(entries, "input_tokens")
    spent_out = _total(entries, "output_tokens")
    if not spent_in and not spent_out:
        return
    cost = _total(entries, "cost_usd")
    print(
        f"  reviewer cost: {spent_in:,} in + {spent_out:,} out tokens"
        f"  (${cost:.2f})"
    )


def main(argv: list | None = None) -> int:
    parser = argparse.ArgumentParser(prog="crosier", description=__doc__.splitlines()[0])
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("status", help="is it on, how much budget is left, what did it last say")
    subparsers.add_parser("report", help="what this session was checked for and what was flagged")
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])
    handlers = {"status": cmd_status, "report": cmd_report}
    handler = handlers.get(args.command)
    if handler is None:
        parser.print_help()
        return 0
    return handler(args)


if __name__ == "__main__":
    sys.exit(main())
