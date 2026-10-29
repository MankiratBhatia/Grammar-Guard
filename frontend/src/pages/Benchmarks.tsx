import { useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
import type { BenchResults, GroupSummary, Rate } from "../api/types";
import { Badge, Card, ErrorBox, Field, Spinner, Stat, cx } from "../components/ui";
import { useAsync } from "../lib/hooks";

type Mode = "unconstrained" | "retry5" | "constrained";
const MODES: { key: Mode; label: string; color: string }[] = [
  { key: "unconstrained", label: "Unconstrained GPT-2", color: "var(--muted)" },
  { key: "retry5", label: "Best-of-5 retries", color: "var(--warn)" },
  { key: "constrained", label: "GrammarGuard", color: "var(--accent)" },
];

/** Headline rows. "+ extract a valid prefix" credits a run if ANY non-empty prefix of the output matches. */
const ROWS: { label: string; color: string; pick: (g: GroupSummary) => Rate | undefined; strong?: boolean }[] = [
  { label: "Unconstrained GPT-2, as is", color: "var(--muted)", pick: (g) => g.unconstrained?.validity },
  { label: "Unconstrained + extract a valid prefix", color: "var(--muted)", pick: (g) => g.unconstrained?.prefix_validity },
  { label: "Best-of-5 retries, as is", color: "var(--warn)", pick: (g) => g.retry5?.validity },
  { label: "Best-of-5 retries + extract a valid prefix", color: "var(--warn)", pick: (g) => g.retry5?.prefix_validity },
  { label: "GrammarGuard", color: "var(--accent)", pick: (g) => g.constrained?.validity, strong: true },
];

/** Prose stays readable: only line breaks and tabs are made visible (spaces are left alone), and long outputs are cut. */
const showOutput = (text: string) => {
  const t = text.replace(/\n/g, "↵").replace(/\t/g, "⇥");
  return t.length > 220 ? `${t.slice(0, 220)}…` : t;
};

const pct = (r: number) => `${(r * 100).toFixed(0)}%`;
const fmt = (x: number | null | undefined, digits = 1) => (x === null || x === undefined ? "–" : x.toFixed(digits));

export default function Benchmarks() {
  const bench = useAsync(() => api.benchmarks());
  useEffect(() => {
    void bench.run();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  if (bench.loading && !bench.data) return <Spinner label="Loading results…" />;
  if (bench.error) return <ErrorBox error={bench.error} />;
  if (!bench.data) return null;
  return <Results data={bench.data} />;
}

function Results({ data }: { data: BenchResults }) {
  const { meta, summary } = data;
  const overall = summary.overall;
  const con = overall.constrained;
  const oracle = data.runs.filter((r) => r.oracle_agrees !== null);
  const disagreements = oracle.filter((r) => r.oracle_agrees === false).length;

  return (
    <div className="space-y-5">
      <Card
        title="Does the output satisfy the validator?"
        subtitle={`${data.runs.length} runs over ${data.cases.length} validators (dates, phone numbers, IDs, emails, policies). Bars show the success rate; whiskers are 95% Wilson confidence intervals.`}
      >
        <div className="space-y-4">
          {ROWS.map((row) => {
            const rate = row.pick(overall);
            return rate ? <RateBar key={row.label} label={row.label} rate={rate} color={row.color} strong={row.strong} /> : null;
          })}
        </div>
        <p className="mt-4 text-xs text-muted">
          <b>As is</b> = the whole output must match, like a real validator. <b>Extract a valid prefix</b> = the best a post-processing step could do:
          a run counts if <i>any</i> non-empty prefix of the output matches. Plain GPT-2 rarely stops where the format ends
          ({data.runs.filter((r) => r.mode === "unconstrained" && r.stop_reason !== "eos").length} of{" "}
          {data.runs.filter((r) => r.mode === "unconstrained").length} runs hit the token limit).
        </p>
      </Card>

      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <Stat label="Speed vs plain decoding" value={overall.slowdown ? `${overall.slowdown.toFixed(2)}×` : "–"} hint="Time per token, constrained ÷ unconstrained (mask + telemetry). Index build excluded." />
        <Stat label="Mean constraint cost" value={con?.mean_cost_bits != null ? `${con.mean_cost_bits.toFixed(1)} bits` : "–"} hint="Information the constraint added per output: −log₂ P(allowed tokens), summed over tokens" />
        <Stat label="Retries needed" value={overall.retry5?.mean_attempts != null ? overall.retry5.mean_attempts.toFixed(1) : "–"} hint="Mean attempts used by best-of-5 rejection sampling (5 = gave up)" />
        <Stat label="DFA vs Python re" value={oracle.length ? (disagreements ? `${disagreements} differ` : "agree") : "–"} hint={`${oracle.length} regex outputs judged by both our DFA and re.fullmatch`} />
      </div>

      <div className="grid gap-5 lg:grid-cols-2">
        <CategoryTable data={data} />
        <Card title="Decoding mode" subtitle="Greedy is deterministic; sampling uses top-20 with two seeds">
          <table className="w-full text-left text-sm">
            <thead className="text-xs text-muted">
              <tr>
                <th className="py-1 font-medium">Mode</th>
                {MODES.map((m) => (
                  <th key={m.key} className="py-1 text-right font-medium">
                    {m.label.split(" ")[0]}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {Object.entries(summary.by_sampling).map(([name, g]) => (
                <tr key={name} className="border-t border-line">
                  <td className="py-1.5 capitalize">{name}</td>
                  {MODES.map((m) => (
                    <td key={m.key} className="py-1.5 text-right tabular-nums">
                      {g[m.key] ? pct(g[m.key]!.validity.rate) : "–"}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      </div>

      <CaseTable data={data} />
      <Explorer data={data} />

      <Card title="How this was measured">
        <ul className="list-disc space-y-1.5 pl-5 text-sm text-muted">
          <li>
            <b className="text-ink">Valid</b> means the <i>entire</i> output is accepted by the validator's DFA (cross-checked with Python's{" "}
            <code>re.fullmatch</code> for plain regexes). The unconstrained baseline is judged just as strictly, because a validator rejects a
            string that merely <i>starts</i> correctly.
          </li>
          <li>
            Each validator has two prompts: a bare one and a few-shot one that shows examples of the format, the best a user can do without a
            constraint. Few-shot prompts end at an item boundary (never mid-item like <code>EMP-</code>), so the baseline is not
            handicapped by the prompt. Both prompts are included in every number above.
          </li>
          <li>
            <b className="text-ink">Best-of-5</b> is the usual workaround: sample up to five times and keep the first output that validates (with extraction: if any attempt has a valid prefix).
          </li>
          <li>
            <b className="text-ink">Cost</b> is the information the constraint injected: −log₂ of the probability GPT-2 itself gave to the allowed
            tokens, summed over the output. 0 bits = the model already wanted to comply.
          </li>
          <li>
            Timing is wall-clock on CPU with the server stopped, and excludes the one-off token-index build. Plain GPT-2 usually runs to the token limit, so compare
            the <i>per-token</i> speed ratio, not total seconds.
          </li>
          <li className="text-xs">
            {meta.model} · {meta.cpus ?? "?"} CPUs · torch {meta.torch} · Python {meta.python} · commit {meta.commit ?? "unknown"} ·{" "}
            {new Date(meta.date).toLocaleDateString()} · up to {meta.max_tokens} tokens per run · {Math.round(meta.wall_seconds / 60)} min wall time
          </li>
        </ul>
      </Card>
    </div>
  );
}

/** One horizontal bar with a whisker for the 95% interval. */
function RateBar({ label, rate, color, strong }: { label: string; rate: Rate; color: string; strong?: boolean }) {
  return (
    <div>
      <div className="mb-1 flex items-baseline justify-between text-sm">
        <span className={cx(strong && "font-semibold")}>{label}</span>
        <span className="tabular-nums">
          <b>{pct(rate.rate)}</b>
          <span className="ml-2 text-xs text-muted">
            {rate.k}/{rate.n} · 95% CI {pct(rate.lo)}–{pct(rate.hi)}
          </span>
        </span>
      </div>
      <div className="relative h-5 rounded bg-panel2" role="img" aria-label={`${label}: ${pct(rate.rate)} valid (95% interval ${pct(rate.lo)} to ${pct(rate.hi)})`}>
        <div className="h-5 rounded" style={{ width: `${Math.max(rate.rate * 100, rate.rate > 0 ? 0.6 : 0)}%`, background: color }} />
        <div className="absolute top-1/2 h-px bg-ink/70" style={{ left: `${rate.lo * 100}%`, width: `${(rate.hi - rate.lo) * 100}%` }} />
        <div className="absolute top-1 h-3 w-px bg-ink/70" style={{ left: `${rate.lo * 100}%` }} />
        <div className="absolute top-1 h-3 w-px bg-ink/70" style={{ left: `${rate.hi * 100}%` }} />
      </div>
    </div>
  );
}

function Mini({ rate, color }: { rate?: Rate; color: string }) {
  if (!rate) return <span className="text-muted">–</span>;
  const r = rate.rate;
  return (
    <div className="flex items-center justify-end gap-2 pl-4">
      <div className="h-1.5 w-12 rounded bg-panel2">
        <div className="h-1.5 rounded" style={{ width: `${r * 100}%`, background: color }} />
      </div>
      <span className="w-9 text-right tabular-nums">{pct(r)}</span>
    </div>
  );
}

function CategoryTable({ data }: { data: BenchResults }) {
  const short = ["Unconstrained", "+ prefix", "Best-of-5", "+ prefix", "GrammarGuard"];
  return (
    <Card title="By category">
      <div className="overflow-x-auto">
        <table className="w-full min-w-[520px] text-left text-sm">
          <thead className="text-xs text-muted">
            <tr>
              <th className="py-1 font-medium">Category</th>
              {short.map((l, i) => (
                <th key={i} className="py-1 text-right font-medium" title={ROWS[i].label}>
                  {l}
                </th>
              ))}
              <th className="py-1 text-right font-medium">Bits</th>
            </tr>
          </thead>
          <tbody>
            {Object.entries(data.summary.by_category).map(([cat, g]) => (
              <tr key={cat} className="border-t border-line">
                <td className="py-1.5 capitalize">{cat}</td>
                {ROWS.map((row) => (
                  <td key={row.label} className="py-1.5">
                    <Mini rate={row.pick(g)} color={row.color} />
                  </td>
                ))}
                <td className="py-1.5 text-right tabular-nums text-muted">{fmt(g.constrained?.mean_cost_bits)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  );
}

function CaseTable({ data }: { data: BenchResults }) {
  return (
    <Card title="Every validator" subtitle="Index = cost of building the complete token index once (large automata use a lazy index and skip it). Bars: whole output must match.">
      <div className="overflow-x-auto">
        <table className="w-full min-w-[640px] text-left text-sm">
          <thead className="text-xs text-muted">
            <tr>
              <th className="py-1 font-medium">Validator</th>
              <th className="py-1 text-right font-medium">States</th>
              <th className="py-1 text-right font-medium">Index</th>
              {MODES.map((m) => (
                <th key={m.key} className="py-1 text-right font-medium">
                  {m.label.split(" ")[0]}
                </th>
              ))}
              <th className="py-1 text-right font-medium">Bits</th>
            </tr>
          </thead>
          <tbody>
            {data.cases.map((c) => {
              const g: GroupSummary | undefined = data.summary.by_case[c.id];
              return (
                <tr key={c.id} className="border-t border-line">
                  <td className="py-1.5">
                    <div className="font-medium">{c.id}</div>
                    <code className="block max-w-xs truncate text-[11px] text-muted" title={c.text}>
                      {c.text}
                    </code>
                  </td>
                  <td className="py-1.5 text-right tabular-nums">{c.dfa_states}</td>
                  <td className="py-1.5 text-right tabular-nums">{c.index_build_ms < 1000 ? `${Math.round(c.index_build_ms)} ms` : `${(c.index_build_ms / 1000).toFixed(1)} s`}</td>
                  {MODES.map((m) => (
                    <td key={m.key} className="py-1.5">
                      <Mini rate={g?.[m.key]?.validity} color={m.color} />
                    </td>
                  ))}
                  <td className="py-1.5 text-right tabular-nums text-muted">{fmt(g?.constrained?.mean_cost_bits)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </Card>
  );
}

/** Look at the raw outputs: what unconstrained GPT-2 actually wrote versus the constrained text. */
function Explorer({ data }: { data: BenchResults }) {
  const [caseId, setCaseId] = useState(data.cases[0]?.id ?? "");
  const [mode, setMode] = useState<"all" | Mode>("all");
  const runs = useMemo(() => data.runs.filter((r) => r.case === caseId && (mode === "all" || r.mode === mode)), [data.runs, caseId, mode]);
  const c = data.cases.find((x) => x.id === caseId);
  return (
    <Card title="Audit the raw outputs" subtitle="Every run is stored with the text it produced">
      <div className="mb-3 flex flex-wrap items-end gap-3">
        <Field label="Validator">
          <select value={caseId} onChange={(e) => setCaseId(e.target.value)} className="rounded-lg border border-line bg-bg px-3 py-2 text-sm">
            {data.cases.map((x) => (
              <option key={x.id} value={x.id}>
                {x.id}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Method">
          <select value={mode} onChange={(e) => setMode(e.target.value as "all" | Mode)} className="rounded-lg border border-line bg-bg px-3 py-2 text-sm">
            <option value="all">all</option>
            {MODES.map((m) => (
              <option key={m.key} value={m.key}>
                {m.label}
              </option>
            ))}
          </select>
        </Field>
        {c && <code className="pb-2 text-xs text-muted">{c.text}</code>}
      </div>
      <div className="max-h-96 overflow-auto rounded-lg border border-line">
        <table className="w-full text-left text-xs">
          <thead className="sticky top-0 bg-panel2 text-muted">
            <tr>
              <th className="px-3 py-1.5 font-medium">Method</th>
              <th className="px-3 py-1.5 font-medium">Prompt / sampling</th>
              <th className="px-3 py-1.5 font-medium">Valid</th>
              <th className="px-3 py-1.5 font-medium">Output</th>
            </tr>
          </thead>
          <tbody>
            {runs.map((r, i) => (
              <tr key={i} className="border-t border-line align-top">
                <td className="whitespace-nowrap px-3 py-1.5">{r.mode}</td>
                <td className="whitespace-nowrap px-3 py-1.5 text-muted">
                  #{r.prompt + 1} · {r.sampling}
                  {r.sampling === "sample" ? ` s${r.seed}` : ""}
                  {r.attempts ? ` · ${r.attempts} tries` : ""}
                </td>
                <td className="px-3 py-1.5">
                  <Badge tone={r.valid ? "good" : "bad"}>{r.valid ? "✓" : "✗"}</Badge>
                </td>
                <td className="max-w-xl break-all px-3 py-1.5 font-mono">{showOutput(r.text)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  );
}


