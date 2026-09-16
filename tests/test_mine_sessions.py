"""Tests for benchmark.corpus.mine_sessions.

Builds small synthetic transcript fixtures under tmp_path (mimicking the real
~/.claude/projects/<project>/<session>.jsonl layout) and asserts on the mined
turns.jsonl / events.jsonl / summary.json output. No model or network calls.
"""

from __future__ import annotations

import json
from pathlib import Path

from benchmark.corpus import mine_sessions as ms


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")


def user_entry(text, is_meta=False, is_sidechain=False):
    return {
        "type": "user",
        "isSidechain": is_sidechain,
        "isMeta": is_meta,
        "message": {"role": "user", "content": text},
    }


def tool_result_entry(tool_use_id, content, is_sidechain=False):
    return {
        "type": "user",
        "isSidechain": is_sidechain,
        "message": {
            "role": "user",
            "content": [
                {"tool_use_id": tool_use_id, "type": "tool_result", "content": content}
            ],
        },
    }


def assistant_text_entry(text, is_sidechain=False):
    return {
        "type": "assistant",
        "isSidechain": is_sidechain,
        "message": {"role": "assistant", "content": [{"type": "text", "text": text}]},
    }


def assistant_tool_use_entry(name, tool_use_id, tool_input, is_sidechain=False):
    return {
        "type": "assistant",
        "isSidechain": is_sidechain,
        "message": {
            "role": "assistant",
            "content": [
                {"type": "tool_use", "id": tool_use_id, "name": name, "input": tool_input}
            ],
        },
    }


def task_notification_entry(tool_use_id, result_text, status="completed"):
    text = (
        f"<task-notification>\n<task-id>t1</task-id>\n"
        f"<tool-use-id>{tool_use_id}</tool-use-id>\n"
        f"<status>{status}</status>\n"
        f"<summary>done</summary>\n<result>{result_text}</result>\n</task-notification>"
    )
    return user_entry(text)


def run_miner(tmp_path: Path, project: str, session: str, rows: list[dict]) -> tuple[dict, Path]:
    root = tmp_path / "root"
    session_file = root / project / f"{session}.jsonl"
    write_jsonl(session_file, rows)
    out_dir = tmp_path / "out"
    summary = ms.mine(root, out_dir)
    return summary, out_dir


def read_jsonl(path: Path) -> list[dict]:
    with open(path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


# --- real prompt vs tool_result vs command/meta entries ------------------


def test_real_prompt_creates_a_turn(tmp_path):
    rows = [user_entry("please fix the bug"), assistant_text_entry("done")]
    summary, out_dir = run_miner(tmp_path, "proj", "s1", rows)
    turns = read_jsonl(out_dir / "turns.jsonl")
    assert len(turns) == 1
    assert turns[0]["user_text"] == "please fix the bug"
    assert turns[0]["turn"] == 0
    assert turns[0]["locator"] == "proj/s1.jsonl:1"


def test_tool_result_entry_is_not_a_turn(tmp_path):
    rows = [
        user_entry("do the thing"),
        tool_result_entry("tu1", "some output"),
    ]
    summary, out_dir = run_miner(tmp_path, "proj", "s2", rows)
    turns = read_jsonl(out_dir / "turns.jsonl")
    assert len(turns) == 1  # only the real prompt, not the tool_result row


def test_command_and_meta_entries_are_not_turns(tmp_path):
    rows = [
        user_entry("<command-name>/compact</command-name>"),
        user_entry("<local-command-stdout>ok</local-command-stdout>"),
        user_entry("<system-reminder>be careful</system-reminder>"),
        user_entry("real question meta", is_meta=True),
        user_entry("the real prompt"),
    ]
    summary, out_dir = run_miner(tmp_path, "proj", "s3", rows)
    turns = read_jsonl(out_dir / "turns.jsonl")
    assert len(turns) == 1
    assert turns[0]["user_text"] == "the real prompt"


def test_continuation_summary_is_flagged(tmp_path):
    rows = [user_entry("This session is being continued from a previous one.")]
    summary, out_dir = run_miner(tmp_path, "proj", "s4", rows)
    turns = read_jsonl(out_dir / "turns.jsonl")
    assert turns[0]["is_continuation"] is True


# --- Agent call linked to tool_result and to task-notification result ----


def test_agent_call_linked_to_tool_result(tmp_path):
    rows = [
        user_entry("please stress test this decision, cold read it"),
        assistant_tool_use_entry(
            "Agent",
            "tu_agent1",
            {"model": "sonnet", "description": "attack the plan", "prompt": "attack this and give a kill list"},
        ),
        tool_result_entry("tu_agent1", "kill list: nothing survives"),
    ]
    summary, out_dir = run_miner(tmp_path, "proj", "s5", rows)
    turns = read_jsonl(out_dir / "turns.jsonl")
    events = read_jsonl(out_dir / "events.jsonl")
    assert turns[0]["agent_calls"] == [{"model": "sonnet", "description": "attack the plan"}]
    attack_events = [e for e in events if e["label"] == "agent_attack_call"]
    assert len(attack_events) == 1
    assert attack_events[0]["result"] == "kill list: nothing survives"
    assert attack_events[0]["prompt"] == "attack this and give a kill list"


def test_agent_call_linked_to_task_notification_result(tmp_path):
    rows = [
        user_entry("spawn a subagent to attack this claim"),
        assistant_tool_use_entry(
            "Agent",
            "tu_agent2",
            {"model": "sonnet", "description": "adversarial review", "prompt": "attack the premise, zero-context"},
        ),
        tool_result_entry("tu_agent2", ""),
        task_notification_entry("tu_agent2", "the premise does not hold"),
    ]
    summary, out_dir = run_miner(tmp_path, "proj", "s6", rows)
    events = read_jsonl(out_dir / "events.jsonl")
    attack_events = [e for e in events if e["label"] == "agent_attack_call"]
    assert len(attack_events) == 1
    assert attack_events[0]["result"] == "the premise does not hold"


# --- test run with its result tail ----------------------------------------


def test_test_run_captures_command_and_result_tail(tmp_path):
    long_tail = "x" * 700 + "PASSED"
    rows = [
        user_entry("run the tests"),
        assistant_tool_use_entry("Bash", "tu_bash1", {"command": "py -3 -m pytest -q"}),
        tool_result_entry("tu_bash1", long_tail),
    ]
    summary, out_dir = run_miner(tmp_path, "proj", "s7", rows)
    turns = read_jsonl(out_dir / "turns.jsonl")
    assert len(turns[0]["test_runs"]) == 1
    test_run = turns[0]["test_runs"][0]
    assert test_run["command"] == "py -3 -m pytest -q"
    assert test_run["result_tail"] == long_tail[-600:]
    assert len(test_run["result_tail"]) == 600


# --- each label ------------------------------------------------------------


def test_user_attack_request_label(tmp_path):
    rows = [user_entry("can you spawn a subagent for a fresh eyes cold read on this?")]
    summary, out_dir = run_miner(tmp_path, "proj", "s8", rows)
    events = read_jsonl(out_dir / "events.jsonl")
    labels = [e["label"] for e in events]
    assert "user_attack_request" in labels


def test_user_correction_label(tmp_path):
    rows = [
        user_entry("first prompt"),
        assistant_text_entry("here is my answer"),
        user_entry("no, that's wrong, you missed the point"),
    ]
    summary, out_dir = run_miner(tmp_path, "proj", "s9", rows)
    events = read_jsonl(out_dir / "events.jsonl")
    labels = [e["label"] for e in events]
    assert "user_correction" in labels


def test_user_correction_skips_long_prompts(tmp_path):
    long_text = "you said this is wrong " + ("filler " * 600)
    rows = [user_entry(long_text)]
    summary, out_dir = run_miner(tmp_path, "proj", "s10", rows)
    events = read_jsonl(out_dir / "events.jsonl")
    assert all(e["label"] != "user_correction" for e in events)


def test_self_retraction_label(tmp_path):
    rows = [
        user_entry("is this claim true?"),
        assistant_text_entry("Correction: I was wrong, that claim does not hold."),
    ]
    summary, out_dir = run_miner(tmp_path, "proj", "s11", rows)
    events = read_jsonl(out_dir / "events.jsonl")
    labels = [e["label"] for e in events]
    assert "self_retraction" in labels


def test_agent_attack_call_requires_sonnet_model(tmp_path):
    rows = [
        user_entry("go"),
        assistant_tool_use_entry(
            "Agent", "tu_haiku", {"model": "haiku", "description": "x", "prompt": "attack this, cold review"}
        ),
        tool_result_entry("tu_haiku", "result"),
    ]
    summary, out_dir = run_miner(tmp_path, "proj", "s12", rows)
    events = read_jsonl(out_dir / "events.jsonl")
    assert all(e["label"] != "agent_attack_call" for e in events)


# --- skip-dirs ---------------------------------------------------------


def test_skip_dirs_are_excluded(tmp_path):
    root = tmp_path / "root"
    write_jsonl(root / "real-project" / "keep.jsonl", [user_entry("keep this one")])
    write_jsonl(root / "crosier-sessions-abc" / "drop.jsonl", [user_entry("drop this one")])
    write_jsonl(root / "some-probe-dir" / "drop2.jsonl", [user_entry("drop this too")])
    write_jsonl(root / "claude-mem-observer-x" / "drop3.jsonl", [user_entry("and this")])
    write_jsonl(root / "benchmark-sessions-y" / "drop4.jsonl", [user_entry("and this too")])

    out_dir = tmp_path / "out"
    summary = ms.mine(root, out_dir)
    turns = read_jsonl(out_dir / "turns.jsonl")
    assert len(turns) == 1
    assert turns[0]["user_text"] == "keep this one"
    assert summary["files_scanned"] == 1


# --- sidechain (subagent) transcripts are flagged, not counted as turns --


def test_sidechain_entries_do_not_create_turns_but_are_flagged_in_events(tmp_path):
    root = tmp_path / "root"
    write_jsonl(
        root / "proj" / "s13" / "subagents" / "agent-xyz.jsonl",
        [
            user_entry("attack this decision, give a kill list", is_sidechain=True),
            assistant_text_entry("I was wrong, this claim does not hold.", is_sidechain=True),
        ],
    )
    out_dir = tmp_path / "out"
    summary = ms.mine(root, out_dir)
    turns = read_jsonl(out_dir / "turns.jsonl")
    events = read_jsonl(out_dir / "events.jsonl")
    assert len(turns) == 0
    assert any(e["is_sidechain"] for e in events)
    assert all(e["turn"] is None for e in events if e["is_sidechain"])
    # a top-level subagent transcript file does not count toward "sessions"
    assert summary["sessions"] == 0


# --- final_text picks the last assistant text block before the next prompt -


def test_final_text_is_last_assistant_block_before_next_prompt(tmp_path):
    rows = [
        user_entry("turn zero"),
        assistant_text_entry("first draft"),
        assistant_text_entry("final answer for turn zero"),
        user_entry("turn one"),
        assistant_text_entry("answer for turn one"),
    ]
    summary, out_dir = run_miner(tmp_path, "proj", "s14", rows)
    turns = read_jsonl(out_dir / "turns.jsonl")
    assert turns[0]["final_text"] == "final answer for turn zero"
    assert turns[1]["final_text"] == "answer for turn one"


# --- consequential calls: git commit/push and Write ------------------------


def test_consequential_calls_capture_git_commit_and_write(tmp_path):
    rows = [
        user_entry("commit and write a file"),
        assistant_tool_use_entry("Bash", "tu1", {"command": "git commit -m 'x'"}),
        assistant_tool_use_entry("Write", "tu2", {"file_path": "foo.py", "content": "x"}),
    ]
    summary, out_dir = run_miner(tmp_path, "proj", "s15", rows)
    turns = read_jsonl(out_dir / "turns.jsonl")
    kinds = {c["kind"] for c in turns[0]["consequential_calls"]}
    assert kinds == {"bash", "write"}


def test_summary_json_has_required_fields(tmp_path):
    rows = [user_entry("hello")]
    summary, out_dir = run_miner(tmp_path, "proj", "s16", rows)
    for key in (
        "files_scanned",
        "sessions",
        "user_turns",
        "label_counts",
        "per_project",
        "bytes_read",
        "runtime_seconds",
    ):
        assert key in summary
    assert (out_dir / "summary.json").exists()
