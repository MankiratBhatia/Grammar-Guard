"""Epsilon-NFA and Thompson's construction.

An epsilon-NFA is a 5-tuple (Q, Sigma, delta, q0, F) where
    delta : Q x (Sigma U {epsilon}) -> P(Q)      (a SET of next states)
We represent it as:
    states : set[int]
    start  : int
    accept : set[int]
    delta  : dict[(state, symbol | None), set[int]]   (None means epsilon)

Thompson's construction builds the NFA by structural induction on the regex
AST. Every fragment has exactly one start state and one accept state, and the
accept state has no outgoing edges, which is what makes the gluing rules work:

    epsilon :  s --e--> f
    a       :  s --a--> f
    [abc]   :  s --a,b,c--> f          (one edge per symbol in the class)
    R S     :  R.f --e--> S.s
    R | S   :  s --e--> R.s, s --e--> S.s, R.f --e--> f, S.f --e--> f
    R*      :  s --e--> R.s, s --e--> f, R.f --e--> R.s, R.f --e--> f
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from grammarguard.core import regex_parser as rp


@dataclass
class NFA:
    """An epsilon-NFA (Q, Sigma, delta, q0, F) with delta mapping to sets of states."""

    states: set[int] = field(default_factory=set)
    start: int = 0
    accept: set[int] = field(default_factory=set)
    delta: dict[tuple[int, str | None], set[int]] = field(default_factory=lambda: defaultdict(set))
    alphabet: frozenset[str] = frozenset()

    def add_edge(self, src: int, symbol: str | None, dst: int) -> None:
        """Add dst to delta(src, symbol); symbol None is an epsilon edge."""
        self.delta[(src, symbol)].add(dst)

    def epsilon_closure(self, states: set[int] | frozenset[int]) -> frozenset[int]:
        """Epsilon-closure E(S): all states reachable from S using only epsilon edges.

        Computed as graph reachability (depth-first search with an explicit stack)
        over the epsilon edges. S itself is always included (zero epsilon moves).
        """
        closure = set(states)
        stack = list(states)
        while stack:
            q = stack.pop()
            for r in self.delta.get((q, None), ()):
                if r not in closure:
                    closure.add(r)
                    stack.append(r)
        return frozenset(closure)

    def move(self, states: frozenset[int], symbol: str) -> frozenset[int]:
        """move(S, a) = union of delta(q, a) for q in S (one real symbol, no epsilons)."""
        result: set[int] = set()
        for q in states:
            result |= self.delta.get((q, symbol), set())
        return frozenset(result)

    def accepts(self, string: str) -> bool:
        """Simulate the NFA directly: track the set of current states.

        current = E({q0}); for each symbol a: current = E(move(current, a)).
        Accept iff the final set contains an accepting state.
        """
        current = self.epsilon_closure({self.start})
        for ch in string:
            current = self.epsilon_closure(self.move(current, ch))
            if not current:
                return False
        return bool(current & self.accept)

    def edges(self) -> list[tuple[int, str | None, int]]:
        """All transitions as (src, symbol, dst) triples, for display and testing."""
        return [(src, sym, dst) for (src, sym), dsts in self.delta.items() for dst in sorted(dsts)]


class _ThompsonBuilder:
    """Holds the state counter so each fragment gets fresh state numbers."""

    def __init__(self) -> None:
        """Start with an empty NFA and state counter 0."""
        self.nfa = NFA()
        self.counter = 0

    def new_state(self) -> int:
        """Allocate a fresh state q_i."""
        q = self.counter
        self.counter += 1
        self.nfa.states.add(q)
        return q

    def build(self, node: rp.Node) -> tuple[int, int]:
        """Thompson's construction by structural induction on the AST.

        Returns the (start, accept) pair of the fragment recognising L(node).
        """
        if isinstance(node, rp.Epsilon):
            s, f = self.new_state(), self.new_state()
            self.nfa.add_edge(s, None, f)
            return s, f
        if isinstance(node, rp.Literal):
            s, f = self.new_state(), self.new_state()
            self.nfa.add_edge(s, node.char, f)
            return s, f
        if isinstance(node, rp.CharClass):
            s, f = self.new_state(), self.new_state()
            for ch in node.chars:
                self.nfa.add_edge(s, ch, f)
            return s, f
        if isinstance(node, rp.Concat):
            # Glue R's accept to S's start with an epsilon edge.
            s1, f1 = self.build(node.left)
            s2, f2 = self.build(node.right)
            self.nfa.add_edge(f1, None, s2)
            return s1, f2
        if isinstance(node, rp.Union):
            # New start branches to both; both accepts join a new accept.
            s, f = self.new_state(), self.new_state()
            s1, f1 = self.build(node.left)
            s2, f2 = self.build(node.right)
            self.nfa.add_edge(s, None, s1)
            self.nfa.add_edge(s, None, s2)
            self.nfa.add_edge(f1, None, f)
            self.nfa.add_edge(f2, None, f)
            return s, f
        if isinstance(node, rp.Star):
            # Skip (zero copies) or enter; loop back from the end for more copies.
            s, f = self.new_state(), self.new_state()
            s1, f1 = self.build(node.child)
            self.nfa.add_edge(s, None, s1)
            self.nfa.add_edge(s, None, f)
            self.nfa.add_edge(f1, None, s1)
            self.nfa.add_edge(f1, None, f)
            return s, f
        raise TypeError(f"unknown AST node {node!r}")


def thompson(node: rp.Node) -> NFA:
    """Thompson's construction: regex AST -> epsilon-NFA with one start and one accept state."""
    builder = _ThompsonBuilder()
    start, accept = builder.build(node)
    nfa = builder.nfa
    nfa.start = start
    nfa.accept = {accept}
    nfa.alphabet = rp.alphabet(node)
    return nfa


def regex_to_nfa(text: str) -> NFA:
    """Convenience: parse a regex string and run Thompson's construction."""
    return thompson(rp.parse(text))
