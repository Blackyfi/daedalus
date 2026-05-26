import { useEffect, useMemo, useState } from "react";
import { parsePatch } from "diff";

interface Props {
  patch: string;
}

type Mode = "split" | "unified";

type Row =
  | { kind: "context"; text: string; oldNum: number; newNum: number }
  | { kind: "delete"; text: string; oldNum: number }
  | { kind: "insert"; text: string; newNum: number }
  | { kind: "change"; oldText: string; oldNum: number; newText: string; newNum: number };

interface FileBlock {
  oldName: string;
  newName: string;
  hunks: Hunk[];
}

interface Hunk {
  header: string;
  rows: Row[];
}

/** Parse a unified diff and zip add/remove pairs into side-by-side rows. */
function buildFileBlocks(patch: string): FileBlock[] {
  const parsed = parsePatch(patch);
  return parsed.map((file) => {
    const hunks: Hunk[] = file.hunks.map((hunk) => {
      const rows: Row[] = [];
      let oldNum = hunk.oldStart;
      let newNum = hunk.newStart;

      let i = 0;
      while (i < hunk.lines.length) {
        const line = hunk.lines[i];
        const marker = line[0];
        const text = line.slice(1);

        if (marker === " " || marker === "\\") {
          rows.push({ kind: "context", text, oldNum, newNum });
          oldNum++;
          newNum++;
          i++;
          continue;
        }

        if (marker === "-") {
          // Look at the run of consecutive deletes followed by inserts and zip them.
          const deletes: string[] = [text];
          let j = i + 1;
          while (j < hunk.lines.length && hunk.lines[j][0] === "-") {
            deletes.push(hunk.lines[j].slice(1));
            j++;
          }
          const inserts: string[] = [];
          while (j < hunk.lines.length && hunk.lines[j][0] === "+") {
            inserts.push(hunk.lines[j].slice(1));
            j++;
          }

          const pairs = Math.min(deletes.length, inserts.length);
          for (let k = 0; k < pairs; k++) {
            rows.push({
              kind: "change",
              oldText: deletes[k],
              oldNum: oldNum + k,
              newText: inserts[k],
              newNum: newNum + k,
            });
          }
          for (let k = pairs; k < deletes.length; k++) {
            rows.push({
              kind: "delete",
              text: deletes[k],
              oldNum: oldNum + k,
            });
          }
          for (let k = pairs; k < inserts.length; k++) {
            rows.push({
              kind: "insert",
              text: inserts[k],
              newNum: newNum + k,
            });
          }
          oldNum += deletes.length;
          newNum += inserts.length;
          i = j;
          continue;
        }

        if (marker === "+") {
          // Lone insert (no preceding delete).
          rows.push({ kind: "insert", text, newNum });
          newNum++;
          i++;
          continue;
        }

        // Unknown marker — skip.
        i++;
      }

      return {
        header: `@@ -${hunk.oldStart},${hunk.oldLines} +${hunk.newStart},${hunk.newLines} @@`,
        rows,
      };
    });

    return {
      oldName: file.oldFileName ?? "(none)",
      newName: file.newFileName ?? "(none)",
      hunks,
    };
  });
}

export default function DiffViewer({ patch }: Props) {
  const files = useMemo(() => buildFileBlocks(patch), [patch]);
  // Default to split on lg+ where two ~340-px code columns fit, unified
  // otherwise. The user can flip the toggle either way.
  const [mode, setMode] = useState<Mode>(() =>
    typeof window !== "undefined" && window.matchMedia("(min-width: 1024px)").matches
      ? "split"
      : "unified",
  );
  useEffect(() => {
    if (typeof window === "undefined") return;
    const mq = window.matchMedia("(min-width: 1024px)");
    const onChange = (e: MediaQueryListEvent) => {
      setMode(e.matches ? "split" : "unified");
    };
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, []);

  if (!patch.trim()) {
    return <p className="text-xs text-muted">No changes against the default branch.</p>;
  }

  if (files.length === 0) {
    return (
      <pre className="max-h-[420px] overflow-auto whitespace-pre text-xs text-muted sm:text-[11px]">
        {patch}
      </pre>
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-end gap-2 text-xs">
        <span className="text-muted">view:</span>
        <button
          type="button"
          onClick={() => setMode("unified")}
          aria-pressed={mode === "unified"}
          className={`btn ${mode === "unified" ? "btn-primary" : ""}`}
        >
          Unified
        </button>
        <button
          type="button"
          onClick={() => setMode("split")}
          aria-pressed={mode === "split"}
          className={`btn ${mode === "split" ? "btn-primary" : ""}`}
        >
          Split
        </button>
      </div>
      {files.map((file, fi) => (
        <article key={fi} className="rounded border border-border bg-bg">
          <header className="flex items-center justify-between border-b border-border bg-panel2 px-3 py-1.5 text-xs">
            <span className="break-all font-mono">
              {file.oldName === file.newName
                ? file.newName
                : `${file.oldName} → ${file.newName}`}
            </span>
            <span className="shrink-0 text-muted">
              {file.hunks.reduce((acc, h) => acc + h.rows.length, 0)} rows
            </span>
          </header>
          <div className="overflow-x-auto">
            {file.hunks.map((hunk, hi) => (
              <table
                key={hi}
                className="w-full border-collapse font-mono text-xs sm:text-[11px]"
              >
                <tbody>
                  <tr>
                    <td
                      colSpan={mode === "split" ? 4 : 3}
                      className="bg-panel2 px-3 py-1 text-[11px] text-muted"
                    >
                      {hunk.header}
                    </td>
                  </tr>
                  {mode === "split"
                    ? hunk.rows.map((row, ri) => (
                        <DiffRowSplit key={ri} row={row} />
                      ))
                    : hunk.rows.flatMap((row, ri) => unifiedRowsFor(row, ri))}
                </tbody>
              </table>
            ))}
          </div>
        </article>
      ))}
    </div>
  );
}

const gutterCls =
  "w-10 select-none px-2 text-right text-muted text-[11px] sm:text-[10px] align-top";
const cellCls = "px-3 py-0.5 align-top whitespace-pre-wrap break-all";

function DiffRowSplit({ row }: { row: Row }) {
  if (row.kind === "context") {
    return (
      <tr>
        <td className={gutterCls}>{row.oldNum}</td>
        <td className={cellCls}>{row.text || " "}</td>
        <td className={gutterCls}>{row.newNum}</td>
        <td className={cellCls}>{row.text || " "}</td>
      </tr>
    );
  }
  if (row.kind === "delete") {
    return (
      <tr>
        <td className={gutterCls}>{row.oldNum}</td>
        <td className={`${cellCls} bg-red-950/40 text-red-200`}>{row.text || " "}</td>
        <td className={gutterCls}></td>
        <td className={cellCls}></td>
      </tr>
    );
  }
  if (row.kind === "insert") {
    return (
      <tr>
        <td className={gutterCls}></td>
        <td className={cellCls}></td>
        <td className={gutterCls}>{row.newNum}</td>
        <td className={`${cellCls} bg-emerald-950/40 text-emerald-200`}>
          {row.text || " "}
        </td>
      </tr>
    );
  }
  return (
    <tr>
      <td className={gutterCls}>{row.oldNum}</td>
      <td className={`${cellCls} bg-red-950/40 text-red-200`}>{row.oldText || " "}</td>
      <td className={gutterCls}>{row.newNum}</td>
      <td className={`${cellCls} bg-emerald-950/40 text-emerald-200`}>
        {row.newText || " "}
      </td>
    </tr>
  );
}

function unifiedRow(
  key: string,
  oldNum: number | "",
  newNum: number | "",
  marker: " " | "+" | "-",
  text: string,
  rowCls: string,
): JSX.Element {
  return (
    <tr key={key}>
      <td className={gutterCls}>{oldNum}</td>
      <td className={gutterCls}>{newNum}</td>
      <td className={`${cellCls} ${rowCls}`}>
        <span className="select-none pr-1 text-muted">{marker}</span>
        {text || " "}
      </td>
    </tr>
  );
}

function unifiedRowsFor(row: Row, ri: number): JSX.Element[] {
  if (row.kind === "context") {
    return [unifiedRow(`${ri}-c`, row.oldNum, row.newNum, " ", row.text, "")];
  }
  if (row.kind === "delete") {
    return [
      unifiedRow(`${ri}-d`, row.oldNum, "", "-", row.text, "bg-red-950/40 text-red-200"),
    ];
  }
  if (row.kind === "insert") {
    return [
      unifiedRow(
        `${ri}-i`,
        "",
        row.newNum,
        "+",
        row.text,
        "bg-emerald-950/40 text-emerald-200",
      ),
    ];
  }
  return [
    unifiedRow(`${ri}-d`, row.oldNum, "", "-", row.oldText, "bg-red-950/40 text-red-200"),
    unifiedRow(
      `${ri}-i`,
      "",
      row.newNum,
      "+",
      row.newText,
      "bg-emerald-950/40 text-emerald-200",
    ),
  ];
}
