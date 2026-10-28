"""Deterministic finite automata.

A DFA is a 5-tuple (Q, Sigma, delta, q0, F) with delta : Q x Sigma -> Q total.
We represent it as:
    states : list[int]                       (always 0..n-1)
    start  : int
    accept : frozenset[int]
    delta  : dict[(state, symbol), state]    (total over symbols() = Sigma + [OTHER])
    dead   : int | None                      (the non-accepting trap state, if any)

About the alphabet: an LLM can emit ANY character, not only those in the
regex. All characters outside Sigma behave identically, so we add ONE extra
symbol OTHER that stands for "any character not in Sigma". It is a real symbol
of the automaton: every state has an OTHER edge. For a DFA built from a regex
the OTHER edges all go to the dead state (the empty subset). After complement
they can lead to an accepting state, which is why OTHER has to be explicit.

Algorithms in this file:
    subset_construction  epsilon-NFA -> DFA      (reachable subsets only)
    minimize             table filling (Myhill-Nerode) or Moore's partitioning method
    live_states          states from which an accepting state is reachable
    complement           swap accepting / non-accepting (DFA must be complete)
    product              closure under intersection/union/difference
    concat, star         closure under concatenation / Kleene star (via NFAs)
    equivalent           L1 = L2  iff  L1 symmetric-difference L2 is empty
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any

from grammarguard.core.nfa import NFA, OTHER, other_char, regex_to_nfa


@dataclass
class DFA:
    """A complete DFA (Q, Sigma + {OTHER}, delta, q0, F)."""

    states: list[int]
    start: int
    accept: frozenset[int]
    delta: dict[tuple[int, str], int]
    alphabet: frozenset[str]
    dead: int | None
    labels: dict[int, str] = field(default_factory=dict)  # for display only

    def symbols(self) -> list[str]:
        """Sigma in sorted order, then OTHER (a fixed order makes BFS canonical)."""
        return sorted(self.alphabet) + [OTHER]

    def step(self, q: int, ch: str) -> int:
        """The transition function delta(q, ch). Characters outside Sigma use the OTHER edge."""
        return self.delta[(q, ch if ch in self.alphabet else OTHER)]

    def run(self, string: str, q: int | None = None) -> int:
        """Extended transition function delta*(q, w): fold step() over the string."""
        q = self.start if q is None else q
        for ch in string:
            q = self.step(q, ch)
        return q

    def accepts(self, string: str) -> bool:
        """w is in L(M) iff delta*(q0, w) is in F."""
        return self.run(string) in self.accept

    def live_states(self) -> frozenset[int]:
        """Live states: those from which SOME accepting state is reachable.

        Computed by backward reachability: BFS from F along reversed edges.
        A non-live state can never lead to acceptance, so a decoder that enters
        one is doomed -- this is exactly what constrained decoding must avoid.
        """
        reverse: dict[int, set[int]] = {q: set() for q in self.states}
        for (src, _), dst in self.delta.items():
            reverse[dst].add(src)
        live = set(self.accept)
        queue = deque(self.accept)
        while queue:
            q = queue.popleft()
            for p in reverse[q]:
                if p not in live:
                    live.add(p)
                    queue.append(p)
        return frozenset(live)

    def reachable_states(self) -> frozenset[int]:
        """States reachable from q0 by forward BFS over Sigma + {OTHER}."""
        seen = {self.start}
        queue = deque([self.start])
        while queue:
            q = queue.popleft()
            for a in self.symbols():
                r = self.delta[(q, a)]
                if r not in seen:
                    seen.add(r)
                    queue.append(r)
        return frozenset(seen)

    def num_states(self) -> int:
        """|Q|, counting the dead state."""
        return len(self.states)

    def with_alphabet(self, alphabet: frozenset[str]) -> DFA:
        """The same language over a larger Sigma: each new character c gets the
        OTHER edge of every state (c was 'other' before, so it behaves the same)."""
        extra = alphabet - self.alphabet
        delta = dict(self.delta)
        for q in self.states:
            for c in extra:
                delta[(q, c)] = self.delta[(q, OTHER)]
        return DFA(self.states, self.start, self.accept, delta, self.alphabet | alphabet, self.dead, dict(self.labels))

    def signature(self) -> str:
        """A canonical text encoding of the DFA (used as a cache key).

        For a minimized DFA renumbered in BFS order this is unique per language,
        because the minimal DFA is unique up to isomorphism (Myhill-Nerode).
        """
        rows = [f"start={self.start};accept={sorted(self.accept)}"]
        for (q, a), r in sorted(self.delta.items()):
            rows.append(f"{q},{a!r},{r}")
        return "\n".join(rows)


def find_dead(states, accept, delta, symbols) -> int | None:
    """The dead state: non-accepting and every symbol loops back to it (a trap).
    A minimal DFA has at most one; None if the language has no dead state."""
    for q in states:
        if q not in accept and all(delta[(q, a)] == q for a in symbols):
            return q
    return None


def _renumber(start, accept, delta, alphabet, labels) -> DFA:
    """Rename states to 0..n-1 in BFS order from the start (Sigma sorted, then OTHER).

    Gives a canonical numbering, so isomorphic DFAs get identical tables.
    """
    symbols = sorted(alphabet) + [OTHER]
    order = [start]
    index = {start: 0}
    queue = deque([start])
    while queue:
        q = queue.popleft()
        for a in symbols:
            r = delta[(q, a)]
            if r not in index:
                index[r] = len(order)
                order.append(r)
                queue.append(r)
    new_delta = {(index[q], a): index[delta[(q, a)]] for q in order for a in symbols}
    new_accept = frozenset(index[q] for q in order if q in accept)
    states = list(range(len(order)))
    return DFA(
        states=states,
        start=0,
        accept=new_accept,
        delta=new_delta,
        alphabet=frozenset(alphabet),
        dead=find_dead(states, new_accept, new_delta, symbols),
        labels={index[q]: labels[q] for q in order},
    )


def _fmt_set(s) -> str:
    """Format a set of states as {1,2,3} for state labels."""
    return "{" + ",".join(str(x) for x in sorted(s)) + "}"


def subset_construction(nfa: NFA, trace: list[dict[str, Any]] | None = None) -> DFA:
    """Subset construction: each DFA state = epsilon-closure of a set of NFA states.

        q0'          = E({q0})
        delta'(S, a) = E(move(S, a))          for a in Sigma and a = OTHER
        F'           = { S : S contains an NFA accepting state }

    Only subsets reachable from q0' are generated (worklist algorithm), so we
    avoid building all 2^|Q| subsets. The empty subset, if reached, is the dead
    state; for a regex NFA it is always reached via OTHER.

    If `trace` is a list, one record per (subset, symbol) step is appended to it
    (used by the Learn page; it is the real algorithm, not a re-implementation).
    """
    symbols = sorted(nfa.alphabet) + [OTHER]
    start_set = nfa.epsilon_closure({nfa.start})
    ids: dict[frozenset[int], int] = {start_set: 0}
    worklist = [start_set]
    delta: dict[tuple[int, str], int] = {}
    while worklist:
        S = worklist.pop()
        for a in symbols:
            moved = nfa.move(S, a)
            U = nfa.epsilon_closure(moved)
            is_new = U not in ids
            if is_new:
                ids[U] = len(ids)
                worklist.append(U)
            delta[(ids[S], a)] = ids[U]
            if trace is not None:
                trace.append(
                    {"from": sorted(S), "symbol": a, "move": sorted(moved), "closure": sorted(U), "new": is_new}
                )
    accept = frozenset(i for S, i in ids.items() if S & nfa.accept)
    labels = {i: ("∅" if not S else _fmt_set(S)) for S, i in ids.items()}
    return _renumber(ids[start_set], accept, delta, nfa.alphabet, labels)


def minimize_table(dfa: DFA, trace: list[dict[str, Any]] | None = None) -> DFA:
    """Table-filling minimization (Myhill-Nerode / Hopcroft's marking algorithm).

    1. Remove unreachable states.
    2. Mark every pair {p, q} with exactly one of p, q accepting
       (the empty string distinguishes them).
    3. Repeat until nothing changes: mark {p, q} if for some symbol a the pair
       {delta(p,a), delta(q,a)} is already marked (a longer distinguishing
       string exists).
    4. Unmarked pairs are equivalent; merge each equivalence class into one state.

    If `trace` is a list, each round's newly marked pairs (with the reason) are appended.
    """
    states = sorted(dfa.reachable_states())
    symbols = dfa.symbols()

    def key(p: int, q: int) -> tuple[int, int]:
        """Unordered pair as an ordered tuple (the table is triangular)."""
        return (p, q) if p < q else (q, p)

    marked: set[tuple[int, int]] = set()
    round0 = []
    for i, p in enumerate(states):
        for q in states[i + 1 :]:
            if (p in dfa.accept) != (q in dfa.accept):
                marked.add((p, q))
                round0.append({"pair": [p, q], "reason": "ε: one accepting, one not"})
    if trace is not None:
        trace.append({"round": 0, "marked": round0})

    changed, rnd = True, 0
    while changed:
        changed, rnd = False, rnd + 1
        newly = []
        for i, p in enumerate(states):
            for q in states[i + 1 :]:
                if (p, q) in marked:
                    continue
                for a in symbols:
                    p2, q2 = dfa.delta[(p, a)], dfa.delta[(q, a)]
                    if p2 != q2 and key(p2, q2) in marked:
                        marked.add((p, q))
                        changed = True
                        newly.append({"pair": [p, q], "symbol": a, "leads_to": list(key(p2, q2))})
                        break
        if trace is not None and newly:
            trace.append({"round": rnd, "marked": newly})

    # Each state's representative = smallest state equivalent to it.
    # (Unmarked = equivalent, and equivalence is transitive, so this is well defined.)
    rep: dict[int, int] = {}
    for q in states:
        rep[q] = next(p for p in states if p == q or (p < q and (p, q) not in marked))

    return _quotient(dfa, states, rep, trace)


def _quotient(dfa: DFA, states: list[int], rep: dict[int, int], trace) -> DFA:
    """Quotient automaton M/~ : one state per equivalence class (named by its representative)."""
    symbols = dfa.symbols()
    classes: dict[int, list[int]] = {}
    for q in states:
        classes.setdefault(rep[q], []).append(q)
    if trace is not None:
        trace.append({"classes": [members for members in classes.values()]})
    delta = {(r, a): rep[dfa.delta[(r, a)]] for r in classes for a in symbols}
    accept = frozenset(r for r in classes if r in dfa.accept)
    labels = {r: _fmt_set(members) for r, members in classes.items()}
    return _renumber(rep[dfa.start], accept, delta, dfa.alphabet, labels)


def minimize_partition(dfa: DFA) -> DFA:
    """Partitioning method (Moore's algorithm): refine {F, Q - F} until stable.

    Round k splits states that are k-distinguishable: two states stay in the
    same block only if, for every symbol, they move into the same block. Each
    round costs O(|Q| * |Sigma|) instead of the O(|Q|^2 * |Sigma|) of table
    filling, so this is used for large automata. Both compute the same
    Myhill-Nerode classes (tested), so the result is the same minimal DFA.
    """
    states = sorted(dfa.reachable_states())
    symbols = dfa.symbols()
    block = {q: int(q in dfa.accept) for q in states}
    while True:
        signature = {q: (block[q], tuple(block[dfa.delta[(q, a)]] for a in symbols)) for q in states}
        numbering: dict[tuple, int] = {}
        new_block = {q: numbering.setdefault(signature[q], len(numbering)) for q in states}
        if len(numbering) == len(set(block.values())):
            break
        block = new_block
    first: dict[int, int] = {}
    for q in states:
        first.setdefault(block[q], q)
    return _quotient(dfa, states, {q: first[block[q]] for q in states}, None)


def minimize(dfa: DFA, trace: list[dict[str, Any]] | None = None, method: str = "auto") -> DFA:
    """Minimize a DFA. method = "table" (table filling), "partition" (Moore), or
    "auto": table filling for small automata or when a trace is wanted, else partition."""
    if method == "table" or (method == "auto" and (trace is not None or dfa.num_states() <= 64)):
        return minimize_table(dfa, trace)
    return minimize_partition(dfa)


def complement(dfa: DFA) -> DFA:
    """Closure under complement: same complete DFA, accepting and non-accepting swapped.

    Correct only because the DFA is complete over ALL characters (OTHER included):
    every string ends in exactly one state, so it is accepted by exactly one of M, M'.
    """
    accept = frozenset(q for q in dfa.states if q not in dfa.accept)
    return _renumber(dfa.start, accept, dfa.delta, dfa.alphabet, dfa.labels)


PRODUCT_MODES = {
    "intersection": lambda x, y: x and y,
    "union": lambda x, y: x or y,
    "difference": lambda x, y: x and not y,
    "symmetric_difference": lambda x, y: x != y,
}


def product(d1: DFA, d2: DFA, mode: str = "intersection") -> DFA:
    """Product construction: run d1 and d2 in parallel on the same input.

        Q = Q1 x Q2,  q0 = (q01, q02),  delta((p,q), a) = (delta1(p,a), delta2(q,a))
        F depends on `mode`: intersection (p in F1 and q in F2), union, difference,
        or symmetric difference.

    This is the proof that regular languages are closed under these operations.
    Sigma = Sigma1 U Sigma2; a character missing from one DFA's Sigma follows
    that DFA's OTHER edge. Only reachable pairs are built.
    """
    accepting = PRODUCT_MODES[mode]
    alphabet = d1.alphabet | d2.alphabet
    symbols = sorted(alphabet) + [OTHER]
    start = (d1.start, d2.start)
    seen = {start}
    queue = deque([start])
    delta: dict[tuple[tuple[int, int], str], tuple[int, int]] = {}
    while queue:
        p, q = queue.popleft()
        for a in symbols:
            nxt = (d1.step(p, a), d2.step(q, a))
            delta[((p, q), a)] = nxt
            if nxt not in seen:
                seen.add(nxt)
                queue.append(nxt)
    accept = {pq for pq in seen if accepting(pq[0] in d1.accept, pq[1] in d2.accept)}
    labels = {pq: f"({pq[0]},{pq[1]})" for pq in seen}
    return _renumber(start, accept, delta, alphabet, labels)


def dfa_to_nfa(dfa: DFA, offset: int = 0) -> NFA:
    """View a DFA as an NFA (every DFA is an NFA with singleton next-state sets).
    States are shifted by `offset` so two automata can be glued together."""
    nfa = NFA(alphabet=dfa.alphabet)
    nfa.states = {q + offset for q in dfa.states}
    nfa.start = dfa.start + offset
    nfa.accept = {q + offset for q in dfa.accept}
    for (q, a), r in dfa.delta.items():
        nfa.add_edge(q + offset, a, r + offset)
    return nfa


def concat(d1: DFA, d2: DFA) -> DFA:
    """Closure under concatenation: glue the NFAs of d1 and d2 with epsilon edges
    from d1's accepting states to d2's start, then determinize and minimize."""
    sigma = d1.alphabet | d2.alphabet
    a, b = d1.with_alphabet(sigma), d2.with_alphabet(sigma)
    n1, n2 = dfa_to_nfa(a), dfa_to_nfa(b, offset=a.num_states())
    nfa = NFA(alphabet=sigma)
    nfa.states = n1.states | n2.states
    nfa.start = n1.start
    nfa.accept = set(n2.accept)
    for (q, s), dsts in list(n1.delta.items()) + list(n2.delta.items()):
        for r in dsts:
            nfa.add_edge(q, s, r)
    for f in n1.accept:
        nfa.add_edge(f, None, n2.start)
    return minimize(subset_construction(nfa))


def star(d: DFA) -> DFA:
    """Closure under Kleene star: new accepting start state with an epsilon edge
    into d, and epsilon edges from d's accepting states back to d's start."""
    nfa = dfa_to_nfa(d, offset=1)
    nfa.states.add(0)
    nfa.add_edge(0, None, nfa.start)
    for f in list(nfa.accept):
        nfa.add_edge(f, None, nfa.start)
    nfa.start = 0
    nfa.accept.add(0)
    return minimize(subset_construction(nfa))


def shortest_accepted(dfa: DFA) -> str | None:
    """Shortest string in L(M), or None if L(M) is empty (emptiness test).

    BFS from q0 explores strings in order of length (and, with symbols tried in
    a fixed order, alphabetically within a length), so the first accepting
    state found gives a shortest witness. We rebuild it from parent pointers;
    an OTHER edge is shown as a concrete character outside Sigma.
    """
    stand_in = other_char(dfa.alphabet)
    parent: dict[int, tuple[int, str] | None] = {dfa.start: None}
    queue = deque([dfa.start])
    while queue:
        q = queue.popleft()
        if q in dfa.accept:
            chars = []
            while (link := parent[q]) is not None:
                q, a = link
                chars.append(stand_in if a == OTHER else a)
            return "".join(reversed(chars))
        for a in dfa.symbols():
            r = dfa.delta[(q, a)]
            if r not in parent:
                parent[r] = (q, a)
                queue.append(r)
    return None


def equivalent(d1: DFA, d2: DFA) -> tuple[bool, str | None]:
    """Equivalence check: L1 = L2 iff (L1 - L2) U (L2 - L1) is empty.

    Build the symmetric-difference product DFA and search for its shortest
    accepted string. Returns (True, None) if equivalent, otherwise
    (False, w) where w is a shortest string accepted by exactly one of them.
    """
    witness = shortest_accepted(product(d1, d2, "symmetric_difference"))
    return (witness is None, witness)


def regex_to_dfa(text: str, minimal: bool = True) -> DFA:
    """Full pipeline: regex -> AST -> epsilon-NFA -> DFA (-> minimal DFA)."""
    dfa = subset_construction(regex_to_nfa(text))
    return minimize(dfa) if minimal else dfa
