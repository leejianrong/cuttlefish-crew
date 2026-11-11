import { describe, expect, it, vi } from "vitest";
import { FleetApiError, type FleetClient } from "./api";
import { deliver, failureText } from "./sendtobuilder";

const fake = () => {
  const steerProject = vi.fn(async () => ({ status: "sent" }));
  const sendChat = vi.fn(async (): Promise<{ sent: "delivered" | "queued"; state: string }> => ({
    sent: "delivered",
    state: "idle",
  }));
  return { steerProject, sendChat, client: { steerProject, sendChat } as unknown as FleetClient };
};

describe("sending to the builder (CUT-291, CUT-293)", () => {
  it("steers the chosen role when a team is running and never types into the chat", async () => {
    const { client, steerProject, sendChat } = fake();
    const said = await deliver(client, "p", { team: true, role: "builder" }, "hi");
    expect(steerProject).toHaveBeenCalledWith("p", "builder", "hi");
    expect(sendChat).not.toHaveBeenCalled();
    expect(said).toMatch(/Sent to builder/);
  });

  it("types into the builder's chat otherwise, and says when it was queued", async () => {
    const { client, steerProject, sendChat } = fake();
    expect(await deliver(client, "p", { team: false, role: "" }, "hi")).toBe("Sent to the builder.");
    expect(sendChat).toHaveBeenCalledWith("p", "hi");
    expect(steerProject).not.toHaveBeenCalled();
    sendChat.mockResolvedValueOnce({ sent: "queued", state: "busy" });
    expect(await deliver(client, "p", { team: false, role: "" }, "hi")).toMatch(/Queued/);
  });

  it("tells a low-tier device how to raise it, and passes the daemon's own words otherwise", () => {
    expect(failureText(new FleetApiError(403, "Forbidden"))).toMatch(/Devices on the PC/);
    expect(failureText(new Error("The builder is not running."))).toBe("The builder is not running.");
    expect(failureText("odd")).toBe("That didn't work.");
  });
});
