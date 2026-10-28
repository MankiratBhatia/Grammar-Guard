"""Manual generation loops: constrained by a DFA, and unconstrained.

Each step of the constrained loop:
    1. run the model to get logits over the whole vocabulary,
    2. set logits of every token NOT in allowed[q] to -inf (masking),
    3. allow the end-of-text token only if q is an accepting state,
    4. pick a token (greedy or top-k sampling) from what is left,
    5. advance the DFA: q <- next[q][token].
Stop on EOS, on a dead end (no legal token and q not accepting) or at max_tokens.

Because every allowed token keeps the DFA in a live state, the output is always
a prefix of some string in L(M); if generation ends with EOS (only possible in
an accepting state) the output is exactly a string of L(M).

Telemetry ("constraint pressure"). With p the model's own next-token distribution
and A the set of tokens the constraint allows, each step records
    mass_kept = p(A)                      how much of the model's belief survived
    pressure  = 1 - mass_kept             how hard the constraint pushed
    cost_bits = -log2 p(A)                = KL(q || p) in bits, where q is p restricted to A
                                          and renormalised: the information the constraint adds
    preferred_legal                       was the model's own top choice allowed?
A run with pressure near 0 means the model already wanted to produce a match.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterator
from dataclasses import asdict, dataclass
from functools import lru_cache
from typing import NamedTuple

import torch

from grammarguard.core.dfa import DFA
from grammarguard.decoding.token_index import LazyTokenIndex, TokenIndex, load_or_build_index


@dataclass(frozen=True)
class Telemetry:
    """What the constraint did at one step (see the module docstring)."""

    mass_kept: float
    pressure: float
    cost_bits: float
    preferred: str  # the model's own top token, as text
    preferred_legal: bool
    p_chosen: float  # raw-model probability of the token that was chosen
    entropy_before: float  # bits
    entropy_after: float  # bits, of the renormalised allowed distribution

    def to_dict(self) -> dict:
        """Plain dict for JSON."""
        return asdict(self)


class Step(NamedTuple):
    """One generated token: its text, the DFA state AFTER it (None if unconstrained),
    how many tokens were allowed when it was chosen, and the telemetry (None if unconstrained)."""

    token_text: str
    dfa_state: int | None
    num_allowed: int
    telemetry: Telemetry | None = None


def summarize(steps: list[Step]) -> dict[str, float | int]:
    """Aggregate the telemetry of a run: mean/max pressure, total cost in bits, overrides.

    `overrides` counts steps where the model's own first choice was illegal and
    the constraint had to pick something else.
    """
    ts = [s.telemetry for s in steps if s.telemetry is not None]
    if not ts:
        return {"steps": len(steps), "constrained_steps": 0}
    n = len(ts)
    overrides = sum(1 for t in ts if not t.preferred_legal)
    return {
        "steps": len(steps),
        "constrained_steps": n,
        "mean_pressure": sum(t.pressure for t in ts) / n,
        "max_pressure": max(t.pressure for t in ts),
        "total_cost_bits": sum(t.cost_bits for t in ts),
        "mean_cost_bits": sum(t.cost_bits for t in ts) / n,
        "overrides": overrides,
        "override_rate": overrides / n,
        "mean_entropy_before": sum(t.entropy_before for t in ts) / n,
        "mean_entropy_after": sum(t.entropy_after for t in ts) / n,
    }


@lru_cache(maxsize=2)
def load_model(name: str = "gpt2"):
    """Load tokenizer and model on CPU (cached across calls)."""
    from transformers import AutoModelForCausalLM, AutoTokenizer

    tokenizer = AutoTokenizer.from_pretrained(name)
    model = AutoModelForCausalLM.from_pretrained(name).eval()
    return tokenizer, model


def _prompt_ids(tokenizer, prompt: str) -> torch.Tensor:
    """Encode the prompt; an empty prompt becomes the EOS token (GPT-2's start-of-text)."""
    ids = tokenizer.encode(prompt) if prompt else [tokenizer.eos_token_id]
    return torch.tensor([ids])


def _pick(logits: torch.Tensor, top_k: int | None, temperature: float, rng: torch.Generator) -> int:
    """Choose a token id from (already masked) logits.

    Greedy (top_k None or 1) = argmax; otherwise sample from the top-k
    renormalised softmax. -inf logits get probability 0 so masked tokens are
    never chosen.
    """
    if top_k is None or top_k <= 1:
        return int(torch.argmax(logits))
    k = min(top_k, int(torch.isfinite(logits).sum()))
    values, ids = torch.topk(logits, k)
    probs = torch.softmax(values / temperature, dim=-1)
    return int(ids[torch.multinomial(probs, 1, generator=rng)])


class NoConstraint:
    """The trivial constraint: every token is allowed, nothing is recorded."""

    def prepare(self, logits: torch.Tensor):
        """Pass the logits through unchanged."""
        return logits, logits.numel()

    def accept(self, tid: int):
        """No automaton state, no telemetry."""
        return None, None


def _generate(
    model, tokenizer, prompt, max_tokens, top_k, temperature, seed, constraint, should_stop=None
) -> Iterator[Step]:
    """Shared loop with the KV cache; `constraint` supplies masking and state.

    constraint.prepare(logits) -> (masked_logits, num_allowed) or None (dead end);
    constraint.accept(token_id) -> (new_state, telemetry).
    The generator's return value is the stop reason:
    eos | dead_end | max_tokens | cancelled.
    """
    rng = torch.Generator().manual_seed(seed)
    ids = _prompt_ids(tokenizer, prompt)
    past = None
    with torch.no_grad():
        for _ in range(max_tokens):
            if should_stop is not None and should_stop():
                return "cancelled"
            out = model(input_ids=ids, past_key_values=past, use_cache=True)
            past = out.past_key_values
            logits = out.logits[0, -1].clone()
            prepared = constraint.prepare(logits)
            if prepared is None:
                return "dead_end"
            masked, n_allowed = prepared
            tid = _pick(masked, top_k, temperature, rng)
            if tid == tokenizer.eos_token_id:
                return "eos"
            text = tokenizer.decode([tid])
            state, telemetry = constraint.accept(tid)
            yield Step(text, state, n_allowed, telemetry)
            ids = torch.tensor([[tid]])
    return "max_tokens"


class DFAMask:
    """The DFA constraint: holds the current state, masks logits, records telemetry."""

    def __init__(self, dfa: DFA, index: TokenIndex | LazyTokenIndex, tokenizer, strings=None):
        """Start in q0."""
        self.dfa, self.index, self.tokenizer = dfa, index, tokenizer
        self.eos_id = tokenizer.eos_token_id
        self.state = dfa.start
        self._ids: dict[int, torch.Tensor] = {}
        self._pending: tuple | None = None

    def prepare(self, logits: torch.Tensor):
        """Mask logits for the current state (None on a dead end); remember what telemetry needs."""
        q = self.state
        if q not in self._ids:
            self._ids[q] = torch.tensor(self.index.allowed_for(q), dtype=torch.long)
        allowed = self._ids[q]
        accepting = q in self.dfa.accept
        if len(allowed) == 0 and not accepting:
            return None
        if accepting:  # EOS is legal exactly in accepting states
            allowed = torch.cat([allowed, torch.tensor([self.eos_id])])
        masked = torch.full_like(logits, float("-inf"))
        masked[allowed] = logits[allowed]
        self._pending = (logits, allowed)
        return masked, len(allowed)

    def accept(self, tid: int):
        """q <- next[q][token]; build this step's telemetry."""
        assert self._pending is not None
        logits, allowed = self._pending
        telemetry = _telemetry(logits, allowed, tid, self.tokenizer)
        self.state = self.index.next_for(self.state, tid)
        return self.state, telemetry


def _telemetry(logits: torch.Tensor, allowed: torch.Tensor, chosen: int, tokenizer) -> Telemetry:
    """Compute the pressure numbers from raw logits and the allowed set (all in log space)."""
    ln2 = math.log(2)
    logp = torch.log_softmax(logits.double(), dim=-1)
    log_mass = torch.logsumexp(logp[allowed], dim=0)
    mass = float(torch.exp(log_mass))
    preferred = int(torch.argmax(logits))
    h_before = float(-(torch.exp(logp) * logp).sum()) / ln2
    cond = logp[allowed] - log_mass  # log of the renormalised distribution on A
    h_after = float(-(torch.exp(cond) * cond).sum()) / ln2
    return Telemetry(
        mass_kept=mass,
        pressure=1.0 - mass,
        cost_bits=float(-log_mass) / ln2,
        preferred=tokenizer.decode([preferred]),
        preferred_legal=bool((allowed == preferred).any()),
        p_chosen=float(torch.exp(logp[chosen])),
        entropy_before=h_before,
        entropy_after=h_after,
    )


def generate_constrained(
    dfa: DFA,
    prompt: str,
    *,
    model_name: str = "gpt2",
    max_tokens: int = 40,
    top_k: int | None = None,
    temperature: float = 1.0,
    seed: int = 0,
    index: TokenIndex | LazyTokenIndex | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> Iterator[Step]:
    """Generate text accepted by `dfa`, yielding Steps (token, state, #allowed, telemetry).

    Returns (via StopIteration.value) the stop reason: eos | dead_end | max_tokens | cancelled.
    """
    tokenizer, model = load_model(model_name)
    index = index or load_or_build_index(dfa, tokenizer, model_name)
    mask = DFAMask(dfa, index, tokenizer)
    return (yield from _generate(model, tokenizer, prompt, max_tokens, top_k, temperature, seed, mask, should_stop))


def generate_unconstrained(
    prompt: str,
    *,
    model_name: str = "gpt2",
    max_tokens: int = 40,
    top_k: int | None = None,
    temperature: float = 1.0,
    seed: int = 0,
    should_stop: Callable[[], bool] | None = None,
) -> Iterator[Step]:
    """Plain decoding with no constraint, for comparison (dfa_state is None)."""
    tokenizer, model = load_model(model_name)
    return (
        yield from _generate(
            model, tokenizer, prompt, max_tokens, top_k, temperature, seed, NoConstraint(), should_stop
        )
    )


def collect(gen: Iterator[Step]) -> tuple[str, list[Step], str]:
    """Drain a generator: returns (full text, steps, stop reason)."""
    steps: list[Step] = []
    while True:
        try:
            steps.append(next(gen))
        except StopIteration as stop:
            return "".join(s.token_text for s in steps), steps, stop.value
