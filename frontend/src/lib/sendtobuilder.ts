// Sending a message to a project's builder from the page (CUT-291, CUT-293): one place for where it
// goes and what to say about it. A running team's role takes it as a steer (Approve tier); otherwise
// the builder's session takes it as chat (Terminal tier, CUT-303), which only some builders accept.
// Only ever called from a button a person pressed: nothing here sends on its own.
import { FleetApiError, type FleetClient } from "./api";

export interface Destination {
  /** A team is running: the message is a steer to `role`. */
  team: boolean;
  role: string;
}

/** Send `message` and say what happened, in a sentence for the page. Throws what the daemon said. */
export async function deliver(
  client: FleetClient,
  projectId: string,
  to: Destination,
  message: string,
): Promise<string> {
  if (to.team) {
    await client.steerProject(projectId, to.role, message);
    return `Sent to ${to.role}. A running team reads it at its next round; a live Codex role reads it at once.`;
  }
  const result = await client.sendChat(projectId, message);
  return result.sent === "queued" ? "Queued. The builder reads it at its next step." : "Sent to the builder.";
}

/** What to tell a person whose send failed: a device below the tier is told how to raise it. */
export function failureText(error: unknown): string {
  if (error instanceof FleetApiError && error.status === 403) {
    return "This device is not allowed to message the builder. Raise it under Devices on the PC.";
  }
  return error instanceof Error ? error.message : "That didn't work.";
}

export const COPIED_NOTE = "Copied. Paste it into the Terminal tab.";
