// Unit: what the bell and the browser notice are made from (notify.ts).

import { afterEach, describe, expect, it, vi } from "vitest";
import type { NeedsYouRequest, ProjectSummary, RoleStatus } from "./api";
import { addEvents, createTracker, eventText, permission, reach, show, summarise, unreadCount } from "./notify";

const req = (id: string) => ({ id, project_id: "p", title: "rm -rf secret-thing" }) as NeedsYouRequest;
const proj = (id: string, status: RoleStatus): ProjectSummary =>
  ({
    id,
    name: id,
    status: { builder: status },
    running: false,
    require_approval: false,
    last_event_at: new Date().toISOString(),
  }) as unknown as ProjectSummary;

afterEach(() => vi.unstubAllGlobals());

describe("tracker", () => {
  it("treats the first poll as a baseline and then reports only new requests", () => {
    const t = createTracker(() => 5);
    expect(t.requests([req("a")])).toEqual([]);
    expect(t.requests([req("a"), req("b")]).map((e) => e.id)).toEqual(["req:b"]);
    expect(t.requests([req("a"), req("b")])).toEqual([]);
  });
  it("reports a project that moves to done, not one that was already done or is new", () => {
    const t = createTracker();
    expect(t.projects([proj("x", "working"), proj("y", "done")], {})).toEqual([]);
    const done = t.projects([proj("x", "done"), proj("y", "done"), proj("z", "done")], {});
    expect(done.map((e) => e.kind)).toEqual(["done"]);
    expect(done[0].id.startsWith("done:x:")).toBe(true);
  });
});

describe("text", () => {
  it("names no project and no request", () => {
    const events = createTracker().requests([]);
    expect(events).toEqual([]);
    expect(eventText("needs-you", 1)).toBe("A project needs you");
    expect(eventText("done", 3)).toBe("3 projects finished");
    expect(summarise([{ id: "1", kind: "needs-you", at: 0 }, { id: "2", kind: "done", at: 0 }])).toBe(
      "A project needs you. A project finished",
    );
  });
  it("says plainly when a notice cannot reach you", () => {
    expect(reach("denied", true)).toContain("blocked");
    expect(reach("granted", true)).toContain("only fire while this dashboard is open");
    expect(reach("unsupported", false)).toContain("cannot show");
  });
});

describe("unread", () => {
  it("counts events after the last look and keeps the newest 20", () => {
    const e = (id: string, at: number) => ({ id, kind: "done" as const, at });
    expect(unreadCount([e("a", 1), e("b", 5)], 3)).toBe(1);
    const many = addEvents([], Array.from({ length: 30 }, (_, i) => e(String(i), i)));
    expect(many).toHaveLength(20);
  });
});

describe("show", () => {
  const events = [{ id: "1", kind: "done" as const, at: 0 }];
  function stub(permissionValue: string, visibility: string) {
    const made = vi.fn();
    class N {
      static permission = permissionValue;
      constructor(...args: unknown[]) {
        made(...args);
      }
    }
    vi.stubGlobal("Notification", N);
    vi.stubGlobal("document", { visibilityState: visibility });
    return made;
  }
  it("fires once, content-free, when on, allowed and the tab is hidden", () => {
    const made = stub("granted", "hidden");
    show(events, true);
    expect(made).toHaveBeenCalledWith("cuttlefish-crew", { body: "A project finished", tag: "cuttlefish-crew" });
  });
  it("stays quiet when muted, denied or the tab is visible", () => {
    const made = stub("granted", "hidden");
    show(events, false);
    stub("denied", "hidden");
    show(events, true);
    const visible = stub("granted", "visible");
    show(events, true);
    expect(made).not.toHaveBeenCalled();
    expect(visible).not.toHaveBeenCalled();
  });
  it("reports unsupported without a Notification", () => {
    vi.stubGlobal("Notification", undefined);
    expect(permission()).toBe("unsupported");
  });
});
