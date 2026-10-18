"""Unit (ADR-0043, CUT-135): the per-address limits."""

from __future__ import annotations

from cuttlefish.relay.limits import (
    ConnectionCounter,
    RateWindow,
    client_address,
)


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_a_rate_window_allows_the_limit_then_refuses_until_the_window_passes() -> None:
    clock = Clock()
    window = RateWindow(3, 60, clock)
    assert [window.hit("a") for _ in range(4)] == [True, True, True, False]
    assert window.hit("b") is True  # another address is its own count
    clock.now += 61
    assert window.hit("a") is True


def test_over_reports_without_counting() -> None:
    window = RateWindow(1, 60, Clock())
    assert window.over("a") is False
    window.hit("a")
    assert window.over("a") is True and window.over("a") is True
    assert window.over("b") is False


def test_idle_addresses_are_forgotten() -> None:
    clock = Clock()
    window = RateWindow(1, 60, clock)
    window.hit("a")
    clock.now += 61
    window.forget_idle()
    assert window._hits == {}


def test_a_connection_counter_caps_open_connections_and_frees_them() -> None:
    counter = ConnectionCounter(2)
    assert [counter.acquire("a") for _ in range(3)] == [True, True, False]
    counter.release("a")
    assert counter.acquire("a") is True
    counter.release("a")
    counter.release("a")
    counter.release("a")  # releasing more than was taken never goes negative
    assert counter.open_for("a") == 0


def test_the_forwarded_address_is_believed_only_when_asked_and_only_its_last_entry() -> None:
    headers = {"x-forwarded-for": "6.6.6.6, 203.0.113.9"}
    assert client_address("10.0.0.2", headers, trust_forwarded=False) == "10.0.0.2"
    assert client_address("10.0.0.2", headers, trust_forwarded=True) == "203.0.113.9"
    assert client_address("10.0.0.2", {}, trust_forwarded=True) == "10.0.0.2"
    assert client_address(None, {}, trust_forwarded=False) == "unknown"
