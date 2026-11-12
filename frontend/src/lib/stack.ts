// A project's docker compose stack on its worker (CUT-216): what to say about it.

import type { StackInfo } from "./api";

/** One short line for the stack's state, naming the machine. */
export function stackLine(stack: StackInfo | null): string {
  if (!stack) return "Not started.";
  const where = stack.worker;
  switch (stack.state) {
    case "starting":
      return `Starting on ${where}…`;
    case "up":
      return stack.ports.length === 0
        ? `Running on ${where}. It published no port.`
        : `Running on ${where}.`;
    case "stopping":
      return `Stopping on ${where}…`;
    case "down":
      return "Stopped.";
    case "failed":
      return `Failed on ${where}${stack.error ? `: ${stack.error}` : "."}`;
  }
}

/** "web 8080 (container 80)". */
export function portLabel(port: StackInfo["ports"][number]): string {
  return `${port.service || "port"} ${port.published} (container ${port.target})`;
}
