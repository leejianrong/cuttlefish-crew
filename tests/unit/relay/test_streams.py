"""Unit: stream ids, chunking and what a peer is allowed to send (ADR-0037)."""

from __future__ import annotations

import pytest

from cuttlefish.relay.protocol import (
    CHUNK_SIZE,
    Frame,
    FrameType,
    Role,
    StreamError,
    StreamMux,
)


def _pair() -> tuple[StreamMux, StreamMux]:
    return StreamMux(Role.INITIATOR), StreamMux(Role.RESPONDER)


def _deliver(frame: Frame, to: StreamMux) -> Frame:
    return to.receive(frame)


def test_a_request_and_its_response_end_to_end() -> None:
    browser, daemon = _pair()
    stream, opening = browser.open(FrameType.REQUEST, b'{"method":"GET","path":"/api/projects"}')
    daemon.receive(opening)
    assert browser.open_streams == 1 and daemon.open_streams == 1

    daemon.receive(browser.close(stream))
    browser.receive(daemon.respond(stream, b'{"status":200}'))
    for frame in daemon.send(stream, b"x" * (CHUNK_SIZE * 2 + 5)):
        browser.receive(frame)
    browser.receive(daemon.close(stream))
    assert browser.open_streams == 0 and daemon.open_streams == 0


def test_browser_ids_are_odd_and_daemon_ids_are_even() -> None:
    browser, daemon = _pair()
    assert [browser.open(FrameType.REQUEST, b"")[0] for _ in range(3)] == [1, 3, 5]
    assert [daemon.open(FrameType.REQUEST, b"")[0] for _ in range(3)] == [2, 4, 6]


def test_a_body_is_cut_into_chunks_that_fit() -> None:
    browser, _ = _pair()
    stream, _ = browser.open(FrameType.REQUEST, b"")
    frames = browser.send(stream, b"x" * 150000)
    assert [len(f.payload) for f in frames] == [60000, 60000, 30000]
    assert all(f.type is FrameType.DATA and f.stream == stream for f in frames)


def test_a_websocket_message_is_one_frame_however_long() -> None:
    browser, daemon = _pair()
    stream, opening = browser.open(FrameType.WS_OPEN, b'{"path":"/terminal"}')
    daemon.receive(opening)
    [frame] = browser.send(stream, b"y" * 1000)
    assert frame.type is FrameType.WS_DATA
    daemon.receive(frame)
    daemon.receive(browser.close(stream, b"\x03\xe8"))
    browser.receive(daemon.close(stream, b"\x03\xe8"))
    assert browser.open_streams == 0 and daemon.open_streams == 0


def test_only_the_side_that_was_asked_can_respond_and_only_once() -> None:
    browser, daemon = _pair()
    stream, opening = browser.open(FrameType.REQUEST, b"")
    with pytest.raises(StreamError):
        browser.respond(stream, b"")
    daemon.receive(opening)
    daemon.respond(stream, b"")
    with pytest.raises(StreamError):
        daemon.respond(stream, b"")


def test_sending_after_closing_is_refused() -> None:
    browser, _ = _pair()
    stream, _ = browser.open(FrameType.REQUEST, b"")
    browser.close(stream)
    with pytest.raises(StreamError):
        browser.send(stream, b"more")
    with pytest.raises(StreamError):
        browser.close(stream)


def test_a_frame_for_a_stream_that_is_not_open_is_an_error() -> None:
    _, daemon = _pair()
    with pytest.raises(StreamError):
        daemon.receive(Frame(FrameType.DATA, 1, b"x"))


def test_a_peer_cannot_send_after_it_has_closed() -> None:
    browser, daemon = _pair()
    stream, opening = browser.open(FrameType.REQUEST, b"")
    daemon.receive(opening)
    daemon.receive(Frame(FrameType.END, stream))
    with pytest.raises(StreamError):
        daemon.receive(Frame(FrameType.DATA, stream, b"late"))


def test_a_peer_cannot_open_a_stream_with_one_of_our_ids_or_go_backwards() -> None:
    _, daemon = _pair()
    with pytest.raises(StreamError):
        daemon.receive(Frame(FrameType.REQUEST, 2))  # even ids are the daemon's own
    with pytest.raises(StreamError):
        daemon.receive(Frame(FrameType.REQUEST, 0 + 2**32 - 2))
    daemon.receive(Frame(FrameType.REQUEST, 5))
    with pytest.raises(StreamError):
        daemon.receive(Frame(FrameType.REQUEST, 3))
    with pytest.raises(StreamError):
        daemon.receive(Frame(FrameType.REQUEST, 5))


def test_a_flood_of_streams_is_refused() -> None:
    browser = StreamMux(Role.INITIATOR, max_open=3)
    daemon = StreamMux(Role.RESPONDER, max_open=3)
    for _ in range(3):
        daemon.receive(browser.open(FrameType.REQUEST, b"")[1])
    with pytest.raises(StreamError):
        browser.open(FrameType.REQUEST, b"")
    with pytest.raises(StreamError):
        daemon.receive(Frame(FrameType.REQUEST, 7))


def test_a_frame_of_the_wrong_kind_for_the_stream_is_an_error() -> None:
    browser, daemon = _pair()
    stream, opening = browser.open(FrameType.REQUEST, b"")
    daemon.receive(opening)
    with pytest.raises(StreamError):
        daemon.receive(Frame(FrameType.WS_DATA, stream, b"x"))
    with pytest.raises(StreamError):
        daemon.receive(Frame(FrameType.WS_CLOSE, stream))


def test_reset_drops_the_stream_on_both_sides_and_a_crossed_reset_is_fine() -> None:
    browser, daemon = _pair()
    stream, opening = browser.open(FrameType.REQUEST, b"")
    daemon.receive(opening)
    daemon.receive(browser.reset(stream, b"cancelled"))
    assert browser.open_streams == 0 and daemon.open_streams == 0
    daemon.receive(Frame(FrameType.RESET, stream))  # arrives after the stream is gone: ignored


def test_ping_is_answered_by_a_pong_with_the_same_payload() -> None:
    browser, daemon = _pair()
    ping = Frame(FrameType.PING, 0, b"t1")
    daemon.receive(ping)
    assert StreamMux.pong_for(ping) == Frame(FrameType.PONG, 0, b"t1")
    with pytest.raises(StreamError):
        StreamMux.pong_for(Frame(FrameType.PONG, 0))
    browser.receive(Frame(FrameType.PONG, 0, b"t1"))
