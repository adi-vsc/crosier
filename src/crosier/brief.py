"""A message-anchored alternative to `digest.build_excerpt`.

`build_excerpt` gives the reviewer the whole unreviewed window (~20k tokens,
89.5% tool traffic — see CLAUDE.md "What a check costs"). Most of that window
has nothing to do with the answer the user is about to read. `build_brief`
gives the reviewer only what the final answer depends on: the goal, the
latest user instruction, the final answer itself, and the tool evidence that
backs it up — mechanically selected, not authored by the agent under review.

Selecting evidence purely by what the final answer *references* would recreate
the exact failure this module exists to avoid: an answer that omits its own
failing test never references the tool result that would contradict it, so a
reference-only brief inherits the omission. Evidence is therefore built from
three sources, in priority order, independent of what the answer chooses to
mention:

1. tool results in the window that signal failure (error, traceback, nonzero
   exit, a positive failed/error count) — the safety net;
2. the window's last test-like run, whatever it says — verification outcomes
   matter even when nothing refers to them;
3. calls the final answer actually references — paths, basenames, backtick
   identifiers (with snake/kebab/camel variants), test names, counts like
   "26/26".

Paraphrases ("the auth test" for `test_auth_flow`) are not chased: the surface
match either finds a locator or it doesn't, and `brief_stats` reports the
counts so that gap stays visible rather than silently assumed away.

Record header style and body escaping are digest.py's: a body that opens a
line with `[label]` cannot forge a second record, because `_render` runs the
same substitution here.
"""

import json
import re
from collections import Counter

from crosier.digest import _cap, _compact_args, _is_noise, _block_text, _render, _user_prompts

GOAL_CHAR_CAP = 600
INSTRUCTION_CHAR_CAP = 1200
ANSWER_CHAR_CAP = 3000
ANSWER_HEAD = 1800
ANSWER_TAIL = 1200

# Tool evidence pulled in because the answer references it.
EVIDENCE_RESULT_HEAD = 300
EVIDENCE_RESULT_TAIL = 400

# Tool evidence pulled in because it signals failure, regardless of whether
# the answer mentions it at all. Smaller head: a traceback's own header line
# ("Traceback (most recent call last):") is what identifies it, the payload
# that matters is the tail.
FAILURE_RESULT_HEAD = 200
FAILURE_RESULT_TAIL = 400
FAILURE_SIGNAL_CAP = 6

_BACKTICK_RE = re.compile(r"`([^`\n]{1,200})`")
_PATH_RE = re.compile(r"\b[\w][\w./\\-]{1,120}\.[A-Za-z]{1,6}\b")
_NUMBER_UNIT_RE = re.compile(
    r"\b\d+/\d+\b|\b\d+\s+(?:failed|passed|error|errors|warning|warnings|skipped)\b",
    re.IGNORECASE,
)
_TEST_NAME_RE = re.compile(r"\btest_[A-Za-z0-9_]+\b")

_TEST_LIKE_RE = re.compile(
    r"\b(pytest|py\.test|npm\s+(?:run\s+)?test|yarn\s+test|go\s+test|jest|mocha"
    r"|unittest|ctest|cargo\s+test|mvn\s+test|rspec|tox)\b",
    re.IGNORECASE,
)

# digest._FORGED_MARKER_RE only knows digest's own label vocabulary (goal,
# user, assistant, tool_use/tool_result, ...). Brief introduces three labels
# digest has never heard of, so a tool result or final answer that opens a
# line with one of these would forge a record `_render` cannot catch on its
# own. Neutralized here, then `_render` still runs its own pass underneath.
_BRIEF_FORGED_RE = re.compile(r"^\[(final answer|evidence|unreferenced)\]", re.MULTILINE)

_TRACEBACK_RE = re.compile(r"Traceback \(most recent call last\)|\bException\b")
_EXIT_CODE_RE = re.compile(r"\b(?:exit code|Exit status)\D{0,3}([1-9]\d*)", re.IGNORECASE)
_FAILED_COUNT_RE = re.compile(r"\b([1-9]\d*)\s+(?:failed|errors?)\b", re.IGNORECASE)
_FAILED_WORD_RE = re.compile(r"\bFAILED\b")
_ERROR_LINE_RE = re.compile(r"^error:", re.IGNORECASE | re.MULTILINE)


def _cut(text: str, head: int, tail: int) -> str:
    """Like digest._head_tail, but the head/tail sizes vary by evidence
    source here (300/400 for referenced calls, 200/400 for failure signals),
    so this takes them as parameters instead of reading module constants."""
    text = (text or "").strip()
    if len(text) <= head + tail:
        return text
    return text[:head].rstrip() + "\n…\n" + text[-tail:].lstrip()


def _case_variants(identifier: str) -> set:
    """snake_case / kebab-case / camelCase / PascalCase spellings of one
    backticked identifier, so `` `test_auth_flow` `` also matches a haystack
    that spells it `test-auth-flow` or `testAuthFlow`."""
    variants = {identifier}
    if "_" in identifier:
        variants.add(identifier.replace("_", "-"))
    if "-" in identifier:
        variants.add(identifier.replace("-", "_"))
    words = [w for w in re.split(r"[_\-]+|(?<=[a-z0-9])(?=[A-Z])", identifier) if w]
    if len(words) > 1:
        lower_words = [w.lower() for w in words]
        variants.add("_".join(lower_words))
        variants.add("-".join(lower_words))
        variants.add(lower_words[0] + "".join(w.capitalize() for w in lower_words[1:]))
        variants.add("".join(w.capitalize() for w in lower_words))
    return variants


def _reference_strings(text: str) -> list:
    """Surface fragments of the final answer worth matching against tool
    evidence: backtick identifiers (plus case variants), file-path-like
    tokens, test names, and counts with units. Not NLP: a paraphrase like
    "the auth test" is deliberately not expanded to `test_auth_flow`."""
    if not text:
        return []
    refs = set()
    for m in _BACKTICK_RE.finditer(text):
        value = m.group(1).strip()
        if value:
            refs.add(value)
            refs.update(_case_variants(value))
    for pattern in (_PATH_RE, _NUMBER_UNIT_RE, _TEST_NAME_RE):
        for m in pattern.finditer(text):
            value = m.group(0).strip()
            if value:
                refs.add(value)
                if pattern is _TEST_NAME_RE:
                    refs.update(_case_variants(value))
    return [r for r in refs if r]


def _tool_calls(lines: list) -> list:
    """tool_use/tool_result pairs from this slice, in call order, numbered
    from 1. A tool_result whose tool_use falls outside the slice is dropped:
    only calls made in this window count as evidence."""
    calls: dict = {}
    order: list = []
    for entry in lines:
        if not isinstance(entry, dict) or _is_noise(entry):
            continue
        message = entry.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            kind = block.get("type")
            if kind == "tool_use":
                tool_id = block.get("id")
                call = {
                    "id": tool_id,
                    "name": block.get("name", "unknown"),
                    "input": block.get("input", {}),
                    "result": None,
                    "is_error": False,
                }
                calls[tool_id] = call
                order.append(call)
            elif kind == "tool_result":
                call = calls.get(block.get("tool_use_id"))
                if call is not None:
                    call["result"] = _block_text(block.get("content", ""))
                    call["is_error"] = bool(block.get("is_error"))
    for i, call in enumerate(order, start=1):
        call["number"] = i
    return order


def _call_haystack(call: dict) -> str:
    tool_input = call.get("input")
    try:
        input_text = json.dumps(tool_input, ensure_ascii=False, sort_keys=True, default=str)
    except (TypeError, ValueError):
        input_text = str(tool_input)
    result_text = call.get("result") or ""
    return f"{call.get('name', '')} {input_text} {result_text}".lower()


def _matches(call: dict, refs: list) -> bool:
    if not refs:
        return False
    haystack = _call_haystack(call)
    for ref in refs:
        ref_l = ref.strip().lower()
        if not ref_l:
            continue
        if ref_l in haystack:
            return True
        base = re.split(r"[\\/]", ref_l)[-1]
        if base and base != ref_l and base in haystack:
            return True
        if "." in base:
            stem = base.rsplit(".", 1)[0]
            if stem and stem != base and stem in haystack:
                return True
    return False


def _signals_failure(call: dict) -> bool:
    if call.get("is_error"):
        return True
    text = call.get("result") or ""
    if not text:
        return False
    return bool(
        _TRACEBACK_RE.search(text)
        or _EXIT_CODE_RE.search(text)
        or _FAILED_COUNT_RE.search(text)
        or _FAILED_WORD_RE.search(text)
        or _ERROR_LINE_RE.search(text)
    )


def _is_test_like(call: dict) -> bool:
    if call.get("name") != "Bash":
        return False
    tool_input = call.get("input")
    command = tool_input.get("command", "") if isinstance(tool_input, dict) else ""
    return bool(_TEST_LIKE_RE.search(str(command)))


def _last_test_like(calls: list):
    for call in reversed(calls):
        if _is_test_like(call):
            return call
    return None


def _render_call(call: dict, source: str) -> list:
    records = [_render(f"tool_use {call['name']}", _compact_args(call["input"]))]
    if call["result"] is not None:
        label = "tool_result ERROR" if call["is_error"] else "tool_result"
        head, tail = (
            (FAILURE_RESULT_HEAD, FAILURE_RESULT_TAIL)
            if source == "failure"
            else (EVIDENCE_RESULT_HEAD, EVIDENCE_RESULT_TAIL)
        )
        records.append(_render(label, _cut(call["result"], head, tail)))
    return records


def _prioritized_candidates(calls: list, refs: list) -> list:
    """(call, source) pairs in selection priority: failure signals first
    (newest 6), then the last test-like run, then referenced calls
    (newest first) — each call appears once, tagged by the highest-priority
    reason it was pulled in."""
    seen = set()
    candidates = []

    failure_calls = sorted((c for c in calls if _signals_failure(c)), key=lambda c: -c["number"])
    for call in failure_calls[:FAILURE_SIGNAL_CAP]:
        if call["id"] not in seen:
            candidates.append((call, "failure"))
            seen.add(call["id"])

    last_test = _last_test_like(calls)
    if last_test is not None and last_test["id"] not in seen:
        candidates.append((last_test, "last_test"))
        seen.add(last_test["id"])

    referenced = [c for c in calls if _matches(c, refs)]
    for call in sorted(referenced, key=lambda c: -c["number"]):
        if call["id"] not in seen:
            candidates.append((call, "referenced"))
            seen.add(call["id"])

    return candidates


def _build(lines: list, since_index: int, last_message: str, char_cap: int) -> dict:
    if not isinstance(lines, list):
        lines = []
    prompts = _user_prompts(lines)
    goal_raw = prompts[0] if prompts else ""
    latest_raw = prompts[-1] if prompts else ""
    answer_raw = (last_message or "").strip()
    answer_text = answer_raw if len(answer_raw) <= ANSWER_CHAR_CAP else _cut(answer_raw, ANSWER_HEAD, ANSWER_TAIL)

    try:
        window = lines[since_index:]
    except TypeError:
        window = []
    calls = _tool_calls(window)
    refs = _reference_strings(answer_raw)
    candidates = _prioritized_candidates(calls, refs)

    records = []
    goal_section = _render("goal", _cap(goal_raw, GOAL_CHAR_CAP)) if goal_raw else None
    if goal_section:
        records.append(goal_section)
    latest_section = _render("latest user instruction", _cap(latest_raw, INSTRUCTION_CHAR_CAP)) if latest_raw else None
    if latest_section:
        records.append(latest_section)
    answer_section = _render("final answer", answer_text)
    records.append(answer_section)

    fixed_used = sum(len(r) + 2 for r in records)
    evidence_budget = max(char_cap - fixed_used, 0)

    kept = []  # (call, source, rendered_records)
    used = 0
    for call, source in candidates:
        rendered = _render_call(call, source)
        size = sum(len(r) + 2 for r in rendered)
        if kept and used + size > evidence_budget:
            break
        kept.append((call, source, rendered))
        used += size
    kept.sort(key=lambda item: item[0]["number"])

    shown_ids = {call["id"] for call, _, _ in kept}
    from_failure = sum(1 for _, source, _ in kept if source == "failure")
    from_last_test = sum(1 for _, source, _ in kept if source == "last_test")
    from_referenced = sum(1 for _, source, _ in kept if source == "referenced")

    evidence_header = _render("evidence", f"{len(kept)} of {len(calls)} tool calls")
    records.append(evidence_header)
    evidence_call_records = []
    for _, _, rendered in kept:
        evidence_call_records.extend(rendered)
        records.extend(rendered)

    omitted = [c for c in calls if c["id"] not in shown_ids]
    omitted_counts = Counter(c["name"] for c in omitted)
    if omitted_counts:
        parts = [f"{name} x{count}" for name, count in sorted(omitted_counts.items(), key=lambda kv: (-kv[1], kv[0]))]
        unref_text = "omitted: " + ", ".join(parts)
    else:
        unref_text = "omitted: none"
    unref_section = _render("unreferenced", unref_text)
    records.append(unref_section)

    text = "\n\n".join(records)
    stats = {
        "goal_chars": len(goal_section) if goal_section else 0,
        "latest_instruction_chars": len(latest_section) if latest_section else 0,
        "final_answer_chars": len(answer_section),
        "evidence_chars": len(evidence_header) + sum(len(r) for r in evidence_call_records),
        "unreferenced_chars": len(unref_section),
        "total_chars": len(text),
        "matched_calls": len(shown_ids),
        "omitted_calls": len(omitted),
        "total_calls": len(calls),
        "evidence_from_failure": from_failure,
        "evidence_from_last_test": from_last_test,
        "evidence_from_referenced": from_referenced,
    }
    return {"text": text, "stats": stats}


def build_brief(lines: list, since_index: int, last_message: str, char_cap: int = 8000) -> str:
    """The reviewer's whole view under the message-anchored design: goal,
    latest user instruction, final answer, and only the tool evidence that
    backs it up or would contradict it."""
    return _build(lines, since_index, last_message, char_cap)["text"]


def brief_stats(lines: list, since_index: int, last_message: str, char_cap: int = 8000) -> dict:
    """Chars per section plus how many evidence items came from each
    selection path (failure / last_test / referenced), so the gap between
    what the answer references and what actually backs it up stays visible."""
    return _build(lines, since_index, last_message, char_cap)["stats"]
