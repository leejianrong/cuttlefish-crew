"""The loopback dashboard token survives a restart (CUT-106)."""

from __future__ import annotations

import stat
from pathlib import Path

from cuttlefish.fleet.serve_token import MIN_TOKEN_LENGTH, load_or_create_token


def test_the_first_start_creates_the_token_privately_and_a_restart_reads_it_back(
    tmp_path: Path,
) -> None:
    path = tmp_path / "home" / ".cuttlefish" / "serve-token"

    first = load_or_create_token(path)
    second = load_or_create_token(path)

    assert first == second
    assert len(first) >= MIN_TOKEN_LENGTH
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_a_token_file_that_is_too_short_or_blank_is_replaced(tmp_path: Path) -> None:
    path = tmp_path / "serve-token"
    for bad in ("", "short\n", "has a space inside it but is long enough to pass the length check"):
        path.write_text(bad)
        token = load_or_create_token(path)
        assert len(token) >= MIN_TOKEN_LENGTH
        assert " " not in token
        assert load_or_create_token(path) == token


def test_a_loose_file_mode_is_tightened(tmp_path: Path) -> None:
    path = tmp_path / "serve-token"
    token = load_or_create_token(path)
    path.chmod(0o644)

    assert load_or_create_token(path) == token
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_rotating_means_deleting_the_file(tmp_path: Path) -> None:
    path = tmp_path / "serve-token"
    old = load_or_create_token(path)
    path.unlink()

    assert load_or_create_token(path) != old
