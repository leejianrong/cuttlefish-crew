"""Advisory checks on a context handoff (CUT-313). No model call (ADR-0042).

A handoff is the document a new session starts from: the agent's own, asked for at a round
checkpoint, or the journal digest. These are string and set checks on its text. They say
"a heading is missing" or "this edited file is not mentioned", never "this handoff is good":
nothing here gates a round, and a person reads the numbers with that in mind.

Pure functions, no I/O; ``cuttlefish.handover`` feeds them what the journal and git hold.
"""

from __future__ import annotations

import dataclasses
import re
from collections.abc import Iterable

HEADINGS = ("Done", "Remaining", "Failed or refused", "Open questions")
#: A handoff shorter or longer than this (characters, repository block excluded) is suspect.
MIN_CHARS = 120
MAX_CHARS = 12_000
_MARKERS = re.compile(r"\bunconfirmed\b|\bnot confirmed\b|\bnot verified\b", re.IGNORECASE)
_EMPTY = {"none", "nothing", "n/a", "na", "none recorded", "nothing is recorded"}
_DIGEST_MARK = "(A digest read from the journal"
_MIN_WORD = 4
_MAX_WORDS = 8


@dataclasses.dataclass(frozen=True, slots=True)
class Checklist:
    """Structure of the text. ``done_names_path`` is ``None`` when there is no known edited path
    to confirm a mention against."""

    headings: int
    remaining_ok: bool
    done_names_path: bool | None
    unconfirmed: int
    length_ok: bool
    digest: bool

    @property
    def passed(self) -> bool:
        return (
            self.headings == len(HEADINGS)
            and self.remaining_ok
            and self.done_names_path is not False
            and self.length_ok
        )


@dataclasses.dataclass(frozen=True, slots=True)
class Recall:
    """How many known facts the text mentions, as (found, total). ``plan_step`` is ``None`` when
    there is no plan."""

    files: tuple[int, int]
    plan_step: bool | None
    failures: tuple[int, int]
    requests: tuple[int, int]


def sections(text: str) -> dict[str, str]:
    """The body under each of the four headings the text has, keyed by the canonical name."""
    found: dict[str, str] = {}
    marks = list(re.finditer(r"^\s*#{1,4}\s*(.+?)\s*$", text, re.MULTILINE))
    for i, mark in enumerate(marks):
        title = mark.group(1).lower()
        for name in HEADINGS:
            if title.startswith(name.lower()) and name not in found:
                end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
                found[name] = text[mark.end() : end].strip()
    return found


def _trivial(body: str) -> bool:
    return body.strip(" \n.-*").lower() in _EMPTY or len(body.strip()) < 10


def _mentions(text: str, needle: str) -> bool:
    """Whether at least half of `needle`'s longer words (up to eight) appear in `text`."""
    hay = text.lower()
    words = [w for w in re.findall(r"\w+", needle.lower()) if len(w) >= _MIN_WORD][:_MAX_WORDS]
    if not words:
        return needle.strip().lower() in hay
    return sum(w in hay for w in words) * 2 >= len(words)


def _path_mentioned(text: str, path: str) -> bool:
    return path in text or path.rsplit("/", 1)[-1] in text


def checklist(text: str, *, known_paths: Iterable[str] = ()) -> Checklist:
    """Measure A. `known_paths` are files git or the journal's edited paths confirm."""
    body = sections(text)
    paths = list(known_paths)
    done = body.get("Done", "")
    return Checklist(
        headings=len(body),
        remaining_ok="Remaining" in body and not _trivial(body["Remaining"]),
        done_names_path=any(_path_mentioned(done, p) for p in paths) if paths else None,
        unconfirmed=len(_MARKERS.findall(text)),
        length_ok=MIN_CHARS <= len(text.strip()) <= MAX_CHARS,
        digest=_DIGEST_MARK in text,
    )


def recall(
    text: str,
    *,
    edited_paths: Iterable[str] = (),
    plan_step: str | None = None,
    failures: Iterable[str] = (),
    open_requests: Iterable[str] = (),
) -> Recall:
    """Measure B: ratios of known facts the text mentions."""
    paths, fails, reqs = list(edited_paths), list(failures), list(open_requests)
    return Recall(
        files=(sum(_path_mentioned(text, p) for p in paths), len(paths)),
        plan_step=None if plan_step is None else _mentions(text, plan_step),
        failures=(sum(_mentions(text, f) for f in fails), len(fails)),
        requests=(sum(_mentions(text, r) for r in reqs), len(reqs)),
    )
