// Unit: the activity log's plain-language helpers (events.ts).

import { describe, expect, it } from "vitest";
import type { EpisodicEventView } from "./api";
import {
  checkpointedRounds,
  commandText,
  eventLabel,
  eventLabelFor,
  failureText,
  installOutput,
  formatWhen,
  isRefusedCommand,
  latestRound,
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
    expect(eventLabel("TeamStopped")).toBe("Stopped");
    expect(summarize(event("TeamStopped", {}))).toContain("You stopped the team");
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
    expect(summarize(view("RequestResolved", { resolution: "allowed_once" }))).toBe("Allowed once");
  });

  it("words a kopicode ask call without claiming it went unanswered", () => {
    const ask = view("ToolCallRecorded", {
      tool: "ask",
      status: "ok",
      detail: '{"question":"Per request or per session?"}',
    });
    expect(summarize(ask)).toBe(
      "Asked a question: Per request or per session?",
    );
    const plain = view("ToolCallRecorded", { tool: "ask", status: "ok", detail: "which limit?" });
    expect(summarize(plain)).toBe("Asked a question: which limit?");
    const cut = view("ToolCallRecorded", {
      tool: "ask",
      status: "ok",
      detail: '{"question":"Which of the two retry settings should win when they disagree, the …',
    });
    expect(summarize(cut)).toBe(
      "Asked a question: Which of the two retry settings should win when they disagree, the …",
    );
  });
});

describe("failureText", () => {
  it("says why in words and keeps the raw reason", () => {
    expect(failureText("max_turns", "stop=max_turns exit_code=4")).toBe(
      "It used all its turns before finishing. (stop=max_turns exit_code=4)",
    );
  });
  it("reads the stop word when an older event has no failure kind", () => {
    expect(failureText(undefined, "stop=max_turns exit_code=4")).toContain("all its turns");
    expect(failureText(null, "stop=verification_failed exit_code=5")).toContain("tests");
  });
  it("shows an unknown reason exactly as it is", () => {
    expect(failureText(null, "rpc error -32000: boom")).toBe("rpc error -32000: boom");
    expect(failureText("something_new", "stop=x")).toBe("stop=x");
  });
});

describe("failed rounds in the activity log", () => {
  it("says a round continued on its own, and why", () => {
    expect(eventLabel("RoundContinued")).toBe("Continued");
    const turns = summarize(event("RoundContinued", { reason: "max_turns", count: 2, limit: 20 }));
    expect(turns).toContain("turn limit");
    expect(turns).toContain("2 of 20");
    expect(summarize(event("RoundContinued", { reason: "budget_exhausted", count: 1, limit: 5 }))).toContain(
      "token budget",
    );
  });

  it("words a round stopped by the time limit", () => {
    expect(failureText("round_timeout", "stopped: the round ran longer than 7200 seconds")).toContain(
      "time limit",
    );
    const text = summarize(event("RoundContinued", { reason: "round_timeout", count: 1, limit: 20 }));
    expect(text).toContain("time limit");
  });

  it("labels the round and the task apart and does not repeat the raw stop reason", () => {
    expect(eventLabel("DelegationFailed")).toBe("Round failed");
    expect(eventLabel("TaskFailed")).toBe("Task failed");
    const round = event("DelegationFailed", {
      reason: "stop=max_turns exit_code=4",
      failure_kind: "max_turns",
      role: "builder",
    });
    const task = event("TaskFailed", { error: "stop=max_turns exit_code=4", role: "builder" });
    expect(summarize(round)).toContain("all its turns");
    expect(summarize(task)).toBe("The task ended because its last round failed.");
  });
  it("says a stuck agent was stopped and shows its last failing command", () => {
    const round = event("DelegationFailed", {
      reason: "stopped after 5 shell commands in a row failed on the environment",
      failure_kind: "environment_stuck",
      detail: "No module named 'numpy'",
      role: "builder",
    });
    expect(summarize(round)).toContain("kept failing on the project's environment");
    expect(installOutput(round)).toBe("No module named 'numpy'");
  });
  it("keeps a task failure that is not a backend stop", () => {
    expect(summarize(event("TaskFailed", { error: "the daemon restarted" }))).toBe(
      "the daemon restarted",
    );
  });
});

describe("latestRound", () => {
  const at = (seq: number, type: string, payload: Record<string, unknown>) =>
    ({ seq, ts: "2026-10-06T10:00:00Z", event_type: type, payload }) as EpisodicEventView;

  it("describes how the role's latest round ended", () => {
    const events = [
      at(1, "TaskSubmitted", { role: "builder", text: "do it" }),
      at(2, "DelegationStarted", { role: "builder", task_text: "do it" }),
      at(3, "DelegationFailed", {
        role: "builder",
        reason: "stop=max_turns exit_code=4",
        failure_kind: "max_turns",
      }),
    ];
    expect(latestRound(events, "builder")).toEqual({
      kind: "failed",
      text: "It used all its turns before finishing. (stop=max_turns exit_code=4)",
      failureKind: "max_turns",
    });
  });
  it("carries the failure kind so the card can say a stuck role waits", () => {
    const events = [
      at(1, "DelegationFailed", { role: "builder", reason: "x", failure_kind: "environment_stuck" }),
    ];
    expect(latestRound(events, "builder")?.failureKind).toBe("environment_stuck");
    expect(latestRound([at(1, "DelegationFailed", { role: "builder", reason: "x" })], "builder"))
      .not.toHaveProperty("failureKind");
  });
  it("is null while a round is running, before one started, or for another role", () => {
    const running = [
      at(1, "DelegationFailed", { role: "builder", reason: "x" }),
      at(2, "DelegationStarted", { role: "builder", task_text: "again" }),
    ];
    expect(latestRound(running, "builder")).toBeNull();
    expect(latestRound([], "builder")).toBeNull();
    expect(latestRound(running, "reviewer")).toBeNull();
  });
});

describe("installing dependencies in the activity log", () => {
  it("says what is being installed and why", () => {
    const started = event("EnvironmentPrepareStarted", {
      ecosystem: "python",
      commands: [
        ["uv", "venv"],
        ["uv", "pip", "install", "-r", "requirements.txt"],
      ],
      reason: ".venv is missing",
    });
    expect(eventLabel("EnvironmentPrepareStarted")).toBe("Installing");
    expect(summarize(started)).toBe(
      "python: uv venv && uv pip install -r requirements.txt (.venv is missing)",
    );
  });
  it("reports success with the time and failure with the cause and the output's end", () => {
    expect(
      summarize(event("EnvironmentPrepared", { ecosystem: "node", ok: true, duration_s: 4.126 })),
    ).toBe("Installed the node dependencies in 4.1s.");
    const failed = event("EnvironmentPrepared", {
      ecosystem: "python",
      ok: false,
      failure: "exit",
      duration_s: 1,
      tail: "no matching distribution\n",
    });
    expect(summarize(failed)).toBe(
      "Couldn't install the python dependencies: the install command failed.",
    );
    expect(installOutput(failed)).toBe("no matching distribution");
    expect(installOutput(event("TaskFailed", { error: "x" }))).toBe("");
    expect(
      summarize(
        event("EnvironmentPrepared", {
          ecosystem: "node",
          ok: false,
          failure: "tool_missing",
          duration_s: 0,
          tail: "",
        }),
      ),
    ).toContain("the tool it needs is not installed");
  });
});

describe("eventLabelFor", () => {
  it("does not call a failed install done", () => {
    expect(eventLabelFor(event("EnvironmentPrepared", { ok: true }))).toBe("Install done");
    expect(eventLabelFor(event("EnvironmentPrepared", { ok: false }))).toBe("Install failed");
    expect(eventLabelFor(event("TaskFailed", { error: "x" }))).toBe("Task failed");
  });
});

describe("checkpointedRounds", () => {
  const at = (seq: number, type: string, payload: Record<string, unknown>) =>
    ({ ...event(type, payload), seq }) as EpisodicEventView;

  it("marks a round that ran out of room and was continued, and no other failure", () => {
    const events = [
      at(1, "DelegationFailed", { role: "dev", failure_kind: "max_turns", reason: "stop=max_turns" }),
      at(2, "HandoverWritten", { role: "dev" }),
      at(3, "RoundContinued", { role: "dev", reason: "max_turns" }),
      at(4, "DelegationFailed", { role: "dev", failure_kind: "max_turns", reason: "stop=max_turns" }),
      at(5, "DelegationFailed", { role: "qa", failure_kind: "provider_auth", reason: "x" }),
    ];
    expect([...checkpointedRounds(events)]).toEqual([1]);
  });
});

describe("the summariser's call", () => {
  it("reads as what it did and what it cost, never as raw JSON", () => {
    const call = event("LlmCallCompleted", {
      model: "m",
      prompt: "p",
      response: "r",
      input_tokens: 12345,
      output_tokens: null,
      role: "builder",
      cost_usd: 0.00314,
    });
    expect(eventLabel("LlmCallCompleted")).toBe("Summary");
    expect(summarize(call)).toBe("Wrote the checkpoint with m (12,345 tokens in, ? out, $0.0031).");
  });
});
