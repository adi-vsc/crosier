"""Cut a rendered excerpt down to the records one drift mode is actually
judged against.

build_excerpt() (digest.py) compresses a session window into labelled
"[label] body" records. Every reviewer call in verdict.py currently sends
the whole excerpt to one zero-context reviewer, whatever mode ends up
flagged. This module is the missing per-mode narrowing: each drift mode in
VERDICT_PROMPT_TEMPLATE names a specific subset of record types as its
evidence, and everything outside that subset is noise for that judgement,
or the evidence for a different mode. Keeping only the relevant subset is
what would let a future per-mode reviewer read less than the full,
cap-sized excerpt - see projection_sizes for the measurement.

This module does not wire that in. It is a pure function of a string,
stdlib only: no parsing of the raw transcript, no network, no model call.
Staying a substring cut over an already-rendered excerpt is also what keeps
a projection safe for evidence checking - verify_evidence in verdict.py
matches a quote against the original excerpt, so a projection only has to
reproduce a kept record's exact rendered text, never rewrite it.
"""

import re

MODES = ("unverified_claim", "off_goal", "ignored_correction", "research_collapse", "loop", "padding")

# Header records name the standard the session is measured against. Every
# mode keeps them: off_goal is not decidable at all without [goal], and a
# quote-checked flag on any other mode still benefits from the reviewer
# seeing what was actually asked.
_HEADER_LABELS = ("goal", "latest user instruction")

# Matches exactly the label shapes digest.py's _render produces: a handful
# of fixed labels, plus the two numbered kinds whose call-number/tool-name
# or call-number/size suffix varies per record.
_LABEL_ALTS = (
    r"goal|latest user instruction|user command|user|assistant|compaction summary|"
    r"tool_use #\d+ [^\]]+|tool_result #\d+(?: ERROR)? \| \d+ chars"
)
# A record only starts at the very beginning of the excerpt or right after
# the "\n\n" join separator build_excerpt uses. Anchoring on that (instead
# of matching "[label] " anywhere) keeps a "[...]"-shaped substring that
# happens to sit inside a tool_result's own body from being mistaken for a
# new record.
_RECORD_START_RE = re.compile(rf"(?:^|(?<=\n\n))\[({_LABEL_ALTS})\] ")


def split_records(excerpt: str) -> list[tuple[str, str]]:
    """Split a rendered excerpt back into (label, body) pairs, in order."""
    if not excerpt:
        return []
    matches = list(_RECORD_START_RE.finditer(excerpt))
    records = []
    for i, match in enumerate(matches):
        label = match.group(1)
        body_start = match.end()
        if i + 1 < len(matches):
            body_end = matches[i + 1].start() - 2  # trim the "\n\n" separator
        else:
            body_end = len(excerpt)
        records.append((label, excerpt[body_start:body_end]))
    return records


def _kind(label: str) -> str:
    """Collapse a numbered label ("tool_use #3 Bash") to its record type
    ("tool_use") so the policy table below doesn't need one row per call."""
    if label.startswith("tool_use #"):
        return "tool_use"
    if label.startswith("tool_result #"):
        return "tool_result"
    return label


# Per mode, which record kinds carry its evidence and how much of each is
# needed. "full" keeps the body; "label" keeps only the "[label]" line (the
# tool_use label alone still names the call number, the tool and its
# target; the tool_result label alone still states the call it answers, its
# ERROR flag and its true byte size - often the whole signal a mode needs,
# without paying for the body). A kind with no entry for a mode is dropped
# entirely: it is not part of what that mode is decided against. Each entry
# cites the VERDICT_PROMPT_TEMPLATE clause (verdict.py) it follows.
MODE_POLICY: dict[str, dict[str, str]] = {
    # "Decide this one against the [tool_result] lines... Locate the
    # [tool_result] in the window that ran the check being claimed" and
    # "Evidence: quote the claim, or the contradicting result line." The
    # claim is assistant prose; the check is a tool_result body. tool_use is
    # label-only: the call that produced a result is already named inside
    # that result's own "#N" label, and the argument blob (a file path, a
    # command string) is not the check's outcome, so it proves nothing about
    # the claim either way.
    "unverified_claim": {
        "assistant": "full",
        "tool_result": "full",
        "tool_use": "label",
    },
    # "the current work no longer serves the [goal] or the [latest user
    # instruction]... Evidence: quote the instruction being missed or the
    # action violating it." The instruction is user prose; the action is
    # assistant prose and which tools were reached for. tool_use and
    # tool_result are label-only: scope creep can show up as "a huge,
    # unrelated file read" from the label's byte size alone, which is what
    # keeps this mode "not large tool_result bodies" as scoped.
    "off_goal": {
        "user": "full",
        "user command": "full",
        "assistant": "full",
        "tool_use": "label",
        "tool_result": "label",
    },
    # "the user corrected or redirected the agent, and later work continues
    # the corrected behaviour. Evidence: quote the correction." Handled
    # positionally below, not through this table: everything the mode
    # judges is downstream of the moment the user spoke.
    # "the agent acts on recalled or assumed facts... where a cheap check (a
    # read, a grep, a run) was available and not done. Evidence: quote the
    # assumption." The assumption is assistant prose. Whether a check ran is
    # answered by a tool_use record existing at all - its label already
    # names the tool (Read/Grep/Bash) - so tool_use and its tool_result stay
    # label-only: this mode needs their presence and outcome shape (ran, or
    # errored), never their content.
    "research_collapse": {
        "assistant": "full",
        "tool_use": "label",
        "tool_result": "label",
    },
    # "the same call hits the same target repeatedly with no change in
    # result, no stated obstacle, and no new approach... Evidence: quote one
    # of the repeated lines." The repeated call and its target live in the
    # tool_use body, kept full: a loop is only provable by comparing
    # arguments across calls. assistant is kept full because "no stated
    # obstacle" and "no new approach" are things the agent would have said
    # in prose, not things a tool_use line shows. tool_result stays
    # label-only: "no change in result" is checkable from the repeated
    # size/ERROR shape without needing the full body.
    "loop": {
        "tool_use": "full",
        "tool_result": "label",
        "assistant": "full",
    },
    # "the agent restates, summarises or re-plans instead of executing...
    # Evidence: quote the repeated material." That comparison is prose
    # against prose: what the user asked for, against what the agent keeps
    # re-saying. Tool records carry neither and are dropped entirely.
    "padding": {
        "assistant": "full",
        "user": "full",
    },
}


def _render(label: str, body: str, keep_body: bool) -> str:
    return f"[{label}] {body}" if keep_body else f"[{label}]"


def _project_ignored_correction(records: list[tuple[str, str]]) -> str:
    # "later work continues the corrected behaviour" - work that predates
    # any correction cannot be the continuation the mode looks for, so drop
    # it (besides the header) and keep everything from the first
    # user-authored record onward, in full.
    kept: list[str] = []
    seen_user = False
    for label, body in records:
        kind = _kind(label)
        if label in _HEADER_LABELS:
            kept.append(_render(label, body, True))
            continue
        if not seen_user:
            if kind in ("user", "user command", "compaction summary"):
                seen_user = True
            else:
                continue
        kept.append(_render(label, body, True))
    return "\n\n".join(kept)


def project(excerpt: str, mode: str) -> str:
    """Return the sub-excerpt that `mode` can actually be judged from."""
    if mode not in MODES:
        return excerpt
    records = split_records(excerpt)
    if not records:
        return excerpt

    if mode == "ignored_correction":
        return _project_ignored_correction(records)

    policy = MODE_POLICY[mode]
    kept: list[str] = []
    for label, body in records:
        if label in _HEADER_LABELS:
            kept.append(_render(label, body, True))
            continue
        action = policy.get(_kind(label))
        if action == "full":
            kept.append(_render(label, body, True))
        elif action == "label":
            kept.append(_render(label, body, False))
        # else: not part of this mode's evidence - dropped outright, unlike
        # tool_use/tool_result a bare label carries no signal worth keeping
        # for a record type the mode's clause never mentions.
    return "\n\n".join(kept)


def projection_sizes(excerpt: str) -> dict[str, int]:
    """Character length of each mode's projection, plus the full excerpt:
    the measurement hook for how much a per-mode reviewer would read."""
    sizes = {"full": len(excerpt)}
    for mode in MODES:
        sizes[mode] = len(project(excerpt, mode))
    return sizes
