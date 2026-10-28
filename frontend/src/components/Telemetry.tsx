import type { AnyToken, Telemetry } from "../api/types";
import { Badge, Stat, cx, visible } from "./ui";

export const pct = (x: number) => {
  if (x > 0 && x < 0.001) return "<0.1%";
  return `${(x * 100).toFixed(x > 0.9995 || x === 0 ? 0 : 1)}%`;
};
export const bits = (x: number) => (x >= 100 ? x.toFixed(0) : x.toFixed(2));

/** The generated text, one coloured chip per token. Darker orange = the constraint had to push harder. */
export function TokenStream({
  tokens,
  selected,
  onSelect,
  empty = "Nothing generated yet.",
}: {
  tokens: AnyToken[];
  selected: number | null;
  onSelect: (i: number) => void;
  empty?: string;
}) {
  if (!tokens.length) return <p className="text-sm text-muted">{empty}</p>;
  return (
    <p className="whitespace-pre-wrap break-words font-mono text-sm leading-8" aria-live="polite">
      {tokens.map((t) => {
        const pressure = t.telemetry?.pressure ?? (t.mass_kept !== undefined ? 1 - t.mass_kept : null);
        return (
          <button
            key={t.index}
            type="button"
            onClick={() => onSelect(t.index)}
            title={pressure === null ? "unconstrained token" : `pressure ${pct(pressure)}`}
            aria-pressed={selected === t.index}
            style={pressure === null ? undefined : { backgroundColor: `color-mix(in srgb, var(--hot) ${Math.round(pressure * 75)}%, transparent)` }}
            className={cx(
              "token token-new mx-px rounded px-0.5 text-left text-ink",
              pressure === null && "bg-panel2",
              selected === t.index && "outline outline-2 outline-accent",
            )}
          >
            {t.text}
          </button>
        );
      })}
    </p>
  );
}

/** One bar per token: how much probability the constraint removed there. Click a bar to inspect that step. */
export function PressureChart({
  tokens,
  selected,
  onSelect,
}: {
  tokens: AnyToken[];
  selected: number | null;
  onSelect: (i: number) => void;
}) {
  const data = tokens.map((t) => ({
    i: t.index,
    p: t.telemetry?.pressure ?? (t.mass_kept !== undefined ? 1 - t.mass_kept : 0),
    override: t.telemetry ? !t.telemetry.preferred_legal : t.preferred_legal === false,
  }));
  if (!data.length) return null;
  const slot = data.length <= 12 ? 30 : 14; // few tokens: draw wider bars
  const w = Math.max(data.length * slot, 120);
  const h = 70;
  return (
    <div className="overflow-x-auto">
      <svg viewBox={`0 0 ${w} ${h + 14}`} style={{ minWidth: w, height: h + 14 }} role="img" aria-label="Constraint pressure per token">
        <line x1="0" x2={w} y1={h} y2={h} stroke="var(--line)" />
        {data.map((d) => {
          const bar = Math.max(d.p * (h - 6), d.p > 0 ? 1.5 : 0);
          return (
            <g key={d.i} onClick={() => onSelect(d.i)} className="cursor-pointer">
              <rect x={d.i * slot} y={0} width={slot} height={h + 14} fill="transparent" />
              <rect
                x={d.i * slot + 2}
                y={h - bar}
                width={slot - 4}
                height={bar}
                rx={2}
                fill="var(--hot)"
                opacity={selected === null || selected === d.i ? 1 : 0.45}
              >
                <title>{`token ${d.i}: pressure ${pct(d.p)}${d.override ? " (model's first choice was not allowed)" : ""}`}</title>
              </rect>
              {d.override && <circle cx={d.i * slot + slot / 2} cy={h + 8} r={2.5} fill="var(--bad)" />}
              {selected === d.i && <rect x={d.i * slot} y={0} width={slot} height={h} fill="none" stroke="var(--accent)" strokeWidth={1.5} rx={2} />}
            </g>
          );
        })}
      </svg>
      <p className="mt-1 text-[11px] text-muted">
        Bar height = share of the model's probability the constraint removed. <span className="text-bad">●</span> = the model's own first
        choice was not allowed.
      </p>
    </div>
  );
}

export function TokenDetail({ token }: { token: AnyToken | undefined }) {
  if (!token) return <p className="text-sm text-muted">Click a token or a bar to inspect that step.</p>;
  const t: Telemetry | null = token.telemetry ?? null;
  return (
    <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-sm">
      <dt className="text-muted">Token</dt>
      <dd className="font-mono">{visible(token.text)}</dd>
      {token.state !== undefined && token.state !== null && (
        <>
          <dt className="text-muted">DFA state after</dt>
          <dd className="font-mono">q{token.state}</dd>
        </>
      )}
      {token.allowed !== undefined && (
        <>
          <dt className="text-muted">Allowed tokens</dt>
          <dd className="tabular-nums">{token.allowed.toLocaleString()} of 50,257</dd>
        </>
      )}
      {token.valid !== undefined && (
        <>
          <dt className="text-muted">Valid / checked</dt>
          <dd className="tabular-nums">
            {token.valid} / {token.checked?.toLocaleString()} {token.fallback && <Badge tone="warn">full-vocabulary fallback</Badge>}
          </dd>
        </>
      )}
      {t && (
        <>
          <dt className="text-muted">Probability kept</dt>
          <dd className="tabular-nums">{pct(t.mass_kept)}</dd>
          <dt className="text-muted">Information cost</dt>
          <dd className="tabular-nums">{bits(t.cost_bits)} bits</dd>
          <dt className="text-muted">Model's first choice</dt>
          <dd>
            <span className="font-mono">{visible(t.preferred)}</span> {t.preferred_legal ? <Badge tone="good">allowed</Badge> : <Badge tone="bad">blocked</Badge>}
          </dd>
          <dt className="text-muted">P(chosen token)</dt>
          <dd className="tabular-nums">{pct(t.p_chosen)}</dd>
          <dt className="text-muted">Entropy before → after</dt>
          <dd className="tabular-nums">
            {t.entropy_before.toFixed(2)} → {t.entropy_after.toFixed(2)} bits
          </dd>
        </>
      )}
      {token.mass_kept !== undefined && !t && (
        <>
          <dt className="text-muted">Probability kept (lower bound)</dt>
          <dd className="tabular-nums">{pct(token.mass_kept)}</dd>
          <dt className="text-muted">Model's first choice</dt>
          <dd>{token.preferred_legal ? <Badge tone="good">allowed</Badge> : <Badge tone="bad">blocked</Badge>}</dd>
        </>
      )}
    </dl>
  );
}

export function SummaryStats({ summary, tokens }: { summary?: Record<string, number>; tokens: number }) {
  if (!summary) return null;
  const mean = summary.mean_pressure ?? summary.mean_pressure_upper_bound;
  return (
    <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
      <Stat label="Tokens" value={tokens} />
      {summary.total_cost_bits !== undefined && (
        <Stat label="Total cost" value={`${bits(summary.total_cost_bits)} bits`} hint="Information the constraint added: sum of −log₂ P(allowed set) over the tokens" />
      )}
      {mean !== undefined && <Stat label="Mean pressure" value={pct(mean)} hint="Average share of the model's probability that was removed" />}
      {summary.overrides !== undefined && (
        <Stat label="Overrides" value={`${summary.overrides}`} hint="Steps where the model's own first choice was not allowed" />
      )}
    </div>
  );
}
