// Unit: the chat thread's pure parts (chat.ts).

import { describe, expect, it } from "vitest";
import {
  EMPTY_CHAT,
  applyChat,
  chatNotice,
  composerState,
  clockTime,
  toRows,
  toolLabel,
  type ChatItem,
  type ChatResponse,
  type ToolCall,
} from "./chat";

const say = (id: string, kind: "user" | "assistant", text: string): ChatItem => ({
  id,
  kind,
  at: "2026-10-22T10:00:00.000Z",
  text,
});
const call = (id: string, name: string, summary = ""): ChatItem => ({
  id: `c${id}`,
  kind: "tool",
  at: "2026-10-22T10:00:01.000Z",
  tool_id: id,
  name,
  summary,
});
const result = (id: string, text: string, is_error = false): ChatItem => ({
  id: `r${id}`,
  kind: "tool_result",
  at: "2026-10-22T10:00:02.000Z",
  tool_id: id,
  text,
  is_error,
});
const read = (items: ChatItem[], extra: Partial<ChatResponse> = {}): ChatResponse => ({
  backend: "claude-code",
  session: "running",
  session_id: "s",
  supported: true,
  items,
  cursor: 10,
  ...extra,
});

describe("applyChat", () => {
  it("appends new items and moves the cursor", () => {
    const first = applyChat(EMPTY_CHAT, read([say("a", "user", "hi")], { cursor: 5 }));
    const next = applyChat(first, read([say("b", "assistant", "yo")], { cursor: 9 }));
    expect(next.items.map((i) => i.id)).toEqual(["a", "b"]);
    expect(next.cursor).toBe(9);
  });

  it("does not show an item twice", () => {
    const first = applyChat(EMPTY_CHAT, read([say("a", "user", "hi")]));
    const again = applyChat(first, read([say("a", "user", "hi")], { cursor: 12 }));
    expect(again.items).toHaveLength(1);
    expect(again.cursor).toBe(12);
  });

  it("keeps the same array when nothing was added, so the page does not redraw", () => {
    const first = applyChat(EMPTY_CHAT, read([say("a", "user", "hi")]));
    expect(applyChat(first, read([], { cursor: 11 })).items).toBe(first.items);
  });

  it("starts again on a reset", () => {
    const first = applyChat(EMPTY_CHAT, read([say("a", "user", "old")]));
    const next = applyChat(first, read([say("z", "user", "new")], { reset: true, cursor: 3 }));
    expect(next.items.map((i) => i.id)).toEqual(["z"]);
  });

  it("holds nothing for a backend it cannot read or a record not there yet", () => {
    expect(applyChat(EMPTY_CHAT, read([], { supported: false }))).toBe(EMPTY_CHAT);
    expect(applyChat(EMPTY_CHAT, read([], { waiting: true }))).toBe(EMPTY_CHAT);
  });
});

describe("toolLabel", () => {
  const calls = (...pairs: [string, string][]): ToolCall[] =>
    pairs.map(([name, summary], i) => ({ toolId: String(i), name, summary, result: null }));

  it("counts commands and distinct edited files", () => {
    expect(
      toolLabel(calls(["Bash", "ls"], ["Bash", "pwd"], ["Edit", "a.py"], ["Edit", "a.py"], ["Write", "b.py"])),
    ).toBe("Ran 2 commands, edited 2 files");
  });

  it("uses singular and names the rest", () => {
    expect(toolLabel(calls(["Bash", "ls"], ["Read", "x"], ["Grep", "y"], ["WebSearch", "z"], ["Weird", ""]))).toBe(
      "Ran 1 command, read 1 file, searched 1 time, looked up 1 page, used 1 other tool",
    );
  });
});

describe("toRows", () => {
  it("folds a run of tool calls into one row and attaches results to their calls", () => {
    const rows = toRows([
      say("u", "user", "do it"),
      call("1", "Bash", "ls"),
      result("1", "a.py"),
      call("2", "Edit", "a.py"),
      result("2", "bad", true),
      say("a", "assistant", "done"),
    ]);
    expect(rows.map((r) => r.t)).toEqual(["message", "tools", "message"]);
    const tools = rows[1];
    if (tools.t !== "tools") throw new Error("expected tools");
    expect(tools.calls.map((c) => c.result?.text)).toEqual(["a.py", "bad"]);
    expect(tools.label).toBe("Ran 1 command, edited 1 file");
    expect(tools.failed).toBe(1);
  });

  it("starts a new group after a message", () => {
    const rows = toRows([call("1", "Bash"), say("a", "assistant", "x"), call("2", "Bash")]);
    expect(rows.map((r) => r.t)).toEqual(["tools", "message", "tools"]);
  });

  it("shows a result whose call was before the window on its own", () => {
    const rows = toRows([result("9", "late")]);
    const tools = rows[0];
    if (tools.t !== "tools") throw new Error("expected tools");
    expect(tools.calls[0].result?.text).toBe("late");
    expect(tools.label).toBe("Used 1 other tool");
  });

  it("keeps dividers, notes and commands as their own rows and says who spoke", () => {
    const rows = toRows([
      { id: "d", kind: "divider", at: null, text: "Context was compacted" },
      { id: "n", kind: "note", at: null, text: "Goal: x" },
      { id: "m", kind: "command", at: null, text: "/compact" },
      say("u", "user", "hi"),
      say("a", "assistant", "yo"),
    ]);
    expect(rows.map((r) => r.t)).toEqual(["divider", "note", "command", "message", "message"]);
    expect(rows.slice(3).map((r) => (r.t === "message" ? r.who : ""))).toEqual(["you", "builder"]);
  });
});

describe("clockTime", () => {
  it("is empty for nothing or nonsense", () => {
    expect(clockTime(null)).toBe("");
    expect(clockTime("later")).toBe("");
  });
  it("is hours and minutes for a time", () => {
    expect(clockTime("2026-10-22T10:04:00.000Z")).toMatch(/\d{1,2}[:.]\d{2}/);
  });
});

describe("chatNotice", () => {
  it("says what it is waiting for, and is silent when there is a thread", () => {
    expect(chatNotice(null, 0)).toContain("Reading");
    expect(chatNotice(read([], { supported: false, backend: "codex" }), 0)).toContain("Codex");
    expect(chatNotice(read([], { supported: false, backend: "kopicode" }), 0)).toContain("kopicode");
    expect(chatNotice(read([], { supported: false, backend: null }), 0)).toContain("Give it a goal");
    expect(chatNotice(read([], { waiting: true }), 0)).toContain("trust it first");
    expect(chatNotice(read([]), 0)).toBe("Nothing has been said yet.");
    expect(chatNotice(read([], { session: "stopped" }), 0)).toBe("This session has no messages.");
    expect(chatNotice(read([say("a", "user", "x")]), 1)).toBeNull();
  });
});

describe("composerState", () => {
  const withActivity = (state: NonNullable<ChatResponse["activity"]>["state"], session: ChatResponse["session"] = "running") =>
    read([], { session, activity: { state, seconds: 1 } });

  it("is hidden for a builder whose input is not wired in", () => {
    expect(composerState(null)).toBeNull();
    expect(composerState(read([], { supported: false, backend: "codex" }))).toBeNull();
  });

  it("is off, with the reason, when the builder is not running", () => {
    const stopped = composerState(withActivity("none", "stopped"));
    expect(stopped?.enabled).toBe(false);
    expect(stopped?.hint).toContain("not running");
  });

  it("is off while a permission waits, and says a message would be its answer", () => {
    const dialog = composerState(withActivity("dialog"));
    expect(dialog?.enabled).toBe(false);
    expect(dialog?.hint).toContain("read as the answer");
  });

  it("says a message to a working builder is queued", () => {
    expect(composerState(withActivity("busy"))).toMatchObject({ enabled: true });
    expect(composerState(withActivity("busy"))?.hint).toContain("queued");
  });

  it("is on when idle, and on with a caveat when it cannot tell", () => {
    expect(composerState(withActivity("idle"))).toEqual({ enabled: true, hint: "The builder is waiting for you." });
    expect(composerState(withActivity("unknown"))?.hint).toContain("not known");
  });
});
