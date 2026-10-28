"""Deterministic finite automata.

A DFA is a 5-tuple (Q, Sigma, delta, q0, F) with delta : Q x Sigma -> Q total.
We represent it as:
    states : list[int]                 (always 0..n-1)
    start  : int
    accept : frozenset[int]
    delta  : dict[(state, symbol), state]   (total over `alphabet`)
    dead   : int                       (the explicit dead/trap state)

About the alphabet: an LLM can emit ANY character, not only those in the
regex. All characters outside Sigma behave identically (none of them can ever
be matched), so conceptually there is one extra symbol "other" that sends every
state to the dead state. We do not store those edges; `step()` applies the rule.
This is why the dead state is always present, even if no symbol in Sigma
reaches it.

Algorithms in this file:
    subset_construction  epsilon-NFA -> DFA      (reachable subsets only)
    minimize             table-filling (Myhill-Nerode) algorithm
    live_states          states from which an accepting state is reachable
    product              closure under intersection/union/difference
    equivalent           L1 = L2  iff  L1 symmetric-difference L2 is empty
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from grammarguard.core.nfa import NFA, regex_to_nfa


@dataclass
class DFA:
    """A complete DFA (Q, Sigma, delta, q0, F) plus an explicit dead state."""

    states: list[int]
    start: int
    accept: frozenset[int]
    delta: dict[tuple[int, str], int]
    alphabet: frozenset[str]
    dead: int
    labels: dict[int, str] = field(default_factory=dict)  # for display only

    def step(self, q: int, ch: str) -> int:
        """The transition function delta(q, ch).

        Symbols outside Sigma go to the dead state (the implicit "other" symbol).
        """
        return self.delta.get((q, ch), self.dead)

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
        """States reachable from q0 by forward BFS (plus the dead state, reachable via 'other')."""
        seen = {self.start, self.dead}
        queue = deque([self.start])
        while queue:
            q = queue.popleft()
            for a in sorted(self.alphabet):
                r = self.step(q, a)
                if r not in seen:
                    seen.add(r)
                    queue.append(r)
        return frozenset(seen)

    def num_states(self) -> int:
        """|Q|, counting the dead state."""
        return len(self.states)

    def signature(self) -> str:
        """A canonical text encoding of the DFA (used as a cache key).

        For a minimized DFA renumbered in BFS order this is unique per language,
        because the minimal DFA is unique up to isomorphism (Myhill-Nerode).
        """
        rows = [f"start={self.start};dead={self.dead};accept={sorted(self.accept)}"]
        for (q, a), r in sorted(self.delta.items()):
            rows.append(f"{q},{a!r},{r}")
        return "\n".join(rows)


def _renumber(start, accept, delta, alphabet, dead, labels) -> DFA:
    """Rename states to 0..n-1 in BFS order from the start (sorted symbols).

    Gives a canonical numbering, so isomorphic DFAs get identical tables.
    The dead state is reached via 'other', so it is numbered last if Sigma
    alone does not reach it.
    """
    order = [start]
    index = {start: 0}
    queue = deque([start])
    while queue:
        q = queue.popleft()
        for a in sorted(alphabet):
            r = delta[(q, a)]
            if r not in index:
                index[r] = len(order)
                order.append(r)
                queue.append(r)
    if dead not in index:
        index[dead] = len(order)
        order.append(dead)
    new_delta = {(index[q], a): index[delta[(q, a)]] for q in order for a in alphabet}
    return DFA(
        states=list(range(len(order))),
        start=0,
        accept=frozenset(index[q] for q in order if q in accept),
        delta=new_delta,
        alphabet=frozenset(alphabet),
        dead=index[dead],
        labels={index[q]: labels[q] for q in order},
    )


def _fmt_set(s) -> str:
    """Format a set of states as {1,2,3} for state labels."""
    return "{" + ",".join(str(x) for x in sorted(s)) + "}"


def subset_construction(nfa: NFA) -> DFA:
    """Subset construction: each DFA state = epsilon-closure of a set of NFA states.

        q0'          = E({q0})
        delta'(S, a) = E(move(S, a))
        F'           = { S : S contains an NFA accepting state }

    Only subsets reachable from q0' are generated (worklist algorithm), so we
    avoid building all 2^|Q| subsets. The empty subset is the dead state; we
    always add it so the DFA is complete for every input character.
    """
    alphabet = sorted(nfa.alphabet)
    start_set = nfa.epsilon_closure({nfa.start})
    ids: dict[frozenset[int], int] = {start_set: 0}
    worklist = [start_set]
    delta: dict[tuple[int, str], int] = {}
    while worklist:
        S = worklist.pop()
        for a in alphabet:
            U = nfa.epsilon_closure(nfa.move(S, a))
            if U not in ids:
                ids[U] = len(ids)
                worklist.append(U)
            delta[(ids[S], a)] = ids[U]
    dead_set: frozenset[int] = frozenset()
    if dead_set not in ids:  # Sigma never reaches it, but "other" symbols do
        ids[dead_set] = len(ids)
        for a in alphabet:
            delta[(ids[dead_set], a)] = ids[dead_set]
    accept = frozenset(i for S, i in ids.items() if S & nfa.accept)
    labels = {i: ("∅" if not S else _fmt_set(S)) for S, i in ids.items()}
    return _renumber(ids[start_set], accept, delta, alphabet, ids[dead_set], labels)


def minimize(dfa: DFA) -> DFA:
    """Table-filling minimization (Myhill-Nerode / Hopcroft's marking algorithm).

    1. Remove unreachable states.
    2. Mark every pair {p, q} with exactly one of p, q accepting
       (the empty string distinguishes them).
    3. Repeat until nothing changes: mark {p, q} if for some symbol a the pair
       {delta(p,a), delta(q,a)} is already marked (a longer distinguishing
       string exists).
    4. Unmarked pairs are equivalent; merge each equivalence class into one state.

    The 'other' symbol sends every state to dead, so it never distinguishes
    a pair and need not be checked.
    """
    states = sorted(dfa.reachable_states())
    alphabet = sorted(dfa.alphabet)

    def key(p: int, q: int) -> tuple[int, int]:
        """Unordered pair as an ordered tuple (the table is triangular)."""
        return (p, q) if p < q else (q, p)

    marked: set[tuple[int, int]] = set()
    for i, p in enumerate(states):
        for q in states[i + 1 :]:
            if (p in dfa.accept) != (q in dfa.accept):
                marked.add((p, q))

    changed = True
    while changed:
        changed = False
        for i, p in enumerate(states):
            for q in states[i + 1 :]:
                if (p, q) in marked:
                    continue
                for a in alphabet:
                    p2, q2 = dfa.step(p, a), dfa.step(q, a)
                    if p2 != q2 and key(p2, q2) in marked:
                        marked.add((p, q))
                        changed = True
                        break

    # Each state's representative = smallest state equivalent to it.
    # (Unmarked = equivalent, and equivalence is transitive, so this is well defined.)
    rep: dict[int, int] = {}
    for q in states:
        rep[q] = next(p for p in states if p == q or (p < q and (p, q) not in marked))

    classes: dict[int, list[int]] = {}
    for q in states:
        classes.setdefault(rep[q], []).append(q)
    delta = {(r, a): rep[dfa.step(r, a)] for r in classes for a in alphabet}
    accept = frozenset(r for r in classes if r in dfa.accept)
    labels = {r: _fmt_set(members) for r, members in classes.items()}
    return _renumber(rep[dfa.start], accept, delta, alphabet, rep[dfa.dead], labels)


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
    Sigma = Sigma1 U Sigma2; a symbol missing from one DFA sends it to its dead
    state. Only reachable pairs are built.
    """
    accepting = PRODUCT_MODES[mode]
    alphabet = sorted(d1.alphabet | d2.alphabet)
    start = (d1.start, d2.start)
    dead = (d1.dead, d2.dead)
    seen = {start, dead}
    queue = deque([start, dead])
    delta: dict[tuple[tuple[int, int], str], tuple[int, int]] = {}
    while queue:
        p, q = queue.popleft()
        for a in alphabet:
            nxt = (d1.step(p, a), d2.step(q, a))
            delta[((p, q), a)] = nxt
            if nxt not in seen:
                seen.add(nxt)
                queue.append(nxt)
    accept = {pq for pq in seen if accepting(pq[0] in d1.accept, pq[1] in d2.accept)}
    labels = {pq: f"({pq[0]},{pq[1]})" for pq in seen}
    return _renumber(start, accept, delta, alphabet, dead, labels)


def shortest_accepted(dfa: DFA) -> str | None:
    """Shortest string in L(M), or None if L(M) is empty (emptiness test).

    BFS from q0 explores strings in order of length (and, with symbols tried in
    sorted order, alphabetically within a length), so the first accepting
    state found gives a shortest witness. We rebuild it from parent pointers.
    """
    parent: dict[int, tuple[int, str] | None] = {dfa.start: None}
    queue = deque([dfa.start])
    while queue:
        q = queue.popleft()
        if q in dfa.accept:
            chars = []
            while (link := parent[q]) is not None:
                q, a = link
                chars.append(a)
            return "".join(reversed(chars))
        for a in sorted(dfa.alphabet):
            r = dfa.step(q, a)
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
