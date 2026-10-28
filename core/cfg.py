"""Context-free grammars and LL(1) parse tables.

A CFG is G = (N, Sigma, P, S): nonterminals N, terminals Sigma, productions P
of the form A -> alpha (alpha a string of grammar symbols), start symbol S.

Our terminals are single CHARACTERS (or sets of characters such as [0-9]),
because the decoder feeds the automaton text character by character. A
nonterminal is a plain string; a terminal is a `Term`.

LL(1) means one symbol of look-ahead is enough to pick the production:
    FIRST(alpha)  = characters that can begin a string derived from alpha
    FOLLOW(A)     = characters that can come right after A in some sentential form
    table[A, c]   = alpha   if c in FIRST(alpha), or (alpha is nullable and c in FOLLOW(A))
The grammar is LL(1) exactly when no table cell receives two productions.
"""

from __future__ import annotations

from dataclasses import dataclass

EOF = "<EOF>"  # end-of-input marker; longer than one char so it cannot clash with a real character


@dataclass(frozen=True)
class Term:
    """A terminal: matches any ONE character from `chars`. `name` is for display."""

    chars: frozenset[str]
    name: str

    def __repr__(self) -> str:
        """Show the terminal by its display name."""
        return self.name


def lit(ch: str) -> Term:
    """A terminal matching exactly one character."""
    return Term(frozenset(ch), repr(ch))


def char_range(lo: str, hi: str) -> Term:
    """A terminal matching any character in lo..hi, e.g. char_range('0','9')."""
    return Term(frozenset(chr(c) for c in range(ord(lo), ord(hi) + 1)), f"[{lo}-{hi}]")


Symbol = str | Term  # nonterminal name or terminal


class GrammarError(ValueError):
    """Raised when a grammar is malformed or is not LL(1)."""


class Grammar:
    """A context-free grammar with FIRST/FOLLOW and LL(1) table construction."""

    def __init__(self, start: str, productions: dict[str, list[list[Symbol]]]):
        """`productions[A]` is the list of right-hand sides for A (an empty list = epsilon)."""
        self.start = start
        self.productions = {a: [tuple(rhs) for rhs in alts] for a, alts in productions.items()}
        for a, alts in self.productions.items():
            for rhs in alts:
                for sym in rhs:
                    if isinstance(sym, str) and sym not in self.productions:
                        raise GrammarError(f"undefined nonterminal {sym!r} in production of {a!r}")
        self.nullable = self._compute_nullable()
        self.first = self._compute_first()
        self.follow = self._compute_follow()

    def _compute_nullable(self) -> set[str]:
        """Nullable(A): A =>* epsilon. Fixed-point iteration: A is nullable if some
        production has a right-hand side made only of nullable nonterminals."""
        nullable: set[str] = set()
        changed = True
        while changed:
            changed = False
            for a, alts in self.productions.items():
                if a not in nullable and any(all(isinstance(s, str) and s in nullable for s in rhs) for rhs in alts):
                    nullable.add(a)
                    changed = True
        return nullable

    def first_of_seq(self, seq: tuple[Symbol, ...], first: dict[str, set[str]] | None = None) -> tuple[set[str], bool]:
        """FIRST of a symbol string, and whether the whole string is nullable.

        Scan left to right: add FIRST of each symbol, continue only while the
        symbol just passed can vanish (derive epsilon)."""
        first = self.first if first is None else first
        out: set[str] = set()
        for sym in seq:
            if isinstance(sym, Term):
                out |= sym.chars
                return out, False
            out |= first[sym]
            if sym not in self.nullable:
                return out, False
        return out, True

    def _compute_first(self) -> dict[str, set[str]]:
        """FIRST(A) for every nonterminal, by fixed-point iteration over productions."""
        first = {a: set() for a in self.productions}
        changed = True
        while changed:
            changed = False
            for a, alts in self.productions.items():
                for rhs in alts:
                    chars, _ = self.first_of_seq(rhs, first)
                    if not chars <= first[a]:
                        first[a] |= chars
                        changed = True
        return first

    def _compute_follow(self) -> dict[str, set[str]]:
        """FOLLOW(A) for every nonterminal, by fixed-point iteration.

        EOF is in FOLLOW(start). For a production B -> x A y: FOLLOW(A) gets
        FIRST(y); and if y is nullable, FOLLOW(A) also gets FOLLOW(B)."""
        follow = {a: set() for a in self.productions}
        follow[self.start].add(EOF)
        changed = True
        while changed:
            changed = False
            for b, alts in self.productions.items():
                for rhs in alts:
                    for i, sym in enumerate(rhs):
                        if not isinstance(sym, str):
                            continue
                        chars, nullable = self.first_of_seq(rhs[i + 1 :])
                        new = chars | (follow[b] if nullable else set())
                        if not new <= follow[sym]:
                            follow[sym] |= new
                            changed = True
        return follow

    def parse_table(self) -> dict[tuple[str, str], tuple[Symbol, ...]]:
        """Build the LL(1) parse table M[A, c] -> right-hand side.

        Raises GrammarError listing every cell with two different productions
        (an LL(1) conflict)."""
        table: dict[tuple[str, str], tuple[Symbol, ...]] = {}
        conflicts = []
        for a, alts in self.productions.items():
            for rhs in alts:
                chars, nullable = self.first_of_seq(rhs)
                if nullable:
                    chars = chars | self.follow[a]
                for c in chars:
                    if (a, c) in table and table[(a, c)] != rhs:
                        conflicts.append(f"M[{a}, {c!r}]: {list(table[(a, c)])} vs {list(rhs)}")
                    table[(a, c)] = rhs
        if conflicts:
            raise GrammarError("not LL(1):\n  " + "\n  ".join(sorted(conflicts)))
        return table


def json_grammar() -> Grammar:
    """LL(1) grammar for a small JSON subset, at character level, no whitespace.

        Doc    -> { Obj | [ Arr                      (top level is an object or array)
        Value  -> { Obj | [ Arr | " Str | 0 | [1-9] Digits | true | false | null
        Obj    -> } | " Str : Value Pairs }
        Pairs  -> , " Str : Value Pairs | epsilon
        Arr    -> ] | Value Vals ]
        Vals   -> , Value Vals | epsilon
        Str    -> [a-z] Str | "                      (lowercase letters only)
        Digits -> [0-9] Digits | epsilon             (non-negative ints, no leading zeros)

    Every nonterminal is split so that the first character decides the
    production (that is what makes it LL(1)), e.g. the '{' is consumed by
    Doc/Value and Obj decides between an empty object and a first pair.
    """
    q, colon, comma = lit('"'), lit(":"), lit(",")
    return Grammar(
        "Doc",
        {
            "Doc": [[lit("{"), "Obj"], [lit("["), "Arr"]],
            "Value": [
                [lit("{"), "Obj"],
                [lit("["), "Arr"],
                [q, "Str"],
                [lit("0")],
                [char_range("1", "9"), "Digits"],
                [lit("t"), lit("r"), lit("u"), lit("e")],
                [lit("f"), lit("a"), lit("l"), lit("s"), lit("e")],
                [lit("n"), lit("u"), lit("l"), lit("l")],
            ],
            "Obj": [[lit("}")], [q, "Str", colon, "Value", "Pairs", lit("}")]],
            "Pairs": [[comma, q, "Str", colon, "Value", "Pairs"], []],
            "Arr": [[lit("]")], ["Value", "Vals", lit("]")]],
            "Vals": [[comma, "Value", "Vals"], []],
            "Str": [[char_range("a", "z"), "Str"], [q]],
            "Digits": [[char_range("0", "9"), "Digits"], []],
        },
    )
