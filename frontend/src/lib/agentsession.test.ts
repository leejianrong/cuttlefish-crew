// Unit: the Agent session card's copy (agentsession.ts).

import { describe, expect, it } from "vitest";
import type { AgentSession } from "./api";
import {
  backendLabel,
  explanation,
  lastActionText,
  stateText,
} from "./agentsession";

function session(over: Partial<AgentSession>): AgentSession {
  return {
    state: "running",
    tmux_session: "cf-p1",
    backend: "claude-code",
    session_id: "abc",
    last_action: null,
    at: null,
    ...over,
  };
}

describe("lastActionText", () => {
  it("says which of the three things happened", () => {
    expect(lastActionText(session({ last_action: "started" }))).toBe(
      "Started a new Claude Code session.",
    );
    expect(lastActionText(session({ last_action: "reattached" }))).toContain(
      "reattached",
    );
    expect(lastActionText(session({ last_action: "relaunched" }))).toContain(
      "resume command",
    );
  });

  it("says nothing before a session has ever come up", () => {
    expect(
      lastActionText(session({ state: "none", last_action: null })),
    ).toBeNull();
  });
});

describe("stateText and explanation", () => {
  it("names each state", () => {
    expect(stateText(session({ state: "running" }))).toBe("Running in tmux");
    expect(stateText(session({ state: "stopped" }))).toBe("Stopped");
    expect(stateText(session({ state: "none" }))).toBe("Not started");
  });

  it("tells a running session's user where permission questions go", () => {
    expect(explanation(session({ state: "running" }))).toContain(
      "not under Needs you",
    );
    expect(explanation(session({ state: "stopped" }))).toContain(
      "where it left off",
    );
  });
});

describe("backendLabel", () => {
  it("uses the product names and falls back to the raw name", () => {
    expect(backendLabel("codex")).toBe("Codex");
    expect(backendLabel("other")).toBe("other");
    expect(backendLabel(null)).toBe("the agent");
  });
});
