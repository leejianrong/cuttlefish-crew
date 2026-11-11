import { describe, expect, it } from "vitest";
import type { AgentSession, Goal } from "./api";
import type { AutopilotStatus } from "./autopilot";
import { stripParts, type StripInput } from "./statusstrip";

const session = (over: Partial<AgentSession> = {}): AgentSession => ({
  state: "running",
  tmux_session: "t",
  backend: "claude-code",
  session_id: null,
  last_action: "started",
  at: "2026-11-12T09:30:00Z",
  ...over,
});
const autopilot = (over: Partial<AutopilotStatus> = {}): AutopilotStatus => ({
  phase: "running",
  plan: null,
  plan_path: "",
  elapsed_s: 4500,
  tokens_used: 1_200_000,
  ...over,
});
const base = (over: Partial<StripInput> = {}): StripInput => ({
  session: session(),
  teamRunning: false,
  usage: {},
  autopilot: autopilot(),
  goal: { text: "g", branch: "cuttlefish/x", pr_url: "https://example.test/pr/1" } as Goal,
  stat: { ok: true, files_changed: 3, additions: 12, deletions: 4, dirty: true },
  ...over,
});
const texts = (input: StripInput) => stripParts(input, new Date("2026-11-12T12:00:00Z")).map((p) => p.text);

describe("status strip (CUT-286)", () => {
  it("says what runs, its figures, the branch with its stat and the pull request", () => {
    const parts = texts(base());
    expect(parts[0]).toBe("Claude Code running");
    expect(parts).toContain("Autopilot: Running on its own");
    expect(parts.find((t) => t.startsWith("Started"))).toBeTruthy();
    expect(parts).toContain("1.2M tokens");
    expect(parts).toContain("1 h 15 m");
    expect(parts).toContain("cuttlefish/x +12 −4 (uncommitted)");
    expect(parts.at(-1)).toBe("Pull request");
  });

  it("shows nothing for a project that was never given work", () => {
    expect(stripParts(base({ session: session({ state: "none" }), goal: null, autopilot: null }))).toEqual([]);
    expect(stripParts(base({ session: null, goal: null, autopilot: null }))).toEqual([]);
  });

  it("marks a stopped builder and a plan waiting for you", () => {
    const stopped = stripParts(
      base({ session: session({ state: "stopped" }), autopilot: autopilot({ phase: "awaiting_approval" }) }),
    );
    expect(stopped[0].text).toBe("Stopped");
    expect(stopped.find((p) => p.key === "autopilot")?.tone).toBe("attention");
  });

  it("uses a team's usage when there is no Autopilot, and leaves the stat out when it has no files", () => {
    const input = base({
      session: null,
      teamRunning: true,
      autopilot: null,
      usage: { a: { tokens: 800, cost_usd: null }, b: { tokens: 700, cost_usd: null } },
      stat: { ok: true, files_changed: 0 },
    });
    expect(texts(input)).toEqual(["Team running", "2k tokens", "cuttlefish/x", "Pull request"]);
  });

  it("does not guess at a stat that was refused", () => {
    expect(texts(base({ stat: { ok: false, code: "on_worker" } }))).toContain("cuttlefish/x");
  });
});
