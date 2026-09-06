# tests/test_claude_cli.py
import json
import os
import subprocess
from unittest.mock import MagicMock, patch

from crosier import claude_cli
from crosier.claude_cli import kill_active, run_claude


def _envelope(result="ok", is_error=False, structured=None):
    data = {"type": "result", "is_error": is_error, "result": result}
    if structured is not None:
        data["structured_output"] = structured
    return json.dumps(data)


def _fake_proc(stdout=None, returncode=0, alive=False):
    proc = MagicMock()
    proc.communicate.return_value = (_envelope() if stdout is None else stdout, "")
    proc.returncode = returncode
    proc.poll.return_value = None if alive else returncode
    proc.pid = 4242
    return proc


def _argv(mock_popen):
    return mock_popen.call_args.args[0]


def _flag_value(argv, flag):
    return argv[argv.index(flag) + 1]


@patch("crosier.claude_cli.subprocess.Popen")
def test_run_claude_returns_the_result_text(mock_popen):
    mock_popen.return_value = _fake_proc(stdout=_envelope(result="## Current task\nfoo\n"))
    out = run_claude("SYSTEM", "payload", "sonnet", 45)
    assert out["result"] == "## Current task\nfoo"
    argv = _argv(mock_popen)
    assert argv[0] == "claude"
    assert "-p" in argv
    assert _flag_value(argv, "--model") == "sonnet"


@patch("crosier.claude_cli.subprocess.Popen")
def test_run_claude_isolates_the_reviewer_from_the_users_setup(mock_popen):
    # Measured on a stock install: a "2+2" prompt in a project directory cost
    # 30,906 input tokens and 10.9s, because `claude -p` loaded the user's
    # CLAUDE.md, SessionStart hooks, plugins and MCP tool schemas. That is
    # neither zero-context nor cheap. With these flags the same call cost 455
    # tokens and 3.0s.
    mock_popen.return_value = _fake_proc()
    run_claude("SYSTEM", "payload", "sonnet", 45)
    argv = _argv(mock_popen)
    assert _flag_value(argv, "--system-prompt") == "SYSTEM"
    assert _flag_value(argv, "--setting-sources") == ""
    assert "--strict-mcp-config" in argv
    assert _flag_value(argv, "--tools") == ""
    assert "--no-session-persistence" in argv
    assert _flag_value(argv, "--output-format") == "json"


@patch("crosier.claude_cli.subprocess.Popen")
def test_run_claude_runs_outside_the_project_directory(mock_popen):
    # CLAUDE.md discovery and .claude/settings.local.json hooks are keyed on
    # cwd; the reviewer must not inherit the project's.
    mock_popen.return_value = _fake_proc()
    run_claude("SYSTEM", "payload", "sonnet", 45)
    cwd = mock_popen.call_args.kwargs["cwd"]
    assert cwd is not None
    assert os.path.isdir(cwd)
    assert os.path.abspath(cwd) != os.path.abspath(os.getcwd())


@patch("crosier.claude_cli.subprocess.Popen")
def test_run_claude_caps_the_spend_of_one_call(mock_popen):
    mock_popen.return_value = _fake_proc()
    run_claude("SYSTEM", "payload", "sonnet", 45)
    assert float(_flag_value(_argv(mock_popen), "--max-budget-usd")) > 0


@patch("crosier.claude_cli.subprocess.Popen")
def test_run_claude_requests_structured_output_when_given_a_schema(mock_popen):
    schema = {"type": "object", "properties": {"status": {"type": "string"}}}
    mock_popen.return_value = _fake_proc(
        stdout=_envelope(result='{"status":"proceed"}', structured={"status": "proceed"})
    )
    out = run_claude("SYSTEM", "payload", "sonnet", 45, json_schema=schema)
    assert json.loads(_flag_value(_argv(mock_popen), "--json-schema")) == schema
    assert out["structured_output"] == {"status": "proceed"}


@patch("crosier.claude_cli.subprocess.Popen")
def test_run_claude_passes_large_payload_via_stdin_not_argv(mock_popen):
    # A 40k-char excerpt embedded in argv can exceed Windows' ~32,767-char
    # CreateProcess command-line limit. It must go via stdin.
    proc = _fake_proc()
    mock_popen.return_value = proc
    payload = "x" * 40_000
    run_claude("SYSTEM", payload, "sonnet", 45)
    assert sum(len(a) for a in _argv(mock_popen)) < 32_000
    proc.communicate.assert_called_once_with(payload, timeout=45)


@patch("crosier.claude_cli.subprocess.Popen")
def test_run_claude_encodes_stdin_as_utf8(mock_popen):
    # text=True alone encodes stdin with the locale codec. On a stock Windows
    # console that is cp1252, which cannot encode an em dash, an arrow or a
    # curly quote — the payload is silently truncated at the first one.
    mock_popen.return_value = _fake_proc()
    run_claude("SYSTEM", "decision — then a step →  and a “quote”", "sonnet", 45)
    assert mock_popen.call_args.kwargs["encoding"] == "utf-8"


@patch("crosier.claude_cli.subprocess.Popen")
def test_run_claude_returns_none_on_nonzero_exit(mock_popen):
    mock_popen.return_value = _fake_proc(stdout="", returncode=1)
    assert run_claude("SYSTEM", "payload", "sonnet", 45) is None


@patch("crosier.claude_cli.subprocess.Popen")
def test_run_claude_returns_none_when_the_cli_reports_an_error(mock_popen):
    # "Not logged in · Please run /login" arrives as a result string. In text
    # mode it would have been digested as if it were the session.
    mock_popen.return_value = _fake_proc(
        stdout=_envelope(result="Not logged in · Please run /login", is_error=True)
    )
    assert run_claude("SYSTEM", "payload", "sonnet", 45) is None


@patch("crosier.claude_cli.subprocess.Popen")
def test_run_claude_returns_none_on_unparseable_envelope(mock_popen):
    mock_popen.return_value = _fake_proc(stdout="   ")
    assert run_claude("SYSTEM", "payload", "sonnet", 45) is None
    mock_popen.return_value = _fake_proc(stdout="not json")
    assert run_claude("SYSTEM", "payload", "sonnet", 45) is None


@patch("crosier.claude_cli._kill_tree")
@patch("crosier.claude_cli.subprocess.Popen")
def test_run_claude_kills_the_tree_on_timeout(mock_popen, mock_kill):
    # A timed-out `claude` must not be left running: it is a launcher, so the
    # node process it started outlives a plain terminate.
    proc = _fake_proc(alive=True)
    proc.communicate.side_effect = subprocess.TimeoutExpired(cmd="claude", timeout=45)
    mock_popen.return_value = proc
    assert run_claude("SYSTEM", "payload", "sonnet", 45) is None
    mock_kill.assert_called_once_with(proc)


@patch("crosier.claude_cli.subprocess.Popen", side_effect=FileNotFoundError())
def test_run_claude_returns_none_when_cli_missing(mock_popen):
    assert run_claude("SYSTEM", "payload", "sonnet", 45) is None


@patch("crosier.claude_cli.subprocess.Popen")
def test_run_claude_clears_the_active_handle_after_success(mock_popen):
    mock_popen.return_value = _fake_proc()
    run_claude("SYSTEM", "payload", "sonnet", 45)
    assert claude_cli._active is None


@patch("crosier.claude_cli._kill_tree")
def test_kill_active_is_a_noop_when_nothing_is_running(mock_kill):
    claude_cli._active = None
    kill_active()
    mock_kill.assert_not_called()


@patch("crosier.claude_cli._kill_tree")
def test_kill_active_kills_the_in_flight_call(mock_kill):
    proc = _fake_proc(alive=True)
    claude_cli._active = proc
    try:
        kill_active()
    finally:
        claude_cli._active = None
    mock_kill.assert_called_once_with(proc)
