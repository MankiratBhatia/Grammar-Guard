"""Pydantic models for every request and response (these generate the OpenAPI docs).

Counts of strings can exceed 2^53, which JavaScript cannot represent exactly, so
every count of strings is sent as a decimal STRING.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class Constraint(BaseModel):
    """What the text must look like: a regex, or an expression in the constraint algebra."""

    kind: Literal["regex", "expression"] = Field(
        "regex",
        description="'regex' = one regular expression; "
        "'expression' = constraint algebra, e.g. email & !contains(/spam/)",
    )
    text: str = Field(..., examples=["[0-9]{4}-[0-9]{2}-[0-9]{2}"])
    names: dict[str, str] = Field(default_factory=dict, description="extra named constraints (name -> regex)")


# ------------------------------------------------------------------ automata views


class Transition(BaseModel):
    """One drawn edge: all symbols between the same two states are merged into one label."""

    source: int
    target: int
    label: str


class AutomatonView(BaseModel):
    """A DFA or NFA ready to display."""

    states: int
    live_states: int | None = None
    start: int
    accepting: list[int]
    dot: str | None = Field(None, description="Graphviz DOT; null when the automaton is too large to draw")
    transitions: list[Transition]
    state_labels: dict[int, str] = Field(default_factory=dict, description="NFA state sets / merged states")


class CompileRequest(BaseModel):
    """Regex to compile."""

    regex: str = Field(..., examples=["(a|b)*abb"])


class CompileResponse(BaseModel):
    """The whole pipeline: AST, ε-NFA, DFA, minimal DFA."""

    regex: str
    ast: str
    alphabet: list[str]
    nfa: AutomatonView
    dfa: AutomatonView
    min_dfa: AutomatonView
    elapsed_ms: float


class MembershipRequest(BaseModel):
    """Run a string through a constraint's DFA."""

    constraint: Constraint
    text: str


class PathStep(BaseModel):
    """δ(from, char) = to."""

    index: int
    char: str
    source: int
    target: int
    live: bool


class MembershipResponse(BaseModel):
    """Verdict plus the state path (this is the certificate's content)."""

    accepted: bool
    path: list[PathStep]
    start: int
    final_state: int
    doomed_at: int | None = Field(None, description="index of the first character after which no completion exists")
    viable_prefix: bool = Field(..., description="could the text still be extended into a match?")
    certificate: dict[str, Any]


# ------------------------------------------------------------------ algebra


class AlgebraRequest(BaseModel):
    """Evaluate a constraint expression."""

    expression: str = Field(..., examples=["email & !contains(/competitor/) & length(5, 40)"])
    names: dict[str, str] = Field(default_factory=dict)
    examples: int = Field(8, ge=0, le=50)


class AlgebraStep(BaseModel):
    """One closure construction used while evaluating."""

    expr: str
    operation: str
    states: int


class AlgebraResponse(BaseModel):
    """Minimal DFA of the expression and what can be said about its language."""

    expression: str
    dfa: AutomatonView
    steps: list[AlgebraStep]
    finite: bool
    size: str | None = Field(None, description="number of strings if the language is finite (over its own alphabet)")
    regex: str | None = Field(None, description="an equivalent regex (Kleene's theorem), when one can be written")
    regex_note: str | None = None
    examples: list[str]
    counts: list[str] = Field(..., description="number of accepted strings of length 0..12")


class EquivalenceRequest(BaseModel):
    """Do two constraints define the same language?"""

    a: Constraint
    b: Constraint


class EquivalenceResponse(BaseModel):
    """Equivalent, or a shortest counterexample with who accepts it."""

    equivalent: bool
    counterexample: str | None = None
    accepted_by_a: bool | None = None
    accepted_by_b: bool | None = None
    states_a: int
    states_b: int


class CountRequest(BaseModel):
    """How many strings, and uniform samples."""

    constraint: Constraint
    max_length: int = Field(12, ge=0)
    sample_length: int | None = Field(None, ge=0)
    samples: int = Field(5, ge=0, le=100)
    seed: int = 0


class CountResponse(BaseModel):
    """Counts per length (dynamic programming over the DFA) and exact uniform samples."""

    counts: list[str]
    finite: bool
    size: str | None
    sample_length: int | None
    samples: list[str]
    shortest: list[str]


class BuiltinsResponse(BaseModel):
    """Names usable in expressions."""

    builtins: dict[str, str]


# ------------------------------------------------------------------ validators


class DiffRequest(BaseModel):
    """Compare an old validator with a new one."""

    old: Constraint
    new: Constraint
    examples: int = Field(5, ge=1, le=50)


class DiffResponse(BaseModel):
    """How the language changed, with witnesses."""

    relation: Literal["equal", "new_stricter", "new_looser", "overlap", "disjoint"]
    summary: str
    only_old: list[str] = Field(..., description="accepted by the OLD validator but rejected by the NEW one")
    only_new: list[str] = Field(..., description="accepted by the NEW validator but rejected by the OLD one")
    both: list[str]
    states_old: int
    states_new: int


class TestgenRequest(BaseModel):
    """Generate a test suite for a validator."""

    constraint: Constraint
    format: Literal["json", "csv", "pytest"] = "json"
    max_cases: int = Field(500, ge=1)


class TestCaseOut(BaseModel):
    """One generated case."""

    text: str
    expected: bool
    kind: str
    reason: str


class TestgenResponse(BaseModel):
    """The suite with its coverage."""

    cases: list[TestCaseOut]
    counts: dict[str, int]
    coverage: dict[str, float | int]
    truncated: bool
    csv: str | None = None
    pytest: str | None = None
    pattern: str | None = Field(None, description="the regex used for the pytest export")


# ------------------------------------------------------------------ certificates


class CertificateVerifyRequest(BaseModel):
    """Certificate to check (as returned by generate / membership)."""

    certificate: dict[str, Any]


class CertificateVerifyResponse(BaseModel):
    """Truthful? In the language?"""

    valid: bool
    accepted: bool
    reason: str
    automaton_states: int | None = None


# ------------------------------------------------------------------ learn


class SubsetStep(BaseModel):
    """One (subset, symbol) step of subset construction."""

    source: list[int] = Field(..., alias="from")
    symbol: str
    move: list[int]
    closure: list[int]
    new: bool

    model_config = {"populate_by_name": True}


class LearnSubsetResponse(BaseModel):
    """Everything the step player needs for subset construction."""

    regex: str
    nfa: AutomatonView
    steps: list[dict[str, Any]]
    dfa: AutomatonView


class LearnMinimizeResponse(BaseModel):
    """Everything the step player needs for table filling."""

    regex: str
    dfa: AutomatonView
    rounds: list[dict[str, Any]]
    classes: list[list[int]]
    min_dfa: AutomatonView


# ------------------------------------------------------------------ generation


class GenerateOptions(BaseModel):
    """Sampling options shared by all generation endpoints."""

    prompt: str = ""
    max_tokens: int = Field(40, ge=1, le=1000)
    top_k: int | None = Field(None, ge=1, le=200, description="null/1 = greedy")
    temperature: float = Field(1.0, gt=0, le=5)
    seed: int = 0
    compare: bool = Field(False, description="also stream an unconstrained run first, for comparison")
    certificate: bool = True


class GenerateRequest(GenerateOptions):
    """Constrained generation with a regex / expression."""

    constraint: Constraint


class JsonGenerateRequest(GenerateOptions):
    """Constrained generation with the JSON PDA or a schema."""

    schema_: dict[str, Any] | None = Field(
        None, alias="schema", description="mini JSON schema; null = any JSON object/array"
    )
    top_n: int = Field(100, ge=1, le=2000)
    max_tokens: int = Field(60, ge=1, le=1000)

    model_config = {"populate_by_name": True}


class BatchRequest(BaseModel):
    """Several distinct valid samples (synthetic test data)."""

    constraint: Constraint
    prompt: str = ""
    count: int = Field(5, ge=1)
    max_tokens: int = Field(40, ge=1, le=1000)
    top_k: int = Field(20, ge=2, le=200)
    temperature: float = Field(1.0, gt=0, le=5)
    seed: int = 0
    certificates: bool = False


class BatchSample(BaseModel):
    """One valid sample."""

    text: str
    seed: int
    cost_bits: float
    certificate: dict[str, Any] | None = None


class BatchResponse(BaseModel):
    """Distinct samples that ended in an accepting state."""

    samples: list[BatchSample]
    requested: int
    attempts: int
    note: str | None = None


# ------------------------------------------------------------------ system


class HealthResponse(BaseModel):
    """Liveness and model state."""

    status: Literal["ok"]
    version: str
    engine: dict[str, Any]
