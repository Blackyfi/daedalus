import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import DiffViewer from "./DiffViewer";

// Valid unified diff: hunk header counts (-1,2 +1,2) match 1 context + 1 del + 1 ins.
const SAMPLE = [
  "diff --git a/calc.py b/calc.py",
  "index 1111111..2222222 100644",
  "--- a/calc.py",
  "+++ b/calc.py",
  "@@ -1,2 +1,2 @@",
  " def add(a, b):",
  "-    return a - b",
  "+    return a + b",
  "",
].join("\n");

describe("DiffViewer", () => {
  it("renders added and removed lines from a unified patch", () => {
    render(<DiffViewer patch={SAMPLE} />);
    // The buggy line and its fix should both be visible.
    expect(screen.getByText(/return a - b/)).toBeInTheDocument();
    expect(screen.getByText(/return a \+ b/)).toBeInTheDocument();
    // File name surfaces in the header.
    expect(screen.getAllByText(/calc\.py/).length).toBeGreaterThan(0);
  });

  it("shows an empty-state message for an empty patch", () => {
    render(<DiffViewer patch="" />);
    expect(screen.getByText(/no changes/i)).toBeInTheDocument();
  });
});
