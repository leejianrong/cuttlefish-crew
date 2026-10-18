"""Unit (ADR-0043, CUT-135): the relay's CLI guards and `relay-admin`. No server is started."""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from cuttlefish.cli import (
    EXIT_CONFIG_ERROR,
    EXIT_OK,
    _relay,
    _relay_admin,
    _relay_page_url,
)
from cuttlefish.relay.accounts import AccountStore


def _args(**over: object) -> argparse.Namespace:
    base: dict[str, object] = {
        "host": "127.0.0.1",
        "port": 0,
        "db": None,
        "pages": None,
        "trust_forwarded": False,
        "insecure_cookies": False,
    }
    return argparse.Namespace(**{**base, **over})


def test_an_open_relay_refuses_a_public_address(capsys: pytest.CaptureFixture[str]) -> None:
    assert _relay(_args(host="0.0.0.0")) == EXIT_CONFIG_ERROR
    assert "only runs on loopback" in capsys.readouterr().err


def test_a_hosted_relay_needs_a_long_owner_secret(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("CUTTLEFISH_RELAY_OWNER_SECRET", raising=False)
    assert _relay(_args(db=str(tmp_path / "r.db"), host="0.0.0.0")) == EXIT_CONFIG_ERROR
    assert "at least 32 characters" in capsys.readouterr().err


def test_a_pages_folder_without_a_build_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert _relay(_args(pages=str(tmp_path))) == EXIT_CONFIG_ERROR
    assert "no index.html" in capsys.readouterr().err


def test_relay_admin_makes_a_room_and_prints_its_token_once(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "r.db"
    store = AccountStore.open(db)
    assert _relay_admin(argparse.Namespace(db=str(db), label="Work PC")) == EXIT_CONFIG_ERROR
    assert "no owner yet" in capsys.readouterr().err
    store.ensure_owner("o" * 40)
    store.close()
    assert _relay_admin(argparse.Namespace(db=str(db), label="Work PC")) == EXIT_OK
    out = capsys.readouterr().out
    room = next(line.split()[1] for line in out.splitlines() if line.startswith("room:"))
    token = next(line.split()[1] for line in out.splitlines() if line.startswith("token:"))
    check = AccountStore.open(db)
    assert check.authorise_daemon(room, token) is not None
    check.close()


@pytest.mark.parametrize(
    ("relay", "tailnet", "room", "page"),
    [
        ("wss://relay.example.com", None, "r00m", "https://relay.example.com"),
        ("ws://127.0.0.1:8450", None, "r00m", "http://127.0.0.1:8450"),
        ("ws://127.0.0.1:8450", None, None, None),
        ("ws://127.0.0.1:8450", "box.ts.net", "r00m", "https://box.ts.net"),
    ],
)
def test_the_page_a_phone_opens(
    relay: str, tailnet: str | None, room: str | None, page: str | None
) -> None:
    assert _relay_page_url(relay, tailnet, room) == page
