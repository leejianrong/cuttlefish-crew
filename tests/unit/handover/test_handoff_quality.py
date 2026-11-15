from __future__ import annotations

from cuttlefish.delegate.handoff import handoff_is_usable
from cuttlefish.handoff_quality import checklist, recall, sections

GOOD = """## Done
Added src/app/parser.py and tests/test_parser.py; the parser tests pass (unconfirmed for CI).

## Remaining
1. Wire the parser into the CLI entry point.
2. Document the new flag.

## Failed or refused
pytest -x failed once on a stale fixture, then passed.

## Open questions
Should the flag default to on?
"""


def test_sections_finds_all_four() -> None:
    assert list(sections(GOOD)) == ["Done", "Remaining", "Failed or refused", "Open questions"]


def test_good_document_passes() -> None:
    result = checklist(GOOD, known_paths=["src/app/parser.py"])
    assert result.headings == 4
    assert result.remaining_ok and result.done_names_path is True
    assert result.unconfirmed == 1
    assert result.passed and not result.digest


def test_missing_headings_and_trivial_remaining() -> None:
    text = "## Done\nsrc/a.py edited " + "x" * 200 + "\n## Remaining\nNone.\n"
    result = checklist(text, known_paths=["src/a.py"])
    assert result.headings == 2
    assert not result.remaining_ok and not result.passed


def test_done_names_no_confirmed_path() -> None:
    assert checklist(GOOD, known_paths=["other/file.py"]).done_names_path is False
    assert checklist(GOOD).done_names_path is None


def test_length_and_digest_marker() -> None:
    short = checklist("## Done\na\n## Remaining\nb")
    assert not short.length_ok
    assert checklist("(A digest read from the journal, not a report.)\n" + GOOD).digest


def test_recall_ratios() -> None:
    result = recall(
        GOOD,
        edited_paths=["src/app/parser.py", "src/app/other.py"],
        plan_step="Wire the parser into the CLI",
        failures=["pytest failed on a stale fixture", "docker build exploded unexpectedly"],
        open_requests=["Should the flag default to on?"],
    )
    assert result.files == (1, 2)
    assert result.plan_step is True
    assert result.failures == (1, 2)
    assert result.requests == (1, 1)
    assert recall(GOOD).plan_step is None


def test_usable_needs_text_under_both_headings() -> None:
    assert handoff_is_usable(GOOD)
    assert handoff_is_usable("# Done\nfixed it\n# Remaining\nnothing\n")
    assert not handoff_is_usable("## Done\n\n## Remaining\n\n")
    assert not handoff_is_usable("I finished everything, no handoff needed.")
    assert not handoff_is_usable("## Done\nfixed it\n")
