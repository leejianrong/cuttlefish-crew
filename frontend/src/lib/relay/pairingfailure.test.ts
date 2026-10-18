import { describe, expect, it } from "vitest";
import { PairingFailed, RelayError, pairingFailure } from "./connection";

describe("pairingFailure", () => {
  it("reads a daemon that drops the connection before any reply as a spent link", () => {
    const out = pairingFailure(new RelayError("The relay connection closed: protocol error"), false);
    expect(out).toBeInstanceOf(PairingFailed);
    expect((out as PairingFailed).message).toMatch(/already used or has expired/);
  });

  it("leaves a protocol error after the daemon has spoken alone", () => {
    const failure = new RelayError("The relay connection closed: protocol error");
    expect(pairingFailure(failure, true)).toBe(failure);
  });

  it("passes other failures on as they came", () => {
    const failure = new RelayError("Could not reach the relay.");
    expect(pairingFailure(failure, false)).toBe(failure);
  });
});
