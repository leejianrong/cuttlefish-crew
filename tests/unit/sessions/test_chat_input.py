"""Unit: what may be pasted into a live session and when (CUT-173)."""

from __future__ import annotations

import pytest

from cuttlefish.sessions.chat_input import (
    MAX_MESSAGE_CHARS,
    MessageError,
    clean_message,
    dialog_open,
)


def test_a_message_is_trimmed_and_its_line_endings_made_plain() -> None:
    assert clean_message("  one\r\ntwo\rthree\n") == "one\ntwo\nthree"


def test_control_characters_are_removed_so_a_message_cannot_end_its_own_paste() -> None:
    hostile = "a\x1b[201~rm -rf /\x1b[200~b\x00c\x7f"
    cleaned = clean_message(hostile)
    assert "\x1b" not in cleaned and "\x00" not in cleaned and "\x7f" not in cleaned
    assert cleaned == "a[201~rm -rf /[200~bc"


def test_tabs_and_newlines_survive() -> None:
    assert clean_message("a\tb\nc") == "a\tb\nc"


def test_nothing_or_too_much_is_refused() -> None:
    for blank in ("", "   \n ", "\x1b\x00"):
        with pytest.raises(MessageError, match="Say something"):
            clean_message(blank)
    with pytest.raises(MessageError, match="at most"):
        clean_message("x" * (MAX_MESSAGE_CHARS + 1))
    assert len(clean_message("x" * MAX_MESSAGE_CHARS)) == MAX_MESSAGE_CHARS


@pytest.mark.parametrize(
    "screen",
    [
        " Do you want to proceed?\n \u276f 1. Yes\n   2. No, tell Claude what to do instead",
        "Do you trust the files in this folder?\n> 1. Yes, I trust this folder",
        "Edit file\n  Esc to cancel",
        "Do you want to make this edit to cli.py?",
    ],
)
def test_a_screen_with_a_dialog_is_recognised(screen: str) -> None:
    assert dialog_open(screen)


@pytest.mark.parametrize(
    "screen",
    [
        "",
        "● Done. All 41 tests pass.\n\n> \n  ? for shortcuts",
        "The list is: 1. first 2. second",  # numbers in prose, no cursor before the first
    ],
)
def test_an_ordinary_screen_is_not(screen: str) -> None:
    assert not dialog_open(screen)
