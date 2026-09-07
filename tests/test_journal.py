"""One line per completed check, for the report surface. Nothing in the
checking path reads it back, so a failed write costs the session nothing."""

import pytest

from crosier.journal import MAX_ENTRIES, read_journal, record_check
from crosier.paths import journal_path


@pytest.fixture(autouse=True)
def _home(monkeypatch, tmp_path):
    monkeypatch.setenv("CROSIER_HOME", str(tmp_path / "home"))


def test_empty_when_nothing_was_recorded():
    assert read_journal("s1") == []


def test_entries_come_back_in_order():
    record_check("s1", {"turn": 1})
    record_check("s1", {"turn": 2})
    assert [e["turn"] for e in read_journal("s1")] == [1, 2]


def test_sessions_do_not_share_a_journal():
    record_check("s1", {"turn": 1})
    assert read_journal("s2") == []


def test_a_corrupt_line_is_skipped_rather_than_fatal():
    record_check("s1", {"turn": 1})
    with open(journal_path("s1"), "a", encoding="utf-8") as f:
        f.write("{not json\n")
    record_check("s1", {"turn": 2})
    assert [e["turn"] for e in read_journal("s1")] == [1, 2]


def test_an_unserializable_entry_is_dropped_not_raised():
    record_check("s1", {"turn": object()})
    assert read_journal("s1") == []


def test_reading_is_bounded():
    for i in range(MAX_ENTRIES + 10):
        record_check("s1", {"turn": i})
    assert len(read_journal("s1")) == MAX_ENTRIES
