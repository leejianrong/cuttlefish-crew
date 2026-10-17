// Unit: stream ids, chunking and what a peer may send (ADR-0037). Mirrors the Python tests.

import { describe, expect, it } from "vitest";
import { FrameType, frame } from "./frames";
import { StreamError, StreamMux } from "./streams";

const none = new Uint8Array(0);
const pair = () => ({ browser: new StreamMux("initiator"), daemon: new StreamMux("responder") });

describe("StreamMux", () => {
  it("carries a request and its response end to end", () => {
    const { browser, daemon } = pair();
    const { stream, frame: opening } = browser.open(FrameType.REQUEST, new TextEncoder().encode("{}"));
    daemon.receive(opening);
    daemon.receive(browser.close(stream));
    browser.receive(daemon.respond(stream, none));
    for (const f of daemon.send(stream, new Uint8Array(60000 * 2 + 5))) browser.receive(f);
    browser.receive(daemon.close(stream));
    expect(browser.openStreams).toBe(0);
    expect(daemon.openStreams).toBe(0);
  });

  it("gives the browser odd ids and the daemon even ones", () => {
    const { browser, daemon } = pair();
    expect([1, 2, 3].map(() => browser.open(FrameType.REQUEST, none).stream)).toEqual([1, 3, 5]);
    expect([1, 2, 3].map(() => daemon.open(FrameType.REQUEST, none).stream)).toEqual([2, 4, 6]);
  });

  it("cuts a body into chunks that fit, but sends a websocket message whole", () => {
    const { browser, daemon } = pair();
    const request = browser.open(FrameType.REQUEST, none);
    expect(browser.send(request.stream, new Uint8Array(150000)).map((f) => f.payload.length)).toEqual([60000, 60000, 30000]);
    const socket = browser.open(FrameType.WS_OPEN, none);
    daemon.receive(socket.frame);
    const [message] = browser.send(socket.stream, new Uint8Array(1000));
    expect(message.type).toBe(FrameType.WS_DATA);
    daemon.receive(message);
    daemon.receive(browser.close(socket.stream, Uint8Array.of(3, 232)));
    browser.receive(daemon.close(socket.stream));
    expect(daemon.openStreams).toBe(0); // the socket is closed both ways; the request never reached the daemon
  });

  it("lets only the asked side respond, once", () => {
    const { browser, daemon } = pair();
    const { stream, frame: opening } = browser.open(FrameType.REQUEST, none);
    expect(() => browser.respond(stream, none)).toThrow(StreamError);
    daemon.receive(opening);
    daemon.respond(stream, none);
    expect(() => daemon.respond(stream, none)).toThrow(StreamError);
  });

  it("refuses sending after closing", () => {
    const { browser } = pair();
    const { stream } = browser.open(FrameType.REQUEST, none);
    browser.close(stream);
    expect(() => browser.send(stream, Uint8Array.of(1))).toThrow(StreamError);
    expect(() => browser.close(stream)).toThrow(StreamError);
  });

  it("refuses frames for a stream that is not open, or after the peer closed", () => {
    const { browser, daemon } = pair();
    expect(() => daemon.receive(frame(FrameType.DATA, 1, Uint8Array.of(1)))).toThrow(StreamError);
    const { frame: opening, stream } = browser.open(FrameType.REQUEST, none);
    daemon.receive(opening);
    daemon.receive(frame(FrameType.END, stream));
    expect(() => daemon.receive(frame(FrameType.DATA, stream, Uint8Array.of(1)))).toThrow(StreamError);
  });

  it("refuses a peer that opens with our ids, reuses one, or goes backwards", () => {
    const { daemon } = pair();
    expect(() => daemon.receive(frame(FrameType.REQUEST, 2))).toThrow(StreamError);
    daemon.receive(frame(FrameType.REQUEST, 5));
    expect(() => daemon.receive(frame(FrameType.REQUEST, 3))).toThrow(StreamError);
    expect(() => daemon.receive(frame(FrameType.REQUEST, 5))).toThrow(StreamError);
  });

  it("refuses a flood of streams", () => {
    const browser = new StreamMux("initiator", 3);
    const daemon = new StreamMux("responder", 3);
    for (let i = 0; i < 3; i++) daemon.receive(browser.open(FrameType.REQUEST, none).frame);
    expect(() => browser.open(FrameType.REQUEST, none)).toThrow(StreamError);
    expect(() => daemon.receive(frame(FrameType.REQUEST, 7))).toThrow(StreamError);
  });

  it("refuses a frame of the wrong kind for the stream", () => {
    const { browser, daemon } = pair();
    const { frame: opening, stream } = browser.open(FrameType.REQUEST, none);
    daemon.receive(opening);
    expect(() => daemon.receive(frame(FrameType.WS_DATA, stream, Uint8Array.of(1)))).toThrow(StreamError);
    expect(() => daemon.receive(frame(FrameType.WS_CLOSE, stream))).toThrow(StreamError);
  });

  it("drops a reset stream on both sides, and ignores a reset that crossed a close", () => {
    const { browser, daemon } = pair();
    const { frame: opening, stream } = browser.open(FrameType.REQUEST, none);
    daemon.receive(opening);
    daemon.receive(browser.reset(stream));
    expect(browser.openStreams + daemon.openStreams).toBe(0);
    expect(() => daemon.receive(frame(FrameType.RESET, stream))).not.toThrow();
  });

  it("answers a ping with a pong carrying the same payload", () => {
    const ping = frame(FrameType.PING, 0, Uint8Array.of(7));
    expect(StreamMux.pongFor(ping)).toEqual(frame(FrameType.PONG, 0, Uint8Array.of(7)));
    expect(() => StreamMux.pongFor(frame(FrameType.PONG, 0))).toThrow(StreamError);
  });
});
