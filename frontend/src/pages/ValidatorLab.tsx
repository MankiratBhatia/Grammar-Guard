import { useMemo, useState } from "react";
import { api } from "../api/client";
import type { Constraint, DiffResponse, TestgenResponse } from "../api/types";
import { ConstraintInput } from "../components/ConstraintInput";
import { Badge, Button, Card, Empty, ErrorBox, Spinner, Stat, cx, downloadText, visible } from "../components/ui";
import { useAsync, useStored } from "../lib/hooks";

const OLD: Constraint = { kind: "regex", text: "[0-9]{3}-[0-9]{2}", names: {} };
const NEW: Constraint = { kind: "regex", text: "[0-9]{3}[-_][0-9]{2}", names: {} };

const RELATION: Record<DiffResponse["relation"], { tone: "good" | "warn" | "bad"; title: string; meaning: string }> = {
  equal: { tone: "good", title: "Equivalent", meaning: "Safe to swap: no input changes its verdict." },
  new_stricter: { tone: "warn", title: "New validator is stricter", meaning: "Inputs that used to be valid will now be rejected. Check that this is intended." },
  new_looser: { tone: "warn", title: "New validator is looser", meaning: "Inputs that used to be rejected will now be accepted. Check for security or data-quality impact." },
  overlap: { tone: "bad", title: "Behaviour changes both ways", meaning: "Some old inputs are now rejected and some new inputs are now accepted." },
  disjoint: { tone: "bad", title: "Completely different", meaning: "No string is accepted by both validators." },
};

export default function ValidatorLab() {
  const [oldC, setOld] = useStored<Constraint>("gg-lab-old", OLD);
  const [newC, setNew] = useStored<Constraint>("gg-lab-new", NEW);
  const diff = useAsync((a: Constraint, b: Constraint) => api.diff(a, b, 6));
  const r = diff.data;
  const rel = r ? RELATION[r.relation] : null;

  return (
    <div className="space-y-5">
      <Card
        title="Validator diff"
        subtitle="Closure properties in action: old − new, new − old and old ∩ new are each built as an automaton and searched for their shortest strings."
      >
        <div className="grid gap-4 md:grid-cols-2">
          <ConstraintInput value={oldC} onChange={setOld} label="Old validator" examples={false} />
          <ConstraintInput value={newC} onChange={setNew} label="New validator" examples={false} />
        </div>
        <div className="mt-4 flex items-center gap-3">
          <Button variant="primary" onClick={() => void diff.run(oldC, newC)} disabled={diff.loading || !oldC.text.trim() || !newC.text.trim()}>
            Compare validators
          </Button>
          {diff.loading && <Spinner />}
        </div>
        <div className="mt-3 space-y-2">
          <ErrorBox error={diff.error} source={oldC.text} />
        </div>
      </Card>

      {!r && !diff.error && !diff.loading && <Empty>Compare two validators to see exactly which inputs change verdict, with examples.</Empty>}

      {r && rel && (
        <Card title={rel.title} actions={<Badge tone={rel.tone}>{r.relation.replace("_", " ")}</Badge>}>
          <p className="mb-4 text-sm">{r.summary} {rel.meaning}</p>
          <div className="grid gap-4 md:grid-cols-3">
            <Witnesses title="Only the OLD accepts" tone="bad" items={r.only_old} note="rejected after the change" />
            <Witnesses title="Only the NEW accepts" tone="warn" items={r.only_new} note="newly accepted" />
            <Witnesses title="Both accept" tone="good" items={r.both} note="unchanged" />
          </div>
          <p className="mt-3 text-xs text-muted">
            Examples are the shortest strings in each set, found by search over the product automaton ({r.states_old} and {r.states_new} states).
          </p>
        </Card>
      )}

      <TestSuite initial={newC} />
    </div>
  );
}

function Witnesses({ title, items, tone, note }: { title: string; items: string[]; tone: "good" | "warn" | "bad"; note: string }) {
  return (
    <div className="rounded-lg border border-line p-3">
      <div className="mb-2 flex items-center justify-between">
        <h3 className="text-sm font-medium">{title}</h3>
        <Badge tone={items.length ? tone : "neutral"}>{items.length ? note : "none"}</Badge>
      </div>
      {items.length ? (
        <ul className="space-y-1">
          {items.map((s) => (
            <li key={s} className="break-all rounded bg-panel2 px-2 py-1 font-mono text-xs">
              {visible(s)}
            </li>
          ))}
        </ul>
      ) : (
        <p className="text-xs text-muted">No such strings.</p>
      )}
    </div>
  );
}

const KIND_LABEL: Record<string, string> = {
  transition: "covers a transition",
  boundary: "breaks right after a valid prefix",
  substitution: "one character swapped",
  deletion: "one character left out",
  truncation: "stops too early",
};

/** Synthesize a test suite for a validator: every automaton transition covered, plus targeted invalid inputs. */
function TestSuite({ initial }: { initial: Constraint }) {
  const [constraint, setConstraint] = useState<Constraint>(initial);
  const [filter, setFilter] = useState("all");
  const suite = useAsync((c: Constraint) => api.testgen(c, "json", 2000));
  const exporter = useAsync(async (c: Constraint, fmt: "csv" | "pytest") => api.testgen(c, fmt, 2000));
  const r: TestgenResponse | null = suite.data;
  const shown = useMemo(() => (r ? r.cases.filter((c) => filter === "all" || c.kind === filter) : []), [r, filter]);

  async function exportAs(fmt: "csv" | "pytest") {
    const out = await exporter.run(constraint, fmt);
    if (!out) return;
    if (fmt === "csv" && out.csv) downloadText("validator-tests.csv", out.csv, "text/csv");
    if (fmt === "pytest" && out.pytest) downloadText("test_validator.py", out.pytest, "text/x-python");
  }

  return (
    <Card
      title="Test-suite synthesis"
      subtitle="Positive cases that exercise every transition of the validator's automaton, plus invalid inputs aimed at where validators usually go wrong."
    >
      <ConstraintInput value={constraint} onChange={setConstraint} label="Validator to test" examples={false} />
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <Button variant="primary" onClick={() => void suite.run(constraint)} disabled={suite.loading || !constraint.text.trim()}>
          Generate test suite
        </Button>
        <Button onClick={() => void setConstraint(initial)} variant="ghost">
          Use the new validator above
        </Button>
        {suite.loading && <Spinner />}
      </div>
      <div className="mt-3">
        <ErrorBox error={suite.error} source={constraint.text} />
        <ErrorBox error={exporter.error} />
      </div>
      {r && (
        <div className="mt-4 space-y-4">
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            <Stat label="Test cases" value={r.cases.length} />
            <Stat
              label="Transition coverage"
              value={`${Math.round(Number(r.coverage.transition_coverage) * 100)}%`}
              hint={`${r.coverage.transitions_covered} of ${r.coverage.transitions_total} useful transitions`}
            />
            <Stat label="Should accept" value={r.cases.filter((c) => c.expected).length} />
            <Stat label="Should reject" value={r.cases.filter((c) => !c.expected).length} />
          </div>
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="flex flex-wrap gap-1.5">
              {["all", ...Object.keys(r.counts)].map((k) => (
                <button
                  key={k}
                  type="button"
                  onClick={() => setFilter(k)}
                  className={cx("rounded-full border px-2.5 py-0.5 text-xs", filter === k ? "border-accent bg-accent-soft text-accent" : "border-line text-muted hover:text-ink")}
                >
                  {k === "all" ? `all (${r.cases.length})` : `${k} (${r.counts[k]})`}
                </button>
              ))}
            </div>
            <div className="flex gap-2">
              <Button onClick={() => void exportAs("pytest")} disabled={exporter.loading}>
                Download pytest file
              </Button>
              <Button onClick={() => void exportAs("csv")} disabled={exporter.loading}>
                Download CSV
              </Button>
              <Button onClick={() => downloadText("validator-tests.json", JSON.stringify(r.cases, null, 2))}>JSON</Button>
            </div>
          </div>
          <div className="max-h-96 overflow-auto rounded-lg border border-line">
            <table className="w-full text-left text-sm">
              <thead className="sticky top-0 bg-panel2 text-xs text-muted">
                <tr>
                  <th className="px-3 py-2 font-medium">Input</th>
                  <th className="px-3 py-2 font-medium">Expected</th>
                  <th className="px-3 py-2 font-medium">Why this case</th>
                </tr>
              </thead>
              <tbody>
                {shown.map((c, i) => (
                  <tr key={i} className="border-t border-line">
                    <td className="px-3 py-1.5 font-mono text-xs">{visible(c.text)}</td>
                    <td className="px-3 py-1.5">
                      <Badge tone={c.expected ? "good" : "bad"}>{c.expected ? "accept" : "reject"}</Badge>
                    </td>
                    <td className="px-3 py-1.5 text-xs text-muted">
                      <span className="mr-1 text-ink">{KIND_LABEL[c.kind] ?? c.kind}:</span>
                      {c.reason}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="text-xs text-muted">
            "Any other character" is infinitely many inputs, so rejecting cases try a few common offenders (space, <code>_</code>, <code>-</code>, <code>.</code>, <code>@</code>, <code>#</code>) rather than all of them.
          </p>
        </div>
      )}
    </Card>
  );
}

