"""Constrained generation of JSON with a pushdown automaton.

A DFA state is one number, so a token index can be precomputed per state. A PDA
configuration includes the whole stack, which is unbounded, so there is no
finite table to precompute. Instead we check tokens at RUNTIME:

    1. take the model's top-N tokens (N = 100) by logit,
    2. keep those the PDA accepts from the current stack (EOS only if the
       stack may end here),
    3. if none survive, fall back to checking the ENTIRE vocabulary.

Greedy decoding gives exactly the same result as masking the full vocabulary:
any valid token inside the top-N outranks every token outside it, and the
fallback covers the case where there is none. With top-k sampling the
restriction to the top-N is an approximation (the tail is never sampled).
"""

from __future__ import annotations

from collections.abc import Iterator
from functools import lru_cache
from typing import NamedTuple

import torch

from grammarguard.core.pda import JSON_PDA, PDA, Stack, format_stack
from grammarguard.decoding.generate import _pick, _prompt_ids, load_model
from grammarguard.decoding.token_index import vocab_strings


class PDAStep(NamedTuple):
    """One generated token with the PDA stack AFTER it (top first), how many
    tokens were valid among those checked, how many were checked, and whether
    the full-vocabulary fallback was needed."""

    token_text: str
    stack: tuple[str, ...]
    num_valid: int
    num_checked: int
    fallback: bool


@lru_cache(maxsize=2)
def _vocab(model_name: str) -> list[str | None]:
    """Exact text of every token id (computed once per model)."""
    return vocab_strings(load_model(model_name)[0])


def generate_json(
    prompt: str,
    *,
    pda: PDA = JSON_PDA,
    model_name: str = "gpt2",
    max_tokens: int = 60,
    top_k: int | None = None,
    temperature: float = 1.0,
    seed: int = 0,
    top_n: int = 100,
) -> Iterator[PDAStep]:
    """Generate text accepted by `pda`. Returns the stop reason: eos | dead_end | max_tokens."""
    tokenizer, model = load_model(model_name)
    strings = _vocab(model_name)
    eos = tokenizer.eos_token_id
    rng = torch.Generator().manual_seed(seed)
    ids, past, stack = _prompt_ids(tokenizer, prompt), None, pda.initial()
    with torch.no_grad():
        for _ in range(max_tokens):
            out = model(input_ids=ids, past_key_values=past, use_cache=True)
            past, logits = out.past_key_values, out.logits[0, -1]
            can_end = pda.accepts_end(stack)

            def valid(t: int, stack: Stack = stack, can_end: bool = can_end) -> bool:
                """Is token t a legal continuation from the current stack?"""
                if t == eos:
                    return can_end
                return bool(strings[t]) and pda.run(stack, strings[t]) is not None

            candidates = torch.topk(logits, top_n).indices.tolist()
            ok, checked, fallback = [t for t in candidates if valid(t)], len(candidates), False
            if not ok:
                fallback, checked = True, len(strings)
                ok = [t for t in range(len(strings)) if valid(t)]
            if not ok:
                return "dead_end"
            masked = torch.full_like(logits, float("-inf"))
            masked[ok] = logits[ok]
            tid = _pick(masked, top_k, temperature, rng)
            if tid == eos:
                return "eos"
            stack = pda.run(stack, strings[tid])
            yield PDAStep(strings[tid], format_stack(stack), len(ok), checked, fallback)
            ids = torch.tensor([[tid]])
    return "max_tokens"
