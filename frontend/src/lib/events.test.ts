// Unit: the activity log's plain-language helpers (events.ts).

import { describe, expect, it } from "vitest";
import type { EpisodicEventView } from "./api";
import {
  commandText,
  eventLabel,
  formatWhen,
  isRefusedCommand,
  refusalReason,
  summarize,
} from "./events";

function event(event_type: string, payload: Record<string, unknown>): EpisodicEventView {
  return { seq: 1, ts: "2026-10-06T10:00:00Z", event_type, payload } as EpisodicEventView;
}

describe("eventLabel", () => {
  it("names known events in plain words and passes unknown ones through", () => {
    expect(eventLabel("ConsentDecided")).toBe("Command");
    expect(eventLabel("DelegationCompleted")).toBe("Round done");
    expect(eventLabel("SomethingNew")).toBe("SomethingNew");
  });
});

describe("refusalReason", () => {
  it("explains each rule family in plain words", () => {
    expect(refusalReason("no_matching_allow_entry")).toBe("it is not on the allowed list");
    expect(refusalReason("no_shell_allowed")).toContain("no commands");
    expect(refusalReason("never_allowed:force_push")).toBe(
      "always blocked: it would rewrite history on a remote",
    );
    expect(refusalReason("never_allowed:write_outside_root")).toContain("outside the project");
  });
  it("falls back to the raw rule rather than hiding it", () => {
    expect(refusalReason("brand_new_rule")).toBe("brand_new_rule");
    expect(refusalReason("never_allowed:brand_new")).toBe("always blocked: brand_new");
  });
});

describe("commandText", () => {
  it("strips the shell wrapper only when present", () => {
    expect(commandText("/bin/sh -c uv run pytest")).toBe("uv run pytest");
    expect(commandText("write ../x")).toBe("write ../x");
  });
});

describe("summarize", () => {
  it("says a refusal and why, without the wrapper or the rule id", () => {
    const text = summarize(
      event("ConsentDecided", {
        kind: "run_shell",
        detail: "/bin/sh -c docker compose up -d",
        answer: "deny",
        rule: "no_matching_allow_entry",
      }),
    );
    expect(text).toBe("Refused docker compose up -d. It is not on the allowed list.");
    expect(text).not.toContain("/bin/sh");
    expect(text).not.toContain("no_matching_allow_entry");
  });
  it("says an allowed command plainly", () => {
    expect(
      summarize(
        event("ConsentDecided", {
          kind: "run_shell",
          detail: "/bin/sh -c uv run pytest -q",
          answer: "allow",
          rule: "allow:uv run pytest",
        }),
      ),
    ).toBe("Allowed uv run pytest -q");
  });
  it("keeps sequence numbers out of the text", () => {
    const text = summarize(
      event("HandoverWritten", { summary: "did the thing", covers_seq_from: 1, covers_seq_to: 9 }),
    );
    expect(text).toBe("did the thing");
    expect(summarize(event("TeamResumed", { resumed_from_seq: 12 }))).not.toContain("12");
  });
  it("shows an unknown event as JSON instead of dropping it", () => {
    expect(summarize(event("Mystery", { a: 1 }))).toBe('{"a":1}');
  });
});

describe("isRefusedCommand", () => {
  it("is true only for a denied consent decision", () => {
    expect(isRefusedCommand(event("ConsentDecided", { answer: "deny" }))).toBe(true);
    expect(isRefusedCommand(event("ConsentDecided", { answer: "allow" }))).toBe(false);
    expect(isRefusedCommand(event("DelegationRefused", { reason: "x" }))).toBe(false);
  });
});

describe("formatWhen", () => {
  it("shows only the time on the same day and the date on another", () => {
    const now = new Date("2026-10-06T12:00:00");
    expect(formatWhen("2026-10-06T10:00:00", now)).not.toMatch(/Oct/);
    expect(formatWhen("2026-10-04T10:00:00", now)).toMatch(/Oct/);
  });
});

describe("Needs-you events", () => {
  const at = "2026-10-06T10:00:00+00:00";
  const view = (event_type: string, payload: Record<string, unknown>) => ({
    seq: 1,
    ts: at,
    event_type,
    payload,
  });

  it("labels and summarises a raised request and how it ended", () => {
    const raised = view("RequestRaised", {
      title: "builder wants to run a command that isn't on the list",
      detail: "docker compose up -d postgres",
    });
    expect(eventLabel("RequestRaised")).toBe("Needs you");
    expect(summarize(raised)).toBe(
      "builder wants to run a command that isn't on the list: docker compose up -d postgres",
    );
    expect(eventLabel("RequestResolved")).toBe("Answered");
    expect(summarize(view("RequestResolved", { resolution: "expired" }))).toBe(
      "Denied: no answer in time",
    );
    expect(summarize(view("RequestResolved", { resolution: "allowed_once" }))).toBe(
      "Allowed once",
    );
  });

  it("says a kopicode ask call could not be answered", () => {
    const ask = view("ToolCallRecorded", {
      tool: "ask",
      status: "ok",
      detail: '{"question":"Per request or per session?"}',
    });
    expect(summarize(ask)).toBe(
      "Asked a question nobody could answer: Per request or per session?",
    );
    const plain = view("ToolCallRecorded", { tool: "ask", status: "ok", detail: "which limit?" });
    expect(summarize(plain)).toBe("Asked a question nobody could answer: which limit?");
  });
});
