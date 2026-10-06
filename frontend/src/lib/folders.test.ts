// Unit: the add-project screen's pure helpers (folders.ts).

import { describe, expect, it } from "vitest";
import { breadcrumbs, folderName, parseAllow, parseLimit } from "./folders";

describe("folderName", () => {
  it("is the last path segment", () => {
    expect(folderName("/home/jian/projects/demo")).toBe("demo");
    expect(folderName("/home/jian/projects/demo/")).toBe("demo");
  });
  it("falls back to the path itself for the filesystem root", () => {
    expect(folderName("/")).toBe("/");
  });
});

describe("breadcrumbs", () => {
  it("is just the root at the root", () => {
    expect(breadcrumbs("/home/jian", "/home/jian", "/home/jian")).toEqual([
      { label: "~", path: "/home/jian" },
    ]);
  });

  it("walks from the root down to the path", () => {
    expect(breadcrumbs("/home/jian/projects/abang-ai", "/home/jian", "/home/jian")).toEqual([
      { label: "~", path: "/home/jian" },
      { label: "projects", path: "/home/jian/projects" },
      { label: "abang-ai", path: "/home/jian/projects/abang-ai" },
    ]);
  });

  it("labels a root that is not home by its folder name", () => {
    expect(breadcrumbs("/work/code/app", "/work/code", "/home/jian")).toEqual([
      { label: "code", path: "/work/code" },
      { label: "app", path: "/work/code/app" },
    ]);
  });

  it("does not treat a sibling that shares a prefix as inside the root", () => {
    expect(breadcrumbs("/home/jian2/x", "/home/jian", "/home/jian")).toEqual([
      { label: "~", path: "/home/jian" },
    ]);
  });
});

describe("parseAllow", () => {
  it("splits one command per line on whitespace and skips blanks", () => {
    expect(parseAllow("go test\n\n  uv run pytest  \n")).toEqual([
      ["go", "test"],
      ["uv", "run", "pytest"],
    ]);
  });
  it("is empty for blank text", () => {
    expect(parseAllow("  \n ")).toEqual([]);
  });
});

describe("parseLimit", () => {
  it("is null for blank, a number for a number, invalid otherwise", () => {
    expect(parseLimit("")).toBeNull();
    expect(parseLimit(" 5000 ")).toBe(5000);
    expect(parseLimit("0.5")).toBe(0.5);
    expect(parseLimit("abc")).toBe("invalid");
    expect(parseLimit("-1")).toBe("invalid");
  });
});
