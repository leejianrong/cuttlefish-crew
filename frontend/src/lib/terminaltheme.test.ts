// Unit: the terminal theme preference (terminaltheme.ts).

import { afterEach, describe, expect, it, vi } from "vitest";
import {
  ansiNames,
  appIsDark,
  loadTerminalTheme,
  resolveTerminalMode,
  saveTerminalTheme,
} from "./terminaltheme";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("resolveTerminalMode", () => {
  it("is dark in either app theme when set to dark", () => {
    expect(resolveTerminalMode("dark", false)).toBe("dark");
    expect(resolveTerminalMode("dark", true)).toBe("dark");
  });
  it("is light in either app theme when set to light", () => {
    expect(resolveTerminalMode("light", true)).toBe("light");
  });
  it("follows the app when set to match", () => {
    expect(resolveTerminalMode("match", true)).toBe("dark");
    expect(resolveTerminalMode("match", false)).toBe("light");
  });
});

describe("loadTerminalTheme / saveTerminalTheme", () => {
  it("defaults to dark", () => {
    vi.stubGlobal("localStorage", { getItem: () => null, setItem: () => {} });
    expect(loadTerminalTheme()).toBe("dark");
  });
  it("ignores a stored value it does not know", () => {
    vi.stubGlobal("localStorage", { getItem: () => "purple", setItem: () => {} });
    expect(loadTerminalTheme()).toBe("dark");
  });
  it("round-trips a choice", () => {
    const data: Record<string, string> = {};
    vi.stubGlobal("localStorage", {
      getItem: (k: string) => data[k] ?? null,
      setItem: (k: string, v: string) => {
        data[k] = v;
      },
    });
    saveTerminalTheme("match");
    expect(loadTerminalTheme()).toBe("match");
  });
  it("survives blocked storage", () => {
    const blocked = () => {
      throw new Error("blocked");
    };
    vi.stubGlobal("localStorage", { getItem: blocked, setItem: blocked });
    expect(loadTerminalTheme()).toBe("dark");
    expect(() => saveTerminalTheme("light")).not.toThrow();
  });
});

describe("appIsDark", () => {
  it("honours a forced theme over the OS", () => {
    expect(appIsDark({ getAttribute: () => "dark" }, () => false)).toBe(true);
    expect(appIsDark({ getAttribute: () => "light" }, () => true)).toBe(false);
  });
  it("asks the OS when nothing is forced", () => {
    expect(appIsDark({ getAttribute: () => null }, () => true)).toBe(true);
  });
});

describe("ansiNames", () => {
  it("lists sixteen names for the mode", () => {
    const names = ansiNames("light");
    expect(names).toHaveLength(16);
    expect(names[15]).toBe("--term-light-ansi-15");
  });
});
