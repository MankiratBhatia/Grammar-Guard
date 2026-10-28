import { useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
import type { AutomatonView, Constraint } from "../api/types";
import { CertificatePanel } from "../components/CertificatePanel";
import { ConstraintInput } from "../components/ConstraintInput";
import { Graph } from "../components/Graph";
import { Badge, Card, Empty, ErrorBox, Field, Segmented, Spinner, Stat, TextInput, cx } from "../components/ui";
import { asExpression } from "../lib/examples";
import { useAsync, useDebounced, useStored } from "../lib/hooks";

type Stage = "nfa" | "dfa" | "min";

export default function Studio() {
  const [constraint, setConstraint] = useStored<Constraint>("gg-studio", { kind: "regex", text: "(a|b)*abb", names: {} });
  const regex = constraint.text;
  const debounced = useDebounced(regex);
  const compile = useAsync((r: string) => api.compile(r));
  const [stage, setStage] = useState<Stage>("min");

  useEffect(() => {
    void compile.run(debounced);
  }, [debounced]); // eslint-disable-line react-hooks/exhaustive-deps

  const c = compile.data;
  const view: AutomatonView | null = c ? { nfa: c.nfa, dfa: c.dfa, min: c.min_dfa }[stage] : null;

  return (
    <div className="space-y-5">
      <Card title="Regular expression" subtitle="Supports | * + ? ( ) [a-z] {n} {n,m} and \ escapes. Every step below is our own implementation.">
        <ConstraintInput value={constraint} onChange={(x) => setConstraint({ ...x, kind: "regex" })} kinds={["regex"]} label="Regex" />
        <div className="mt-3">
          <ErrorBox error={compile.error} source={regex} />
        </div>
      </Card>

      {!c && !compile.error && <Empty>{compile.loading ? <Spinner label="Compiling…" /> : "Enter a regex to see its automata."}</Empty>}

      {c && (
        <>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            <Stat label="ε-NFA states" value={c.nfa.states} hint="Thompson's construction" />
            <Stat label="DFA states" value={c.dfa.states} hint="Subset construction (reachable subsets only, plus the dead state)" />
            <Stat label="Minimal DFA" value={c.min_dfa.states} hint="Table-filling (Myhill–Nerode) minimization" />
            <Stat label="Alphabet" value={c.alphabet.length} hint={c.alphabet.join(" ")} />
          </div>
          <Card
            title="Automata"
            subtitle={
              <>
                Parsed as <code className="font-mono">{c.ast}</code>
              </>
            }
            actions={
              <Segmented
                label="Automaton stage"
                value={stage}
                onChange={setStage}
                options={[
                  { value: "nfa", label: "ε-NFA" },
                  { value: "dfa", label: "DFA" },
                  { value: "min", label: "Minimal DFA" },
                ]}
              />
            }
          >
            {view && (
              <div className="space-y-3">
                <p className="text-xs text-muted">
                  {stage === "nfa" && "Thompson's construction: one start and one accepting state per fragment, glued with ε edges."}
                  {stage === "dfa" && "Subset construction: each state is the ε-closure of a set of NFA states (shown in braces). Missing edges go to the hidden dead state."}
                  {stage === "min" && "Table filling merged indistinguishable states (labels list the merged DFA states). Dashed = cannot reach acceptance."}
                  {" "}Double circle = accepting.
                </p>
                <Graph dot={view.dot ?? null} label={`${stage} automaton`} className="rounded-lg border border-line bg-bg p-3" />
                <TransitionTable view={view} />
              </div>
            )}
          </Card>
          <Tester constraint={constraint} />
        </>
      )}
    </div>
  );
}

function TransitionTable({ view }: { view: AutomatonView }) {
  const [open, setOpen] = useState(false);
  return (
    <div>
      <button type="button" className="text-xs text-accent underline" onClick={() => setOpen((o) => !o)}>
        {open ? "Hide" : "Show"} transition table ({view.transitions.length} edges)
      </button>
      {open && (
        <div className="mt-2 max-h-72 overflow-auto rounded-lg border border-line">
          <table className="w-full text-left font-mono text-xs">
            <thead className="sticky top-0 bg-panel2 text-muted">
              <tr>
                <th className="px-3 py-1.5 font-medium">from</th>
                <th className="px-3 py-1.5 font-medium">symbols</th>
                <th className="px-3 py-1.5 font-medium">to</th>
              </tr>
            </thead>
            <tbody>
              {view.transitions.map((t, i) => (
                <tr key={i} className="border-t border-line">
                  <td className="px-3 py-1">
                    q{t.source}
                    {t.source === view.start && " (start)"}
                  </td>
                  <td className="px-3 py-1">{t.label}</td>
                  <td className="px-3 py-1">
                    q{t.target}
                    {view.accepting.includes(t.target) && " ✓"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

/** Run a string through the minimal DFA: scrub through the path, see where it dies. */
function Tester({ constraint }: { constraint: Constraint }) {
  const [text, setText] = useStored("gg-studio-test", "aabb");
  const debounced = useDebounced(text, 250);
  const member = useAsync((cst: Constraint, t: string) => api.membership(cst, t));
  const drawing = useAsync((cst: Constraint) => api.algebra(asExpression(cst), cst.names ?? {}, 0));
  const [step, setStep] = useState<number | null>(null);
  const regex = useDebounced(constraint.text);

  useEffect(() => {
    void member.run({ ...constraint, text: regex }, debounced);
    setStep(null);
  }, [regex, debounced]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    void drawing.run({ ...constraint, text: regex });
  }, [regex]); // eslint-disable-line react-hooks/exhaustive-deps

  const m = member.data;
  const at = m ? (step ?? m.path.length) : 0;
  const state = useMemo(() => (m ? (at === 0 ? m.start : m.path[at - 1].target) : null), [m, at]);

  return (
    <Card title="Try a string" subtitle="Run it through the minimal DFA, one character at a time">
      <div className="space-y-3">
        <Field label="Input string">
          <TextInput mono value={text} onChange={(e) => setText(e.target.value)} placeholder="type something…" />
        </Field>
        <ErrorBox error={member.error} />
        {m && (
          <>
            <div className="flex flex-wrap items-center gap-2">
              <Badge tone={m.accepted ? "good" : "bad"}>{m.accepted ? "✓ accepted" : "✗ rejected"}</Badge>
              {!m.accepted && m.viable_prefix && <Badge tone="warn">valid prefix: could still be completed</Badge>}
              {m.doomed_at !== null && m.doomed_at !== undefined && <Badge tone="bad">no completion exists after character {m.doomed_at + 1}</Badge>}
            </div>
            <div className="flex flex-wrap gap-1 font-mono text-sm" aria-label="State path">
              <PathChip label="start" state={`q${m.start}`} active={at === 0} onClick={() => setStep(0)} />
              {m.path.map((p, i) => (
                <PathChip key={i} label={p.char === " " ? "␣" : p.char} state={`q${p.target}`} bad={!p.live} active={at === i + 1} onClick={() => setStep(i + 1)} />
              ))}
            </div>
            {m.path.length > 0 && (
              <input
                type="range"
                min={0}
                max={m.path.length}
                value={at}
                onChange={(e) => setStep(Number(e.target.value))}
                aria-label="Step through the run"
                className="w-full accent-[var(--accent)]"
              />
            )}
            <Graph dot={drawing.data?.dfa.dot ?? null} highlight={state} label="Minimal DFA with the current state highlighted" className="rounded-lg border border-line bg-bg p-3" />
            <details className="rounded-lg border border-line p-3">
              <summary className="cursor-pointer text-sm font-medium">Certificate for this run</summary>
              <div className="mt-3">
                <CertificatePanel certificate={m.certificate} />
              </div>
            </details>
          </>
        )}
      </div>
    </Card>
  );
}

function PathChip({ label, state, bad, active, onClick }: { label: string; state: string; bad?: boolean; active: boolean; onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cx(
        "flex flex-col items-center rounded-md border px-2 py-1 leading-tight transition",
        bad ? "border-bad/50 bg-bad-soft text-bad" : "border-line bg-panel2",
        active && "outline outline-2 outline-accent",
      )}
    >
      <span className="text-base">{label}</span>
      <span className="text-[10px] text-muted">{state}</span>
    </button>
  );
}
