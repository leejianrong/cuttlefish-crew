"""CUT-216: the controller's record of a project's compose stack on a worker."""

from __future__ import annotations

import asyncio

import pytest

from cuttlefish.fleet.stacks import (
    MAX_BODY_BYTES,
    RESEND_AFTER_SECONDS,
    StackBoard,
    StackError,
    clean_ports,
)


def _board() -> tuple[StackBoard, list[float]]:
    now = [1000.0]
    return StackBoard(now=lambda: now[0]), now


def test_an_order_is_handed_over_once_and_again_only_after_a_while() -> None:
    board, now = _board()
    board.request("p", "w1", "laptop", "https://x/y.git", "up")
    assert board.status("p") == {
        "worker": "laptop", "state": "starting", "ports": [], "error": "", "at": 1000.0,
    }  # fmt: skip
    assert board.claim("w2") == {}
    assert board.has_work("w1")
    assert board.claim("w1")["stacks"] == [
        {"op": "up", "project_id": "p", "name": "laptop", "repo": "https://x/y.git"}
    ]
    assert board.claim("w1") == {} and not board.has_work("w1")
    now[0] += RESEND_AFTER_SECONDS  # the worker never answered: ask again
    assert len(board.claim("w1")["stacks"]) == 1


def test_a_report_sets_the_state_and_keeps_only_clean_ports() -> None:
    board, _ = _board()
    board.request("p", "w1", "laptop", None, "up")
    board.report(
        "w1",
        "p",
        "up",
        [{"service": "web\x00", "target": 80, "published": 8080}, {"target": "x"}, 5],
        "ignored",
    )
    shown = board.status("p")
    assert shown is not None and shown["state"] == "up" and shown["error"] == ""
    assert shown["ports"] == [{"service": "web", "target": 80, "published": 8080}]
    board.report("w1", "p", "failed", [{"target": 1, "published": 2}], "boom\x07" + "x" * 500)
    shown = board.status("p")
    assert shown is not None and shown["ports"] == [] and len(shown["error"]) == 300
    with pytest.raises(StackError):
        board.report("w2", "p", "up", [], "")  # not this worker's stack
    with pytest.raises(StackError):
        board.report("w1", "p", "exploded", [], "")
    with pytest.raises(StackError):
        board.report("w1", "nope", "up", [], "")


def test_ports_are_bounded_numbers_and_names() -> None:
    assert clean_ports("x") == []
    many = [{"service": "s", "target": 1, "published": i + 1} for i in range(100)]
    assert len(clean_ports(many)) == 20
    assert clean_ports([{"target": True, "published": 80}, {"target": 1, "published": 70000}]) == []


def test_stop_keeps_the_ports_until_the_worker_says_it_is_down() -> None:
    board, _ = _board()
    board.request("p", "w1", "laptop", None, "up")
    board.report("w1", "p", "up", [{"service": "web", "target": 80, "published": 8080}], "")
    assert board.active("p") and board.active("p", "w1") and not board.active("p", "w2")
    board.request("p", "w1", "laptop", None, "down")
    shown = board.status("p")
    assert shown is not None and shown["state"] == "stopping" and shown["ports"]
    board.report("w1", "p", "down", [], "")
    assert not board.active("p")


async def test_a_page_request_goes_to_the_worker_and_is_answered_from_its_reply() -> None:
    board, _ = _board()
    board.request("p", "w1", "laptop", None, "up")
    board.report("w1", "p", "up", [{"service": "web", "target": 80, "published": 8080}], "")
    waiting = asyncio.create_task(board.fetch("p", 8080, "/a?b=1"))
    await asyncio.sleep(0.01)
    [ask] = board.claim("w1")["pages"]
    assert (ask["port"], ask["path"]) == (8080, "/a?b=1") and board.claim("w1") == {}
    with pytest.raises(StackError):
        board.answer("w2", ask["id"], 200, "text/html", b"x")  # another worker may not answer
    board.answer("w1", ask["id"], 200, "text/html; charset=utf-8", b"<p>hi</p>")
    page = await waiting
    assert (page.status, page.content_type, page.body) == (
        200,
        "text/html; charset=utf-8",
        b"<p>hi</p>",
    )
    with pytest.raises(StackError):
        board.answer("w1", ask["id"], 200, "text/html", b"late")  # one answer per request


async def test_a_page_request_is_refused_before_it_is_queued_when_it_cannot_be_served() -> None:
    board, _ = _board()
    with pytest.raises(StackError, match="not running"):
        await board.fetch("p", 8080, "/")
    board.request("p", "w1", "laptop", None, "up")
    board.report("w1", "p", "up", [{"service": "web", "target": 80, "published": 8080}], "")
    for port, path, why in (
        (9999, "/", "published no port"),
        (8080, "x", "not a path"),
        (8080, "/../x", "not a path"),
        (8080, "/a b", "not a path"),
        (8080, "/\r\nHost: x", "not a path"),
    ):
        with pytest.raises(StackError, match=why):
            await board.fetch("p", port, path)
    assert board.claim("w1") == {}  # nothing about a page was queued


async def test_a_reply_is_capped_and_a_removed_worker_ends_waiting_requests() -> None:
    board, _ = _board()
    board.request("p", "w1", "laptop", None, "up")
    board.report("w1", "p", "up", [{"service": "web", "target": 80, "published": 8080}], "")
    big = asyncio.create_task(board.fetch("p", 8080, "/"))
    await asyncio.sleep(0.01)
    [ask] = board.claim("w1")["pages"]
    board.answer("w1", ask["id"], 99, "x" * 500, b"y" * (MAX_BODY_BYTES + 5))
    page = await big
    assert page.status == 502 and len(page.content_type) <= 100 and len(page.body) == MAX_BODY_BYTES
    waiting = asyncio.create_task(board.fetch("p", 8080, "/"))
    await asyncio.sleep(0.01)
    board.forget_worker("w1")
    assert (await waiting).status == 502 and board.status("p") is None
