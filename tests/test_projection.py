# tests/test_projection.py
"""projection.py narrows an already-rendered excerpt to what one drift mode
is actually decided against. Fixtures are built through build_excerpt, the
same renderer verdict.py's reviewer reads, so a kept record's rendered form
is checked against the real output shape, not a hand-typed approximation."""

from crosier.digest import build_excerpt
from crosier.projection import MODES, project, projection_sizes, split_records


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


# Window starts at since_index=1, one turn after the goal, so the window
# itself opens on an assistant turn rather than duplicating the goal as its
# first "[user]" record - that keeps the ignored_correction fixture honest
# about what came "before" the correction.
_LINES = [
    _user("fix the flaky importer test"),
    _assistant("ok, starting"),
    _tool_use("Read", {"file_path": "conftest.py"}, tool_id="t0"),
    _tool_result("fixtures here", tool_id="t0"),
    _assistant("I will look at the test file first."),
    _tool_use("Read", {"file_path": "tests/test_importer.py"}, tool_id="t1"),
    _tool_result("def test_import(): ...\n" * 5, tool_id="t1"),
    _user("also check the CSV parser, not just the test"),
    _assistant("Checking the parser now."),
    _tool_use("Bash", {"command": "pytest -q"}, tool_id="t2"),
    _tool_result("1 failed, 4 passed\nTraceback:\n  line 1\n  line 2", tool_id="t2", is_error=True),
    _assistant("All tests pass now."),
]
EXCERPT = build_excerpt(_LINES, since_index=1)


# --- split_records -----------------------------------------------------------


def test_split_records_round_trips_a_realistic_excerpt():
    records = split_records(EXCERPT)
    labels = [label for label, _ in records]
    assert labels[0] == "goal"
    assert any(label.startswith("tool_result #3 ERROR") for label in labels)
    rebuilt = "\n\n".join(f"[{label}] {body}" for label, body in records)
    assert rebuilt == EXCERPT


def test_split_records_keeps_a_multiline_body_intact():
    records = dict(split_records(EXCERPT))
    error_label = next(l for l in records if l.startswith("tool_result #3 ERROR"))
    body = records[error_label]
    assert "Traceback:" in body
    assert "line 1" in body
    assert "line 2" in body


def test_split_records_on_empty_string_is_safe():
    assert split_records("") == []


# --- header preservation -------------------------------------------------------


def test_goal_header_is_kept_for_every_mode():
    for mode in MODES:
        assert project(EXCERPT, mode).startswith("[goal] fix the flaky importer test")


def test_latest_user_instruction_header_is_kept_for_every_mode():
    lines = [
        _user("build the importer"),
        _user("stop mocking the client, use the real one"),
    ] + [_tool_use("Edit", {"file_path": f"f{i}.py"}, tool_id=f"e{i}") for i in range(3)]
    excerpt = build_excerpt(lines, since_index=2)
    assert "[latest user instruction] stop mocking the client, use the real one" in excerpt  # sanity on the fixture
    for mode in MODES:
        assert "[latest user instruction] stop mocking the client, use the real one" in project(excerpt, mode)


# --- size ----------------------------------------------------------------------


def test_every_mode_is_no_larger_than_the_full_excerpt():
    for mode in MODES:
        assert len(project(EXCERPT, mode)) <= len(EXCERPT)


# --- quote fidelity (protects verify_evidence) ----------------------------------


def test_a_kept_quote_is_byte_identical_to_the_source():
    quote = "1 failed, 4 passed"
    projected = project(EXCERPT, "unverified_claim")
    assert quote in projected
    assert quote in EXCERPT


# --- per-mode content --------------------------------------------------------


def test_unverified_claim_keeps_tool_result_bodies_and_assistant_claims():
    projected = project(EXCERPT, "unverified_claim")
    assert "All tests pass now." in projected
    assert "1 failed, 4 passed" in projected


def test_unverified_claim_drops_tool_use_argument_bodies():
    projected = project(EXCERPT, "unverified_claim")
    assert "pytest -q" not in projected
    assert "[tool_use #3 Bash]" in projected


def test_off_goal_keeps_user_and_assistant_prose_but_not_tool_result_bodies():
    projected = project(EXCERPT, "off_goal")
    assert "also check the CSV parser, not just the test" in projected
    assert "Checking the parser now." in projected
    assert "def test_import" not in projected
    assert "[tool_result #2" in projected


def test_padding_keeps_only_assistant_and_user_prose():
    projected = project(EXCERPT, "padding")
    assert "also check the CSV parser" in projected
    assert "All tests pass now." in projected
    assert "tool_use" not in projected
    assert "tool_result" not in projected


def test_loop_keeps_tool_use_bodies_in_full():
    projected = project(EXCERPT, "loop")
    assert '"file_path": "tests/test_importer.py"' in projected


def test_research_collapse_keeps_assistant_claims_and_whether_a_tool_ran():
    projected = project(EXCERPT, "research_collapse")
    assert "I will look at the test file first." in projected
    assert "[tool_use #2 Read]" in projected
    assert "def test_import" not in projected


def test_ignored_correction_drops_everything_before_the_first_user_record():
    projected = project(EXCERPT, "ignored_correction")
    assert "I will look at the test file first." not in projected
    assert "also check the CSV parser, not just the test" in projected
    assert "All tests pass now." in projected


# --- robustness ------------------------------------------------------------------


def test_unknown_mode_returns_the_excerpt_unchanged():
    assert project(EXCERPT, "not_a_real_mode") == EXCERPT


def test_empty_excerpt_is_safe_for_every_mode():
    for mode in MODES:
        assert project("", mode) == ""
    assert project("", "bogus") == ""


def test_projection_sizes_reports_full_and_every_mode():
    sizes = projection_sizes(EXCERPT)
    assert sizes["full"] == len(EXCERPT)
    for mode in MODES:
        assert sizes[mode] <= sizes["full"]
