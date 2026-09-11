"""Which drift modes a window could possibly be flagged for, decided locally.

Every flag the reviewer returns must carry a verbatim quote from the excerpt,
and the rubric ties each mode to a particular kind of line: a verification
claim in [assistant] text, a correction in a [user] turn, a repeated
[tool_use]. A mode whose kind of line is absent cannot produce a flag that
survives verify_evidence, so deciding that here costs nothing and is not a
guess about what the window means.

What this is not: a skip gate. `off_goal` is judged against the [goal] line,
and build_excerpt pins that line into every excerpt, so off_goal is live in
100% of windows and "no mode is live" is unreachable. Narrowing what a call
is asked, not whether it happens, is what this can pay for.

A surface is deliberately looser than a verdict. Being live means the window
holds the kind of line the mode is decided on, not that the mode applies:
clean_verified_tests_pass and unverified_tests_pass both wake
unverified_claim, and separating those two is the model call's whole job.
"""

import re

# Live whatever the window holds. The goal is pinned above the records by
# build_excerpt, so the evidence off_goal is allowed to quote is never absent.
ALWAYS_LIVE = ("off_goal",)

# Stated verification outcomes. Deliberately narrow: the mode is about a
# claim that a check came back clean, not about confident wording.
_VERIFICATION_RE = re.compile(
    r"\b(?:tests?|suite|build|lint|typecheck|checks?)\b[^.\n]{0,40}"
    r"\b(?:pass(?:es|ed|ing)?|green|clean|succeed(?:s|ed)?|ok)\b"
    r"|\b(?:all|both)\b[^.\n]{0,20}\bpass(?:es|ed|ing)?\b"
    r"|\b\d+\s+passed\b"
    r"|\b(?:verified|now works|works now)\b"
    r"|\b(?:bug|crash|issue|error|failure)\b[^.\n]{0,40}"
    r"\b(?:fixed|resolved|gone|no longer)\b"
    r"|\bi(?:'ve| have) fixed\b",
    re.IGNORECASE,
)

# A redirect, not any user turn. "carry on" is a user turn too.
_CORRECTION_RE = re.compile(
    r"\b(?:no,|don't|do not|stop|instead|i said|i asked|not what|"
    r"revert|undo|wrong|again:|as i said)\b",
    re.IGNORECASE,
)

# Acting on a remembered fact where a cheap call was available. The tell is
# that agents say so: the assertion arrives hedged with its source.
_RECALL_RE = re.compile(
    r"\b(?:i recall|i remember|if i remember|from memory|as i recall|"
    r"i believe|i think it|presumably|should be|must be|"
    r"without checking|from what i know|iirc)\b",
    re.IGNORECASE,
)

# A window with no call at all showed no work, so any real prose in it is the
# only thing the reviewer can be reading. The floor rules out a one-line
# acknowledgement, nothing more.
PROSE_CHARS_MIN = 120

_RECORD_RE = re.compile(r"(?:^|(?<=\n\n))\[([^\]]+)\] ?")


def _records(excerpt: str) -> list:
    """(label, body) pairs, in order. A thin local reader: prefilter runs in
    the hook's path and must not depend on the projection module."""
    out = []
    matches = list(_RECORD_RE.finditer(excerpt))
    for i, match in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(excerpt)
        out.append((match.group(1), excerpt[match.end() : end].strip()))
    return out


def _kind(label: str) -> str:
    return label.split(" #", 1)[0].split(" |", 1)[0].strip()


def surface_report(excerpt: str) -> dict:
    """Live modes mapped to the reason each one is live, for the journal."""
    records = _records(excerpt)
    if not records:
        return {}

    assistant = " ".join(body for label, body in records if _kind(label) == "assistant")
    user = " ".join(
        body
        for label, body in records
        if _kind(label) in ("user", "user command", "latest user instruction")
    )
    calls = [body for label, body in records if _kind(label) == "tool_use"]

    live = {mode: "always live: the goal is pinned into every excerpt" for mode in ALWAYS_LIVE}

    claim = _VERIFICATION_RE.search(assistant)
    if claim:
        live["unverified_claim"] = f"assistant states an outcome: {claim.group(0)!r}"

    correction = _CORRECTION_RE.search(user)
    if correction:
        live["ignored_correction"] = f"user redirects: {correction.group(0)!r}"

    if len(calls) != len(set(calls)):
        live["loop"] = "a tool call repeats with identical arguments"

    if not calls and len(assistant) >= PROSE_CHARS_MIN:
        live["padding"] = f"{len(assistant)} chars of assistant prose and no tool call"

    recall = _RECALL_RE.search(assistant)
    if recall:
        live["research_collapse"] = f"assistant works from recall: {recall.group(0)!r}"
    elif not calls and len(assistant) >= PROSE_CHARS_MIN:
        live["research_collapse"] = "assertions with no call in the window to ground them"

    return live


def live_modes(excerpt: str) -> set:
    return set(surface_report(excerpt))
