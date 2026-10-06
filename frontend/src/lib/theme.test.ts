// Unit: the theme preference store (theme.ts) -- storage and the root attribute only.

import { afterEach, describe, expect, it, vi } from "vitest";
import { applyTheme, isThemePreference, loadTheme, nextTheme, saveTheme } from "./theme";

function fakeStorage(initial: Record<string, string> = {}) {
  const data = { ...initial };
  return {
    getItem: (key: string) => data[key] ?? null,
    setItem: (key: string, value: string) => {
      data[key] = value;
    },
    data,
  };
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("nextTheme", () => {
  it("cycles system, light, dark and back", () => {
    expect(nextTheme("system")).toBe("light");
    expect(nextTheme("light")).toBe("dark");
    expect(nextTheme("dark")).toBe("system");
  });
});

describe("loadTheme / saveTheme", () => {
  it("defaults to system when nothing is stored", () => {
    vi.stubGlobal("localStorage", fakeStorage());
    expect(loadTheme()).toBe("system");
  });

  it("round-trips a saved preference", () => {
    vi.stubGlobal("localStorage", fakeStorage());
    saveTheme("dark");
    expect(loadTheme()).toBe("dark");
  });

  it("ignores a stored value it does not recognise", () => {
    vi.stubGlobal("localStorage", fakeStorage({ "cuttlefish.theme": "sepia" }));
    expect(loadTheme()).toBe("system");
  });

  it("survives storage that throws (a private window)", () => {
    const blocked = {
      getItem: () => {
        throw new Error("blocked");
      },
      setItem: () => {
        throw new Error("blocked");
      },
    };
    vi.stubGlobal("localStorage", blocked);
    expect(loadTheme()).toBe("system");
    expect(() => saveTheme("light")).not.toThrow();
  });
});

describe("applyTheme", () => {
  function root() {
    const attributes: Record<string, string> = {};
    return {
      attributes,
      setAttribute: (name: string, value: string) => {
        attributes[name] = value;
      },
      removeAttribute: (name: string) => {
        delete attributes[name];
      },
    };
  }

  it("forces light or dark with data-theme", () => {
    const element = root();
    applyTheme("dark", element);
    expect(element.attributes["data-theme"]).toBe("dark");
    applyTheme("light", element);
    expect(element.attributes["data-theme"]).toBe("light");
  });

  it("removes the attribute for system so the OS decides", () => {
    const element = root();
    applyTheme("dark", element);
    applyTheme("system", element);
    expect(element.attributes).toEqual({});
  });
});

describe("isThemePreference", () => {
  it("accepts only the three preferences", () => {
    expect(["system", "light", "dark"].every(isThemePreference)).toBe(true);
    expect(isThemePreference("auto")).toBe(false);
    expect(isThemePreference(null)).toBe(false);
  });
});
