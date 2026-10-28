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

import math
from collections.abc import Callable, Iterator
from functools import lru_cache
from typing import NamedTuple

import torch

from grammarguard.core.pda import JSON_PDA, PDA, Stack, format_stack
from grammarguard.decoding.generate import _pick, _prompt_ids, load_model
from grammarguard.decoding.token_index import VocabTrie, vocab_trie


class PDAStep(NamedTuple):
    """One generated token with the PDA stack AFTER it (top first), how many
    tokens were valid among those checked, how many were checked, whether the
    full-vocabulary fallback was needed, and telemetry.

    `mass_kept` is the model probability of the valid tokens that were checked.
    After a top-N check it is a LOWER bound on the true mass (valid tokens
    outside the top N are not counted); after a fallback it is exact.
    """

    token_text: str
    stack: tuple[str, ...]
    num_valid: int
    num_checked: int
    fallback: bool
    mass_kept: float = 1.0
    preferred_legal: bool = True
    p_chosen: float = 0.0


@lru_cache(maxsize=2)
def _vocab(model_name: str) -> tuple[list[str | None], VocabTrie]:
    """(token texts, trie) for a model's tokenizer (computed once per model)."""
    return vocab_trie(load_model(model_name)[0])


def valid_tokens(pda: PDA, stack: Stack, trie: VocabTrie) -> dict[int, Stack]:
    """All tokens the PDA accepts from `stack`, found by walking the vocabulary trie.

    Same idea as the DFA token index, with the stack carried along: each trie
    edge is one PDA move; when the PDA rejects a character the whole subtree
    below it is pruned (no token with that prefix can be valid). Returns
    {token id: stack after the token}. This is the fallback when none of the
    top-N tokens is valid, and it replaces a scan that ran the PDA over all
    50 257 token strings separately.
    """
    found: dict[int, Stack] = {}
    work: list[tuple[int, Stack]] = [(0, stack)]
    while work:
        node, st = work.pop()
        for ch, child in trie.children[node].items():
            nxt = pda.step(st, ch)
            if nxt is None:
                continue
            for tid in trie.tokens_at[child]:
                found[tid] = nxt
            work.append((child, nxt))
    return found


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
    should_stop: Callable[[], bool] | None = None,
) -> Iterator[PDAStep]:
    """Generate text accepted by `pda` (the JSON subset, or any schema compiled with
    PDA(schema_to_grammar(schema))).

    Returns the stop reason: eos | dead_end | max_tokens | cancelled.
    """
    tokenizer, model = load_model(model_name)
    strings, trie = _vocab(model_name)
    eos = tokenizer.eos_token_id
    rng = torch.Generator().manual_seed(seed)
    ids, past, stack = _prompt_ids(tokenizer, prompt), None, pda.initial()
    with torch.no_grad():
        for _ in range(max_tokens):
            if should_stop is not None and should_stop():
                return "cancelled"
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
                ok = sorted(valid_tokens(pda, stack, trie))
                if can_end:
                    ok.append(eos)
            if not ok:
                return "dead_end"
            masked = torch.full_like(logits, float("-inf"))
            masked[ok] = logits[ok]
            tid = _pick(masked, top_k, temperature, rng)
            logp = torch.log_softmax(logits.double(), dim=-1)
            mass = float(torch.exp(torch.logsumexp(logp[ok], dim=0)))
            preferred_legal = int(torch.argmax(logits)) in set(ok)
            if tid == eos:
                return "eos"
            stack = pda.run(stack, strings[tid])
            yield PDAStep(
                strings[tid],
                format_stack(stack),
                len(ok),
                checked,
                fallback,
                mass_kept=mass,
                preferred_legal=preferred_legal,
                p_chosen=math.exp(float(logp[tid])),
            )
            ids = torch.tensor([[tid]])
    return "max_tokens"
