# tests/test_digest.py
import subprocess
from unittest.mock import MagicMock, patch

from crosier.digest import build_digest_prompt, generate_digest


def test_build_digest_prompt_includes_excerpt():
    prompt = build_digest_prompt("some session excerpt")
    assert "some session excerpt" in prompt
    assert "Current task" in prompt
    assert "Assumptions currently being built on" in prompt


@patch("crosier.digest.subprocess.run")
def test_generate_digest_returns_stdout_on_success(mock_run):
    mock_run.return_value = MagicMock(returncode=0, stdout="## Current task\nfoo\n")
    result = generate_digest("excerpt text", model="sonnet")
    assert result == "## Current task\nfoo"
    args = mock_run.call_args.args[0]
    assert args[0] == "claude"
    assert "-p" in args
    assert "sonnet" in args


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
