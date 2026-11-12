import { describe, expect, it } from "vitest";
import type { StackInfo } from "./api";
import { portLabel, stackLine } from "./stack";

const base: StackInfo = { worker: "laptop", state: "up", ports: [], error: "", at: 0 };

describe("stack text", () => {
  it("names the machine and the state", () => {
    expect(stackLine(null)).toBe("Not started.");
    expect(stackLine({ ...base, state: "starting" })).toBe("Starting on laptop…");
    expect(stackLine({ ...base, state: "up" })).toBe("Running on laptop. It published no port.");
    expect(stackLine({ ...base, ports: [{ service: "web", target: 80, published: 8080 }] })).toBe("Running on laptop.");
    expect(stackLine({ ...base, state: "stopping" })).toBe("Stopping on laptop…");
    expect(stackLine({ ...base, state: "down" })).toBe("Stopped.");
    expect(stackLine({ ...base, state: "failed", error: "no compose file" })).toBe("Failed on laptop: no compose file");
  });

  it("labels a port", () => {
    expect(portLabel({ service: "web", target: 80, published: 8080 })).toBe("web 8080 (container 80)");
    expect(portLabel({ service: "", target: 80, published: 8080 })).toBe("port 8080 (container 80)");
  });
});
