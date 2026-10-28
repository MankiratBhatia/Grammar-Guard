import type {
  AlgebraResponse,
  BatchResponse,
  Certificate,
  CertificateVerifyResponse,
  CompileResponse,
  Constraint,
  CountResponse,
  DiffResponse,
  EquivalenceResponse,
  HealthResponse,
  LearnMinimizeResponse,
  LearnSubsetResponse,
  MembershipResponse,
  TestgenResponse,
} from "./types";

const BASE = "/api/v1";

/** An error returned by the API (the backend always uses one envelope: code, message, position). */
export class ApiError extends Error {
  constructor(
    message: string,
    readonly code: string,
    readonly status: number,
    readonly position: number | null = null,
    readonly details: unknown = null,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function failure(res: Response): Promise<ApiError> {
  try {
    const body = await res.json();
    const e = body?.error;
    if (e) return new ApiError(e.message, e.code, res.status, e.position ?? null, e.details ?? null);
  } catch {
    /* not JSON */
  }
  return new ApiError(`${res.status} ${res.statusText}`.trim(), "http", res.status);
}

async function request<T>(method: "GET" | "POST", path: string, body?: unknown, signal?: AbortSignal): Promise<T> {
  let res: Response;
  try {
    res = await fetch(BASE + path, {
      method,
      headers: body === undefined ? undefined : { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal,
    });
  } catch (e) {
    if (e instanceof DOMException && e.name === "AbortError") throw e;
    throw new ApiError("Cannot reach the GrammarGuard server. Is it running?", "network", 0);
  }
  if (!res.ok) throw await failure(res);
  return (await res.json()) as T;
}

const post = <T>(path: string, body: unknown, signal?: AbortSignal) => request<T>("POST", path, body, signal);

export const api = {
  health: () => request<HealthResponse>("GET", "/system/health"),
  builtins: () => request<{ builtins: Record<string, string> }>("GET", "/builtins"),
  compile: (regex: string) => post<CompileResponse>("/compile", { regex }),
  membership: (constraint: Constraint, text: string) => post<MembershipResponse>("/membership", { constraint, text }),
  verify: (certificate: Certificate) => post<CertificateVerifyResponse>("/certificate/verify", { certificate }),
  algebra: (expression: string, names: Record<string, string> = {}, examples = 8) =>
    post<AlgebraResponse>("/algebra", { expression, names, examples }),
  equivalence: (a: Constraint, b: Constraint) => post<EquivalenceResponse>("/equivalence", { a, b }),
  count: (constraint: Constraint, max_length: number, sample_length: number | null, samples: number, seed: number) =>
    post<CountResponse>("/count", { constraint, max_length, sample_length, samples, seed }),
  diff: (oldC: Constraint, newC: Constraint, examples = 5) =>
    post<DiffResponse>("/validators/diff", { old: oldC, new: newC, examples }),
  testgen: (constraint: Constraint, format: "json" | "csv" | "pytest", max_cases = 500) =>
    post<TestgenResponse>("/validators/testgen", { constraint, format, max_cases }),
  learnSubset: (regex: string) =>
    request<LearnSubsetResponse>("GET", `/learn/subset?regex=${encodeURIComponent(regex)}`),
  learnMinimize: (regex: string) =>
    request<LearnMinimizeResponse>("GET", `/learn/minimize?regex=${encodeURIComponent(regex)}`),
  batch: (body: Record<string, unknown>, signal?: AbortSignal) => post<BatchResponse>("/generate/batch", body, signal),
};

export const streamPaths = { dfa: `${BASE}/generate`, pda: `${BASE}/generate/json` } as const;
