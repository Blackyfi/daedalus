import { useMemo } from "react";
import { parsePatch } from "diff";

interface Props {
  patch: string;
}

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

  if (!patch.trim()) {
    return <p className="text-xs text-muted">No changes against the default branch.</p>;
  }

  if (files.length === 0) {
    return (
      <pre className="max-h-[420px] overflow-auto whitespace-pre text-[11px] text-muted">
        {patch}
      </pre>
    );
  }

  return (
    <div className="space-y-4">
      {files.map((file, fi) => (
        <article key={fi} className="rounded border border-border bg-bg">
          <header className="flex items-center justify-between border-b border-border bg-panel2 px-3 py-1.5 text-xs">
            <span className="font-mono">
              {file.oldName === file.newName
                ? file.newName
                : `${file.oldName} → ${file.newName}`}
            </span>
            <span className="text-muted">
              {file.hunks.reduce((acc, h) => acc + h.rows.length, 0)} rows
            </span>
          </header>
          <div className="overflow-x-auto">
            {file.hunks.map((hunk, hi) => (
              <table
                key={hi}
                className="w-full border-collapse font-mono text-[11px]"
              >
                <tbody>
                  <tr>
                    <td
                      colSpan={4}
                      className="bg-panel2 px-3 py-1 text-muted text-[10px]"
                    >
                      {hunk.header}
                    </td>
                  </tr>
                  {hunk.rows.map((row, ri) => (
                    <DiffRow key={ri} row={row} />
                  ))}
                </tbody>
              </table>
            ))}
          </div>
        </article>
      ))}
    </div>
  );
}

function DiffRow({ row }: { row: Row }) {
  const gutter = "w-10 select-none px-2 text-right text-muted text-[10px] align-top";
  const cell = "px-3 py-0.5 align-top whitespace-pre-wrap break-all";

  if (row.kind === "context") {
    return (
      <tr>
        <td className={gutter}>{row.oldNum}</td>
        <td className={cell}>{row.text || " "}</td>
        <td className={gutter}>{row.newNum}</td>
        <td className={cell}>{row.text || " "}</td>
      </tr>
    );
  }
  if (row.kind === "delete") {
    return (
      <tr>
        <td className={gutter}>{row.oldNum}</td>
        <td className={`${cell} bg-red-950/40 text-red-200`}>{row.text || " "}</td>
        <td className={gutter}></td>
        <td className={cell}></td>
      </tr>
    );
  }
  if (row.kind === "insert") {
    return (
      <tr>
        <td className={gutter}></td>
        <td className={cell}></td>
        <td className={gutter}>{row.newNum}</td>
        <td className={`${cell} bg-emerald-950/40 text-emerald-200`}>{row.text || " "}</td>
      </tr>
    );
  }
  return (
    <tr>
      <td className={gutter}>{row.oldNum}</td>
      <td className={`${cell} bg-red-950/40 text-red-200`}>{row.oldText || " "}</td>
      <td className={gutter}>{row.newNum}</td>
      <td className={`${cell} bg-emerald-950/40 text-emerald-200`}>{row.newText || " "}</td>
    </tr>
  );
}
