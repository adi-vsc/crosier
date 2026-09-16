# tests/test_attack_command.py
"""commands/attack.md ships the manual "spawn a Sonnet subagent to attack
this" loop as a plugin slash command. No CLI, no new deps: parse the YAML
frontmatter with a small hand-rolled parser (single-line `key: value`
pairs only, which is all this file needs)."""

from pathlib import Path

COMMAND_PATH = Path(__file__).resolve().parent.parent / "commands" / "attack.md"


def _split_frontmatter(text: str) -> tuple[dict, str]:
    assert text.startswith("---\n"), "frontmatter must open on the file's first line"
    _, _, rest = text.partition("---\n")
    raw_frontmatter, _, body = rest.partition("\n---\n")

    fields = {}
    for line in raw_frontmatter.splitlines():
        if not line.strip():
            continue
        key, _, value = line.partition(":")
        fields[key.strip()] = value.strip()
    return fields, body


def test_file_exists():
    assert COMMAND_PATH.is_file()


def test_frontmatter_parses_with_name_and_description():
    text = COMMAND_PATH.read_text(encoding="utf-8")
    fields, _ = _split_frontmatter(text)
    assert fields["name"] == "attack"
    assert fields["description"]


def test_body_references_arguments_and_model():
    text = COMMAND_PATH.read_text(encoding="utf-8")
    _, body = _split_frontmatter(text)
    assert "$ARGUMENTS" in body
    assert "sonnet" in body


def test_body_requires_locators_and_a_kill_list():
    text = COMMAND_PATH.read_text(encoding="utf-8")
    _, body = _split_frontmatter(text)
    assert "locator" in body
    assert "kill list" in body


def test_body_instructs_stripping_history_phrasing():
    text = COMMAND_PATH.read_text(encoding="utf-8")
    _, body = _split_frontmatter(text)
    assert "Strip authorship" in body
    assert '"we established"' in body
