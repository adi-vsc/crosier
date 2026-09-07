"""Pure, deterministic escalation logic. No network calls, no LLM calls —
this decides only whether the (expensive) reviewer call should run.

The unit of drift is the model call: each one is a chance for the model to
build on its own previous output as if it were settled ground. User prompts
are far too coarse a clock — replaying real sessions, several ran past 250k
context tokens on three to six prompts — so the primary trigger and the
cooldown both count calls, which the PostToolBatch hook sees one by one.
"""

from crosier.config import CrosierConfig
from crosier.state import SessionState

REPETITION_WINDOW = 10

# Fallback only, for transcripts that report no usage. Real context growth is
# read from the transcript's own token counts wherever they are available.
CHARS_PER_TOKEN = 4

# A window is "bulk work" when one tool owns most of it but keeps hitting
# different targets. Six of ten is dominance; three fifths of those being
# distinct is a sweep rather than a loop.
BULK_DOMINANCE_MIN = 6
BULK_DISTINCT_RATIO = 0.6


def tool_repetition_rate(recent_tool_calls: list) -> float:
    """Share of the last REPETITION_WINDOW calls that duplicate another call.

    Zero until the window is full: on a partial window a single duplicated
    pair produces a rate high enough to clear the default threshold, which
    made a file read twice look identical to a loop.
    """
    window = recent_tool_calls[-REPETITION_WINDOW:]
    if len(window) < REPETITION_WINDOW:
        return 0.0
    counts: dict = {}
    for call in window:
        counts[call] = counts.get(call, 0) + 1
    repeated = sum(c - 1 for c in counts.values() if c > 1)
    return repeated / len(window)


def is_bulk_operation(recent_tool_calls: list) -> bool:
    """True when the window looks like a codebase-wide sweep.

    A rename across twenty files is twenty Edit calls in a row, and a static
    "same tool N times" rule cannot tell that apart from an agent stuck
    re-reading one file. The arguments can: a sweep walks new targets, a loop
    does not. Calls are fingerprinted as `name:hash-of-arguments`, so distinct
    fingerprints under one tool name mean distinct targets.
    """
    window = recent_tool_calls[-REPETITION_WINDOW:]
    if len(window) < REPETITION_WINDOW:
        return False
    names: dict = {}
    for call in window:
        name = call.split(":", 1)[0]
        names[name] = names.get(name, 0) + 1
    top_name, top_count = max(names.items(), key=lambda item: item[1])
    if top_count < BULK_DOMINANCE_MIN:
        return False
    prefix = top_name + ":"
    distinct = len({call for call in window if call.startswith(prefix)})
    return distinct / top_count >= BULK_DISTINCT_RATIO


def budget_exhausted(state: SessionState, config: CrosierConfig) -> bool:
    """A stuck session burns tokens on its own; a check that fires on every
    call of that loop doubles the bill it was supposed to cut short."""
    return state.checks_run >= config.max_checks_per_session


def _call_threshold(state: SessionState, config: CrosierConfig) -> int:
    if state.checks_run == 0:
        return min(config.first_check_call_threshold, config.call_threshold)
    return config.call_threshold


def should_escalate(
    state: SessionState, config: CrosierConfig, context_growth: int | None = None
) -> bool:
    if budget_exhausted(state, config):
        return False
    # A long conversation drifts whether or not tools ran. The turn trigger
    # is not behind the call cooldown, which exists to stop a stuck loop from
    # re-triggering on the same evidence, not to silence a chat.
    if state.turns_since_check >= config.turn_threshold:
        return True
    if state.calls_since_check < config.min_calls_between_checks:
        return False
    # The first check of a session fires sooner than the rest. The damage a
    # long session never recovers from is committed early (arXiv:2505.06120),
    # and under one flat threshold that window is the one stretch of the
    # session nobody looks at. The budget and the cooldown are unchanged, so
    # this moves the first check earlier rather than adding checks.
    if state.calls_since_check >= _call_threshold(state, config):
        return True
    if context_growth is not None:
        if context_growth >= config.token_threshold:
            return True
    elif state.chars_since_check >= config.token_threshold * CHARS_PER_TOKEN:
        return True
    if tool_repetition_rate(state.recent_tool_calls) >= config.repetition_threshold:
        return not is_bulk_operation(state.recent_tool_calls)
    return False
