"""Tests for benchmark.replay.replay_gate.

No real `claude` calls anywhere here: every reviewer call in these tests goes
through a stubbed `_real_run_claude`, so a test that forgets to stub it and
somehow reaches the real function will fail loudly (FileNotFoundError /
timeout) rather than spend money. Synthetic transcripts only, built in
tmp_path.
"""

import argparse
import json

import pytest

from benchmark.replay import replay_gate as rg
from crosier import verdict as verdict_mod
from crosier.errors import COOLDOWN_REVIEWS, MAX_CONSECUTIVE_FAILURES

pytestmark = pytest.mark.filterwarnings("ignore")


# --- transcript fixtures -----------------------------------------------------------


def user_prompt(text, ts=None):
    entry = {"type": "user", "message": {"role": "user", "content": text}}
    if ts is not None:
        entry["timestamp"] = ts
    return entry


def assistant_text(text, ts=None):
    entry = {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}}
    if ts is not None:
        entry["timestamp"] = ts
    return entry


def tool_use(name, tool_id, ts=None):
    entry = {
        "type": "assistant",
        "message": {"role": "assistant", "content": [{"type": "tool_use", "id": tool_id, "name": name, "input": {}}]},
    }
    if ts is not None:
        entry["timestamp"] = ts
    return entry


def tool_result(tool_id, text="ok", ts=None):
    entry = {
        "type": "user",
        "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": tool_id, "content": text}]},
    }
    if ts is not None:
        entry["timestamp"] = ts
    return entry


def interrupt(ts=None):
    # Real interrupt entries carry list content (a "text" block), not a bare
    # string -- that is what keeps crosier.gate._is_user_prompt (which
    # requires string content) from treating the marker as a new user prompt
    # and splitting the interrupted turn at it.
    entry = {
        "type": "user",
        "message": {"role": "user", "content": [{"type": "text", "text": "[Request interrupted by user for tool use]"}]},
    }
    if ts is not None:
        entry["timestamp"] = ts
    return entry


def write_transcript(path, entries):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(e) for e in entries) + "\n", encoding="utf-8")


def edit_cycle(i, answer="Fixed -- all tests pass."):
    """One risky turn: a prompt, an Edit, its result, and a claim-of-done answer."""
    return [
        user_prompt(f"fix thing {i}"),
        tool_use("Edit", f"e{i}"),
        tool_result(f"e{i}", "edited"),
        assistant_text(answer),
    ]


@pytest.fixture(autouse=True)
def _crosier_home(tmp_path, monkeypatch):
    monkeypatch.setenv("CROSIER_HOME", str(tmp_path / "crosier_home"))
    monkeypatch.delenv("CROSIER_DISABLED", raising=False)


def _hook_module(monkeypatch):
    hook_module = rg._load_hook_module()
    monkeypatch.setattr(hook_module, "read_transcript_lines", rg._patched_read_transcript_lines)
    return hook_module


def _recorder(tmp_path, dry_run=False, max_cost=None):
    return rg.CallRecorder(
        cache_dir=tmp_path / "cache", excerpts_dir=tmp_path / "excerpts", dry_run=dry_run, max_cost=max_cost
    )


PROCEED_ENVELOPE = {
    "result": "{}",
    "structured_output": {"status": "proceed", "confidence": "high"},
    "input_tokens": 100,
    "output_tokens": 10,
    "cost_usd": 0.001,
}


def _flag_envelope(evidence):
    return {
        "result": "{}",
        "structured_output": {
            "status": "flag",
            "confidence": "medium",
            "category": "unverified_claim",
            "flagged_claim": "tests pass",
            "reason": "no test run shown in the window",
            "suggested_check": "run pytest",
            "evidence": evidence,
        },
        "input_tokens": 500,
        "output_tokens": 60,
        "cost_usd": 0.01,
    }


# --- stop-point enumeration ---------------------------------------------------------


def test_enumerate_stop_points_skips_and_includes_eof():
    entries = [
        assistant_text("preamble before any real prompt"),  # 0: pre-first-prompt stretch
        user_prompt("do thing 1"),  # 1
        assistant_text("done with thing 1"),  # 2 -> real stop point
        user_prompt("do thing 2, will be interrupted"),  # 3
        assistant_text("working on thing 2"),  # 4
        interrupt(),  # 5 -> interrupted, skipped
        user_prompt("do thing 3, tool-only"),  # 6
        tool_use("Bash", "t1"),  # 7
        tool_result("t1", "output"),  # 8 -> no assistant text, skipped
        user_prompt("do thing 4"),  # 9
        assistant_text("done with thing 4"),  # 10 -> EOF stop point, real
    ]
    line_nos = list(range(1, len(entries) + 1))

    points = rg.enumerate_stop_points(entries, line_nos)

    assert [p["skip_reason"] for p in points] == [
        "before_first_prompt",
        None,
        "interrupted",
        "no_assistant_text",
        None,
    ]
    real_points = [p for p in points if p["skip_reason"] is None]
    assert real_points[0]["last_assistant_message"] == "done with thing 1"
    assert real_points[0]["turn_prompt_line"] == 2
    assert real_points[0]["next_prompt_line"] == 4
    # EOF stop point ends the transcript, so there is no next prompt line.
    assert real_points[1]["last_assistant_message"] == "done with thing 4"
    assert real_points[1]["cut"] == len(entries)
    assert real_points[1]["next_prompt_line"] is None


def test_until_cutoff_stops_reading_transcript(tmp_path):
    entries = [
        user_prompt("first", ts="2026-09-01T00:00:00Z"),
        assistant_text("answer one", ts="2026-09-01T00:00:01Z"),
        user_prompt("second", ts="2026-09-02T00:00:00Z"),
        assistant_text("answer two", ts="2026-09-02T00:00:01Z"),
    ]
    path = tmp_path / "session.jsonl"
    write_transcript(path, entries)

    all_entries, _ = rg.load_transcript(path, until=None)
    assert len(all_entries) == 4

    cut_entries, cut_lines = rg.load_transcript(path, until="2026-09-02T00:00:00Z")
    assert len(cut_entries) == 2
    assert cut_lines == [1, 2]


# --- the hook, not a reimplementation, decides --------------------------------------


def test_hook_really_decides_and_delivers_a_verified_flag(tmp_path, monkeypatch):
    hook_module = _hook_module(monkeypatch)
    entries = edit_cycle(1)
    line_nos = list(range(1, len(entries) + 1))

    envelope = _flag_envelope("Fixed -- all tests pass.")
    monkeypatch.setattr(rg, "_real_run_claude", lambda **kw: envelope)
    recorder = _recorder(tmp_path)
    monkeypatch.setattr(verdict_mod, "run_claude", recorder)

    rows = []
    finished = rg.process_session("s1", entries, line_nos, "six", hook_module, recorder, tmp_path / "scratch", rows.append)

    assert finished
    real_rows = [r for r in rows if r["skip_reason"] is None]
    assert len(real_rows) == 1
    row = real_rows[0]
    assert row["reviewed"] is True
    assert row["status"] == "flag"
    assert row["confidence"] == "medium"
    assert row["evidence_verified"] is True
    assert row["delivered"] is True
    assert row["trigger_reason"] is not None


def test_reviewer_failures_start_a_cooldown_that_suppresses_later_reviews(tmp_path, monkeypatch):
    hook_module = _hook_module(monkeypatch)
    entries = []
    for i in range(4):
        entries.extend(edit_cycle(i))
    line_nos = list(range(1, len(entries) + 1))

    monkeypatch.setattr(rg, "_real_run_claude", lambda **kw: None)
    recorder = _recorder(tmp_path)
    monkeypatch.setattr(verdict_mod, "run_claude", recorder)

    rows = []
    rg.process_session("s2", entries, line_nos, "six", hook_module, recorder, tmp_path / "scratch", rows.append)
    real_rows = [r for r in rows if r["skip_reason"] is None]
    assert len(real_rows) == 4

    # The first MAX_CONSECUTIVE_FAILURES reviews are attempted and fail (no
    # verdict -> no journal entry -> reviewed True, status None).
    assert MAX_CONSECUTIVE_FAILURES == 2
    for row in real_rows[:2]:
        assert row["reviewed"] is True
        assert row["status"] is None
        assert row["delivered"] is False
    # Past the threshold the cooldown skips the review opportunity entirely:
    # the reviewer is never even called.
    for row in real_rows[2:4]:
        assert row["reviewed"] is False


def test_budget_of_twelve_reviews_per_session_is_respected(tmp_path, monkeypatch):
    hook_module = _hook_module(monkeypatch)
    entries = []
    for i in range(14):
        entries.extend(edit_cycle(i))
    line_nos = list(range(1, len(entries) + 1))

    monkeypatch.setattr(rg, "_real_run_claude", lambda **kw: dict(PROCEED_ENVELOPE))
    recorder = _recorder(tmp_path)
    monkeypatch.setattr(verdict_mod, "run_claude", recorder)

    rows = []
    rg.process_session("s3", entries, line_nos, "six", hook_module, recorder, tmp_path / "scratch", rows.append)
    real_rows = [r for r in rows if r["skip_reason"] is None]
    assert len(real_rows) == 14
    assert sum(1 for r in real_rows if r["reviewed"]) == 12
    assert all(not r["reviewed"] for r in real_rows[12:])


# --- the "two" arm's prompt/schema surgery -------------------------------------------


def test_two_arm_prompt_drops_five_modes_and_keeps_two():
    prompt = rg.build_two_arm_prompt(verdict_mod.VERDICT_PROMPT_TEMPLATE)
    for name in rg.REMOVE_MODES:
        assert f"\n- {name}:" not in prompt
    for name in rg.KEEP_MODES:
        assert f"\n- {name}:" in prompt
    assert ", ".join(rg.KEEP_MODES) in prompt
    assert ", ".join(verdict_mod.CATEGORIES) not in prompt


def test_two_arm_prompt_removal_asserts_when_a_bullet_is_missing():
    broken_template = verdict_mod.VERDICT_PROMPT_TEMPLATE.replace("\n- loop:", "\n- lupe:")
    with pytest.raises(AssertionError):
        rg.build_two_arm_prompt(broken_template)


def test_two_arm_schema_reduces_the_category_enum():
    two_schema = rg.build_two_arm_schema(verdict_mod.VERDICT_SCHEMA)
    assert two_schema["properties"]["category"]["enum"] == ["unverified_claim", "ignored_correction", None]
    # The original schema (module-level, shared) must be untouched.
    assert set(verdict_mod.VERDICT_SCHEMA["properties"]["category"]["enum"]) == {*verdict_mod.CATEGORIES, None}


# --- reviewer call recorder + cache --------------------------------------------------


def test_cache_hit_does_not_call_the_underlying_function(tmp_path, monkeypatch):
    calls = []

    def fake_real(**kwargs):
        calls.append(kwargs)
        return dict(PROCEED_ENVELOPE)

    monkeypatch.setattr(rg, "_real_run_claude", fake_real)
    recorder = _recorder(tmp_path)

    kwargs = dict(system_prompt="sp", stdin_text="the excerpt", model="sonnet", timeout=10, json_schema={"a": 1}, effort="low")
    first = recorder(**kwargs)
    second = recorder(**kwargs)

    assert len(calls) == 1
    assert first == second


def test_dry_run_never_calls_the_real_function(tmp_path, monkeypatch):
    monkeypatch.setattr(rg, "_real_run_claude", lambda **kw: (_ for _ in ()).throw(AssertionError("dry run must not call the real function")))
    recorder = _recorder(tmp_path, dry_run=True)
    envelope = recorder(system_prompt="sp", stdin_text="the excerpt", model="sonnet", timeout=10, json_schema=None, effort="low")
    assert envelope["structured_output"]["status"] == "proceed"
    cache_path = tmp_path / "cache"
    assert list(cache_path.glob("*.json")) == []  # never cached


# --- resumability ----------------------------------------------------------------------


def _args(tmp_path, **over):
    base = dict(
        arm="six",
        out=tmp_path / "out",
        turns=tmp_path / "turns.jsonl",
        projects_root=tmp_path / "projects",
        until=None,
        workers=1,
        dry_run=True,
        max_cost=None,
        limit_sessions=None,
    )
    base.update(over)
    return argparse.Namespace(**base)


def _write_corpus(tmp_path, session, entries):
    project = "proj1"
    session_path = tmp_path / "projects" / project / f"{session}.jsonl"
    write_transcript(session_path, entries)
    turns = [{"session": session, "project": project, "turn": 0, "locator": f"{project}/{session}.jsonl:1"}]
    (tmp_path / "turns.jsonl").write_text("\n".join(json.dumps(t) for t in turns) + "\n", encoding="utf-8")


def test_resume_skips_a_session_already_completed(tmp_path, monkeypatch):
    _write_corpus(tmp_path, "sessA", edit_cycle(1))

    first = rg.run_arm(_args(tmp_path))
    assert first["sessions_processed"] == 1
    assert first["sessions_skipped_resume"] == 0
    rows_path = tmp_path / "out" / "six" / "rows.jsonl"
    first_row_count = sum(1 for _ in rows_path.open(encoding="utf-8"))

    second = rg.run_arm(_args(tmp_path))
    assert second["sessions_processed"] == 0
    assert second["sessions_skipped_resume"] == 1
    second_row_count = sum(1 for _ in rows_path.open(encoding="utf-8"))
    assert second_row_count == first_row_count


def test_resolve_sessions_reports_resolved_and_unresolved(tmp_path):
    _write_corpus(tmp_path, "sessA", edit_cycle(1))
    turns_path = tmp_path / "turns.jsonl"
    rows = [json.loads(l) for l in turns_path.read_text(encoding="utf-8").splitlines()]
    rows.append({"session": "missing", "project": "proj1", "turn": 0, "locator": "proj1/missing.jsonl:1"})
    turns_path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")

    resolved, unresolved = rg.resolve_sessions(turns_path, tmp_path / "projects")
    assert list(resolved) == ["sessA"]
    assert unresolved == ["missing"]
