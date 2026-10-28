"""Deterministic pushdown automaton driven by an LL(1) parse table.

A PDA is a finite automaton plus a stack. For an LL(1) grammar a one-state PDA
suffices; the stack holds the grammar symbols still to be matched, top first:

    top is a nonterminal A, look-ahead c : pop A, push the right-hand side of
                                           M[A, c] (last symbol first). This is
                                           the "expand" move; it reads no input.
    top is a terminal t, input c in t    : pop it ("match" move; consumes c).
    anything else                        : reject.

Because M[A, c] has at most one entry, the PDA is DETERMINISTIC. Accepting by
empty stack: the input is in L(G) iff the stack can be emptied at end of input,
where only epsilon-expansions (M[A, EOF]) are still allowed.

Stacks are immutable tuples (top at the END of the tuple) so a decoder can try
a candidate token on a stack without disturbing it.
"""

from __future__ import annotations

from grammarguard.core.cfg import EOF, Grammar, Symbol, Term, json_grammar

Stack = tuple[Symbol, ...]


class PDA:
    """One-state deterministic PDA built from an LL(1) grammar."""

    def __init__(self, grammar: Grammar):
        """Build the parse table (raises GrammarError if the grammar is not LL(1))."""
        self.grammar = grammar
        self.table = grammar.parse_table()

    def initial(self) -> Stack:
        """Initial configuration: the stack holds just the start symbol."""
        return (self.grammar.start,)

    def step(self, stack: Stack, c: str) -> Stack | None:
        """Consume one character: expand nonterminals on top (using c as look-ahead),
        then match the terminal underneath. Returns the new stack, or None = reject."""
        work: list[Symbol] = list(stack)
        while work and isinstance(work[-1], str):
            nonterminal = work.pop()
            assert isinstance(nonterminal, str)
            rhs = self.table.get((nonterminal, c))
            if rhs is None:
                return None
            work.extend(reversed(rhs))
        if not work:  # input left over but nothing left to match
            return None
        top = work.pop()
        assert isinstance(top, Term)
        return tuple(work) if c in top.chars else None

    def run(self, stack: Stack, text: str) -> Stack | None:
        """Extended transition: feed a whole string; None as soon as it is rejected."""
        for c in text:
            nxt = self.step(stack, c)
            if nxt is None:
                return None
            stack = nxt
        return stack

    def accepts_end(self, stack: Stack) -> bool:
        """May the input end here? Only epsilon-expansions (look-ahead EOF) may
        remain, and they must empty the stack."""
        work: list[Symbol] = list(stack)
        while work:
            top = work.pop()
            if isinstance(top, Term):
                return False
            rhs = self.table.get((top, EOF))
            if rhs is None:
                return False
            work.extend(reversed(rhs))
        return True

    def accepts(self, text: str) -> bool:
        """text is in L(G) iff running it leaves a stack that may end."""
        stack = self.run(self.initial(), text)
        return stack is not None and self.accepts_end(stack)

    def is_viable_prefix(self, text: str) -> bool:
        """Can text be extended to a string of L(G)? For LL(1) grammars whose
        nonterminals are all productive, that holds iff the PDA has not rejected."""
        return self.run(self.initial(), text) is not None


def format_stack(stack: Stack) -> tuple[str, ...]:
    """Stack for display, top first, e.g. ('Value', 'Vals', ']')."""
    return tuple(s if isinstance(s, str) else s.name.strip("'") for s in reversed(stack))


JSON_PDA = PDA(json_grammar())
