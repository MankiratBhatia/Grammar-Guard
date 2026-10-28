import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../api/client";
import * as client from "../api/client";
import * as sse from "../api/sse";
import { StackView } from "../components/StackView";

// Graphviz-as-WebAssembly is not under test here: replace the drawing with a marker.
vi.mock("../components/Graph", () => ({
  Graph: ({ dot, highlight, label }: { dot: string | null; highlight?: number | null; label: string }) => (
    <div data-testid="graph" data-highlight={highlight ?? ""} data-has-dot={dot ? "yes" : "no"}>
      {label}
    </div>
  ),
  withHighlight: (d: string) => d,
}));

vi.mock("../api/client", async (orig) => {
  const actual = await orig<typeof import("../api/client")>();
  const fn = () => vi.fn();
  return {
    ...actual,
    api: {
      health: fn(), builtins: fn(), compile: fn(), membership: fn(), verify: fn(), algebra: fn(), equivalence: fn(),
      count: fn(), diff: fn(), testgen: fn(), learnSubset: fn(), learnMinimize: fn(), batch: fn(),
    },
  };
});
vi.mock("../api/sse", async (orig) => ({ ...(await orig<typeof import("../api/sse")>()), streamEvents: vi.fn() }));

const api = client.api as unknown as Record<string, ReturnType<typeof vi.fn>>;
const streamEvents = sse.streamEvents as unknown as ReturnType<typeof vi.fn>;

const view = (states: number, extra: object = {}) => ({
  states, live_states: states - 1, start: 0, accepting: [states - 1], dot: "digraph{}", transitions: [{ source: 0, target: 1, label: "a" }],
  state_labels: {}, ...extra,
});

beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
  window.location.hash = "";
  api.health.mockResolvedValue({ status: "ok", version: "t", engine: { model: "gpt2", loaded: true, busy: false } });
  api.builtins.mockResolvedValue({ builtins: { digits: "[0-9]+", email: "x" } });
});

const tel = (pressure: number, legal = true) => ({
  mass_kept: 1 - pressure, pressure, cost_bits: 1.5, preferred: "x", preferred_legal: legal, p_chosen: 0.2, entropy_before: 3, entropy_after: 2,
});

describe("Generate page", () => {
  function script(events: [string, object][]) {
    streamEvents.mockImplementation(async (_url: string, _body: unknown, onEvent: (e: { event: string; data: unknown }) => void) => {
      for (const [event, data] of events) onEvent({ event, data });
    });
  }

  it("streams tokens, shows the match verdict and cost, and verifies the certificate", async () => {
    api.algebra.mockResolvedValue({ dfa: view(5) });
    api.verify.mockResolvedValue({ valid: true, accepted: true, reason: "all 4 transitions check out" });
    script([
      ["meta", { kind: "dfa", constraint: "/x/", states: 5, live_states: 4, compare: true }],
      ["token", { source: "unconstrained", index: 0, text: "Hello", state: null, allowed: 50257, telemetry: null }],
      ["token", { source: "constrained", index: 0, text: "2019", state: 2, allowed: 981, telemetry: tel(0.9, false) }],
      ["token", { source: "constrained", index: 1, text: "-", state: 3, allowed: 1, telemetry: tel(0.1) }],
      ["done", { source: "unconstrained", stop_reason: "max_tokens", text: "Hello", accepted: false, tokens: 1 }],
      ["done", { source: "constrained", stop_reason: "eos", text: "2019-", accepted: true, tokens: 2, certificate: { kind: "dfa" },
        summary: { total_cost_bits: 3, mean_pressure: 0.5, overrides: 1 } }],
    ]);
    const { default: Generate } = await import("../pages/Generate");
    render(<Generate />);
    await userEvent.click(screen.getByRole("button", { name: "Generate" }));

    expect(await screen.findByText("2019")).toBeInTheDocument();
    expect(screen.getByText("Hello")).toBeInTheDocument();
    expect(screen.getByText("✓ matches")).toBeInTheDocument();
    expect(screen.getByText("✗ no match")).toBeInTheDocument();
    expect(screen.getByText("3.00 bits")).toBeInTheDocument();
    expect(screen.getByText("50.0%")).toBeInTheDocument();
    // body sent to the server
    expect(streamEvents.mock.calls[0][1]).toMatchObject({ compare: true, max_tokens: 40, top_k: null, certificate: true });
    // clicking a token shows its details and highlights its DFA state
    await userEvent.click(screen.getByRole("button", { name: "2019" }));
    expect(screen.getByText("blocked")).toBeInTheDocument();
    expect(screen.getAllByTestId("graph").some((g) => g.getAttribute("data-highlight") === "2")).toBe(true);
    // certificate
    await userEvent.click(screen.getByRole("button", { name: "Verify certificate" }));
    expect(await screen.findByText("certificate valid")).toBeInTheDocument();
    expect(screen.getByText("text is in the language")).toBeInTheDocument();
    expect(api.verify).toHaveBeenCalledWith({ kind: "dfa" });
  });

  it("shows server-side errors from the stream and from the request", async () => {
    api.algebra.mockResolvedValue({ dfa: view(2) });
    streamEvents.mockRejectedValue(new ApiError("missing ')' for '(' at pos 3", "regex_syntax", 422, 3));
    const { default: Generate } = await import("../pages/Generate");
    render(<Generate />);
    await userEvent.click(screen.getByRole("button", { name: "Generate" }));
    const alert = await screen.findByRole("alert");
    expect(within(alert).getByText("regex_syntax")).toBeInTheDocument();
    expect(alert).toHaveTextContent("missing ')'");
    expect(screen.getByRole("button", { name: "Generate" })).toBeEnabled(); // usable again
  });

  it("reports an error event sent mid-stream", async () => {
    api.algebra.mockResolvedValue({ dfa: view(2) });
    script([["error", { code: "internal", message: "boom" }]]);
    const { default: Generate } = await import("../pages/Generate");
    render(<Generate />);
    await userEvent.click(screen.getByRole("button", { name: "Generate" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("boom");
  });

  it("does not allow generating with an empty constraint", async () => {
    const { default: Generate } = await import("../pages/Generate");
    render(<Generate />);
    await userEvent.clear(screen.getByLabelText("Constraint"));
    expect(screen.getByRole("button", { name: "Generate" })).toBeDisabled();
  });
});

describe("Studio page", () => {
  const compiled = { regex: "(a|b)*abb", ast: "(a|b)*abb", alphabet: ["a", "b"], nfa: view(14), dfa: view(6), min_dfa: view(5), elapsed_ms: 1 };

  it("shows the pipeline sizes and lets you switch automaton", async () => {
    api.compile.mockResolvedValue(compiled);
    api.algebra.mockResolvedValue({ dfa: view(5) });
    api.membership.mockResolvedValue({
      accepted: true, start: 0, final_state: 4, doomed_at: null, viable_prefix: true, certificate: { kind: "dfa" },
      path: [{ index: 0, char: "a", source: 0, target: 1, live: true }, { index: 1, char: "b", source: 1, target: 4, live: true }],
    });
    const { default: Studio } = await import("../pages/Studio");
    render(<Studio />);
    expect(await screen.findByText("14")).toBeInTheDocument();
    expect(screen.getByText("6")).toBeInTheDocument();
    expect(screen.getByText("5")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("radio", { name: "ε-NFA" }));
    expect(screen.getAllByTestId("graph")[0]).toHaveTextContent("nfa automaton");
    expect(await screen.findByText("✓ accepted")).toBeInTheDocument();
    // scrubbing the path moves the highlight on the minimal DFA
    fireInput(screen.getByLabelText("Step through the run"), 1);
    await waitFor(() => expect(screen.getAllByTestId("graph").some((g) => g.getAttribute("data-highlight") === "1")).toBe(true));
  });

  it("points at the error position in the regex", async () => {
    api.compile.mockRejectedValue(new ApiError("missing ')'", "regex_syntax", 422, 2));
    api.membership.mockResolvedValue(null);
    api.algebra.mockResolvedValue(null);
    const { default: Studio } = await import("../pages/Studio");
    render(<Studio />);
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("regex_syntax");
    expect(alert.textContent).toMatch(/\(a\|b\)\*abb\s+\s{2}\^/);
  });
});

function fireInput(el: HTMLElement, value: number) {
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!;
  setter.call(el, String(value));
  el.dispatchEvent(new Event("input", { bubbles: true }));
}

describe("Validator Lab", () => {
  it("explains how a validator change alters behaviour, with witnesses", async () => {
    api.diff.mockResolvedValue({
      relation: "new_looser", summary: "The new validator accepts more strings.", only_old: [], only_new: ["000_00", "001_00"], both: ["000-00"],
      states_old: 9, states_new: 9,
    });
    const { default: Lab } = await import("../pages/ValidatorLab");
    render(<Lab />);
    await userEvent.click(screen.getByRole("button", { name: "Compare validators" }));
    expect(await screen.findByText("New validator is looser")).toBeInTheDocument();
    expect(screen.getByText("000_00")).toBeInTheDocument();
    expect(screen.getByText("newly accepted")).toBeInTheDocument();
    expect(screen.getByText("No such strings.")).toBeInTheDocument(); // nothing is only-old
  });

  it("generates a suite, filters by kind and reports coverage", async () => {
    api.testgen.mockResolvedValue({
      cases: [
        { text: "123-45", expected: true, kind: "transition", reason: "covers q0" },
        { text: "123_45", expected: false, kind: "substitution", reason: "position 3" },
        { text: "123-4", expected: false, kind: "truncation", reason: "stops early" },
      ],
      counts: { transition: 1, substitution: 1, truncation: 1 }, coverage: { transition_coverage: 1, transitions_covered: 7, transitions_total: 7 }, truncated: false,
    });
    const { default: Lab } = await import("../pages/ValidatorLab");
    render(<Lab />);
    await userEvent.click(screen.getByRole("button", { name: "Generate test suite" }));
    expect(await screen.findByText("100%")).toBeInTheDocument();
    expect(screen.getByText("123_45")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "truncation (1)" }));
    expect(screen.queryByText("123_45")).not.toBeInTheDocument();
    expect(screen.getByText("123-4")).toBeInTheDocument();
  });
});

describe("Learn page", () => {
  it("steps through subset construction", async () => {
    api.learnSubset.mockResolvedValue({
      regex: "a", nfa: view(4), dfa: view(2),
      steps: [
        { from: [0, 1], symbol: "a", move: [2], closure: [2, 3], new: true },
        { from: [2, 3], symbol: "other", move: [], closure: [], new: true },
      ],
    });
    api.learnMinimize.mockResolvedValue(null);
    const { default: Learn } = await import("../pages/Learn");
    render(<Learn />);
    expect(await screen.findByText(/From the DFA state/)).toHaveTextContent("{0, 1}");
    expect(screen.getByText("1 / 2")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Next step" }));
    expect(screen.getByText("2 / 2")).toBeInTheDocument();
    expect(screen.getByText("dead state")).toBeInTheDocument();
    expect(screen.getByText(/any other character/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Next step" })).toBeDisabled();
  });

  it("fills the distinguishability table round by round", async () => {
    api.learnSubset.mockResolvedValue(null);
    api.learnMinimize.mockResolvedValue({
      regex: "x", dfa: view(3, { accepting: [2] }), min_dfa: view(2), classes: [[0, 1], [2]],
      rounds: [
        { round: 0, marked: [{ pair: [0, 2], reason: "ε: one accepting, one not" }, { pair: [1, 2], reason: "ε: one accepting, one not" }] },
      ],
    });
    const { default: Learn } = await import("../pages/Learn");
    render(<Learn />);
    await userEvent.click(await screen.findByRole("radio", { name: "Table filling" }));
    const table = await screen.findByRole("table", { name: "Distinguishability table" });
    expect(within(table).getAllByText("R0")).toHaveLength(2);
    expect(within(table).queryByText("≡")).not.toBeInTheDocument(); // still marking
    await userEvent.click(screen.getByRole("button", { name: "Last step" }));
    expect(within(table).getByText("≡")).toBeInTheDocument(); // q0 and q1 were never separated
    expect(screen.getByText(/3 states merge into 2/)).toBeInTheDocument();
  });
});

describe("JSON page", () => {
  it("refuses an invalid schema before calling the server", async () => {
    const { default: Json } = await import("../pages/JsonSchema");
    render(<Json />);
    const box = screen.getByLabelText(/^Schema/);
    await userEvent.clear(box);
    await userEvent.type(box, "{{ not json");
    expect(await screen.findByText(/Schema is not valid JSON/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Generate" })).toBeDisabled();
  });

  it("streams PDA steps and shows the stack at the selected token", async () => {
    streamEvents.mockImplementation(async (_u: string, _b: unknown, onEvent: (e: { event: string; data: unknown }) => void) => {
      onEvent({ event: "token", data: { source: "constrained", index: 0, text: "{", stack: ["Obj"], valid: 5, checked: 100, fallback: false, mass_kept: 0.9, preferred_legal: true, p_chosen: 0.3 } });
      onEvent({ event: "token", data: { source: "constrained", index: 1, text: '"a"', stack: [":", "Value", "Pairs", "}"], valid: 3, checked: 100, fallback: true, mass_kept: 0.5, preferred_legal: false, p_chosen: 0.1 } });
      onEvent({ event: "done", data: { source: "constrained", stop_reason: "max_tokens", text: '{"a"', accepted: false, tokens: 2 } });
    });
    const { default: Json } = await import("../pages/JsonSchema");
    render(<Json />);
    await userEvent.click(screen.getByRole("button", { name: "Generate" }));
    await userEvent.click(await screen.findByRole("button", { name: '"a"' }));
    const stack = screen.getByRole("list", { name: "PDA stack, top first" });
    expect(within(stack).getAllByRole("listitem").map((li) => li.textContent)).toEqual([":match", "Valueexpand", "Pairsexpand", "}match"]);
    expect(screen.getByText("full-vocabulary fallback")).toBeInTheDocument();
    expect(screen.getByText("prefix only")).toBeInTheDocument();
    const body = streamEvents.mock.calls[0][1] as { schema: unknown };
    expect(body.schema).toMatchObject({ type: "object" });
  });
});

describe("StackView", () => {
  it("tells nonterminals from terminals and says when the stack is empty", () => {
    const { rerender } = render(<StackView stack={["Value", "[a-z]", "E", "V3"]} />);
    const items = screen.getAllByRole("listitem").map((li) => li.textContent);
    expect(items).toEqual(["Valueexpand", "[a-z]match", "Ematch", "V3expand"]);
    rerender(<StackView stack={[]} />);
    expect(screen.getByText(/Stack empty/)).toBeInTheDocument();
  });
});

describe("App shell", () => {
  it("navigates between pages by hash and shows the model status", async () => {
    api.compile.mockResolvedValue(null);
    api.algebra.mockResolvedValue(null);
    api.membership.mockResolvedValue(null);
    const { default: App } = await import("../App");
    render(<App />);
    expect(await screen.findByRole("heading", { level: 1, name: "Studio" })).toBeInTheDocument();
    expect(await screen.findByText("gpt2 ready")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("link", { name: /Validator Lab/ }));
    expect(await screen.findByRole("heading", { level: 1, name: "Validator Lab" })).toBeInTheDocument();
    expect(window.location.hash).toBe("#/validators");
    expect(screen.getByRole("link", { name: /Validator Lab/ })).toHaveAttribute("aria-current", "page");
  });

  it("says when the server is offline", async () => {
    api.health.mockRejectedValue(new ApiError("down", "network", 0));
    const { default: App } = await import("../App");
    render(<App />);
    expect(await screen.findByText("Server offline")).toBeInTheDocument();
  });
});
