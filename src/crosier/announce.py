"""Turn a verdict into what the hook prints.

Two channels, deliberately separate. `hookSpecificOutput.additionalContext`
reaches the agent as a system reminder at the point the hook fired, and is
used only for a real flag. `systemMessage` reaches the user's screen and never
the agent, so a clean verdict can be shown without spending a line of the
agent's attention on it. At Stop, any additionalContext continues the
conversation: right for a flag, wrong for "no issues found".

Two failure modes live in the flag wording rather than in any code. A note
that sounds like a challenge gets argued with, and the agent spends its next
turn defending itself instead of working. A note that sounds like a mandate
gets obeyed too hard, and a half-finished, perfectly good debugging tree gets
abandoned because a reviewer that cannot see the repository got impatient.
The message is deliberately advisory, bounded, and closed to debate.
"""

CONFIDENCE_ORDER = {"low": 0, "medium": 1, "high": 2}

ADVISORY_SUFFIX = (
    "Advisory only: written by a zero-context reviewer that cannot see this repository, "
    "the environment, or what you already ruled out. Finish the step in progress first. "
    "Then either act on this or dismiss it in one line — do not justify earlier turns, "
    "and do not reply to this note."
)


def _meets_confidence(verdict: dict, minimum: str) -> bool:
    have = CONFIDENCE_ORDER.get(verdict.get("confidence"), 0)
    need = CONFIDENCE_ORDER.get(minimum, 0)
    return have >= need


def flag_text(verdict: dict) -> str:
    claim = verdict.get("flagged_claim") or "an assumption in the recent work"
    category = verdict.get("category")
    label = f" ({category})" if category else ""
    parts = [f"Crosier direction check flagged a possible issue{label}: {claim}."]
    if verdict.get("reason"):
        parts.append(f"Reason: {verdict['reason']}.")
    if verdict.get("evidence"):
        parts.append(f"Evidence from the session: \"{verdict['evidence']}\".")
    if verdict.get("suggested_check"):
        parts.append(f"Suggested check: {verdict['suggested_check']}.")
    parts.append(ADVISORY_SUFFIX)
    return " ".join(parts)


def _user_only(message: str) -> dict:
    return {"systemMessage": message}


def hook_output(
    event: str,
    verdict: dict | None,
    turn_number: int,
    announce_mode: str,
    min_flag_confidence: str = "low",
) -> dict | None:
    """The JSON the hook prints for a finished verdict, or None for silence."""
    if verdict is None:
        return None
    show_clean = announce_mode == "always"
    if verdict.get("status") == "proceed":
        return _user_only(f"Crosier direction check (turn {turn_number}): no issues found.") if show_clean else None
    if not _meets_confidence(verdict, min_flag_confidence):
        # A guess from a blind reviewer is not worth a train of thought.
        if not show_clean:
            return None
        return _user_only(f"Crosier direction check (turn {turn_number}): nothing worth interrupting for.")
    claim = verdict.get("flagged_claim") or "an assumption in the recent work"
    return {
        "hookSpecificOutput": {"hookEventName": event, "additionalContext": flag_text(verdict)},
        "systemMessage": f"Crosier flagged: {claim}",
    }


def stale_output(announce_mode: str) -> dict | None:
    if announce_mode != "always":
        return None
    return _user_only("Crosier direction check result discarded as stale — the session moved on before it returned.")


def backoff_output(log_path: str) -> dict:
    return _user_only(f"Crosier direction checks disabled for this session after repeated errors — see {log_path}.")
