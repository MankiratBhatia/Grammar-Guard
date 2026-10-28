import { useEffect, useMemo, useRef, useState } from "react";
import { api, ApiError, streamPaths } from "../api/client";
import { streamEvents } from "../api/sse";
import type { AnyToken, Constraint, DoneEvent, MetaEvent, ServerError, Source } from "../api/types";
import { CertificatePanel } from "../components/CertificatePanel";
import { ConstraintInput } from "../components/ConstraintInput";
import { Graph } from "../components/Graph";
import { PressureChart, SummaryStats, TokenDetail, TokenStream } from "../components/Telemetry";
import { Badge, Button, Card, Empty, ErrorBox, Field, Spinner, TextArea, TextInput, visible } from "../components/ui";
import { asExpression } from "../lib/examples";
import { useAsync, useStored } from "../lib/hooks";

type Phase = "idle" | "connecting" | "queued" | "running" | "done";

const STOP_TEXT: Record<string, string> = {
  eos: "model chose to stop (end-of-text, only allowed in an accepting state)",
  max_tokens: "reached the token limit",
  dead_end: "no allowed token (cannot happen with GPT-2 and plain-text constraints)",
  cancelled: "stopped",
  timeout: "stopped by the server time limit",
};

export default function Generate() {
  const [constraint, setConstraint] = useStored<Constraint>("gg-gen-constraint", { kind: "regex", text: "[0-9]{4}-[0-9]{2}-[0-9]{2}", names: {} });
  const [prompt, setPrompt] = useStored("gg-gen-prompt", "Date: ");
  const [maxTokens, setMaxTokens] = useState(40);
  const [topK, setTopK] = useState(1);
  const [temperature, setTemperature] = useState(1);
  const [seed, setSeed] = useState(0);
  const [compare, setCompare] = useState(true);

  const [phase, setPhase] = useState<Phase>("idle");
  const [tokens, setTokens] = useState<Record<Source, AnyToken[]>>({ constrained: [], unconstrained: [] });
  const [done, setDone] = useState<Partial<Record<Source, DoneEvent>>>({});
  const [meta, setMeta] = useState<MetaEvent | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [selected, setSelected] = useState<number | null>(null);
  const abort = useRef<AbortController | null>(null);
  const graph = useAsync((c: Constraint) => api.algebra(asExpression(c), c.names ?? {}, 0));

  useEffect(() => () => abort.current?.abort(), []);

  const constrained = tokens.constrained;
  const followed = selected ?? (constrained.length ? constrained[constrained.length - 1].index : null);
  const detail = constrained.find((t) => t.index === followed);
  const highlight = detail?.state ?? null;
  const running = phase === "connecting" || phase === "queued" || phase === "running";

  async function start() {
    abort.current?.abort();
    const ctl = new AbortController();
    abort.current = ctl;
    setPhase("connecting");
    setTokens({ constrained: [], unconstrained: [] });
    setDone({});
    setMeta(null);
    setError(null);
    setSelected(null);
    void graph.run(constraint);
    try {
      await streamEvents(
        streamPaths.dfa,
        { constraint, prompt, max_tokens: maxTokens, top_k: topK > 1 ? topK : null, temperature, seed, compare, certificate: true },
        ({ event, data }) => {
          if (event === "meta") setMeta(data as MetaEvent);
          else if (event === "status") setPhase("queued");
          else if (event === "token") {
            const t = data as AnyToken;
            setPhase("running");
            setTokens((cur) => ({ ...cur, [t.source]: [...cur[t.source], t] }));
          } else if (event === "done") {
            const d = data as DoneEvent;
            setDone((cur) => ({ ...cur, [d.source]: d }));
          } else if (event === "error") {
            const e = data as ServerError;
            setError(new ApiError(e.message, e.code, 200, e.position ?? null));
          }
        },
        ctl.signal,
      );
    } catch (e) {
      setError(e);
    } finally {
      if (abort.current === ctl) setPhase("done");
    }
  }

  const finalConstrained = done.constrained;
  return (
    <div className="grid gap-5 lg:grid-cols-[minmax(320px,380px)_1fr]">
      <div className="space-y-5">
        <Card title="Setup" subtitle="Describe what the output must look like, then generate.">
          <div className="space-y-4">
            <ConstraintInput value={constraint} onChange={setConstraint} onExample={setPrompt} />
            <Field label="Prompt">
              <TextArea value={prompt} onChange={(e) => setPrompt(e.target.value)} rows={3} mono placeholder="Text the model continues from" />
            </Field>
            <div className="grid grid-cols-2 gap-3">
              <Field label="Max tokens">
                <TextInput type="number" min={1} max={200} value={maxTokens} onChange={(e) => setMaxTokens(Math.max(1, Math.min(200, Number(e.target.value) || 1)))} />
              </Field>
              <Field label="Top-k (1 = greedy)">
                <TextInput type="number" min={1} max={100} value={topK} onChange={(e) => setTopK(Math.max(1, Math.min(100, Number(e.target.value) || 1)))} />
              </Field>
              <Field label="Temperature">
                <TextInput type="number" min={0.1} max={5} step={0.1} value={temperature} disabled={topK <= 1} onChange={(e) => setTemperature(Number(e.target.value) || 1)} />
              </Field>
              <Field label="Seed">
                <TextInput type="number" min={0} value={seed} disabled={topK <= 1} onChange={(e) => setSeed(Math.max(0, Number(e.target.value) || 0))} />
              </Field>
            </div>
            <label className="flex items-center gap-2 text-sm">
              <input type="checkbox" checked={compare} onChange={(e) => setCompare(e.target.checked)} className="h-4 w-4 accent-[var(--accent)]" />
              Also run without the constraint, for comparison
            </label>
            <div className="flex items-center gap-2">
              <Button variant="primary" onClick={start} disabled={running || !constraint.text.trim()}>
                {running ? "Generating…" : "Generate"}
              </Button>
              {running && (
                <Button
                  onClick={() => {
                    abort.current?.abort();
                    setPhase("done");
                  }}
                >
                  Stop
                </Button>
              )}
            </div>
            {phase === "queued" && <p className="text-xs text-warn">Another generation is running; yours is queued.</p>}
            {phase === "connecting" && <Spinner label="Compiling constraint and loading the token index…" />}
          </div>
        </Card>
        <BatchCard constraint={constraint} prompt={prompt} maxTokens={maxTokens} />
      </div>

      <div className="min-w-0 space-y-5">
        <ErrorBox error={error} source={constraint.text} />
        {phase === "idle" && !error && (
          <Empty>
            Pick an example or write a regex, then press <b>Generate</b>. You will see the model's own output next to output that is{" "}
            <i>guaranteed</i> to match, and how hard the constraint had to push at each token.
          </Empty>
        )}
        {phase !== "idle" && (
          <div className={compare ? "grid gap-4 xl:grid-cols-2" : ""}>
            {compare && (
              <Card title="Unconstrained" subtitle="What GPT-2 writes on its own" actions={done.unconstrained && <MatchBadge ok={done.unconstrained.accepted} />}>
                <TokenStream tokens={tokens.unconstrained} selected={null} onSelect={() => undefined} empty={running ? "…" : "—"} />
              </Card>
            )}
            <Card
              title="Constrained"
              subtitle={meta ? `${meta.states} DFA states, ${meta.live_states} live` : "Output restricted to the language"}
              actions={finalConstrained && <MatchBadge ok={finalConstrained.accepted} />}
            >
              <TokenStream tokens={constrained} selected={followed} onSelect={setSelected} empty={running ? "…" : "—"} />
              {finalConstrained && (
                <p className="mt-3 text-xs text-muted">
                  Stopped: {STOP_TEXT[finalConstrained.stop_reason] ?? finalConstrained.stop_reason}.
                  {!finalConstrained.accepted && finalConstrained.stop_reason === "max_tokens" && " The output is a valid prefix but not yet a complete match; raise the token limit."}
                </p>
              )}
            </Card>
          </div>
        )}
        {constrained.length > 0 && (
          <>
            <SummaryStats summary={finalConstrained?.summary} tokens={constrained.length} />
            <div className="grid gap-4 xl:grid-cols-2">
              <Card title="Constraint pressure" subtitle="How much of the model's belief the constraint removed, token by token">
                <PressureChart tokens={constrained} selected={followed} onSelect={setSelected} />
              </Card>
              <Card title={detail ? `Step ${detail.index + 1}` : "Step details"} subtitle="The selected token (follows the latest while generating)">
                <TokenDetail token={detail} />
              </Card>
            </div>
            <Card
              title="Automaton"
              subtitle={highlight !== null ? `Current state: q${highlight}` : "The minimal DFA the generator walks"}
              actions={graph.loading ? <Spinner label="" /> : undefined}
            >
              {graph.error ? <ErrorBox error={graph.error} /> : <Graph dot={graph.data?.dfa.dot ?? null} highlight={highlight} label="Minimal DFA with the current state highlighted" />}
            </Card>
          </>
        )}
        {finalConstrained?.certificate && (
          <Card title="Certificate" subtitle="Proof that the text belongs to the language">
            <CertificatePanel certificate={finalConstrained.certificate} />
          </Card>
        )}
      </div>
    </div>
  );
}

function MatchBadge({ ok }: { ok: boolean }) {
  return <Badge tone={ok ? "good" : "bad"}>{ok ? "✓ matches" : "✗ no match"}</Badge>;
}

/** Several DISTINCT valid strings: synthetic test data that is guaranteed to pass the validator. */
function BatchCard({ constraint, prompt, maxTokens }: { constraint: Constraint; prompt: string; maxTokens: number }) {
  const [count, setCount] = useState(5);
  const batch = useAsync((c: Constraint, p: string, n: number, m: number) => api.batch({ constraint: c, prompt: p, count: n, max_tokens: m, top_k: 20, certificates: false }));
  const samples = useMemo(() => batch.data?.samples ?? [], [batch.data]);
  return (
    <Card title="Sample data" subtitle="Several different strings, each guaranteed to match">
      <div className="flex items-end gap-2">
        <Field label="How many" className="w-24">
          <TextInput type="number" min={1} max={50} value={count} onChange={(e) => setCount(Math.max(1, Math.min(50, Number(e.target.value) || 1)))} />
        </Field>
        <Button onClick={() => void batch.run(constraint, prompt, count, maxTokens)} disabled={batch.loading || !constraint.text.trim()}>
          Generate samples
        </Button>
      </div>
      <div className="mt-3 space-y-2">
        {batch.loading && <Spinner />}
        <ErrorBox error={batch.error} source={constraint.text} />
        {samples.length > 0 && (
          <ul className="divide-y divide-line rounded-lg border border-line">
            {samples.map((s) => (
              <li key={s.text} className="flex items-center justify-between gap-2 px-3 py-1.5 text-sm">
                <code className="break-all">{visible(s.text)}</code>
                <span className="shrink-0 text-xs text-muted">{s.cost_bits.toFixed(1)} bits</span>
              </li>
            ))}
          </ul>
        )}
        {batch.data?.note && <p className="text-xs text-warn">{batch.data.note}</p>}
      </div>
    </Card>
  );
}
