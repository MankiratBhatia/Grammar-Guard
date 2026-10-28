import { cx } from "./ui";

/** Nonterminals are named like Value, Pairs, V3, More7; terminals are single characters or classes like [a-z]. */
const isNonterminal = (s: string) => /^[A-Z][A-Za-z0-9_]+$/.test(s);

/** The pushdown automaton's stack, top first, drawn as a column. Nonterminals are still to be expanded; terminals still to be matched. */
export function StackView({ stack, previous }: { stack: string[]; previous?: string[] }) {
  const grew = previous !== undefined && stack.length > previous.length;
  return (
    <div>
      <div className="mb-1 flex items-center justify-between text-[11px] text-muted">
        <span>top</span>
        <span>
          depth {stack.length}
          {previous !== undefined && stack.length !== previous.length && (
            <span className={grew ? "text-warn" : "text-good"}> ({grew ? "+" : ""}{stack.length - previous.length})</span>
          )}
        </span>
      </div>
      {stack.length === 0 ? (
        <p className="rounded-lg border border-dashed border-good/50 px-3 py-4 text-center text-sm text-good">Stack empty: the document is complete.</p>
      ) : (
        <ol className="flex max-h-72 flex-col gap-1 overflow-y-auto" aria-label="PDA stack, top first">
          {stack.map((sym, i) => (
            <li
              key={`${i}-${sym}`}
              className={cx(
                "rounded-md border px-3 py-1 font-mono text-xs",
                isNonterminal(sym) ? "border-accent/40 bg-accent-soft text-accent" : "border-line bg-panel2 text-ink",
                i === 0 && "outline outline-2 outline-accent",
              )}
            >
              {sym}
              <span className="ml-2 text-[10px] text-muted">{isNonterminal(sym) ? "expand" : "match"}</span>
            </li>
          ))}
        </ol>
      )}
      <div className="mt-1 text-[11px] text-muted">bottom</div>
    </div>
  );
}

/** Stack depth after every token: nesting shows up as hills. */
export function DepthChart({ depths, selected, onSelect }: { depths: number[]; selected: number | null; onSelect: (i: number) => void }) {
  if (!depths.length) return null;
  const max = Math.max(...depths, 1);
  const w = Math.max(depths.length * 12, 120);
  const h = 56;
  return (
    <div className="overflow-x-auto">
      <svg viewBox={`0 0 ${w} ${h}`} style={{ minWidth: w, height: h }} role="img" aria-label="Stack depth per token">
        {depths.map((d, i) => (
          <rect
            key={i}
            x={i * 12 + 1}
            y={h - (d / max) * (h - 4)}
            width={10}
            height={(d / max) * (h - 4)}
            rx={2}
            fill="var(--accent)"
            opacity={selected === null || selected === i ? 1 : 0.4}
            className="cursor-pointer"
            onClick={() => onSelect(i)}
          >
            <title>{`after token ${i + 1}: depth ${d}`}</title>
          </rect>
        ))}
      </svg>
    </div>
  );
}
