# tests/test_digest.py
"""The digest is built mechanically, not by a model. A summarising model that
reads the agent's own framing inherits it — "tests pass" becomes a fact in the
summary — and a reviewer cannot check quotes against a summary. So the
compression here is structural: who said what, what ran, what came back, with
the noise cut and the goal pinned on top."""

from crosier.digest import (
    EXCERPT_CHAR_CAP,
    _head_tail,
    GOAL_CHAR_CAP,
    TOOL_RESULT_HEAD,
    TOOL_RESULT_TAIL,
    build_excerpt,
)


def _user(text, **extra):
    return {"type": "user", "message": {"role": "user", "content": text}, **extra}


def _assistant(text):
    return {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}}


def _tool_use(name, tool_input, tool_id="toolu_1"):
    return {
        "type": "assistant",
        "message": {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": tool_id, "name": name, "input": tool_input}],
        },
    }


def _tool_result(text, tool_id="toolu_1", is_error=False):
    block = {"type": "tool_result", "tool_use_id": tool_id, "content": text}
    if is_error:
        block["is_error"] = True
    return {"type": "user", "message": {"role": "user", "content": [block]}}


def test_excerpt_labels_every_speaker_in_order():
    lines = [
        _user("fix the login bug"),
        _assistant("I will read the auth module."),
        _tool_use("Read", {"file_path": "auth.py"}),
        _tool_result("def login(): ..."),
    ]
    out = build_excerpt(lines, since_index=0)
    i_user = out.index("[user] fix the login bug")
    i_asst = out.index("[assistant] I will read the auth module.")
    i_use = out.index("[tool_use #1 Read]")
    i_res = out.index("[tool_result #1")
    assert i_user < i_asst < i_use < i_res
    assert '"file_path": "auth.py"' in out
    assert "def login(): ..." in out


def test_tool_results_are_cut_to_head_and_tail_with_their_true_size():
    # One `Read` of a large file used to fill the whole 40k window on its own,
    # pushing out the assistant text a drift reviewer actually needs.
    body = "H" * 2000 + "M" * 5000 + "T" * 2000
    lines = [_tool_use("Read", {"file_path": "big.py"}), _tool_result(body)]
    out = build_excerpt(lines, since_index=0)
    assert f"[tool_result #1 | {len(body)} chars]" in out
    assert "H" * TOOL_RESULT_HEAD in out
    assert "T" * TOOL_RESULT_TAIL in out
    assert "M" * 100 not in out
    assert "…" in out


def test_failed_tool_results_are_marked():
    lines = [_tool_use("Bash", {"command": "pytest"}), _tool_result("1 failed", is_error=True)]
    assert "[tool_result #1 ERROR" in build_excerpt(lines, since_index=0)


def test_goal_is_pinned_on_top_even_when_the_window_starts_later():
    # After the first check the excerpt is delta-only. Without the original
    # request in view, "off-goal" is not a judgement a reviewer can make.
    lines = [_user("Build a CSV importer with tests"), _assistant("ok"), _user("now add logging"), _assistant("adding")]
    out = build_excerpt(lines, since_index=2)
    assert out.startswith("[goal] Build a CSV importer with tests")
    assert "[user] now add logging" in out
    assert "[assistant] ok" not in out


def test_goal_skips_meta_and_command_echoes():
    lines = [
        _user("<local-command-caveat>Caveat: ...</local-command-caveat>", isMeta=True),
        _user("<command-name>/model</command-name>\n<command-message>model</command-message>"),
        _user("real request here"),
    ]
    assert build_excerpt(lines, since_index=0).startswith("[goal] real request here")


def test_goal_is_capped():
    lines = [_user("g" * 5000), _assistant("x")]
    first_line = build_excerpt(lines, since_index=0).splitlines()[0]
    assert len(first_line) <= GOAL_CHAR_CAP + len("[goal] …")


def test_latest_user_instruction_is_carried_when_the_window_has_none():
    # A long autonomous turn: the last thing the user said is far above the
    # window, but it is the standard the work is measured against.
    lines = [_user("goal"), _user("stop using mocks, use the real client")]
    lines += [_tool_use("Edit", {"file_path": f"f{i}.py"}, tool_id=f"t{i}") for i in range(5)]
    out = build_excerpt(lines, since_index=2)
    assert "[latest user instruction] stop using mocks, use the real client" in out


def test_latest_user_instruction_is_not_duplicated_when_already_in_window():
    lines = [_user("goal"), _assistant("a"), _user("second ask"), _assistant("b")]
    out = build_excerpt(lines, since_index=1)
    assert out.count("second ask") == 1
    assert "[user] second ask" in out


def test_noise_entries_are_dropped():
    lines = [
        _user("goal"),
        {"type": "attachment", "attachment": {"type": "hook_success", "content": "HOOK TEXT"}},
        {"type": "system", "subtype": "turn_duration", "durationMs": 5},
        _user("<local-command-stdout>Login interrupted</local-command-stdout>"),
        _user("<local-command-caveat>Caveat</local-command-caveat>", isMeta=True),
        _assistant("SIDE"),
    ]
    lines[-1]["isSidechain"] = True
    out = build_excerpt(lines, since_index=0)
    assert "HOOK TEXT" not in out
    assert "Login interrupted" not in out
    assert "Caveat" not in out
    assert "SIDE" not in out


def test_slash_commands_are_shown_as_user_commands():
    lines = [_user("goal"), _user("<command-name>/compact</command-name>\n<command-message>compact</command-message>\n<command-args></command-args>")]
    assert "[user command] /compact" in build_excerpt(lines, since_index=0)


def test_compaction_summary_is_labelled():
    lines = [_user("goal"), _user("This session is being continued...", isCompactSummary=True)]
    assert "[compaction summary] This session is being continued..." in build_excerpt(lines, since_index=0)


def test_window_keeps_the_most_recent_records_under_the_cap():
    lines = [_user("goal")] + [_assistant(f"step {i} " + "x" * 3000) for i in range(30)]
    out = build_excerpt(lines, since_index=0)
    assert len(out) <= EXCERPT_CHAR_CAP + GOAL_CHAR_CAP + 200
    assert "step 29 " in out
    assert "step 0 " not in out
    assert out.startswith("[goal] goal")


def test_tool_use_arguments_are_capped():
    lines = [_tool_use("Write", {"file_path": "a.py", "content": "c" * 10_000})]
    out = build_excerpt(lines, since_index=0)
    assert "c" * 1000 not in out
    assert '"file_path": "a.py"' in out


def test_empty_window_yields_empty_string():
    assert build_excerpt([], since_index=0) == ""


def test_goal_inside_a_skill_invocation_is_the_command_args():
    # A prompt typed as `/skill the real request` arrives wrapped in
    # <command-message>/<command-name>/<command-args>. The request is the args.
    wrapped = (
        "<command-message>superpowers:using-superpowers</command-message>\n"
        "<command-name>/superpowers:using-superpowers</command-name>\n"
        "<command-args>build the importer with tests</command-args>"
    )
    lines = [_user(wrapped), _assistant("ok")]
    out = build_excerpt(lines, since_index=0)
    assert out.startswith("[goal] build the importer with tests")
    assert "[user] build the importer with tests" in out
    assert "<command-message>" not in out


def test_bare_slash_command_is_a_user_command_not_a_request():
    lines = [_user("real goal"), _user("/compact")]
    out = build_excerpt(lines, since_index=0)
    assert "[user command] /compact" in out
    assert "[user] /compact" not in out


def test_a_test_summary_survives_the_cut_behind_trailing_noise():
    """The line an unverified_claim turns on sits at the tail of a run's
    output, and runners bury it: npm appends its own error block after jest's
    summary. A cut that keeps only npm's boilerplate leaves the reviewer with
    nothing to decide the claim against."""
    noise = "\n".join(f"  PASS src/mod{i}.test.js" for i in range(40))
    tail = """
Tests:       2 failed, 48 passed, 50 total
Snapshots:   0 total
Ran all test suites.
npm ERR! code ELIFECYCLE
npm ERR! errno 1
npm ERR! app@1.0.0 test: jest --coverage
npm ERR! Exit status 1
npm ERR! Failed at the app@1.0.0 test script.
npm ERR! A complete log of this run can be found in:
npm ERR!     /home/u/.npm/_logs/2026-09-07T10_00_00_000Z-debug-0.log"""
    assert "Tests:       2 failed, 48 passed, 50 total" in _head_tail(noise + tail)


def test_a_short_result_is_never_cut_at_all():
    # Only results longer than head+tail are touched, so the wider tail costs
    # nothing on the ordinary short result that makes up most of a window.
    assert _head_tail("200 passed in 1.34s") == "200 passed in 1.34s"
