"""Unit: `cuttlefish.fleet.server._self_origin` (ADR-0013, KAN-1708) -- the
non-loopback bind's own origin, always trusted by `SessionAuth` in addition to
`--allow-origin`."""

from __future__ import annotations

import pytest
import satay.control

from cuttlefish.fleet.auth import MIN_PASSWORD_LENGTH, SessionAuth
from cuttlefish.fleet.server import _self_origin


def test_formats_a_plain_http_origin() -> None:
    assert _self_origin("100.64.1.2", 8420) == "http://100.64.1.2:8420"


def test_session_auth_accepts_a_request_whose_origin_matches_the_bind() -> None:
    """Mirrors exactly how `run_daemon` wires `SessionAuth` for a non-loopback
    bind (ADR-0013): the daemon's own origin is always in `allowed_origins`,
    with no explicit `--allow-origin` needed for the common "browse straight
    to the printed URL" case."""
    password = "a" * MIN_PASSWORD_LENGTH
    self_origin = _self_origin("100.64.1.2", 8420)
    auth = SessionAuth(password=password, allowed_origins=frozenset({self_origin}))
    token = auth.login(password)

    auth.check(token=token, host=None, origin=self_origin)  # does not raise


def test_session_auth_still_rejects_an_unrelated_origin() -> None:
    password = "a" * MIN_PASSWORD_LENGTH
    self_origin = _self_origin("100.64.1.2", 8420)
    auth = SessionAuth(password=password, allowed_origins=frozenset({self_origin}))
    token = auth.login(password)

    with pytest.raises(satay.control.AuthError):
        auth.check(token=token, host=None, origin="https://evil.example")
