import { useEffect, useMemo, useRef, useState } from "react";
import { ApiError, streamPaths } from "../api/client";
import { streamEvents } from "../api/sse";
import type { AnyToken, DoneEvent, ServerError, Source } from "../api/types";
import { CertificatePanel } from "../components/CertificatePanel";
import { DepthChart, StackView } from "../components/StackView";
import { SummaryStats, TokenDetail, TokenStream } from "../components/Telemetry";
import { Badge, Button, Card, Empty, ErrorBox, Field, Segmented, Spinner, TextArea, TextInput, CodeBlock } from "../components/ui";
import { DEFAULT_JSON_PROMPT, DEFAULT_SCHEMA } from "../lib/examples";
import { useStored } from "../lib/hooks";

const SCHEMA_PROMPT = '{"name":"alice","age":30,"role":"admin","tags":["a","b"],"active":true}\n';

export default function JsonSchema() {
  const [mode, setMode] = useStored<"any" | "schema">("gg-json-mode", "schema");
  const [schemaText, setSchemaText] = useStored("gg-json-schema", DEFAULT_SCHEMA);
  const [prompt, setPrompt] = useStored("gg-json-prompt", SCHEMA_PROMPT);
  const [maxTokens, setMaxTokens] = useState(80);
  const [topK, setTopK] = useState(1);
  const [seed, setSeed] = useState(0);
  const [compare, setCompare] = useState(true);

  const [running, setRunning] = useState(false);
  const [tokens, setTokens] = useState<Record<Source, AnyToken[]>>({ constrained: [], unconstrained: [] });
  const [done, setDone] = useState<Partial<Record<Source, DoneEvent>>>({});
  const [error, setError] = useState<unknown>(null);
  const [selected, setSelected] = useState<number | null>(null);
  const abort = useRef<AbortController | null>(null);
  useEffect(() => () => abort.current?.abort(), []);

  const schemaError = useMemo(() => {
    if (mode !== "schema") return null;
    try {
      JSON.parse(schemaText);
      return null;
    } catch (e) {
      return e instanceof Error ? e.message : "invalid JSON";
    }
  }, [mode, schemaText]);

  const constrained = tokens.constrained;
  const followed = selected ?? (constrained.length ? constrained.length - 1 : null);
  const detail = followed !== null ? constrained[followed] : undefined;
  const previous = followed !== null && followed > 0 ? constrained[followed - 1]?.stack : [];
  const final = done.constrained;
  const pretty = useMemo(() => {
    if (!final?.accepted) return null;
    try {
      return JSON.stringify(JSON.parse(final.text), null, 2);
    } catch {
      return null;
    }
  }, [final]);

  async function start() {
    abort.current?.abort();
    const ctl = new AbortController();
    abort.current = ctl;
    setRunning(true);
    setTokens({ constrained: [], unconstrained: [] });
    setDone({});
    setError(null);
    setSelected(null);
    try {
      await streamEvents(
        streamPaths.pda,
        {
          prompt,
          schema: mode === "schema" ? JSON.parse(schemaText) : null,
          max_tokens: maxTokens,
          top_k: topK > 1 ? topK : null,
          seed,
          compare,
          certificate: true,
        },
        ({ event, data }) => {
          if (event === "token") {
            const t = data as AnyToken;
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
      if (abort.current === ctl) setRunning(false);
    }
  }

  return (
    <div className="grid gap-5 lg:grid-cols-[minmax(320px,400px)_1fr]">
      <div className="space-y-5">
        <Card title="Setup" subtitle="Context-free constraints need a stack: nested brackets cannot be counted by a finite automaton.">
          <div className="space-y-4">
            <Segmented
              label="Constraint mode"
              value={mode}
              onChange={(m) => {
                setMode(m);
                setPrompt(m === "schema" ? SCHEMA_PROMPT : DEFAULT_JSON_PROMPT);
              }}
              options={[
                { value: "schema", label: "Schema" },
                { value: "any", label: "Any JSON object / array" },
              ]}
            />
            {mode === "schema" && (
              <Field
                label="Schema"
                hint="Supported: object (fixed key order), array (minItems 0/1), string, integer, boolean, null, enum of lowercase words."
              >
                <TextArea mono rows={13} value={schemaText} onChange={(e) => setSchemaText(e.target.value)} className="text-xs" />
              </Field>
            )}
            {schemaError && <p className="text-xs text-bad">Schema is not valid JSON: {schemaError}</p>}
            <Field label="Prompt (examples of the format help a small model a lot)">
              <TextArea mono rows={4} value={prompt} onChange={(e) => setPrompt(e.target.value)} className="text-xs" />
            </Field>
            <div className="grid grid-cols-3 gap-3">
              <Field label="Max tokens">
                <TextInput type="number" min={1} max={200} value={maxTokens} onChange={(e) => setMaxTokens(Math.max(1, Math.min(200, Number(e.target.value) || 1)))} />
              </Field>
              <Field label="Top-k">
                <TextInput type="number" min={1} max={100} value={topK} onChange={(e) => setTopK(Math.max(1, Math.min(100, Number(e.target.value) || 1)))} />
              </Field>
              <Field label="Seed">
                <TextInput type="number" min={0} value={seed} disabled={topK <= 1} onChange={(e) => setSeed(Math.max(0, Number(e.target.value) || 0))} />
              </Field>
            </div>
            <label className="flex items-center gap-2 text-sm">
              <input type="checkbox" checked={compare} onChange={(e) => setCompare(e.target.checked)} className="h-4 w-4 accent-[var(--accent)]" />
              Also run without the constraint
            </label>
            <div className="flex gap-2">
              <Button variant="primary" onClick={start} disabled={running || !!schemaError}>
                {running ? "Generating…" : "Generate"}
              </Button>
              {running && <Button onClick={() => abort.current?.abort()}>Stop</Button>}
            </div>
          </div>
        </Card>
      </div>

      <div className="min-w-0 space-y-5">
        <ErrorBox error={error} />
        {!running && !constrained.length && !error && (
          <Empty>
            Press <b>Generate</b>. Each token is checked against the pushdown automaton: the model's 100 best candidates first, the whole vocabulary only if none of those fits.
          </Empty>
        )}
        {(running || constrained.length > 0) && (
          <div className={compare ? "grid gap-4 xl:grid-cols-2" : ""}>
            {compare && (
              <Card title="Unconstrained" actions={done.unconstrained && <Badge tone={done.unconstrained.accepted ? "good" : "bad"}>{done.unconstrained.accepted ? "✓ valid" : "✗ not valid"}</Badge>}>
                <TokenStream tokens={tokens.unconstrained} selected={null} onSelect={() => undefined} empty={running ? "…" : "—"} />
              </Card>
            )}
            <Card title="Constrained (PDA)" actions={final && <Badge tone={final.accepted ? "good" : "warn"}>{final.accepted ? "✓ valid" : "prefix only"}</Badge>}>
              <TokenStream tokens={constrained} selected={followed} onSelect={setSelected} empty={running ? "…" : "—"} />
              {running && !constrained.length && <Spinner label="Loading the model…" />}
            </Card>
          </div>
        )}
        {constrained.length > 0 && (
          <>
            <SummaryStats summary={final?.summary} tokens={constrained.length} />
            <div className="grid gap-4 md:grid-cols-2">
              <Card title="Stack" subtitle={detail ? `After token ${detail.index + 1}: ${JSON.stringify(detail.text)}` : "Click a token to see the stack at that point"}>
                <StackView stack={detail?.stack ?? []} previous={previous} />
              </Card>
              <div className="space-y-4">
                <Card title="Nesting depth" subtitle="Stack height after each token">
                  <DepthChart depths={constrained.map((t) => t.stack?.length ?? 0)} selected={followed} onSelect={setSelected} />
                </Card>
                <Card title="Step details">
                  <TokenDetail token={detail} />
                </Card>
              </div>
            </div>
          </>
        )}
        {pretty && (
          <Card title="Parsed output" subtitle="Valid JSON, checked against the grammar">
            <CodeBlock>{pretty}</CodeBlock>
          </Card>
        )}
        {final?.certificate && (
          <Card title="Certificate" subtitle="The stack after every character, replayable without the model">
            <CertificatePanel certificate={final.certificate} />
          </Card>
        )}
      </div>
    </div>
  );
}
