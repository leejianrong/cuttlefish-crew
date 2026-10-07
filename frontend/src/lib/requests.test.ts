import { describe, expect, it } from "vitest";
import type { NeedsYouRequest } from "./api";
import {
  answerProblem,
  backendLabel,
  badgeText,
  formatRemaining,
  isStartOfCommand,
  landsLabel,
  outcomeLabel,
  parseRule,
  ruleText,
  secondsLeft,
} from "./requests";

function request(overrides: Partial<NeedsYouRequest> = {}): NeedsYouRequest {
  return {
    id: "r1",
    kind: "permission",
    state: "pending",
    title: "Builder wants to run a command",
    detail: "docker compose up -d postgres",
    why: "It is not on this project's command list.",
    answers: ["allow_once", "allow_always", "deny"],
    suggested_rule: ["docker", "compose", "up"],
    role: "builder",
    backend: "kopicode",
    lands: "now",
    expires_at: "2026-10-06T10:00:00+00:00",
    project_id: "p1",
    project_name: "alpha",
    expires_in_s: 272,
    ...overrides,
  };
}

describe("formatRemaining", () => {
  it("is m:ss and never negative", () => {
    expect(formatRemaining(272)).toBe("4:32");
    expect(formatRemaining(59.9)).toBe("0:59");
    expect(formatRemaining(600)).toBe("10:00");
    expect(formatRemaining(-5)).toBe("0:00");
  });
});

describe("secondsLeft", () => {
  it("counts down from when the list was fetched", () => {
    expect(secondsLeft(request(), 1_000, 1_000)).toBe(272);
    expect(secondsLeft(request(), 1_000, 11_000)).toBe(262);
    expect(secondsLeft(request(), 1_000, 999_000)).toBe(0);
  });
});

describe("rules", () => {
  it("round-trips words and collapses extra spaces", () => {
    expect(parseRule("  docker   compose up ")).toEqual(["docker", "compose", "up"]);
    expect(parseRule("   ")).toEqual([]);
    expect(ruleText(["make", "test"])).toBe("make test");
    expect(ruleText(null)).toBe("");
  });

  it("accepts only a start of the command", () => {
    const command = "docker compose up -d postgres";
    expect(isStartOfCommand(["docker", "compose"], command)).toBe(true);
    expect(isStartOfCommand(command.split(" "), command)).toBe(true);
    expect(isStartOfCommand(["docker", "run"], command)).toBe(false);
    expect(isStartOfCommand([], command)).toBe(false);
    expect(isStartOfCommand([...command.split(" "), "x"], command)).toBe(false);
  });
});

describe("labels", () => {
  it("says when an answer lands", () => {
    expect(landsLabel("now")).toBe("Paused until you answer");
    expect(landsLabel("end_of_turn")).toBe("Delivered when this turn ends");
    expect(landsLabel("next_round")).toBe("Takes effect next round");
  });

  it("says how a request ended", () => {
    expect(outcomeLabel(request({ state: "denied" }))).toBe("Denied");
    expect(outcomeLabel(request({ state: "expired" }))).toBe("Denied: no answer in time");
    expect(outcomeLabel(request({ state: "cancelled" }))).toContain("stopped");
    expect(outcomeLabel(request({ state: "abandoned" }))).toContain("ended");
    expect(outcomeLabel(request({ state: "allowed_always", rule: ["make", "test"] }))).toBe(
      "Always allowed: make test",
    );
  });

  it("never says a blocked request was denied, since nothing was asked", () => {
    const blocked = (state: NeedsYouRequest["state"]) => request({ kind: "blocked", state });
    expect(outcomeLabel(blocked("pending"))).toBe("Needs a fix");
    expect(outcomeLabel(blocked("superseded"))).toBe("Ended: you steered it");
    expect(outcomeLabel(blocked("cancelled"))).toBe("Ended: the team was stopped");
    expect(outcomeLabel(blocked("abandoned"))).toBe("Ended: the team ended");
  });

  it("explains an answer that did not go through", () => {
    expect(answerProblem(409, "x")).toContain("already ended");
    expect(answerProblem(404, "x")).toContain("gone");
    expect(answerProblem(422, "'sh' is too broad")).toBe("'sh' is too broad");
    expect(answerProblem(500, "x")).toContain("daemon");
  });

  it("shows no badge at zero and caps it", () => {
    expect(badgeText(0)).toBe("");
    expect(badgeText(3)).toBe("3");
    expect(badgeText(12)).toBe("9+");
  });
});

describe("backendLabel", () => {
  it("names the agents the way people do", () => {
    expect(backendLabel("claude-code")).toBe("Claude Code");
    expect(backendLabel("codex")).toBe("Codex");
    expect(backendLabel("kopicode")).toBe("kopicode");
    expect(backendLabel("other")).toBe("other");
  });
});
