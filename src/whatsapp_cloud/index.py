"""Remembering which group is which, so a re-run does not make a second one.

Creating a group is not idempotent: send the same subject twice and you get two groups, and
the client ends up in the one nobody is watching. The obvious fix is to look for an existing
group with that subject before creating, which this package does — but that search lists
every group the number owns, and a number may own ten thousand. Paying for a full scan on
every create, most of which are the first create for that subject, is the wrong shape.

So the scan is the fallback and an index is the fast path. The index is a plain mapping from
subject to group id that the caller owns: in memory for one run, on disk for a repeated job,
or an adapter over whatever database the application already has.

The index is never trusted blindly. An id it returns is confirmed against the API before it
is used, because a group can be deleted, suspended or renamed while a file on disk says
otherwise, and handing back a stale id would be worse than the scan this avoids.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Protocol


class GroupIndex(Protocol):
    """A subject-to-id memory. Every method must tolerate being wrong."""

    def lookup(self, subject: str) -> str | None: ...

    def remember(self, subject: str, group_id: str) -> None: ...

    def forget(self, subject: str) -> None: ...


def normalise_subject(subject: str) -> str:
    """The key. Whitespace-collapsed and case-folded, because a subject is typed by a human.

    Deliberately *not* used when comparing against the API's own subjects: there the match
    is exact, since a subject that differs only in case is a different group as far as Meta
    is concerned. Here it only decides whether we already know the answer.
    """
    return " ".join(subject.split()).casefold()


class NoIndex:
    """Remembers nothing: every lookup scans. The default, and always correct."""

    def lookup(self, subject: str) -> str | None:
        return None

    def remember(self, subject: str, group_id: str) -> None:
        return None

    def forget(self, subject: str) -> None:
        return None


class MemoryIndex:
    """Remembers for the life of the process.

    Enough for a provisioning run that creates a few hundred groups in one pass, which is
    the case where the repeated full scan actually hurts.
    """

    def __init__(self, initial: dict[str, str] | None = None) -> None:
        self._by_subject: dict[str, str] = {
            normalise_subject(k): v for k, v in (initial or {}).items()
        }

    def lookup(self, subject: str) -> str | None:
        return self._by_subject.get(normalise_subject(subject))

    def remember(self, subject: str, group_id: str) -> None:
        self._by_subject[normalise_subject(subject)] = group_id

    def forget(self, subject: str) -> None:
        self._by_subject.pop(normalise_subject(subject), None)

    def __len__(self) -> int:
        return len(self._by_subject)

    def as_dict(self) -> dict[str, str]:
        return dict(self._by_subject)


class JsonFileIndex:
    """Remembers between runs, in a JSON file.

    Written atomically: a crash halfway through a write would otherwise leave a truncated
    file, and a truncated index is read as "no groups exist", which is exactly the situation
    that creates duplicates. The file is rewritten in full each time, which is fine at this
    size and removes a whole class of partial-update bug.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._cache: dict[str, str] | None = None

    def _load(self) -> dict[str, str]:
        if self._cache is not None:
            return self._cache
        if not self.path.exists():
            self._cache = {}
            return self._cache
        try:
            loaded = json.loads(self.path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            # A corrupt index is a cache miss, not a crash: the scan still works.
            self._cache = {}
            return self._cache
        self._cache = (
            {str(k): str(v) for k, v in loaded.items() if isinstance(v, str)}
            if isinstance(loaded, dict)
            else {}
        )
        return self._cache

    def _save(self) -> None:
        data = self._load()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle, temporary = tempfile.mkstemp(dir=self.path.parent, suffix=".tmp")
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as file:
                json.dump(data, file, ensure_ascii=False, indent=2, sort_keys=True)
                file.write("\n")
            os.replace(temporary, self.path)
        except BaseException:
            Path(temporary).unlink(missing_ok=True)
            raise

    def lookup(self, subject: str) -> str | None:
        return self._load().get(normalise_subject(subject))

    def remember(self, subject: str, group_id: str) -> None:
        self._load()[normalise_subject(subject)] = group_id
        self._save()

    def forget(self, subject: str) -> None:
        if self._load().pop(normalise_subject(subject), None) is not None:
            self._save()

    def __len__(self) -> int:
        return len(self._load())
