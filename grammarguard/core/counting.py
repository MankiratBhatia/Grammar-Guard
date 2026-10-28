"""Counting, enumerating and sampling the strings of a regular language.

All counts are over the DFA's alphabet Sigma (the characters the constraint
mentions). OTHER stands for infinitely many characters, so it is excluded
when counting: "how many valid IDs of length 8" means over the ID's own symbols.

    N(q, 0) = 1 if q is accepting else 0
    N(q, k) = sum over a in Sigma of N(delta(q, a), k - 1)

This dynamic programme counts accepted strings of length k from state q in
O(k * |Q| * |Sigma|) time, although the number of strings itself grows
exponentially. The same table gives exact uniform sampling: from state q
with k symbols left, choose a with probability N(delta(q,a), k-1) / N(q, k).
"""

from __future__ import annotations

import random
from collections import deque

from grammarguard.core.dfa import DFA
from grammarguard.core.nfa import OTHER, other_char


def count_table(dfa: DFA, max_len: int) -> list[dict[int, int]]:
    """N[k][q] for k = 0..max_len (the dynamic-programming table)."""
    sigma = sorted(dfa.alphabet)
    table = [{q: int(q in dfa.accept) for q in dfa.states}]
    for _ in range(max_len):
        prev = table[-1]
        table.append({q: sum(prev[dfa.delta[(q, a)]] for a in sigma) for q in dfa.states})
    return table


def counts_by_length(dfa: DFA, max_len: int) -> list[int]:
    """Number of accepted strings of each length 0..max_len (over Sigma)."""
    return [row[dfa.start] for row in count_table(dfa, max_len)]


def is_finite(dfa: DFA) -> bool:
    """Is L(M) finite (over Sigma)? Decision procedure: L is infinite iff some
    useful state (reachable AND live) lies on a cycle, because that cycle can
    be pumped. We look for a cycle with a DFS colouring (white/grey/black)."""
    useful = dfa.reachable_states() & dfa.live_states()
    colour = dict.fromkeys(useful, 0)  # 0 = unvisited, 1 = on the DFS stack, 2 = done

    def has_cycle(q: int) -> bool:
        colour[q] = 1
        for a in sorted(dfa.alphabet):
            r = dfa.delta[(q, a)]
            if r not in useful:
                continue
            if colour[r] == 1 or (colour[r] == 0 and has_cycle(r)):
                return True
        colour[q] = 2
        return False

    return not any(colour[q] == 0 and has_cycle(q) for q in sorted(useful))


def language_size(dfa: DFA) -> int | None:
    """|L| over Sigma if finite, else None. A finite language has no string
    longer than |Q| - 1 (a longer one would repeat a state, i.e. contain a cycle)."""
    if not is_finite(dfa):
        return None
    return sum(counts_by_length(dfa, max(dfa.num_states() - 1, 0)))


def sample_uniform(dfa: DFA, length: int, k: int = 1, seed: int = 0) -> list[str]:
    """k strings of the given length, each drawn uniformly at random from
    L ∩ Sigma^length (with replacement). Empty list if there are none."""
    table = count_table(dfa, length)
    if table[length][dfa.start] == 0:
        return []
    rng = random.Random(seed)
    sigma = sorted(dfa.alphabet)
    out = []
    for _ in range(k):
        q, chars = dfa.start, []
        for left in range(length, 0, -1):
            weights = [table[left - 1][dfa.delta[(q, a)]] for a in sigma]
            a = rng.choices(sigma, weights=weights)[0]
            chars.append(a)
            q = dfa.delta[(q, a)]
        out.append("".join(chars))
    return out


def shortest_strings(dfa: DFA, k: int, max_len: int = 64, include_other: bool = True) -> list[str]:
    """The first k accepted strings in shortlex order (by length, then by symbol order).

    BFS over (state, prefix) pairs, never entering non-live states, so every
    branch explored can still reach acceptance. OTHER is shown as a concrete
    character outside Sigma.
    """
    live = dfa.live_states()
    if dfa.start not in live:
        return []
    stand_in = other_char(dfa.alphabet)
    symbols = dfa.symbols() if include_other else sorted(dfa.alphabet)
    found: list[str] = []
    queue: deque[tuple[int, str]] = deque([(dfa.start, "")])
    while queue and len(found) < k:
        q, w = queue.popleft()
        if q in dfa.accept:
            found.append(w)
        if len(w) >= max_len:
            continue
        for a in symbols:
            r = dfa.delta[(q, a)]
            if r in live:
                queue.append((r, w + (stand_in if a == OTHER else a)))
    return found
