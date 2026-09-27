"""Unit: `cuttlefish.fleet.auth.SessionAuth` (ADR-0011, KAN-1706) -- the
non-loopback password/session guard, independent of any real HTTP surface."""

from __future__ import annotations

import time

import pytest
import satay.control

from cuttlefish.fleet.auth import MIN_PASSWORD_LENGTH, SessionAuth, WeakPasswordError


def test_rejects_a_password_shorter_than_the_minimum() -> None:
    with pytest.raises(WeakPasswordError):
        SessionAuth(password="short")


def test_login_with_the_right_password_mints_a_token_that_checks_out() -> None:
    auth = SessionAuth(password="a" * MIN_PASSWORD_LENGTH)
    token = auth.login("a" * MIN_PASSWORD_LENGTH)
    auth.check(token=token, host=None, origin=None)  # does not raise


def test_login_with_the_wrong_password_is_rejected() -> None:
    auth = SessionAuth(password="a" * MIN_PASSWORD_LENGTH)
    with pytest.raises(satay.control.AuthError) as excinfo:
        auth.login("wrong-password")
    assert excinfo.value.status == 401


def test_check_rejects_a_missing_or_malformed_token() -> None:
    auth = SessionAuth(password="a" * MIN_PASSWORD_LENGTH)
    with pytest.raises(satay.control.AuthError):
        auth.check(token=None, host=None, origin=None)
    with pytest.raises(satay.control.AuthError):
        auth.check(token="not-a-real-token", host=None, origin=None)


def test_check_rejects_a_token_minted_by_a_different_secret() -> None:
    password = "a" * MIN_PASSWORD_LENGTH
    minted_by = SessionAuth(password=password)
    token = minted_by.login(password)
    checked_by = SessionAuth(password=password)
    with pytest.raises(satay.control.AuthError):
        checked_by.check(token=token, host=None, origin=None)


def test_check_rejects_an_expired_token() -> None:
    auth = SessionAuth(password="a" * MIN_PASSWORD_LENGTH, ttl_seconds=-1)
    token = auth.login("a" * MIN_PASSWORD_LENGTH)
    with pytest.raises(satay.control.AuthError):
        auth.check(token=token, host=None, origin=None)


def test_lockout_after_repeated_failures() -> None:
    password = "a" * MIN_PASSWORD_LENGTH
    auth = SessionAuth(password=password)
    for _ in range(5):
        with pytest.raises(satay.control.AuthError) as excinfo:
            auth.login("wrong")
        assert excinfo.value.status == 401

    with pytest.raises(satay.control.AuthError) as excinfo:
        auth.login(password)
    assert excinfo.value.status == 429


def test_lockout_expires(monkeypatch: pytest.MonkeyPatch) -> None:
    password = "a" * MIN_PASSWORD_LENGTH
    auth = SessionAuth(password=password)
    for _ in range(5):
        with pytest.raises(satay.control.AuthError):
            auth.login("wrong")
    assert auth._locked_until > time.time()

    monkeypatch.setattr(time, "time", lambda: auth._locked_until + 1)
    token = auth.login(password)
    assert token


def test_check_rejects_a_disallowed_origin() -> None:
    password = "a" * MIN_PASSWORD_LENGTH
    auth = SessionAuth(password=password, allowed_origins=frozenset({"https://good.example"}))
    token = auth.login(password)
    auth.check(token=token, host=None, origin="https://good.example")  # does not raise
    with pytest.raises(satay.control.AuthError):
        auth.check(token=token, host=None, origin="https://evil.example")


def test_check_allows_no_origin_header_regardless_of_allowlist() -> None:
    password = "a" * MIN_PASSWORD_LENGTH
    auth = SessionAuth(password=password)
    token = auth.login(password)
    auth.check(token=token, host=None, origin=None)  # does not raise


def test_check_allows_a_loopback_origin_even_with_no_allowlist_entry() -> None:
    """Live-found (ADR-0011): the dashboard's own Vite dev server
    (`http://localhost:<port>`) must be able to reach a non-loopback-bound
    daemon over `127.0.0.1` without the operator adding every local dev port
    to `--allow-origin` -- same carve-out `satay.control.SecurityPolicy`
    already has for `Origin`."""
    password = "a" * MIN_PASSWORD_LENGTH
    auth = SessionAuth(password=password)  # no allowed_origins configured at all
    token = auth.login(password)
    auth.check(token=token, host=None, origin="http://localhost:5183")  # does not raise
    auth.check(token=token, host=None, origin="http://127.0.0.1:5183")  # does not raise
