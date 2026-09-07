"""One line per completed check, appended by the hook.

The check pipeline never reads this back — it exists so `crosier report` can
show a session what was looked at and what was said, and so the on/off
effectiveness benchmark has a data source that is not a screen scrape. It is
written where state is written, by the process that owns state, so a killed
worker can no more corrupt the journal than it can corrupt the session.
"""

import json

from crosier.paths import journal_path

__all__ = ["read_journal", "record_check"]

# A session is budgeted to a dozen checks; anything past this is a runaway
# writer, and a report is not worth an unbounded read.
MAX_ENTRIES = 200


def record_check(session_id: str, entry: dict) -> None:
    try:
        path = journal_path(session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except (OSError, TypeError, ValueError):
        # Reporting is a convenience; it never takes a turn down.
        pass


def read_journal(session_id: str) -> list:
    path = journal_path(session_id)
    if not path.exists():
        return []
    entries = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(data, dict):
                    entries.append(data)
    except OSError:
        return []
    return entries[-MAX_ENTRIES:]
