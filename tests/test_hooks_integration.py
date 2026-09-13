"""End-to-end through the single hook entrypoint, with the detached worker
stubbed out. The transcript fixtures mimic the real JSONL shape: a user prompt
is a string-content user entry, a model call is an assistant entry holding a
tool_use, and its result is a user entry holding a tool_result."""

import importlib.util
import io
import json
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from crosier.pending import read_result, write_result
from crosier.spawn import marker_path
from crosier.state import load_state
from crosier.trigger import request_check


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

    It is `systemMessage` and nothing else: no `hookSpecificOutput`, so it
    never reaches the agent and never continues a turn at Stop.
    """
    return (
        isinstance(out, dict)
        and set(out) == {"systemMessage"}
        and out["systemMessage"].startswith("Crosier active")
    )


def _capturing_spawn(jobs):
    def fake_spawn(session_id, job):
        jobs.append(job)
        return True

    return fake_spawn


def _answering_spawn(jobs, verdict, ok=True):
    """A worker that has already finished by the time the next hook fires."""

    def fake_spawn(session_id, job):
        jobs.append(job)
        result = {
            "ok": ok,
            "verdict": verdict if ok else None,
            "turn_number": job["turn_number"],
            "transcript_index": job["transcript_index"],
            "context_tokens": job["context_tokens"],
            "created_at": time.time(),
        }
        if not ok:
            result["error"] = "verdict failed"
        write_result(session_id, result)
        return True

    return fake_spawn


def _config(session, **values):
    body = "\n".join(f"{k} = {json.dumps(v)}" for k, v in values.items())
    (session.root / ".crosier.toml").write_text(f"[crosier]\n{body}\n", encoding="utf-8")


# --- triggering ------------------------------------------------------------------


def test_hook_is_silent_below_every_threshold(tmp_path, monkeypatch, capsys):
    s = Session(tmp_path)
    s.prompt("goal")
    # The first invocation of a session carries the one activation line; every
    # invocation after it is silent until a verdict is actually ready.
    assert _activation_only(s.fire("UserPromptSubmit", monkeypatch, capsys))
    s.batch()
    assert s.fire("PostToolBatch", monkeypatch, capsys) is None


def test_post_tool_batch_dispatches_at_the_call_threshold_without_waiting(tmp_path, monkeypatch, capsys):
    # An autonomous turn is hundreds of model calls on one user prompt. The
    # check has to fire on calls, from inside the turn, and never block it.
    jobs = []
    s = Session(tmp_path)
    _config(s, call_threshold=5, min_calls_between_checks=1)
    s.prompt("build the importer")
    s.fire("UserPromptSubmit", monkeypatch, capsys)
    with patch("crosier.pipeline.spawn_worker", _capturing_spawn(jobs)):
        for _ in range(5):
            s.batch()
            out = s.fire("PostToolBatch", monkeypatch, capsys)
    assert out is None
    assert len(jobs) == 1
    assert jobs[0]["excerpt"].startswith("[goal] build the importer")
    assert "[tool_use #5 Read]" in jobs[0]["excerpt"]
    assert "transcript_path" not in jobs[0]
    assert s.state().calls_since_check == 0


def test_user_prompt_submit_dispatches_at_the_turn_threshold(tmp_path, monkeypatch, capsys):
    jobs = []
    s = Session(tmp_path)
    _config(s, turn_threshold=3)
    with patch("crosier.pipeline.spawn_worker", _capturing_spawn(jobs)):
        for i in range(3):
            s.prompt(f"ask {i}")
            s.fire("UserPromptSubmit", monkeypatch, capsys)
    assert len(jobs) == 1
    assert jobs[0]["turn_number"] == 3


def test_context_growth_triggers_and_rebases_after_compaction(tmp_path, monkeypatch, capsys):
    # Compaction appends to the transcript and the reported context drops.
    # Growth must be measured from the post-compaction size, not wait until
    # the session climbs back past the old peak.
    jobs = []
    s = Session(tmp_path)
    _config(s, token_threshold=40_000, min_calls_between_checks=1, call_threshold=10_000)
    s.prompt("goal")
    s.fire("UserPromptSubmit", monkeypatch, capsys)
    with patch("crosier.pipeline.spawn_worker", _capturing_spawn(jobs)):
        s.batch(context=200_000)
        s.fire("PostToolBatch", monkeypatch, capsys)
        assert len(jobs) == 1
        assert s.state().tokens_at_last_check == 200_003
        s.batch(context=85_000)  # compacted
        s.fire("PostToolBatch", monkeypatch, capsys)
        assert len(jobs) == 1
        assert s.state().tokens_at_last_check == 85_003
        s.batch(context=130_000)
        s.fire("PostToolBatch", monkeypatch, capsys)
    assert len(jobs) == 2


def test_pre_compact_dispatches_regardless_of_thresholds(tmp_path, monkeypatch, capsys):
    jobs = []
    s = Session(tmp_path)
    s.prompt("goal")
    s.batch()
    with patch("crosier.pipeline.spawn_worker", _capturing_spawn(jobs)):
        s.fire("PreCompact", monkeypatch, capsys, trigger="auto")
    assert len(jobs) == 1
    assert "[goal] goal" in jobs[0]["excerpt"]


def test_stop_dispatches_only_when_a_threshold_is_met(tmp_path, monkeypatch, capsys):
    jobs = []
    s = Session(tmp_path)
    _config(s, call_threshold=3, min_calls_between_checks=1)
    s.prompt("goal")
    with patch("crosier.pipeline.spawn_worker", _capturing_spawn(jobs)):
        s.batch()
        s.fire("Stop", monkeypatch, capsys, stop_hook_active=False)
        assert jobs == []
        s.batch()
        s.fire("PostToolBatch", monkeypatch, capsys)
        s.batch()
        s.fire("PostToolBatch", monkeypatch, capsys)
    assert len(jobs) == 1


# --- delivery --------------------------------------------------------------------


def test_flag_is_injected_mid_turn_at_the_next_batch(tmp_path, monkeypatch, capsys):
    # The whole point: the fix reaches the agent before the degraded answer
    # reaches the user, not at the user's next prompt.
    jobs = []
    s = Session(tmp_path)
    _config(s, call_threshold=2, min_calls_between_checks=1)
    s.prompt("goal")
    with patch("crosier.pipeline.spawn_worker", _answering_spawn(jobs, FLAG)):
        s.batch()
        s.fire("PostToolBatch", monkeypatch, capsys)
        s.batch()
        during = s.fire("PostToolBatch", monkeypatch, capsys)
        s.batch()
        after = s.fire("PostToolBatch", monkeypatch, capsys)
    assert during is None
    assert after["hookSpecificOutput"]["hookEventName"] == "PostToolBatch"
    assert "risky assumption" in after["hookSpecificOutput"]["additionalContext"]
    assert "do not justify earlier turns" in after["hookSpecificOutput"]["additionalContext"]
    assert "risky assumption" in after["systemMessage"]
    assert s.state().last_flag == "risky assumption"


def test_the_next_review_is_told_about_the_previous_flag(tmp_path, monkeypatch, capsys):
    jobs = []
    s = Session(tmp_path)
    _config(s, call_threshold=2, min_calls_between_checks=1)
    s.prompt("goal")
    with patch("crosier.pipeline.spawn_worker", _answering_spawn(jobs, FLAG)):
        for _ in range(5):
            s.batch()
            s.fire("PostToolBatch", monkeypatch, capsys)
    assert len(jobs) >= 2
    assert jobs[0].get("previous_flag") is None
    assert jobs[-1]["previous_flag"] == "risky assumption"


def test_clean_verdict_is_shown_to_the_user_but_not_the_agent(tmp_path, monkeypatch, capsys):
    jobs = []
    s = Session(tmp_path)
    _config(s, call_threshold=1, min_calls_between_checks=1)
    s.prompt("goal")
    with patch("crosier.pipeline.spawn_worker", _answering_spawn(jobs, PROCEED)):
        s.batch()
        s.fire("PostToolBatch", monkeypatch, capsys)
        s.batch()
        out = s.fire("PostToolBatch", monkeypatch, capsys)
    assert "no issues" in out["systemMessage"]
    assert "hookSpecificOutput" not in out


def test_user_prompt_submit_still_delivers_a_waiting_verdict(tmp_path, monkeypatch, capsys):
    s = Session(tmp_path)
    s.prompt("goal")
    write_result("s1", {"ok": True, "verdict": FLAG, "turn_number": 1, "transcript_index": 1, "created_at": time.time()})
    out = s.fire("UserPromptSubmit", monkeypatch, capsys)
    assert out["hookSpecificOutput"]["hookEventName"] == "UserPromptSubmit"
    assert "risky assumption" in out["hookSpecificOutput"]["additionalContext"]


def test_stop_delivers_a_flag_so_the_agent_acts_before_the_answer_stands(tmp_path, monkeypatch, capsys):
    s = Session(tmp_path)
    s.prompt("goal")
    s.batch()
    write_result("s1", {"ok": True, "verdict": FLAG, "turn_number": 1, "transcript_index": 3, "created_at": time.time()})
    out = s.fire("Stop", monkeypatch, capsys, stop_hook_active=False)
    assert out["hookSpecificOutput"]["hookEventName"] == "Stop"
    assert "risky assumption" in out["hookSpecificOutput"]["additionalContext"]
    assert read_result("s1") is None


def test_stop_never_continues_the_turn_for_a_clean_verdict(tmp_path, monkeypatch, capsys):
    s = Session(tmp_path)
    s.prompt("goal")
    write_result("s1", {"ok": True, "verdict": PROCEED, "turn_number": 1, "transcript_index": 1, "created_at": time.time()})
    out = s.fire("Stop", monkeypatch, capsys, stop_hook_active=False)
    assert out is None or "hookSpecificOutput" not in out


def test_stop_leaves_the_result_alone_while_already_continuing(tmp_path, monkeypatch, capsys):
    # stop_hook_active means Claude is continuing because a stop hook already
    # asked it to. Injecting again here is how a hook loops eight times.
    s = Session(tmp_path)
    s.prompt("goal")
    write_result("s1", {"ok": True, "verdict": FLAG, "turn_number": 1, "transcript_index": 1, "created_at": time.time()})
    out = s.fire("Stop", monkeypatch, capsys, stop_hook_active=True)
    # Only the activation line, which is user-channel: nothing continues the turn.
    assert _activation_only(out)
    assert read_result("s1") is not None


def test_pre_compact_does_not_consume_a_waiting_result(tmp_path, monkeypatch, capsys):
    # PreCompact stdout is not injected into context. Consuming here would
    # clear the result and lose the verdict; it is delivered after compaction.
    jobs = []
    s = Session(tmp_path)
    s.prompt("goal")
    write_result("s1", {"ok": True, "verdict": FLAG, "turn_number": 1, "transcript_index": 1, "created_at": time.time()})
    with patch("crosier.pipeline.spawn_worker", _capturing_spawn(jobs)):
        out = s.fire("PreCompact", monkeypatch, capsys, trigger="manual")
    assert _activation_only(out)
    assert read_result("s1") is not None


def test_stale_verdict_is_discarded_rather_than_announced(tmp_path, monkeypatch, capsys):
    s = Session(tmp_path)
    s.prompt("goal")
    for _ in range(100):
        s.batch()
    write_result("s1", {"ok": True, "verdict": FLAG, "turn_number": 1, "transcript_index": 3, "created_at": time.time()})
    out = s.fire("PostToolBatch", monkeypatch, capsys)
    assert "hookSpecificOutput" not in out
    assert "stale" in out["systemMessage"]


def test_low_confidence_flag_does_not_interrupt(tmp_path, monkeypatch, capsys):
    s = Session(tmp_path)
    s.prompt("goal")
    write_result("s1", {"ok": True, "verdict": {**FLAG, "confidence": "low"}, "turn_number": 1, "transcript_index": 1, "created_at": time.time()})
    out = s.fire("PostToolBatch", monkeypatch, capsys)
    assert "hookSpecificOutput" not in out


def test_consumed_result_is_not_announced_twice(tmp_path, monkeypatch, capsys):
    s = Session(tmp_path)
    s.prompt("goal")
    write_result("s1", {"ok": True, "verdict": PROCEED, "turn_number": 1, "transcript_index": 1, "created_at": time.time()})
    first = s.fire("PostToolBatch", monkeypatch, capsys)
    second = s.fire("PostToolBatch", monkeypatch, capsys)
    assert "no issues" in first["systemMessage"]
    assert second is None
    assert read_result("s1") is None


# --- guards ------------------------------------------------------------------------


def test_workers_do_not_stack(tmp_path, monkeypatch, capsys):
    jobs = []
    s = Session(tmp_path)
    _config(s, call_threshold=1, min_calls_between_checks=1)
    marker = marker_path("s1")
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("999", encoding="utf-8")
    s.prompt("goal")
    with patch("crosier.pipeline.spawn_worker", _capturing_spawn(jobs)):
        for _ in range(5):
            s.batch()
            s.fire("PostToolBatch", monkeypatch, capsys)
    assert jobs == []


def test_budget_stops_checks_for_the_session(tmp_path, monkeypatch, capsys):
    jobs = []
    s = Session(tmp_path)
    _config(s, call_threshold=1, min_calls_between_checks=1, max_checks_per_session=3)
    s.prompt("goal")
    with patch("crosier.pipeline.spawn_worker", _answering_spawn(jobs, PROCEED)):
        for _ in range(12):
            s.batch()
            s.fire("PostToolBatch", monkeypatch, capsys)
    assert len(jobs) == 3


def test_failed_check_keeps_the_window_and_backs_off_after_two(tmp_path, monkeypatch, capsys, _home):
    jobs = []
    s = Session(tmp_path)
    _config(s, call_threshold=1, min_calls_between_checks=1)
    s.prompt("goal")
    outs = []
    with patch("crosier.pipeline.spawn_worker", _answering_spawn(jobs, None, ok=False)):
        for _ in range(4):
            s.batch()
            outs.append(s.fire("PostToolBatch", monkeypatch, capsys))
    state = s.state()
    # The delta was never reviewed, so the read cursor must not skip past it.
    assert state.last_line_index == 0
    assert state.disabled_for_session is True
    assert any(o and "disabled" in o.get("systemMessage", "") for o in outs)
    assert str(_home / "errors.log") in next(o["systemMessage"] for o in outs if o and "disabled" in o.get("systemMessage", ""))


def test_unrecognized_transcript_format_backs_off(tmp_path, monkeypatch, capsys):
    jobs = []
    s = Session(tmp_path)
    s.add([{"kind": "v2-event", "payload": {"text": "hi"}} for _ in range(8)])
    with patch("crosier.pipeline.spawn_worker", _capturing_spawn(jobs)):
        for _ in range(3):
            s.fire("PostToolBatch", monkeypatch, capsys)
    assert jobs == []
    assert s.state().disabled_for_session is True


def test_disabled_config_produces_no_output_and_no_state(tmp_path, monkeypatch, capsys):
    s = Session(tmp_path)
    _config(s, enabled=False)
    s.prompt("goal")
    assert s.fire("UserPromptSubmit", monkeypatch, capsys) is None
    assert s.state().total_turns == 0


def test_malformed_payload_is_ignored(monkeypatch, capsys):
    monkeypatch.setattr(sys, "stdin", io.StringIO("not json"))
    assert hook.main() == 0
    assert capsys.readouterr().out == ""


def test_unknown_event_is_a_no_op(tmp_path, monkeypatch, capsys):
    s = Session(tmp_path)
    s.prompt("goal")
    assert s.fire("SessionStart", monkeypatch, capsys) is None
    assert s.state().total_turns == 0


# --- control surface: kill switch, activation, on-demand check ------------------


def test_env_kill_switch_makes_the_hook_do_nothing_at_all(tmp_path, monkeypatch, capsys):
    # One session off, no file, no restart. It has to cost nothing: no output,
    # no state file, no dispatch — otherwise "disabled" is not disabled.
    jobs = []
    s = Session(tmp_path)
    _config(s, call_threshold=1, min_calls_between_checks=0)
    s.prompt("goal")
    monkeypatch.setenv("CROSIER_DISABLED", "1")
    with patch("crosier.pipeline.spawn_worker", _capturing_spawn(jobs)):
        for _ in range(5):
            s.batch()
            assert s.fire("PostToolBatch", monkeypatch, capsys) is None
    assert jobs == []
    assert not (tmp_path / "home" / "state").exists()


def test_kill_switch_off_values_leave_crosier_running(tmp_path, monkeypatch, capsys):
    s = Session(tmp_path)
    s.prompt("goal")
    monkeypatch.setenv("CROSIER_DISABLED", "0")
    assert _activation_only(s.fire("UserPromptSubmit", monkeypatch, capsys))


def test_activation_line_is_announced_once_per_session(tmp_path, monkeypatch, capsys):
    s = Session(tmp_path)
    s.prompt("goal")
    assert _activation_only(s.fire("UserPromptSubmit", monkeypatch, capsys))
    assert s.state().announced_activation is True
    for _ in range(3):
        s.batch()
        assert s.fire("PostToolBatch", monkeypatch, capsys) is None


def test_activation_line_is_silent_when_disabled_in_config(tmp_path, monkeypatch, capsys):
    s = Session(tmp_path)
    _config(s, enabled=False)
    s.prompt("goal")
    assert s.fire("UserPromptSubmit", monkeypatch, capsys) is None


def test_activation_line_shares_the_slot_with_a_waiting_verdict(tmp_path, monkeypatch, capsys):
    # A hook prints one JSON object. If a verdict lands on the same invocation
    # as the activation line, neither may silently drop the other.
    s = Session(tmp_path)
    s.prompt("goal")
    write_result("s1", {"ok": True, "verdict": FLAG, "turn_number": 1, "transcript_index": 1, "created_at": time.time()})
    out = s.fire("UserPromptSubmit", monkeypatch, capsys)
    assert "Crosier active" in out["systemMessage"]
    assert "risky assumption" in out["systemMessage"]
    assert "risky assumption" in out["hookSpecificOutput"]["additionalContext"]


def test_a_requested_check_dispatches_below_every_threshold(tmp_path, monkeypatch, capsys):
    # The point of `crosier check`: a new user sees Crosier work on their first
    # session instead of waiting for a threshold that may never trip.
    jobs = []
    s = Session(tmp_path)
    _config(s, call_threshold=999, turn_threshold=999, min_calls_between_checks=999)
    s.prompt("goal")
    s.batch()
    request_check()
    with patch("crosier.pipeline.spawn_worker", _capturing_spawn(jobs)):
        s.fire("PostToolBatch", monkeypatch, capsys)
    assert len(jobs) == 1
    assert s.state().checks_run == 1


def test_a_requested_check_is_consumed_and_does_not_repeat(tmp_path, monkeypatch, capsys):
    jobs = []
    s = Session(tmp_path)
    _config(s, call_threshold=999, turn_threshold=999, min_calls_between_checks=999)
    s.prompt("goal")
    request_check()
    with patch("crosier.pipeline.spawn_worker", _capturing_spawn(jobs)):
        s.batch()
        s.fire("PostToolBatch", monkeypatch, capsys)
        s.batch()
        s.fire("PostToolBatch", monkeypatch, capsys)
    assert len(jobs) == 1


def test_a_requested_check_still_obeys_the_session_budget(tmp_path, monkeypatch, capsys):
    # "Check now" overrides the threshold, not the guards: an unbounded manual
    # trigger is an unbounded bill.
    jobs = []
    s = Session(tmp_path)
    _config(s, max_checks_per_session=0)
    s.prompt("goal")
    s.batch()
    request_check()
    with patch("crosier.pipeline.spawn_worker", _capturing_spawn(jobs)):
        s.fire("PostToolBatch", monkeypatch, capsys)
    assert jobs == []


def test_first_check_of_a_session_fires_before_the_steady_state_threshold(tmp_path, monkeypatch, capsys):
    # Early premature commitments are where multi-turn sessions lose the plot,
    # so the first check does not wait for the ordinary cadence.
    jobs = []
    s = Session(tmp_path)
    _config(s, call_threshold=30, first_check_call_threshold=12, min_calls_between_checks=8)
    s.prompt("build the importer")
    s.fire("UserPromptSubmit", monkeypatch, capsys)
    with patch("crosier.pipeline.spawn_worker", _capturing_spawn(jobs)):
        for _ in range(11):
            s.batch()
            s.fire("PostToolBatch", monkeypatch, capsys)
        assert jobs == []
        s.batch()
        s.fire("PostToolBatch", monkeypatch, capsys)
    assert len(jobs) == 1


def test_a_completed_check_is_written_to_the_journal(tmp_path, monkeypatch, capsys):
    from crosier.journal import read_journal

    s = Session(tmp_path)
    s.prompt("goal")
    s.fire("UserPromptSubmit", monkeypatch, capsys)
    write_result("s1", {"ok": True, "verdict": FLAG, "turn_number": 2, "transcript_index": 1,
                        "context_tokens": 51000, "created_at": time.time()})
    s.batch()
    s.fire("PostToolBatch", monkeypatch, capsys)
    entries = read_journal("s1")
    assert len(entries) == 1
    assert entries[0]["category"] == "unverified_claim"
    assert entries[0]["delivered_to_agent"] is True
    assert entries[0]["context_tokens"] == 51000


# --- stop gate: a synchronous review of the final answer -------------------------


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
    _config(s, stop_gate=True, **config)
    s.prompt("fix the parser")
    s.batch(name="Edit", target="parser.py")
    return s


def _stop(s, monkeypatch, capsys, reviewer, answer="Fixed — all tests pass.", active=False):
    with patch("crosier.pipeline.generate_verdict", reviewer):
        return s.fire("Stop", monkeypatch, capsys, stop_hook_active=active, last_assistant_message=answer)


def test_stop_gate_is_off_by_default(tmp_path, monkeypatch, capsys):
    s = Session(tmp_path)
    s.prompt("fix the parser")
    s.batch(name="Edit", target="parser.py")
    reviewer = _Reviewer(FLAG)
    out = _stop(s, monkeypatch, capsys, reviewer)
    assert reviewer.excerpts == []
    assert "decision" not in (out or {})


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
    _config(s, stop_gate=True)
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
    assert entries[0]["delivered_to_agent"] is True


def test_stop_gate_steps_aside_when_a_waiting_flag_already_continues_the_turn(tmp_path, monkeypatch, capsys):
    s = _gated_turn(tmp_path)
    write_result("s1", {"ok": True, "verdict": FLAG, "turn_number": 1, "transcript_index": 3, "created_at": time.time()})
    reviewer = _Reviewer(FLAG)
    out = _stop(s, monkeypatch, capsys, reviewer)
    assert reviewer.excerpts == []
    assert "risky assumption" in out["hookSpecificOutput"]["additionalContext"]
    assert "decision" not in out


def test_unsupported_interpreter_exits_nonzero_so_the_fallback_chain_advances(monkeypatch, capsys):
    # plugin.json runs `python3 X || python X || py -3 X`. That chain only
    # advances on a nonzero exit, so a hook that always exits 0 pins itself to
    # whatever `python3` happens to be - a 3.10 that cannot read .crosier.toml.
    monkeypatch.setattr(sys, "version_info", (3, 10, 20, "final", 0))
    monkeypatch.setattr(sys, "stdin", io.StringIO("{}"))
    assert hook.main() == 1
    assert capsys.readouterr().out == ""
