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
"""

from __future__ import annotations

from collections.abc import Iterator
from functools import lru_cache
from typing import NamedTuple

import torch

from grammarguard.core.dfa import DFA
from grammarguard.decoding.token_index import TokenIndex, load_or_build_index


class Step(NamedTuple):
    """One generated token: its text, the DFA state AFTER it (None if unconstrained),
    and how many tokens were allowed when it was chosen."""

    token_text: str
    dfa_state: int | None
    num_allowed: int


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


def _generate(model, tokenizer, prompt, max_tokens, top_k, temperature, seed, choose_mask) -> Iterator[Step]:
    """Shared loop with the KV cache; `choose_mask(logits)` hooks in the constraint.

    `choose_mask` returns (masked_logits, num_allowed), or None to signal a
    dead end. The generator's return value is the stop reason.
    """
    rng = torch.Generator().manual_seed(seed)
    ids = _prompt_ids(tokenizer, prompt)
    past = None
    with torch.no_grad():
        for _ in range(max_tokens):
            out = model(input_ids=ids, past_key_values=past, use_cache=True)
            past = out.past_key_values
            logits = out.logits[0, -1].clone()
            masked = choose_mask(logits)
            if masked is None:
                return "dead_end"
            logits, n_allowed = masked
            tid = _pick(logits, top_k, temperature, rng)
            if tid == tokenizer.eos_token_id:
                return "eos"
            text = tokenizer.decode([tid])
            state = choose_mask.advance(tid) if hasattr(choose_mask, "advance") else None
            yield Step(text, state, n_allowed)
            ids = torch.tensor([[tid]])
    return "max_tokens"


class DFAMask:
    """The constraint: holds the current DFA state and masks logits accordingly."""

    def __init__(self, dfa: DFA, index: TokenIndex, eos_id: int, vocab_size: int):
        """Start in q0. Pre-builds one tensor of allowed ids per state lazily."""
        self.dfa, self.index, self.eos_id, self.vocab_size = dfa, index, eos_id, vocab_size
        self.state = dfa.start
        self._ids: dict[int, torch.Tensor] = {}

    def __call__(self, logits: torch.Tensor):
        """Mask logits for the current state, or None on a dead end."""
        q = self.state
        if q not in self._ids:
            self._ids[q] = torch.tensor(self.index.allowed.get(q, []), dtype=torch.long)
        allowed = self._ids[q]
        accepting = q in self.dfa.accept
        if len(allowed) == 0 and not accepting:
            return None
        masked = torch.full_like(logits, float("-inf"))
        masked[allowed] = logits[allowed]
        if accepting:  # EOS is legal exactly in accepting states
            masked[self.eos_id] = logits[self.eos_id]
        return masked, len(allowed) + (1 if accepting else 0)

    def advance(self, tid: int) -> int:
        """q <- next[q][token]."""
        self.state = self.index.next_state[self.state][tid]
        return self.state


def generate_constrained(
    dfa: DFA,
    prompt: str,
    *,
    model_name: str = "gpt2",
    max_tokens: int = 40,
    top_k: int | None = None,
    temperature: float = 1.0,
    seed: int = 0,
    index: TokenIndex | None = None,
) -> Iterator[Step]:
    """Generate text accepted by `dfa`, yielding (token_text, dfa_state, num_allowed).

    Returns (via StopIteration.value) the stop reason: eos | dead_end | max_tokens.
    """
    tokenizer, model = load_model(model_name)
    index = index or load_or_build_index(dfa, tokenizer, model_name)
    mask = DFAMask(dfa, index, tokenizer.eos_token_id, model.config.vocab_size)
    return (yield from _generate(model, tokenizer, prompt, max_tokens, top_k, temperature, seed, mask))


def generate_unconstrained(
    prompt: str,
    *,
    model_name: str = "gpt2",
    max_tokens: int = 40,
    top_k: int | None = None,
    temperature: float = 1.0,
    seed: int = 0,
) -> Iterator[Step]:
    """Plain decoding with no constraint, for comparison (dfa_state is None)."""
    tokenizer, model = load_model(model_name)

    def no_mask(logits):
        """Every token is allowed."""
        return logits, logits.numel()

    return (yield from _generate(model, tokenizer, prompt, max_tokens, top_k, temperature, seed, no_mask))


def collect(gen: Iterator[Step]) -> tuple[str, list[Step], str]:
    """Drain a generator: returns (full text, steps, stop reason)."""
    steps: list[Step] = []
    while True:
        try:
            steps.append(next(gen))
        except StopIteration as stop:
            return "".join(s.token_text for s in steps), steps, stop.value
