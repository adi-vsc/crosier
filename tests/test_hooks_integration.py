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
    assert s.fire("UserPromptSubmit", monkeypatch, capsys) is None
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
    assert out is None
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
    assert out is None
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
