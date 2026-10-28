import { useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
import type { AutomatonView } from "../api/types";
import { Graph } from "../components/Graph";
import { Badge, Button, Card, Empty, ErrorBox, Field, Segmented, Spinner, TextInput, cx } from "../components/ui";
import { useAsync, useDebounced, useStored } from "../lib/hooks";
import { usePlayer } from "../lib/player";

interface SubsetStep {
  from: number[];
  symbol: string;
  move: number[];
  closure: number[];
  new: boolean;
}
interface Mark {
  pair: [number, number];
  reason?: string;
  symbol?: string;
  leads_to?: [number, number];
}
interface Round {
  round: number;
  marked: Mark[];
}

const set = (xs: number[]) => (xs.length ? `{${xs.join(", ")}}` : "∅");
const sameSet = (a: number[], b: number[]) => a.length === b.length && a.every((x, i) => x === b[i]);

export default function Learn() {
  const [regex, setRegex] = useStored("gg-learn-regex", "(a|b)*abb");
  const [tab, setTab] = useState<"subset" | "minimize">("subset");
  const debounced = useDebounced(regex);
  const subset = useAsync((r: string) => api.learnSubset(r));
  const minimize = useAsync((r: string) => api.learnMinimize(r));

  useEffect(() => {
    if (!debounced.trim()) return;
    void subset.run(debounced);
    void minimize.run(debounced);
  }, [debounced]); // eslint-disable-line react-hooks/exhaustive-deps

  const active = tab === "subset" ? subset : minimize;
  return (
    <div className="space-y-5">
      <Card title="Regular expression" subtitle="Small regexes work best here (the step views are limited to 40 DFA states).">
        <div className="flex flex-wrap items-end gap-3">
          <Field label="Regex" className="min-w-64 flex-1">
            <TextInput mono value={regex} onChange={(e) => setRegex(e.target.value)} />
          </Field>
          <Segmented
            label="Algorithm"
            value={tab}
            onChange={setTab}
            options={[
              { value: "subset", label: "Subset construction" },
              { value: "minimize", label: "Table filling" },
            ]}
          />
        </div>
        <div className="mt-3">
          <ErrorBox error={active.error} source={regex} />
        </div>
      </Card>
      {!active.data && !active.error && <Empty>{active.loading ? <Spinner /> : "Enter a regex."}</Empty>}
      {tab === "subset" && subset.data && (
        <SubsetPlayer steps={subset.data.steps as unknown as SubsetStep[]} nfa={subset.data.nfa} dfa={subset.data.dfa} />
      )}
      {tab === "minimize" && minimize.data && (
        <MinimizePlayer rounds={minimize.data.rounds as unknown as Round[]} dfa={minimize.data.dfa} minDfa={minimize.data.min_dfa} classes={minimize.data.classes} />
      )}
    </div>
  );
}

function Controls({ player, last, label }: { player: ReturnType<typeof usePlayer>; last: number; label: string }) {
  return (
    <div className="flex flex-wrap items-center gap-2">
      <Button onClick={() => player.setI(0)} disabled={player.i === 0} aria-label="First step">
        ⏮
      </Button>
      <Button onClick={() => player.setI(player.i - 1)} disabled={player.i === 0} aria-label="Previous step">
        ◀
      </Button>
      <Button variant="primary" onClick={player.toggle} aria-label={player.playing ? "Pause" : "Play"}>
        {player.playing ? "⏸ Pause" : "▶ Play"}
      </Button>
      <Button onClick={() => player.setI(player.i + 1)} disabled={player.i >= last} aria-label="Next step">
        ▶
      </Button>
      <Button onClick={() => player.setI(last)} disabled={player.i >= last} aria-label="Last step">
        ⏭
      </Button>
      <input
        type="range"
        min={0}
        max={last}
        value={player.i}
        onChange={(e) => {
          player.stop();
          player.setI(Number(e.target.value));
        }}
        aria-label={label}
        className="min-w-32 flex-1 accent-[var(--accent)]"
      />
      <span className="text-xs tabular-nums text-muted">
        {player.i + 1} / {last + 1}
      </span>
    </div>
  );
}

function SubsetPlayer({ steps, nfa, dfa }: { steps: SubsetStep[]; nfa: AutomatonView; dfa: AutomatonView }) {
  const last = Math.max(steps.length - 1, 0);
  const player = usePlayer(last);
  const step = steps[player.i];
  const seen = useMemo(() => {
    const known: number[][] = steps.length ? [steps[0].from] : [];
    steps.slice(0, player.i + 1).forEach((s) => {
      if (s.new && !known.some((k) => sameSet(k, s.closure))) known.push(s.closure);
    });
    return known;
  }, [steps, player.i]);

  if (!step) return <Empty>No steps.</Empty>;
  return (
    <div className="space-y-5">
      <Card title="Subset construction" subtitle="Each DFA state is a SET of NFA states. From a set S and a symbol a, go to ε-closure(move(S, a)).">
        <Controls player={player} last={last} label="Step through subset construction" />
        <div className="mt-4 rounded-lg border border-accent/40 bg-accent-soft p-4 text-sm leading-relaxed text-ink">
          <p>
            From the DFA state <b className="font-mono">{set(step.from)}</b>, read{" "}
            <b className="font-mono">{step.symbol === "other" ? "any other character" : `'${step.symbol}'`}</b>:
          </p>
          <p className="mt-1">
            move = <code className="font-mono">{set(step.move)}</code>
            {"  →  "}ε-closure = <b className="font-mono">{set(step.closure)}</b>
            {"  "}
            {step.new ? <Badge tone="good">new DFA state</Badge> : <Badge>already known</Badge>}
            {step.closure.length === 0 && <Badge tone="warn">dead state</Badge>}
          </p>
        </div>
        <div className="mt-4">
          <h3 className="mb-1 text-xs font-medium text-muted">DFA states found so far</h3>
          <div className="flex flex-wrap gap-1.5">
            {seen.map((s, i) => (
              <span key={i} className={cx("rounded-md border px-2 py-0.5 font-mono text-xs", sameSet(s, step.closure) ? "border-accent bg-accent-soft" : "border-line bg-panel2")}>
                {set(s)}
              </span>
            ))}
          </div>
        </div>
      </Card>
      <div className="grid gap-5 lg:grid-cols-2">
        <Card title="Steps so far">
          <div className="max-h-80 overflow-auto rounded-lg border border-line">
            <table className="w-full text-left font-mono text-xs">
              <thead className="sticky top-0 bg-panel2 text-muted">
                <tr>
                  <th className="px-2 py-1.5 font-medium">S</th>
                  <th className="px-2 py-1.5 font-medium">a</th>
                  <th className="px-2 py-1.5 font-medium">move(S, a)</th>
                  <th className="px-2 py-1.5 font-medium">ε-closure</th>
                </tr>
              </thead>
              <tbody>
                {steps.slice(0, player.i + 1).map((s, i) => (
                  <tr key={i} className={cx("cursor-pointer border-t border-line", i === player.i && "bg-accent-soft")} onClick={() => player.setI(i)}>
                    <td className="px-2 py-1">{set(s.from)}</td>
                    <td className="px-2 py-1">{s.symbol}</td>
                    <td className="px-2 py-1">{set(s.move)}</td>
                    <td className="px-2 py-1">
                      {set(s.closure)} {s.new && "★"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="mt-2 text-xs text-muted">★ = a DFA state seen for the first time. Only reachable subsets are built.</p>
        </Card>
        <Card title="The ε-NFA" subtitle="Input to the algorithm">
          <Graph dot={nfa.dot ?? null} label="ε-NFA" className="rounded-lg border border-line bg-bg p-2" />
        </Card>
      </div>
      <Card title="Resulting DFA" subtitle={`${dfa.states} states; nodes show the NFA state sets they stand for`}>
        <Graph dot={dfa.dot ?? null} label="DFA from subset construction" className="rounded-lg border border-line bg-bg p-2" />
      </Card>
    </div>
  );
}

function MinimizePlayer({ rounds, dfa, minDfa, classes }: { rounds: Round[]; dfa: AutomatonView; minDfa: AutomatonView; classes: number[][] }) {
  const last = rounds.length; // one extra step shows the final equivalence classes
  const player = usePlayer(last, 1400);
  const n = dfa.states;
  const markedAt = useMemo(() => {
    const m = new Map<string, { round: number; mark: Mark }>();
    rounds.forEach((r) => r.marked.forEach((mk) => m.set(`${mk.pair[0]},${mk.pair[1]}`, { round: r.round, mark: mk })));
    return m;
  }, [rounds]);
  const finalStep = player.i >= last;
  const current = !finalStep ? rounds[player.i] : null;
  const label = (q: number) => dfa.state_labels?.[q] ?? `q${q}`;

  return (
    <div className="space-y-5">
      <Card title="Table-filling minimization" subtitle="Mark every pair of states that can be told apart; pairs left unmarked are equivalent and get merged.">
        <Controls player={player} last={last} label="Step through minimization" />
        <div className="mt-4 rounded-lg border border-accent/40 bg-accent-soft p-4 text-sm leading-relaxed">
          {current ? (
            <>
              <p className="font-medium">
                {current.round === 0
                  ? "Round 0: mark every pair where exactly one state is accepting. The empty string tells them apart."
                  : `Round ${current.round}: mark a pair if some symbol leads it to an already-marked pair.`}
              </p>
              <ul className="mt-2 space-y-0.5 text-xs">
                {current.marked.slice(0, 12).map((m, i) => (
                  <li key={i} className="font-mono">
                    (q{m.pair[0]}, q{m.pair[1]}){" "}
                    {m.reason ?? `on '${m.symbol === "other" ? "any other" : m.symbol}' → (q${m.leads_to?.[0]}, q${m.leads_to?.[1]}) is marked`}
                  </li>
                ))}
                {current.marked.length > 12 && <li className="text-muted">… and {current.marked.length - 12} more</li>}
              </ul>
            </>
          ) : (
            <p className="font-medium">
              Nothing more can be marked. Unmarked pairs (≡) are equivalent: {dfa.states} states merge into {classes.length}.
            </p>
          )}
        </div>
        <div className="mt-4 overflow-x-auto">
          <table className="border-collapse text-center font-mono text-xs" aria-label="Distinguishability table">
            <thead>
              <tr>
                <th />
                {Array.from({ length: n - 1 }, (_, j) => (
                  <th key={j} className="px-1 pb-1 font-medium text-muted" title={label(j)}>
                    q{j}
                    {dfa.accepting.includes(j) && "✓"}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {Array.from({ length: n - 1 }, (_, k) => {
                const i = k + 1;
                return (
                  <tr key={i}>
                    <th className="pr-2 text-right font-medium text-muted" title={label(i)}>
                      q{i}
                      {dfa.accepting.includes(i) && "✓"}
                    </th>
                    {Array.from({ length: n - 1 }, (_, j) => {
                      if (j >= i) return <td key={j} className="h-8 w-9" />;
                      const hit = markedAt.get(`${j},${i}`);
                      const shown = hit !== undefined && hit.round <= player.i;
                      const equal = finalStep && !hit;
                      return (
                        <td
                          key={j}
                          title={shown ? `Marked in round ${hit!.round}` : equal ? "Equivalent" : "Not marked yet"}
                          className={cx(
                            "h-8 w-9 border border-line",
                            shown && hit!.round === current?.round && "bg-warn-soft font-semibold text-warn",
                            shown && hit!.round !== current?.round && "bg-panel2 text-muted",
                            equal && "bg-good-soft font-semibold text-good",
                          )}
                        >
                          {shown ? `R${hit!.round}` : equal ? "≡" : ""}
                        </td>
                      );
                    })}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </Card>
      <div className="grid gap-5 lg:grid-cols-2">
        <Card title="Before" subtitle={`${dfa.states} states`}>
          <Graph dot={dfa.dot ?? null} label="DFA before minimization" className="rounded-lg border border-line bg-bg p-2" />
        </Card>
        <Card title="After" subtitle={`${minDfa.states} states; labels list the merged states`}>
          {finalStep ? (
            <Graph dot={minDfa.dot ?? null} label="Minimal DFA" className="rounded-lg border border-line bg-bg p-2" />
          ) : (
            <Empty>Step to the end of the table to see the merged automaton.</Empty>
          )}
        </Card>
      </div>
    </div>
  );
}
