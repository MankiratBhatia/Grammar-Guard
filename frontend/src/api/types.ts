import type { components } from "./schema";

type S = components["schemas"];
export type Constraint = S["Constraint"];
export type AutomatonView = S["AutomatonView"];
export type CompileResponse = S["CompileResponse"];
export type MembershipResponse = S["MembershipResponse"];
export type AlgebraResponse = S["AlgebraResponse"];
export type EquivalenceResponse = S["EquivalenceResponse"];
export type CountResponse = S["CountResponse"];
export type DiffResponse = S["DiffResponse"];
export type TestgenResponse = S["TestgenResponse"];
export type CertificateVerifyResponse = S["CertificateVerifyResponse"];
export type LearnSubsetResponse = S["LearnSubsetResponse"];
export type LearnMinimizeResponse = S["LearnMinimizeResponse"];
export type BatchResponse = S["BatchResponse"];
export type HealthResponse = S["HealthResponse"];

export type Certificate = Record<string, unknown>;

/** Per-token numbers describing how hard the constraint pushed against the model (see the backend docs). */
export interface Telemetry {
  mass_kept: number;
  pressure: number;
  cost_bits: number;
  preferred: string;
  preferred_legal: boolean;
  p_chosen: number;
  entropy_before: number;
  entropy_after: number;
}

export type Source = "constrained" | "unconstrained";

/** SSE `token` event for DFA-constrained (or unconstrained) generation. */
export interface DfaToken {
  source: Source;
  index: number;
  text: string;
  state: number | null;
  allowed: number;
  telemetry: Telemetry | null;
}

/** SSE `token` event for PDA-constrained generation. */
export interface PdaToken {
  source: Source;
  index: number;
  text: string;
  stack: string[];
  valid: number;
  checked: number;
  fallback: boolean;
  mass_kept: number;
  preferred_legal: boolean;
  p_chosen: number;
}

export type AnyToken = Partial<DfaToken> & Partial<PdaToken> & { source: Source; index: number; text: string };

export interface DoneEvent {
  source: Source;
  stop_reason: "eos" | "max_tokens" | "dead_end" | "cancelled" | "timeout";
  text: string;
  accepted: boolean;
  tokens: number;
  summary?: Record<string, number>;
  certificate?: Certificate;
}

export interface MetaEvent {
  kind: "dfa" | "pda";
  constraint: string;
  states?: number;
  live_states?: number;
  compare: boolean;
}

export interface ServerError {
  code: string;
  message: string;
  position?: number | null;
}

// ---- benchmark results (GET /benchmarks); the document is produced by `python -m grammarguard.eval` ----

export interface Rate {
  k: number;
  n: number;
  rate: number;
  lo: number;
  hi: number;
}

export interface ModeSummary {
  validity: Rate;
  prefix_validity?: Rate;
  mean_tokens: number | null;
  mean_seconds: number | null;
  mean_tokens_per_second: number | null;
  mean_attempts?: number | null;
  mean_pressure?: number | null;
  mean_cost_bits?: number | null;
  mean_overrides?: number | null;
}

export interface GroupSummary {
  unconstrained?: ModeSummary;
  retry5?: ModeSummary;
  constrained?: ModeSummary;
  slowdown?: number;
}

export interface BenchRun {
  case: string;
  prompt: number;
  sampling: string;
  seed: number;
  mode: "unconstrained" | "retry5" | "constrained";
  valid: boolean;
  valid_prefix: boolean;
  stop_reason: string;
  tokens: number;
  seconds: number;
  text: string;
  oracle_agrees: boolean | null;
  attempts?: number;
  total_cost_bits?: number | null;
}

export interface BenchCase {
  id: string;
  category: string;
  kind: string;
  text: string;
  dfa_states: number;
  live_states: number;
  index_build_ms: number;
}

export interface BenchResults {
  meta: {
    date: string;
    model: string;
    torch: string;
    python: string;
    platform: string;
    cpus: number | null;
    commit: string | null;
    max_tokens: number;
    retries: number;
    wall_seconds: number;
  };
  cases: BenchCase[];
  runs: BenchRun[];
  summary: {
    overall: GroupSummary;
    by_category: Record<string, GroupSummary>;
    by_case: Record<string, GroupSummary>;
    by_sampling: Record<string, GroupSummary>;
  };
}
