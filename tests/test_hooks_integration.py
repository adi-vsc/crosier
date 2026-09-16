"""End-to-end through the single hook entrypoint, with the reviewer call
stubbed out. The transcript fixtures mimic the real JSONL shape: a user prompt
is a string-content user entry, a model call is an assistant entry holding a
tool_use, and its result is a user entry holding a tool_result."""

import importlib.util
import io
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from crosier.state import load_state


def _load_hook_module():
    path = Path(__file__).resolve().parent.parent / "hooks" / "crosier_hook.py"
    spec = importlib.util.spec_from_file_location("crosier_hook", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


hook = _load_hook_module()

PROCEED = {"status": "proceed", "confidence": "high", "category": None, "flagged_claim": None, "reason": None, "suggested_check": None, "evidence": None}
FLAG = {
    "status": "flag",
    "confidence": "medium",
    "category": "unverified_claim",
    "flagged_claim": "risky assumption",
    "reason": None,
    "suggested_check": None,
    "evidence": "[assistant] all tests pass",
    "evidence_verified": True,
}


@pytest.fixture(autouse=True)
def _home(monkeypatch, tmp_path):
    monkeypatch.setenv("CROSIER_HOME", str(tmp_path / "home"))
    # A developer running the suite with the kill switch set in their own shell
    # would otherwise see every hook test pass by doing nothing.
    monkeypatch.delenv("CROSIER_DISABLED", raising=False)
    return tmp_path / "home"


# --- transcript fixtures ---------------------------------------------------------


def _prompt(text):
    return {"type": "user", "message": {"role": "user", "content": text}}


def _call(i, name="Read", target=None, context=None):
    tool_id = f"toolu_{i}"
    use = {"type": "tool_use", "id": tool_id, "name": name, "input": {"file_path": target or f"f{i}.py"}}
    assistant = {"type": "assistant", "message": {"role": "assistant", "content": [use]}}
    if context is not None:
        assistant["message"]["usage"] = {"input_tokens": 3, "cache_creation_input_tokens": 0, "cache_read_input_tokens": context, "output_tokens": 5}
    result = {"type": "user", "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": tool_id, "content": f"contents of {target or f'f{i}.py'}"}]}}
    return [assistant, result]


class Session:
    """A growing transcript plus a way to fire hook events against it."""

    def __init__(self, tmp_path, session_id="s1"):
        self.root = tmp_path / "project"
        self.root.mkdir(exist_ok=True)
        self.transcript = tmp_path / "transcript.jsonl"
        self.session_id = session_id
        self.lines = []
        self.calls = 0

    def add(self, entries):
        self.lines.extend(entries)
        self.transcript.write_text("\n".join(json.dumps(e) for e in self.lines), encoding="utf-8")

    def prompt(self, text="do the thing"):
        self.add([_prompt(text)])

    def batch(self, name="Read", target=None, context=None):
        self.calls += 1
        self.add(_call(self.calls, name, target, context))

    def fire(self, event, monkeypatch, capsys, **extra):
        payload = {
            "hook_event_name": event,
            "cwd": str(self.root),
            "session_id": self.session_id,
            "transcript_path": str(self.transcript),
            **extra,
        }
        monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
        code = hook.main()
        assert code == 0
        out = capsys.readouterr().out.strip()
        return json.loads(out) if out else None

    def state(self):
        return load_state(self.session_id)


def _activation_only(out):
    """The one user-channel line a fresh session gets on its first hook run.

    It is `systemMessage` and nothing else: no `decision`, so it never holds
    the turn.
    """
    return (
        isinstance(out, dict)
        and set(out) == {"systemMessage"}
        and out["systemMessage"].startswith("Crosier active")
    )


class _Reviewer:
    """Stands in for generate_verdict; records the excerpts it was shown."""

    def __init__(self, verdict=None, raises=None):
        self.verdict = verdict
        self.raises = raises
        self.excerpts = []

    def __call__(self, excerpt, **kwargs):
        self.excerpts.append(excerpt)
        self.kwargs = kwargs
        if self.raises:
            raise self.raises
        return self.verdict


def _gated_turn(tmp_path, **config):
    s = Session(tmp_path)
    if config:
        _config(s, **config)
    s.prompt("fix the parser")
    s.batch(name="Edit", target="parser.py")
    return s


def _stop(s, monkeypatch, capsys, reviewer, answer="Fixed — all tests pass.", active=False):
    with patch("crosier.pipeline.generate_verdict", reviewer):
        return s.fire("Stop", monkeypatch, capsys, stop_hook_active=active, last_assistant_message=answer)


def _config(session, **values):
    body = "\n".join(f"{k} = {json.dumps(v)}" for k, v in values.items())
    (session.root / ".crosier.toml").write_text(f"[crosier]\n{body}\n", encoding="utf-8")


# --- event routing, kill switch, activation ---------------------------------------


def test_events_other_than_stop_are_a_no_op(tmp_path, monkeypatch, capsys):
    # Older installs registered PostToolBatch, UserPromptSubmit and PreCompact
    # on this script. They must cost nothing and print nothing.
    s = Session(tmp_path)
    s.prompt("goal")
    for event in ("UserPromptSubmit", "PostToolBatch", "PreCompact", "SessionStart"):
        s.batch()
        assert s.fire(event, monkeypatch, capsys) is None
    assert not (tmp_path / "home" / "state").exists()


def test_malformed_payload_is_ignored(monkeypatch, capsys):
    monkeypatch.setattr(sys, "stdin", io.StringIO("not json"))
    assert hook.main() == 0
    assert capsys.readouterr().out == ""


def test_disabled_config_produces_no_output_and_no_review(tmp_path, monkeypatch, capsys):
    s = _gated_turn(tmp_path, enabled=False)
    reviewer = _Reviewer(FLAG)
    assert _stop(s, monkeypatch, capsys, reviewer) is None
    assert reviewer.excerpts == []
    assert s.state().total_turns == 0


def test_env_kill_switch_makes_the_hook_do_nothing_at_all(tmp_path, monkeypatch, capsys):
    # One session off, no file, no restart. It has to cost nothing: no output,
    # no state file, no review — otherwise "disabled" is not disabled.
    s = _gated_turn(tmp_path)
    monkeypatch.setenv("CROSIER_DISABLED", "1")
    reviewer = _Reviewer(FLAG)
    assert _stop(s, monkeypatch, capsys, reviewer) is None
    assert reviewer.excerpts == []
    assert not (tmp_path / "home" / "state").exists()


def test_kill_switch_off_values_leave_crosier_running(tmp_path, monkeypatch, capsys):
    s = Session(tmp_path)
    s.prompt("where is it?")
    monkeypatch.setenv("CROSIER_DISABLED", "0")
    assert _activation_only(_stop(s, monkeypatch, capsys, _Reviewer(FLAG), answer="In parser.py."))


def test_activation_line_is_announced_once_per_session(tmp_path, monkeypatch, capsys):
    s = Session(tmp_path)
    s.prompt("where is it?")
    assert _activation_only(_stop(s, monkeypatch, capsys, _Reviewer(FLAG), answer="In parser.py."))
    assert s.state().announced_activation is True
    s.prompt("and the lexer?")
    assert _stop(s, monkeypatch, capsys, _Reviewer(FLAG), answer="In lexer.py.") is None


def test_activation_line_shares_the_slot_with_a_block(tmp_path, monkeypatch, capsys):
    # A hook prints one JSON object. If a block lands on the same invocation as
    # the activation line, neither may silently drop the other.
    s = _gated_turn(tmp_path)
    out = _stop(s, monkeypatch, capsys, _Reviewer(FLAG))
    assert out["decision"] == "block"
    assert "Crosier active" in out["systemMessage"]
    assert "risky assumption" in out["systemMessage"]


def test_unrecognized_transcript_format_starts_a_cooldown_after_two(tmp_path, monkeypatch, capsys):
    from crosier.errors import COOLDOWN_REVIEWS

    s = Session(tmp_path)
    s.add([{"kind": "v2-event", "payload": {"text": "hi"}} for _ in range(8)])
    reviewer = _Reviewer(FLAG)
    outs = [_stop(s, monkeypatch, capsys, reviewer) for _ in range(2)]
    assert reviewer.excerpts == []
    state = s.state()
    # A transient format problem gets a cooldown, not a permanent disable.
    assert state.disabled_for_session is False
    assert state.cooldown_remaining_reviews == COOLDOWN_REVIEWS
    assert state.total_failures == 2
    assert any(out and "pausing the next" in out.get("systemMessage", "") for out in outs)


def test_turns_are_counted_once_per_user_turn_at_stop(tmp_path, monkeypatch, capsys):
    # Journal `turn` is what the per-turn benchmark joins on. A forced revision
    # is the same turn, so a Stop with stop_hook_active does not count.
    s = Session(tmp_path)
    s.prompt("where is it?")
    _stop(s, monkeypatch, capsys, _Reviewer(PROCEED), answer="In parser.py.")
    _stop(s, monkeypatch, capsys, _Reviewer(PROCEED), answer="In parser.py.", active=True)
    s.prompt("and the lexer?")
    _stop(s, monkeypatch, capsys, _Reviewer(PROCEED), answer="In lexer.py.")
    assert s.state().total_turns == 2


# --- stop gate: a synchronous review of the final answer -------------------------


def test_stop_gate_is_on_without_any_config(tmp_path, monkeypatch, capsys):
    s = _gated_turn(tmp_path)
    reviewer = _Reviewer(FLAG)
    assert _stop(s, monkeypatch, capsys, reviewer)["decision"] == "block"
    assert len(reviewer.excerpts) == 1


def test_stop_gate_blocks_a_flagged_final_answer_before_it_stands(tmp_path, monkeypatch, capsys):
    s = _gated_turn(tmp_path)
    reviewer = _Reviewer(FLAG)
    out = _stop(s, monkeypatch, capsys, reviewer)
    # Stop and SubagentStop take top-level decision/reason, not hookSpecificOutput
    # (code.claude.com/docs/en/hooks, "Stop decision control").
    assert out["decision"] == "block"
    assert "risky assumption" in out["reason"]
    assert "hookSpecificOutput" not in out
    assert len(reviewer.excerpts) == 1
    assert "Fixed — all tests pass." in reviewer.excerpts[0]


def test_stop_gate_lets_a_clean_final_answer_stand(tmp_path, monkeypatch, capsys):
    s = _gated_turn(tmp_path)
    reviewer = _Reviewer(PROCEED)
    out = _stop(s, monkeypatch, capsys, reviewer)
    assert len(reviewer.excerpts) == 1
    assert "decision" not in (out or {})
    assert "hookSpecificOutput" not in (out or {})


def test_stop_gate_never_reviews_a_revision_it_already_forced(tmp_path, monkeypatch, capsys):
    # stop_hook_active means this Stop is the revision. One per turn, never a loop.
    s = _gated_turn(tmp_path)
    reviewer = _Reviewer(FLAG)
    out = _stop(s, monkeypatch, capsys, reviewer, active=True)
    assert reviewer.excerpts == []
    assert "decision" not in (out or {})


def test_stop_gate_costs_nothing_on_an_answer_the_prefilter_passes(tmp_path, monkeypatch, capsys):
    s = Session(tmp_path)
    s.prompt("where is the parser?")
    s.batch(name="Read", target="parser.py")
    reviewer = _Reviewer(FLAG)
    _stop(s, monkeypatch, capsys, reviewer, answer="It lives in parser.py.")
    assert reviewer.excerpts == []


def test_stop_gate_fails_open_when_the_review_returns_nothing(tmp_path, monkeypatch, capsys):
    s = _gated_turn(tmp_path)
    out = _stop(s, monkeypatch, capsys, _Reviewer(None))
    assert "decision" not in (out or {})
    assert s.state().consecutive_failures == 1


def test_stop_gate_fails_open_when_the_review_raises(tmp_path, monkeypatch, capsys):
    s = _gated_turn(tmp_path)
    out = _stop(s, monkeypatch, capsys, _Reviewer(raises=RuntimeError("boom")))
    assert "decision" not in (out or {})
    # Caught inside the gate, not only by main(): a crash still counts toward
    # backing off, and the rest of the invocation still saves state.
    assert s.state().consecutive_failures == 1


def test_stop_gate_does_not_block_on_a_flag_below_the_confidence_floor(tmp_path, monkeypatch, capsys):
    # verify_evidence demotes an unquotable flag to low; that must not hold a turn.
    s = _gated_turn(tmp_path)
    out = _stop(s, monkeypatch, capsys, _Reviewer({**FLAG, "confidence": "low", "evidence_verified": False}))
    assert "decision" not in (out or {})


def test_stop_gate_obeys_the_session_budget(tmp_path, monkeypatch, capsys):
    s = _gated_turn(tmp_path, max_checks_per_session=0)
    reviewer = _Reviewer(FLAG)
    _stop(s, monkeypatch, capsys, reviewer)
    assert reviewer.excerpts == []


def test_stop_gate_bounds_its_call_below_the_stop_hook_timeout(tmp_path, monkeypatch, capsys):
    from crosier.pipeline import GATE_CALL_TIMEOUT_CAP

    s = _gated_turn(tmp_path, call_timeout=500)
    reviewer = _Reviewer(PROCEED)
    _stop(s, monkeypatch, capsys, reviewer)
    assert reviewer.kwargs["timeout"] == GATE_CALL_TIMEOUT_CAP


def test_stop_gate_counts_as_a_check_and_is_journalled(tmp_path, monkeypatch, capsys):
    from crosier.journal import read_journal

    s = _gated_turn(tmp_path)
    _stop(s, monkeypatch, capsys, _Reviewer(FLAG))
    state = s.state()
    assert state.checks_run == 1
    assert state.last_line_index == len(s.lines)
    assert state.last_flag == "risky assumption"
    entries = read_journal("s1")
    assert len(entries) == 1
    assert entries[0]["stop_gate"] is True
    assert entries[0]["event"] == "Stop"
    assert entries[0]["delivered_to_agent"] is True
    # The per-turn benchmark reads why a draft was held, not only that it was.
    assert entries[0]["evidence"] == FLAG["evidence"]
    assert "reason" in entries[0]


def test_the_next_review_is_told_about_the_previous_flag(tmp_path, monkeypatch, capsys):
    s = _gated_turn(tmp_path)
    _stop(s, monkeypatch, capsys, _Reviewer(FLAG))
    s.prompt("keep going")
    s.batch(name="Edit", target="parser.py")
    reviewer = _Reviewer(PROCEED)
    _stop(s, monkeypatch, capsys, reviewer)
    assert reviewer.kwargs["previous_flag"] == "risky assumption"


# --- cooldown: transient failures pause the gate instead of killing it ------------


def test_a_failing_gate_starts_a_cooldown_after_two_instead_of_disabling(tmp_path, monkeypatch, capsys):
    from crosier.errors import COOLDOWN_REVIEWS

    s = _gated_turn(tmp_path)
    outs = []
    for _ in range(2):
        outs.append(_stop(s, monkeypatch, capsys, _Reviewer(None)))
        s.prompt("again")
        s.batch(name="Edit", target="parser.py")
    state = s.state()
    # The window was never reviewed, so the read cursor must not skip past it.
    assert state.last_line_index == 0
    # Two failures in a row (e.g. the reviewer timing out) must not kill the
    # gate for the rest of the session -- only pause it.
    assert state.disabled_for_session is False
    assert state.cooldown_remaining_reviews == COOLDOWN_REVIEWS
    assert state.consecutive_failures == 0
    assert state.total_failures == 2
    # Exactly one systemMessage announces the backoff, on the turn it starts.
    assert outs[0] is None or "pausing" not in (outs[0] or {}).get("systemMessage", "")
    assert "decision" not in (outs[1] or {})
    assert "pausing the next 5 reviews" in outs[1]["systemMessage"]


def test_cooldown_skips_the_gate_for_reviews_not_the_reviewer_call(tmp_path, monkeypatch, capsys):
    # A skipped-during-cooldown Stop must not reach the reviewer at all.
    s = _gated_turn(tmp_path)
    for _ in range(2):
        _stop(s, monkeypatch, capsys, _Reviewer(None))
        s.prompt("again")
        s.batch(name="Edit", target="parser.py")
    assert s.state().cooldown_remaining_reviews == 5
    reviewer = _Reviewer(FLAG)
    out = _stop(s, monkeypatch, capsys, reviewer)
    assert reviewer.excerpts == []
    assert "decision" not in (out or {})
    assert s.state().cooldown_remaining_reviews == 4


def test_cooldown_counts_review_opportunities_not_turns(tmp_path, monkeypatch, capsys):
    # A session where only 1 in 10 Stops is risky must serve its cooldown in
    # risky Stops, not in raw turns -- a non-risky Stop never reaches the
    # gate in the first place, so it must not spend cooldown budget either.
    from crosier.errors import COOLDOWN_REVIEWS

    s = Session(tmp_path)

    def risky_turn():
        s.prompt("fix it")
        s.batch(name="Edit", target="parser.py")
        return _stop(s, monkeypatch, capsys, _Reviewer(None), answer="Fixed — all tests pass.")

    def quiet_turn(i):
        s.prompt(f"where is thing {i}?")
        s.batch(name="Read", target="parser.py")
        return _stop(s, monkeypatch, capsys, _Reviewer(FLAG), answer="In parser.py.")

    # Two risky failures in a row start the cooldown.
    risky_turn()
    risky_turn()
    state = s.state()
    assert state.cooldown_remaining_reviews == COOLDOWN_REVIEWS
    assert state.disabled_for_session is False

    # Nine quiet (non-risky) Stops for every risky one: none of them should
    # touch the cooldown counter, and the reviewer must never be called.
    reviewer = _Reviewer(FLAG)
    risky_stops = 0
    i = 0
    while state.cooldown_remaining_reviews > 0:
        for _ in range(9):
            i += 1
            quiet_turn(i)
            assert s.state().cooldown_remaining_reviews == state.cooldown_remaining_reviews
        s.prompt("fix it again")
        s.batch(name="Edit", target="parser.py")
        out = _stop(s, monkeypatch, capsys, reviewer, answer="Fixed — all tests pass.")
        assert reviewer.excerpts == []
        assert "decision" not in (out or {})
        risky_stops += 1
        state = s.state()

    assert risky_stops == COOLDOWN_REVIEWS
    assert state.cooldown_remaining_reviews == 0

    # The cooldown has been served: the very next risky Stop reaches the
    # reviewer again.
    s.prompt("fix it once more")
    s.batch(name="Edit", target="parser.py")
    retry_reviewer = _Reviewer(FLAG)
    out = _stop(s, monkeypatch, capsys, retry_reviewer, answer="Fixed — all tests pass.")
    assert len(retry_reviewer.excerpts) == 1
    assert out["decision"] == "block"


def test_a_successful_review_after_a_cooldown_resets_consecutive_failures(tmp_path, monkeypatch, capsys):
    s = _gated_turn(tmp_path)
    for _ in range(2):
        _stop(s, monkeypatch, capsys, _Reviewer(None))
        s.prompt("again")
        s.batch(name="Edit", target="parser.py")
    # Serve out the cooldown.
    for _ in range(5):
        _stop(s, monkeypatch, capsys, _Reviewer(FLAG))
        s.prompt("again")
        s.batch(name="Edit", target="parser.py")
    out = _stop(s, monkeypatch, capsys, _Reviewer(PROCEED))
    assert "decision" not in (out or {})
    state = s.state()
    assert state.consecutive_failures == 0
    assert state.total_failures == 2
    assert state.disabled_for_session is False


def test_hard_stop_after_max_total_failures_across_cooldowns(tmp_path, monkeypatch, capsys):
    from crosier.errors import COOLDOWN_REVIEWS, MAX_TOTAL_FAILURES

    s = _gated_turn(tmp_path)
    messages = []

    def fail_twice():
        for _ in range(2):
            out = _stop(s, monkeypatch, capsys, _Reviewer(None))
            if out and out.get("systemMessage"):
                messages.append(out["systemMessage"])
            s.prompt("again")
            s.batch(name="Edit", target="parser.py")

    def serve_cooldown():
        for _ in range(COOLDOWN_REVIEWS):
            _stop(s, monkeypatch, capsys, _Reviewer(FLAG))
            s.prompt("again")
            s.batch(name="Edit", target="parser.py")

    cycles = MAX_TOTAL_FAILURES // 2
    for cycle in range(cycles):
        fail_twice()
        state = s.state()
        if state.total_failures >= MAX_TOTAL_FAILURES:
            break
        serve_cooldown()

    state = s.state()
    assert state.total_failures == MAX_TOTAL_FAILURES
    assert state.disabled_for_session is True
    # Exactly one cooldown message and one hard-stop message across the
    # session -- never one per cooldown cycle.
    assert sum("pausing the next" in m for m in messages) == 1
    assert sum("off for the rest of this session" in m for m in messages) == 1
    # Once hard-stopped, later Stops are silent and never reach the reviewer.
    reviewer = _Reviewer(FLAG)
    assert _stop(s, monkeypatch, capsys, reviewer) is None
    assert reviewer.excerpts == []


def test_unsupported_interpreter_exits_nonzero_so_the_fallback_chain_advances(monkeypatch, capsys):
    # plugin.json runs `python3 X || python X || py -3 X`. That chain only
    # advances on a nonzero exit, so a hook that always exits 0 pins itself to
    # whatever `python3` happens to be - a 3.10 that cannot read .crosier.toml.
    monkeypatch.setattr(sys, "version_info", (3, 10, 20, "final", 0))
    monkeypatch.setattr(sys, "stdin", io.StringIO("{}"))
    assert hook.main() == 1
    assert capsys.readouterr().out == ""
