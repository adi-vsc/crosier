# tests/test_digest.py
import subprocess
from unittest.mock import MagicMock, patch

from crosier.digest import build_digest_prompt, generate_digest


def test_build_digest_prompt_references_stdin():
    prompt = build_digest_prompt()
    assert "stdin" in prompt
    assert "Current task" in prompt
    assert "Assumptions currently being built on" in prompt


@patch("crosier.digest.subprocess.run")
def test_generate_digest_returns_stdout_on_success(mock_run):
    mock_run.return_value = MagicMock(returncode=0, stdout="## Current task\nfoo\n")
    result = generate_digest("excerpt text", model="sonnet")
    assert result == "## Current task\nfoo"
    args = mock_run.call_args.args[0]
    kwargs = mock_run.call_args.kwargs
    assert args[0] == "claude"
    assert "-p" in args
    assert "sonnet" in args
    assert kwargs["input"] == "excerpt text"
    assert "excerpt text" not in args


@patch("crosier.digest.subprocess.run")
def test_generate_digest_passes_large_excerpt_via_stdin_not_argv(mock_run):
    # Regression test: a 40k-char excerpt embedded in argv can exceed Windows'
    # ~32,767-char CreateProcess command-line limit. It must go via stdin.
    mock_run.return_value = MagicMock(returncode=0, stdout="ok")
    long_excerpt = "x" * 40_000
    generate_digest(long_excerpt, model="sonnet")
    args = mock_run.call_args.args[0]
    kwargs = mock_run.call_args.kwargs
    assert kwargs["input"] == long_excerpt
    assert sum(len(a) for a in args) < 32_000


@patch("crosier.digest.subprocess.run")
def test_generate_digest_returns_none_on_nonzero_exit(mock_run):
    mock_run.return_value = MagicMock(returncode=1, stdout="")
    assert generate_digest("excerpt", model="sonnet") is None


@patch("crosier.digest.subprocess.run")
def test_generate_digest_returns_none_on_empty_output(mock_run):
    mock_run.return_value = MagicMock(returncode=0, stdout="   ")
    assert generate_digest("excerpt", model="sonnet") is None


@patch("crosier.digest.subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="claude", timeout=45))
def test_generate_digest_returns_none_on_timeout(mock_run):
    assert generate_digest("excerpt", model="sonnet") is None


@patch("crosier.digest.subprocess.run", side_effect=FileNotFoundError())
def test_generate_digest_returns_none_when_cli_missing(mock_run):
    assert generate_digest("excerpt", model="sonnet") is None
