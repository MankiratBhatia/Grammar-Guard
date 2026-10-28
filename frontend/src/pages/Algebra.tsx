import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { AlgebraResponse, Constraint } from "../api/types";
import { ConstraintInput } from "../components/ConstraintInput";
import { Graph } from "../components/Graph";
import { Badge, Button, Card, Empty, ErrorBox, Field, Spinner, Stat, TextInput, visible } from "../components/ui";
import { useAsync, useDebounced, useStored } from "../lib/hooks";

const DEFAULT: Constraint = { kind: "expression", text: "email & !contains(/spam/) & length(8, 30)", names: {} };

/** 1234567 -> "1,234,567"; numbers longer than 12 digits -> "3.62 × 10^45" (the exact value is in the tooltip). */
export function formatBig(digits: string): string {
  if (digits.length > 12) return `${digits[0]}.${digits.slice(1, 3)} × 10^${digits.length - 1}`;
  return digits.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
}

export default function Algebra() {
  const [constraint, setConstraint] = useStored<Constraint>("gg-algebra", DEFAULT);
  const debounced = useDebounced(constraint, 600);
  const evaluation = useAsync((c: Constraint) => api.algebra(c.text, c.names ?? {}, 8));

  useEffect(() => {
    if (debounced.text.trim()) void evaluation.run(debounced);
  }, [debounced]); // eslint-disable-line react-hooks/exhaustive-deps

  const r = evaluation.data;
  return (
    <div className="space-y-5">
      <Card
        title="Constraint expression"
        subtitle="Regular languages are closed under ∩ ∪ − ¬ concatenation and star. Each operator below is one of those constructions, applied to automata."
        actions={evaluation.loading ? <Spinner label="" /> : undefined}
      >
        <ConstraintInput value={constraint} onChange={(c) => setConstraint({ ...c, kind: "expression" })} kinds={["expression"]} label="Expression" />
        <div className="mt-3">
          <ErrorBox error={evaluation.error} source={constraint.text} />
        </div>
      </Card>

      {!r && !evaluation.error && <Empty>{evaluation.loading ? <Spinner label="Evaluating…" /> : "Write an expression to build its automaton."}</Empty>}

      {r && (
        <>
          <Summary r={r} />
          <div className="grid gap-5 lg:grid-cols-2">
            <Card title="How it was built" subtitle="One closure construction per step; every intermediate automaton is minimized">
              <ol className="space-y-2">
                {r.steps.map((s, i) => (
                  <li key={i} className="flex items-start justify-between gap-3 rounded-lg border border-line px-3 py-2 text-sm">
                    <div className="min-w-0">
                      <code className="block truncate font-mono text-xs" title={s.expr}>
                        {s.expr}
                      </code>
                      <span className="text-xs text-muted">{s.operation}</span>
                    </div>
                    <Badge tone="accent">{s.states} states</Badge>
                  </li>
                ))}
              </ol>
            </Card>
            <Card title="Strings in the language" subtitle="The shortest accepted strings (any character outside the alphabet is shown as a stand-in)">
              {r.examples.length ? (
                <ul className="flex flex-wrap gap-2">
                  {r.examples.map((e) => (
                    <li key={e} className="rounded-md border border-line bg-panel2 px-2 py-1 font-mono text-xs">
                      {visible(e)}
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="text-sm text-muted">The language is empty: no string satisfies all the constraints.</p>
              )}
              <Counts counts={r.counts} />
            </Card>
          </div>
          <Card title="Automaton" subtitle={`${r.dfa.states} states (${r.dfa.live_states} live). Dashed = can never reach acceptance.`}>
            <Graph dot={r.dfa.dot ?? null} label="Minimal DFA of the expression" className="rounded-lg border border-line bg-bg p-3" />
          </Card>
          <Sampler constraint={constraint} />
          <Equivalence a={constraint} />
        </>
      )}
    </div>
  );
}

function Summary({ r }: { r: AlgebraResponse }) {
  return (
    <div className="space-y-3">
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Stat label="Minimal DFA states" value={r.dfa.states} />
        <Stat label="Live states" value={r.dfa.live_states ?? "—"} hint="States from which an accepting state is reachable" />
        <Stat label="Language" value={r.finite ? "finite" : "infinite"} hint="Infinite iff a useful state lies on a cycle" />
        <Stat label="Strings" value={r.size ? formatBig(r.size) : "∞"} hint={r.size ? `Exactly ${r.size}` : "Unbounded"} />
      </div>
      <Card title="Equivalent regular expression" subtitle="Kleene's theorem: state elimination turns the DFA back into a regex">
        {r.regex !== null && r.regex !== undefined ? (
          <div className="flex items-start justify-between gap-3">
            <code className="min-w-0 break-all font-mono text-sm">{r.regex}</code>
            <Button variant="ghost" onClick={() => void navigator.clipboard?.writeText(r.regex ?? "")}>
              Copy
            </Button>
          </div>
        ) : (
          <p className="text-sm text-muted">{r.regex_note}</p>
        )}
      </Card>
    </div>
  );
}

/** Number of accepted strings per length; bar length = number of digits (a log scale, since counts explode). */
function Counts({ counts }: { counts: string[] }) {
  const nonzero = counts.map((c, n) => ({ n, c })).filter((x) => x.c !== "0");
  if (!nonzero.length) return null;
  const max = Math.max(...nonzero.map((x) => x.c.length));
  return (
    <div className="mt-4">
      <h3 className="mb-1 text-xs font-medium text-muted">Accepted strings by length (log scale)</h3>
      <div className="space-y-1">
        {nonzero.slice(0, 13).map(({ n, c }) => (
          <div key={n} className="flex items-center gap-2 text-xs">
            <span className="w-14 shrink-0 text-muted">length {n}</span>
            <div className="h-2 flex-1 rounded bg-panel2">
              <div className="h-2 rounded bg-accent" style={{ width: `${(c.length / max) * 100}%` }} />
            </div>
            <span className="w-28 shrink-0 text-right font-mono tabular-nums" title={c}>{formatBig(c)}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

/** Exact uniform sampling from the language, by a dynamic programme over the DFA. */
function Sampler({ constraint }: { constraint: Constraint }) {
  const [length, setLength] = useState(12);
  const [seed, setSeed] = useState(0);
  const sampler = useAsync((c: Constraint, n: number, s: number) => api.count(c, 12, n, 8, s));
  return (
    <Card title="Uniform random samples" subtitle="Every string of the chosen length is equally likely (counted exactly, not guessed)">
      <div className="flex flex-wrap items-end gap-3">
        <Field label="Length" className="w-28">
          <TextInput type="number" min={0} max={64} value={length} onChange={(e) => setLength(Math.max(0, Math.min(64, Number(e.target.value) || 0)))} />
        </Field>
        <Field label="Seed" className="w-28">
          <TextInput type="number" min={0} value={seed} onChange={(e) => setSeed(Math.max(0, Number(e.target.value) || 0))} />
        </Field>
        <Button onClick={() => void sampler.run(constraint, length, seed)} disabled={sampler.loading}>
          Sample
        </Button>
      </div>
      <div className="mt-3 space-y-2">
        <ErrorBox error={sampler.error} />
        {sampler.data &&
          (sampler.data.samples.length ? (
            <ul className="flex flex-wrap gap-2">
              {sampler.data.samples.map((s, i) => (
                <li key={i} className="rounded-md border border-line bg-panel2 px-2 py-1 font-mono text-xs">
                  {visible(s)}
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-sm text-muted">No string of length {length} satisfies the expression. Try another length.</p>
          ))}
      </div>
    </Card>
  );
}

/** L1 = L2 iff their symmetric difference is empty; otherwise a shortest string in it. */
function Equivalence({ a }: { a: Constraint }) {
  const [b, setB] = useState<Constraint>({ kind: "regex", text: "", names: {} });
  const eq = useAsync((x: Constraint, y: Constraint) => api.equivalence(x, y));
  const r = eq.data;
  return (
    <Card title="Compare with another constraint" subtitle="Are they the same language? If not, you get a shortest string that tells them apart.">
      <ConstraintInput value={b} onChange={setB} label="Other constraint" examples={false} />
      <div className="mt-3 flex items-center gap-3">
        <Button variant="primary" onClick={() => void eq.run(a, b)} disabled={eq.loading || !b.text.trim()}>
          Compare
        </Button>
        {eq.loading && <Spinner />}
      </div>
      <div className="mt-3 space-y-2">
        <ErrorBox error={eq.error} source={b.text} />
        {r &&
          (r.equivalent ? (
            <Badge tone="good">Equivalent: both define exactly the same language</Badge>
          ) : (
            <div className="space-y-1 text-sm">
              <Badge tone="bad">Not equivalent</Badge>
              <p>
                Shortest difference: <code className="rounded bg-panel2 px-1.5 py-0.5 font-mono">{visible(r.counterexample ?? "")}</code>
              </p>
              <p className="text-muted">
                The expression above {r.accepted_by_a ? "accepts" : "rejects"} it; the other constraint {r.accepted_by_b ? "accepts" : "rejects"} it.
              </p>
            </div>
          ))}
      </div>
    </Card>
  );
}
