import { describe, expect, it } from "vitest";
import { keyboardOpen, MIN_SCREEN_PX, screenHeight } from "./terminallayout";

const base = { visible: 844, layout: 844, top: 170, nav: 72, chrome: 96 };

describe("keyboardOpen", () => {
  it("is false when the visible height is the layout height", () => {
    expect(keyboardOpen(844, 844)).toBe(false);
  });
  it("ignores a browser bar sliding in or out", () => {
    expect(keyboardOpen(800, 844)).toBe(false);
  });
  it("is true when about a third of the screen is taken", () => {
    expect(keyboardOpen(520, 844)).toBe(true);
  });
});

describe("screenHeight", () => {
  it("fills what is left between the header and the bottom bar", () => {
    expect(screenHeight(base)).toBe(844 - 170 - 72 - 96);
  });

  it("with a keyboard open uses the whole visible height less the status line and keys", () => {
    expect(screenHeight({ ...base, visible: 440 })).toBe(440 - 96);
  });

  it("never goes below a usable minimum", () => {
    expect(screenHeight({ ...base, visible: 200, layout: 844 })).toBe(MIN_SCREEN_PX);
  });

  it("subtracts no bottom bar on a wide screen", () => {
    expect(screenHeight({ ...base, nav: 0 })).toBe(844 - 170 - 96);
  });
});
