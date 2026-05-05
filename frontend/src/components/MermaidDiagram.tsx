import { useEffect, useRef, useState } from "react";
import mermaid from "mermaid";

let initialised = false;
function ensureInit(): void {
  if (initialised) return;
  initialised = true;
  mermaid.initialize({
    startOnLoad: false,
    theme: "dark",
    securityLevel: "strict",
    fontFamily: '"JetBrains Mono", "SF Mono", Menlo, monospace',
    themeVariables: {
      // Match the Daedalus dark palette so the diagrams blend in.
      darkMode: true,
      background: "#0a0e14",
      primaryColor: "#0d3149",
      primaryTextColor: "#e6edf3",
      primaryBorderColor: "#0ea5b7",
      lineColor: "#475569",
      secondaryColor: "#1f2937",
      tertiaryColor: "#0f172a",
      mainBkg: "#0f1924",
      nodeBorder: "#334155",
      clusterBkg: "#0a121a",
      clusterBorder: "#1e293b",
    },
  });
}

interface Props {
  /** Diagram source. Anything Mermaid v11 understands (flowchart, sequence,
   *  state, etc). */
  chart: string;
  /** Stable id used to namespace the rendered SVG. Must be unique on the page. */
  id: string;
}

/** Renders a Mermaid diagram into an SVG. Errors are caught and shown inline
 *  with the original source so a typo doesn't blank the whole page. */
export default function MermaidDiagram({ chart, id }: Props) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    ensureInit();
    let cancelled = false;
    setError(null);
    (async () => {
      try {
        const { svg } = await mermaid.render(`mermaid-${id}`, chart);
        if (cancelled) return;
        if (containerRef.current) {
          containerRef.current.innerHTML = svg;
        }
      } catch (e: any) {
        if (cancelled) return;
        setError(e?.message ?? String(e));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [chart, id]);

  return (
    <div className="rounded border border-border bg-bg p-3">
      {error ? (
        <div>
          <p className="text-xs text-danger">Diagram error: {error}</p>
          <pre className="mt-2 overflow-x-auto text-[10px] text-muted whitespace-pre">
            {chart}
          </pre>
        </div>
      ) : (
        <div
          ref={containerRef}
          className="overflow-x-auto [&_svg]:mx-auto [&_svg]:max-w-full"
        />
      )}
    </div>
  );
}
