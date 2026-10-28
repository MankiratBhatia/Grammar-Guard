import { useEffect, useRef, useState } from "react";
import { cx } from "./ui";

type VizModule = typeof import("@viz-js/viz");
let vizPromise: Promise<Awaited<ReturnType<VizModule["instance"]>>> | null = null;

/** Graphviz compiled to WebAssembly: loaded once, only when the first graph is drawn. */
function getViz() {
  vizPromise ??= import("@viz-js/viz").then((m) => m.instance());
  return vizPromise;
}

/** Add a highlight to one state of a DOT string produced by the backend (nodes are named q<number>). */
export function withHighlight(dot: string, state: number | null | undefined): string {
  if (state === null || state === undefined) return dot;
  const at = dot.lastIndexOf("}");
  return `${dot.slice(0, at)}  q${state} [style=filled, fillcolor="#ffd966"];\n}`;
}

export function Graph({ dot, highlight, className, label }: { dot: string | null; highlight?: number | null; className?: string; label: string }) {
  const host = useRef<HTMLDivElement>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const source = dot ? withHighlight(dot, highlight) : null;

  useEffect(() => {
    if (!source || !host.current) return;
    let cancelled = false;
    setBusy(true);
    getViz()
      .then((viz) => {
        if (cancelled || !host.current) return;
        const svg = viz.renderSVGElement(source);
        svg.querySelector("g.graph > polygon")?.remove(); // drop the white page background (breaks dark mode)
        svg.setAttribute("role", "img");
        svg.setAttribute("aria-label", label);
        host.current.replaceChildren(svg);
        setError(null);
      })
      .catch((e: unknown) => !cancelled && setError(e instanceof Error ? e.message : String(e)))
      .finally(() => !cancelled && setBusy(false));
    return () => {
      cancelled = true;
    };
  }, [source, label]);

  if (!dot) {
    return <p className="rounded-lg border border-dashed border-line p-4 text-center text-sm text-muted">This automaton is too large to draw legibly. Use the state count and examples above, or simplify the constraint.</p>;
  }
  return (
    <div className={cx("graph relative overflow-x-auto", className)}>
      <div ref={host} className="flex min-h-16 justify-center" />
      {busy && <span className="absolute right-2 top-2 text-xs text-muted">drawing…</span>}
      {error && <p className="text-sm text-bad">Could not draw the graph: {error}</p>}
    </div>
  );
}
