import { describe, expect, it, vi } from "vitest";
import { WS_ACCEPTED, WS_BINARY, WS_TEXT } from "./frames";
import { RelayError, RelaySocket } from "./connection";

function open() {
  const sent: { kind: number; data: Uint8Array }[] = [];
  const closes: [number, string][] = [];
  const socket = new RelaySocket();
  socket.attach({
    sendData: (kind, data) => sent.push({ kind, data }),
    sendClose: (code, reason) => {
      closes.push([code, reason]);
      socket.finish(code, reason);
    },
  });
  return { socket, sent, closes };
}

describe("RelaySocket", () => {
  it("is connecting until the daemon's app accepts it, then open", () => {
    const { socket } = open();
    const onopen = vi.fn();
    socket.onopen = onopen;
    expect(socket.readyState).toBe(0);
    expect(() => socket.send("too soon")).toThrow(RelayError);
    socket.accepted();
    expect(socket.readyState).toBe(1);
    expect(onopen).toHaveBeenCalledOnce();
  });

  it("sends text and bytes with their kind byte", () => {
    const { socket, sent } = open();
    socket.accepted();
    socket.send("hi");
    socket.send(new Uint8Array([1, 2, 3]));
    socket.send(new Uint8Array([9]).buffer);
    expect(sent.map((s) => [s.kind, [...s.data]])).toEqual([
      [WS_TEXT, [104, 105]],
      [WS_BINARY, [1, 2, 3]],
      [WS_BINARY, [9]],
    ]);
  });

  it("hands text as a string and bytes as an ArrayBuffer, like a WebSocket set to arraybuffer", () => {
    const { socket } = open();
    const seen: unknown[] = [];
    socket.onmessage = (event) => seen.push(event.data);
    socket.accepted();
    socket.message(WS_TEXT, new TextEncoder().encode('{"type":"ready"}'));
    socket.message(WS_BINARY, new Uint8Array([7, 8]));
    expect(seen[0]).toBe('{"type":"ready"}');
    expect(seen[1]).toBeInstanceOf(ArrayBuffer);
    expect([...new Uint8Array(seen[1] as ArrayBuffer)]).toEqual([7, 8]);
    socket.message(WS_ACCEPTED, new Uint8Array(0));
    expect(seen).toHaveLength(2);
  });

  it("closes once, with the code and reason it was given, whichever side closes", () => {
    const { socket, closes } = open();
    const onclose = vi.fn();
    socket.onclose = onclose;
    socket.accepted();
    socket.close(4000, "bye");
    socket.close(1000);
    socket.finish(1006, "late");
    expect(closes).toEqual([[4000, "bye"]]);
    expect(onclose).toHaveBeenCalledOnce();
    expect(onclose.mock.calls[0][0]).toMatchObject({ code: 4000, reason: "bye" });
    expect(socket.readyState).toBe(3);
  });

  it("reports a connection that died as 1006", () => {
    const { socket } = open();
    const onclose = vi.fn();
    socket.onclose = onclose;
    socket.finish(1006, "connection lost");
    expect(onclose.mock.calls[0][0]).toMatchObject({ code: 1006 });
  });
});
