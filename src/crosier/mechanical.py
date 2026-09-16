"""A zero-token check for the most concrete kind of drift: the final answer of
a turn claims the work is done while the latest test run in that same turn
failed.

This runs before the paid reviewer and can block a turn with no model call.
Everything here is pure and defensive: a parser that cannot recognise a
runner's output returns None rather than guessing, and every public function
wraps its body in try/except so a transcript shape this module has never seen
degrades to "no opinion" instead of raising inside a hook.
"""

import re
from dataclasses import dataclass


@dataclass
class TestOutcome:
    runner: str
    passed: int | None
    failed: int | None
    errors: int | None
    ok: bool
    summary_line: str


# ---------------------------------------------------------------------------
# Per-runner summary-line parsers. Each takes the raw stdout/stderr text and
# returns a TestOutcome for the LAST recognisable summary line it finds, or
# None. Kept narrow on purpose: a line is only "recognised" when its shape is
# distinctive enough to a test runner that unrelated command output is very
# unlikely to match it by accident (a false block costs Crosier more than a
# missed one).

_COUNT_WORD_RE = re.compile(r"(\d+)\s+([a-zA-Z]+)")

# A pytest summary line, optionally wrapped in `=` padding, e.g.
# "===== 2 failed, 48 passed in 3.45s =====" or "48 passed in 1.02s".
_PYTEST_COUNTS_RE = re.compile(
    r"^(?:\d+\s+[a-zA-Z]+(?:,\s*)?)+(?:\s*in\s+[\d.]+s)?$", re.IGNORECASE
)
_PYTEST_NO_TESTS_RE = re.compile(r"^no tests ran(?:\s+in\s+[\d.]+s)?$", re.IGNORECASE)


def _parse_pytest(output: str) -> TestOutcome | None:
    for raw_line in reversed(output.splitlines()):
        core = raw_line.strip().strip("=").strip()
        if not core:
            continue
        no_tests = bool(_PYTEST_NO_TESTS_RE.match(core))
        if not no_tests and not _PYTEST_COUNTS_RE.match(core):
            continue
        failed = passed = errors = None
        for num, word in _COUNT_WORD_RE.findall(core):
            w = word.lower()
            n = int(num)
            if w.startswith("fail"):
                failed = (failed or 0) + n
            elif w.startswith("pass"):
                passed = (passed or 0) + n
            elif w.startswith("error"):
                errors = (errors or 0) + n
        if no_tests:
            ok = False
        elif failed or errors:
            ok = False
        elif passed is not None or errors is not None or failed is not None:
            ok = True
        else:
            continue
        return TestOutcome("pytest", passed, failed, errors, ok, core)
    return None


# unittest always prints "Ran N tests in X.XXXs" immediately before its final
# OK/FAILED line; requiring that line keeps a bare "OK" elsewhere in output
# from being mistaken for a test result.
_UNITTEST_RAN_RE = re.compile(r"^Ran \d+ tests? in [\d.]+s$", re.MULTILINE)
_UNITTEST_FAILED_RE = re.compile(r"^FAILED\s*\(([^)]*)\)\s*$", re.MULTILINE)
_UNITTEST_OK_RE = re.compile(r"^OK(?:\s*\([^)]*\))?\s*$", re.MULTILINE)


def _parse_unittest(output: str) -> TestOutcome | None:
    if not _UNITTEST_RAN_RE.search(output):
        return None
    failed_match = None
    for m in _UNITTEST_FAILED_RE.finditer(output):
        failed_match = m
    if failed_match:
        body = failed_match.group(1)
        failures = _extract_int(r"failures=(\d+)", body)
        errors = _extract_int(r"errors=(\d+)", body)
        return TestOutcome("unittest", None, failures, errors, False, failed_match.group(0).strip())
    ok_match = None
    for m in _UNITTEST_OK_RE.finditer(output):
        ok_match = m
    if ok_match:
        return TestOutcome("unittest", None, 0, 0, True, ok_match.group(0).strip())
    return None


def _extract_int(pattern: str, text: str) -> int | None:
    m = re.search(pattern, text)
    return int(m.group(1)) if m else None


# jest/vitest: "Tests:       2 failed, 48 passed, 50 total".
_JEST_LINE_RE = re.compile(r"^\s*Tests:\s+(.+)$", re.MULTILINE)


def _parse_jest(command: str, output: str) -> TestOutcome | None:
    match = None
    for m in _JEST_LINE_RE.finditer(output):
        match = m
    if not match:
        return None
    body = match.group(1)
    failed = passed = None
    for num, word in _COUNT_WORD_RE.findall(body):
        w = word.lower()
        n = int(num)
        if w.startswith("fail"):
            failed = (failed or 0) + n
        elif w.startswith("pass"):
            passed = (passed or 0) + n
    runner = "vitest" if "vitest" in (command or "").lower() else "jest"
    ok = not failed
    return TestOutcome(runner, passed, failed, None, ok, match.group(0).strip())


# cargo test: "test result: ok. 5 passed; 0 failed; ..." /
# "test result: FAILED. 3 passed; 2 failed; ...".
_CARGO_RE = re.compile(
    r"^test result: (ok|FAILED)\. (\d+) passed; (\d+) failed;.*$",
    re.MULTILINE | re.IGNORECASE,
)


def _parse_cargo(output: str) -> TestOutcome | None:
    match = None
    for m in _CARGO_RE.finditer(output):
        match = m
    if not match:
        return None
    status, passed, failed = match.group(1), int(match.group(2)), int(match.group(3))
    return TestOutcome("cargo", passed, failed, None, status.lower() == "ok", match.group(0).strip())


# go test: "ok  \texample.com/pkg\t0.003s" or a failing package's
# "FAIL\texample.com/pkg\t0.003s", each backed by "--- FAIL: TestName" lines.
_GO_OK_RE = re.compile(r"^ok\s+\S+\s+[\d.]+s(?:\s+\(cached\))?\s*$", re.MULTILINE)
_GO_FAIL_SUMMARY_RE = re.compile(r"^FAIL\s+\S+\s+[\d.]+s\s*$", re.MULTILINE)
_GO_FAIL_TEST_RE = re.compile(r"^--- FAIL: (\S+)", re.MULTILINE)


def _parse_go(output: str) -> TestOutcome | None:
    fail_tests = _GO_FAIL_TEST_RE.findall(output)
    fail_summary = None
    for m in _GO_FAIL_SUMMARY_RE.finditer(output):
        fail_summary = m
    if fail_tests or fail_summary:
        line = fail_summary.group(0).strip() if fail_summary else f"--- FAIL: {fail_tests[0]}"
        failed = len(fail_tests) if fail_tests else None
        return TestOutcome("go", None, failed, None, False, line)
    ok_match = None
    for m in _GO_OK_RE.finditer(output):
        ok_match = m
    if ok_match:
        return TestOutcome("go", None, None, None, True, ok_match.group(0).strip())
    return None


# npm: only ever a fallback when nothing runner-specific parsed, and never ok
# — "npm ERR!" / "Exit status N" carry no pass/fail counts of their own.
_NPM_ERR_RE = re.compile(r"npm ERR!.*")
_NPM_EXIT_RE = re.compile(r"Exit status \d+.*", re.IGNORECASE)


def _parse_npm_fallback(output: str) -> TestOutcome | None:
    match = _NPM_ERR_RE.search(output) or _NPM_EXIT_RE.search(output)
    if not match:
        return None
    return TestOutcome("npm", None, None, None, False, match.group(0).strip())


def parse_test_summary(command: str, output: str) -> TestOutcome | None:
    """The last recognisable test-runner summary in `output`, or None.

    Never guesses: a command/output pair whose shape doesn't match a known
    runner's summary line returns None rather than a low-confidence outcome.
    """
    try:
        cmd = command or ""
        text = output or ""
        for parser in (
            lambda: _parse_pytest(text),
            lambda: _parse_cargo(text),
            lambda: _parse_jest(cmd, text),
            lambda: _parse_go(text),
            lambda: _parse_unittest(text),
        ):
            result = parser()
            if result is not None:
                return result
        if "npm ERR!" in text or re.search(r"\bnpm\b|\byarn\b|\bpnpm\b", cmd, re.IGNORECASE):
            return _parse_npm_fallback(text)
        return None
    except Exception:
        return None


def _content_text(content) -> str:
    """A tool_result's `content` field: a plain string, or a list of typed
    blocks carrying `text`. Read-only local copy of the same shape
    digest._block_text reads, kept separate so this module has no import-time
    dependency on the excerpt pipeline."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"
        )
    return ""


def latest_test_outcome(turn_entries: list):
    """The LAST parsable test-runner outcome in this turn's Bash calls, paired
    with a locator (the command and the tool_use id it ran under). None when
    the turn ran no test command, or ran commands whose output nothing here
    can parse.

    A tool_result marked `is_error` still counts: a failing test run makes
    Bash exit non-zero, and that is exactly the run this check cares about.
    """
    try:
        commands_by_id: dict = {}
        for entry in turn_entries:
            if not isinstance(entry, dict):
                continue
            message = entry.get("message")
            if not isinstance(message, dict) or message.get("role") != "assistant":
                continue
            content = message.get("content")
            if not isinstance(content, list):
                continue
            for block in content:
                if not (isinstance(block, dict) and block.get("type") == "tool_use" and block.get("name") == "Bash"):
                    continue
                block_input = block.get("input")
                command = block_input.get("command") if isinstance(block_input, dict) else None
                if isinstance(command, str):
                    commands_by_id[block.get("id")] = command

        if not commands_by_id:
            return None

        latest = None
        for entry in turn_entries:
            if not isinstance(entry, dict):
                continue
            message = entry.get("message")
            if not isinstance(message, dict) or message.get("role") != "user":
                continue
            content = message.get("content")
            if not isinstance(content, list):
                continue
            for block in content:
                if not (isinstance(block, dict) and block.get("type") == "tool_result"):
                    continue
                tool_use_id = block.get("tool_use_id")
                command = commands_by_id.get(tool_use_id)
                if command is None:
                    continue
                output = _content_text(block.get("content", ""))
                outcome = parse_test_summary(command, output)
                if outcome is not None:
                    latest = (outcome, {"command": command, "tool_use_id": tool_use_id})
        return latest
    except Exception:
        return None


# Phrases that read as a claim the tests are verified, not just that the work
# is generically finished. Deliberately narrow: "done"/"complete"/"fixed"/
# "ready" alone say nothing about test results and must not block on their
# own — only a claim that names tests, the suite, or a pass count does.
_SUCCESS_RE = re.compile(
    r"\btests? pass(?:es|ed|ing)?\b"
    r"|\btests? (?:are|is) passing\b"
    r"|\bsuite (?:is )?(?:green|pass(?:es|ing)?)\b"
    r"|\b\d+\s+passed\b"
    r"|\bverified\b",
    re.IGNORECASE,
)

# "works"/"working" only counts as a success claim when it shares a sentence
# with a mention of tests or the suite — "the fix works" alone is not a test
# claim, "the tests work now" is.
_WORKS_RE = re.compile(r"\bworking\b|\bworks?\b", re.IGNORECASE)
_TEST_CONTEXT_RE = re.compile(r"\btests?\b|\bsuite\b", re.IGNORECASE)
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?\n])\s+")


def _has_contextual_works_claim(text: str) -> bool:
    for sentence in _SENTENCE_SPLIT_RE.split(text):
        if _WORKS_RE.search(sentence) and _TEST_CONTEXT_RE.search(sentence):
            return True
    return False


def _is_test_success_claim(text: str) -> bool:
    return bool(_SUCCESS_RE.search(text)) or _has_contextual_works_claim(text)


# Phrases that mean the answer is naming the failure rather than hiding it.
# Checked first: an honest mention beats a nearby success word in the same
# answer.
_HONEST_RE = re.compile(
    r"\bfailing\b"
    r"|\bfailed\b"
    r"|\bstill fails?\b"
    r"|\b\d+\s+failed\b"
    r"|\bnot yet\b"
    r"|\bexcept\b"
    r"|\bremaining\b"
    r"|\bbroken\b"
    r"|\bxfail\w*\b"
    r"|\bpre-existing\b"
    r"|\bunrelated\b",
    re.IGNORECASE,
)

_REASON_CHAR_CAP = 400

_EDIT_TOOLS = ("Edit", "MultiEdit", "Write", "NotebookEdit")
_FILE_TOKEN_RE = re.compile(r"[\w./\\-]+\.[A-Za-z0-9]{1,6}\b")


def _edited_paths(turn_entries: list) -> set:
    paths = set()
    for entry in turn_entries:
        if not isinstance(entry, dict):
            continue
        message = entry.get("message")
        if not isinstance(message, dict) or message.get("role") != "assistant":
            continue
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not (isinstance(block, dict) and block.get("type") == "tool_use" and block.get("name") in _EDIT_TOOLS):
                continue
            block_input = block.get("input")
            path = block_input.get("file_path") if isinstance(block_input, dict) else None
            if isinstance(path, str) and path:
                paths.add(path)
    return paths


def _basenames(paths) -> set:
    return {p.replace("\\", "/").rsplit("/", 1)[-1] for p in paths}


def _command_target_tokens(command: str) -> set:
    tokens = set()
    for tok in command.split()[1:]:
        if tok.startswith("-"):
            continue
        if "/" in tok or "\\" in tok or _FILE_TOKEN_RE.fullmatch(tok):
            tokens.add(tok)
    return tokens


def _targets_other_files(command: str, last_message: str, turn_entries: list) -> bool:
    """True only when it is positively decidable that the failing command
    targeted files the turn never touched, and the answer's own file mentions
    are confined to the files that were touched. Any ambiguity — no decodable
    command target, no edits this turn, or the answer naming something
    outside the edited set — returns False, so the normal check still runs.

    Known risk: a command with multiple targets (e.g. `pytest a/ b/`) where
    only one overlaps the edited files is treated as overlapping (no skip),
    even if the answer's claim was really only about the other target. See
    test_mechanical.py's multi-target case.
    """
    edited = _edited_paths(turn_entries)
    if not edited:
        return False
    targets = _command_target_tokens(command)
    if not targets:
        return False
    edited_bases = _basenames(edited)
    if _basenames(targets) & edited_bases:
        return False
    mentions = set(_FILE_TOKEN_RE.findall(last_message))
    if not mentions:
        return False
    if _basenames(mentions) - edited_bases:
        return False
    return True


def contradiction_reason(last_message: str, turn_entries: list) -> str | None:
    """Why this final answer contradicts this turn's own test run, or None to
    let it stand.

    Fires only when the latest test run in the turn failed AND the final
    answer claims success AND the answer does not already name the failure
    honestly. Reads as a challenge, not a mandate, so a genuinely correct
    answer (a known, unrelated failure called out in one line) survives it.
    """
    try:
        text = last_message or ""
        if not text.strip():
            return None
        found = latest_test_outcome(turn_entries)
        if not found:
            return None
        outcome, locator = found
        if outcome.ok:
            return None
        if _HONEST_RE.search(text):
            return None
        if not _is_test_success_claim(text):
            return None
        if _targets_other_files(locator.get("command", ""), text, turn_entries):
            return None
        reason = (
            f"The last test run in this turn reports `{outcome.summary_line}`, but the "
            "answer says the tests pass. Re-run or correct the answer; if the failure is "
            "known and unrelated, say so in one line."
        )
        return reason[:_REASON_CHAR_CAP]
    except Exception:
        return None
