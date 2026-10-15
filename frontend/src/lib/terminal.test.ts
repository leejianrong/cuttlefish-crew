// Unit: the terminal tab's plumbing (terminal.ts).

import { describe, expect, it } from "vitest";
import {
  KEY_ROW,
  authMessage,
  describeClose,
  resizeMessage,
  terminalUrl,
} from "./terminal";

describe("terminalUrl", () => {
  it("swaps the scheme and keeps the host and port", () => {
    expect(terminalUrl("http://127.0.0.1:8420", "p1")).toBe(
      "ws://127.0.0.1:8420/api/projects/p1/terminal",
    );
    expect(terminalUrl("https://box.tailnet.ts.net/", "p1")).toBe(
      "wss://box.tailnet.ts.net/api/projects/p1/terminal",
    );
  });

  it("escapes the project id", () => {
    expect(terminalUrl("http://h", "a/b")).toContain("a%2Fb");
  });
});

describe("messages", () => {
  it("signs in with the token in the first frame, never in the address", () => {
    expect(JSON.parse(authMessage("tok", 100, 30))).toEqual({
      type: "auth",
      token: "tok",
      size: { cols: 100, rows: 30 },
    });
    expect(terminalUrl("http://h", "p")).not.toContain("tok");
  });

  it("resizes", () => {
    expect(JSON.parse(resizeMessage(80, 24))).toEqual({
      type: "resize",
      cols: 80,
      rows: 24,
    });
  });
});

describe("KEY_ROW", () => {
  it("sends the bytes a terminal expects", () => {
    const byLabel = Object.fromEntries(
      KEY_ROW.map((key) => [key.label, key.send]),
    );
    expect(byLabel["Esc"]).toBe("\x1b");
    expect(byLabel["Ctrl-C"]).toBe("\x03");
    expect(byLabel["↑"]).toBe("\x1b[A");
    expect(byLabel["Enter"]).toBe("\r");
  });

  it("labels every key for a screen reader", () => {
    expect(KEY_ROW.every((key) => key.aria.length > 0)).toBe(true);
  });
});

describe("describeClose", () => {
  it("names the daemon's own codes", () => {
    expect(describeClose(4401, "not authorised").kind).toBe("unauthorised");
    const none = describeClose(
      4404,
      "no agent session is running; start it from the Overview first",
    );
    expect(none.kind).toBe("no-session");
    expect(none.text).toContain("start it from the Overview");
  });

  it("says the agent keeps running when the view goes away", () => {
    expect(describeClose(1006, "").text).toContain("still running in tmux");
    expect(describeClose(1000, "").kind).toBe("ended");
  });
});
