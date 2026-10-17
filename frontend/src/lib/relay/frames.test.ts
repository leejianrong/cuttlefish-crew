// Unit: the frame format (ADR-0037). The byte layout is the same one the Python tests pin.

import { describe, expect, it } from "vitest";
import { CHUNK_SIZE, FrameError, FrameType, HEADER_LEN, MAX_PAYLOAD, chunks, decode, encode, frame } from "./frames";
import { MAX_MESSAGE_LEN, TAG_LEN } from "./noise";

describe("frames", () => {
  it("round trips every type", () => {
    for (const type of Object.values(FrameType)) {
      const stream = type === FrameType.PING || type === FrameType.PONG || type === FrameType.CONTROL ? 0 : 3;
      const original = frame(type, stream, Uint8Array.of(0, 255));
      expect(decode(encode(original))).toEqual(original);
    }
  });

  it("has a five byte big-endian header, matching the Python side", () => {
    expect([...encode(frame(FrameType.END, 0x01020304))]).toEqual([4, 1, 2, 3, 4]);
    expect(HEADER_LEN).toBe(5);
  });

  it("a full frame fits exactly in one Noise message", () => {
    const full = frame(FrameType.DATA, 1, new Uint8Array(MAX_PAYLOAD));
    expect(encode(full).length + TAG_LEN).toBe(MAX_MESSAGE_LEN);
    expect(CHUNK_SIZE).toBeLessThan(MAX_PAYLOAD);
    expect(() => frame(FrameType.DATA, 1, new Uint8Array(MAX_PAYLOAD + 1))).toThrow(FrameError);
  });

  it("refuses bytes that are not a frame", () => {
    expect(() => decode(Uint8Array.of(3, 0, 0))).toThrow(FrameError);
    expect(() => decode(Uint8Array.of(99, 0, 0, 0, 1))).toThrow(FrameError);
    expect(() => decode(new Uint8Array(0))).toThrow(FrameError);
  });

  it("keeps ping and pong on stream 0 and everything else off it", () => {
    expect(() => frame(FrameType.PING, 5)).toThrow(FrameError);
    expect(() => frame(FrameType.DATA, 0)).toThrow(FrameError);
    expect(() => frame(FrameType.PONG, 0)).not.toThrow();
  });

  it("limits stream ids to 32 bits", () => {
    expect(() => frame(FrameType.DATA, 2 ** 32 - 1)).not.toThrow();
    expect(() => frame(FrameType.DATA, 2 ** 32)).toThrow(FrameError);
    expect(() => frame(FrameType.DATA, -1)).toThrow(FrameError);
    expect(() => frame(FrameType.DATA, 1.5)).toThrow(FrameError);
  });

  it("decodes from a view into a larger buffer", () => {
    const wire = encode(frame(FrameType.DATA, 9, Uint8Array.of(1, 2, 3)));
    const padded = new Uint8Array(wire.length + 4);
    padded.set(wire, 2);
    expect(decode(padded.subarray(2, 2 + wire.length))).toEqual(frame(FrameType.DATA, 9, Uint8Array.of(1, 2, 3)));
  });

  it("cuts a body without losing a byte, and an empty body is no chunks", () => {
    const body = Uint8Array.from({ length: 250000 }, (_, i) => i % 251);
    const pieces = chunks(body);
    expect(pieces.map((p) => p.length)).toEqual([60000, 60000, 60000, 60000, 10000]);
    expect(Uint8Array.from(pieces.flatMap((p) => [...p]))).toEqual(body);
    expect(chunks(new Uint8Array(0))).toEqual([]);
    expect(() => chunks(body, 0)).toThrow(FrameError);
  });
});

describe("websocket kind bytes", () => {
  it("match the daemon's (frames.py pins the same three numbers)", async () => {
    const { WS_TEXT, WS_BINARY, WS_ACCEPTED } = await import("./frames");
    expect([WS_TEXT, WS_BINARY, WS_ACCEPTED]).toEqual([0, 1, 2]);
  });
});
