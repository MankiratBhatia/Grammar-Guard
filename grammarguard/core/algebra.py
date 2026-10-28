"""Constraint algebra: combine regular languages with the closure properties.

Regular languages are closed under union, intersection, complement,
difference, concatenation and star. Each closure proof is a construction,
and here each one is an operator you can type:

    A | B        union            product construction (F = F1 or F2)
    A & B        intersection     product construction (F = F1 and F2)
    A - B        difference       product construction (F = F1 and not F2)
    !A           complement       swap accepting states of the complete DFA
    concat(A, B, ...)             NFA gluing + subset construction
    star(A)                       NFA loop + subset construction

Operands:
    /regex/            a regex in our syntax (write \\/ for a literal slash)
    name               a named constraint (built-ins below, or user-defined)
    any                any ONE character
    length(n) length(n, m) length(n, *)   any string with that many characters
    contains(A)  starts(A)  ends(A)       ANY* A ANY*,  A ANY*,  ANY* A

Grammar (recursive descent, loosest first):
    expr    := term ('|' term)*
    term    := unary (('&' | '-') unary)*
    unary   := '!' unary | primary
    primary := REGEX | NAME | NAME '(' args ')' | '(' expr ')'

Every intermediate DFA is minimized, which keeps products small.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from grammarguard.core import dfa as ops
from grammarguard.core.dfa import DFA
from grammarguard.core.nfa import OTHER
from grammarguard.core.regex_parser import RegexSyntaxError

BUILTINS: dict[str, str] = {
    "digit": "[0-9]",
    "digits": "[0-9]+",
    "lower": "[a-z]+",
    "upper": "[A-Z]+",
    "word": "[a-zA-Z]+",
    "alnum": "[a-zA-Z0-9]+",
    "integer": "-?(0|[1-9][0-9]*)",
    "decimal": "-?(0|[1-9][0-9]*)(\\.[0-9]+)?",
    "iso_date": "[0-9]{4}-(0[1-9]|1[0-2])-(0[1-9]|[12][0-9]|3[01])",
    "time24": "([01][0-9]|2[0-3]):[0-5][0-9]",
    "email": "[a-z0-9._]+@[a-z0-9]+(\\.[a-z0-9]+)*\\.[a-z]{2,6}",
    "us_phone": "\\([0-9]{3}\\) [0-9]{3}-[0-9]{4}",
    "intl_phone": "\\+[0-9]{1,3} [0-9]{4,12}",
    "zip_us": "[0-9]{5}(-[0-9]{4})?",
    "hex_color": "#[0-9a-f]{6}",
    "uuid": "[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
    "ipv4_shape": "[0-9]{1,3}\\.[0-9]{1,3}\\.[0-9]{1,3}\\.[0-9]{1,3}",
    "username": "[a-z][a-z0-9_]{2,15}",
    "slug": "[a-z0-9]+(-[a-z0-9]+)*",
    "semver": "(0|[1-9][0-9]*)\\.(0|[1-9][0-9]*)\\.(0|[1-9][0-9]*)",
}


class AlgebraError(ValueError):
    """Syntax or evaluation error in a constraint expression, with a position."""

    def __init__(self, message: str, pos: int = 0):
        """Remember where in the expression the problem is."""
        super().__init__(f"{message} (at position {pos})")
        self.pos = pos


# ------------------------------------------------------------- primitive automata


def _make(alphabet, delta, start, accept) -> DFA:
    """Build, renumber and minimize a hand-written DFA."""
    states = sorted({q for q, _ in delta})
    dfa = DFA(states, start, frozenset(accept), delta, frozenset(alphabet), None, {q: str(q) for q in states})
    return ops.minimize(dfa)


def any_star() -> DFA:
    """ANY*: one accepting state looping on every character (Sigma is empty, so only OTHER)."""
    return _make((), {(0, OTHER): 0}, 0, {0})


def length_dfa(n: int, m: int | None) -> DFA:
    """Strings of length n..m (m None = unbounded): a counter automaton."""
    if m is None:
        delta = {(i, OTHER): min(i + 1, n) for i in range(n + 1)}
        return _make((), delta, 0, {n})
    dead = m + 1
    delta = {(i, OTHER): i + 1 for i in range(m + 1)}
    delta[(dead, OTHER)] = dead
    return _make((), delta, 0, set(range(n, m + 1)))


# ------------------------------------------------------------- evaluation result


@dataclass
class Step:
    """One evaluated sub-expression, for the 'explain' view."""

    expr: str
    operation: str
    states: int


@dataclass
class Result:
    """The minimal DFA of the whole expression and how it was built."""

    dfa: DFA
    steps: list[Step] = field(default_factory=list)


# ------------------------------------------------------------- parser + evaluator


class _Parser:
    """Recursive-descent parser that evaluates as it parses (each rule returns a DFA)."""

    def __init__(self, text: str, names: dict[str, str], max_states: int, regex_limit: int | None = None):
        """Expression text, the name -> regex library, and a state cap."""
        self.text, self.pos, self.names, self.max_states = text, 0, names, max_states
        self.regex_limit = regex_limit or max_states * 10
        self.steps: list[Step] = []

    # -- lexical helpers
    def skip(self) -> None:
        """Skip whitespace."""
        while self.pos < len(self.text) and self.text[self.pos].isspace():
            self.pos += 1

    def peek(self) -> str:
        """Next non-space character, or '' at the end."""
        self.skip()
        return self.text[self.pos] if self.pos < len(self.text) else ""

    def expect(self, ch: str) -> None:
        """Consume ch or fail."""
        if self.peek() != ch:
            raise AlgebraError(f"expected {ch!r}", self.pos)
        self.pos += 1

    def ident(self) -> str:
        """NAME := [A-Za-z_][A-Za-z0-9_]*"""
        start = self.pos
        while self.pos < len(self.text) and (self.text[self.pos].isalnum() or self.text[self.pos] == "_"):
            self.pos += 1
        return self.text[start : self.pos]

    def number(self) -> int | None:
        """A non-negative integer, or '*' (returned as None) for 'unbounded'."""
        if self.peek() == "*":
            self.pos += 1
            return None
        start = self.pos
        while self.pos < len(self.text) and self.text[self.pos].isdigit():
            self.pos += 1
        if start == self.pos:
            raise AlgebraError("expected a number", start)
        return int(self.text[start : self.pos])

    def product(self, left: DFA, right: DFA, mode: str, start: int) -> DFA:
        """Product construction with a cap on the intermediate size (before minimization)."""
        try:
            return ops.product(left, right, mode, limit=self.max_states * 20)
        except ops.StateLimitError as e:
            raise AlgebraError(str(e), start) from None

    def record(self, start: int, operation: str, dfa: DFA) -> DFA:
        """Log a step and enforce the state cap."""
        if dfa.num_states() > self.max_states:
            raise AlgebraError(f"automaton too large ({dfa.num_states()} > {self.max_states} states)", start)
        self.steps.append(Step(self.text[start : self.pos].strip(), operation, dfa.num_states()))
        return dfa

    # -- grammar rules
    def parse(self) -> DFA:
        """Whole input must be one expression."""
        result = self.expr()
        if self.peek():
            raise AlgebraError(f"unexpected {self.peek()!r}", self.pos)
        return result

    def expr(self) -> DFA:
        """expr := term ('|' term)*"""
        start = self.pos
        left = self.term()
        while self.peek() == "|":
            self.pos += 1
            left = self.record(start, "union (product)", ops.minimize(self.product(left, self.term(), "union", start)))
        return left

    def term(self) -> DFA:
        """term := unary (('&' | '-') unary)*"""
        start = self.pos
        left = self.unary()
        while self.peek() in ("&", "-"):
            op = self.text[self.pos]
            self.pos += 1
            right = self.unary()
            mode, label = (
                ("intersection", "intersection (product)") if op == "&" else ("difference", "difference (product)")
            )
            left = self.record(start, label, ops.minimize(self.product(left, right, mode, start)))
        return left

    def unary(self) -> DFA:
        """unary := '!' unary | primary"""
        start = self.pos
        if self.peek() == "!":
            self.pos += 1
            return self.record(start, "complement", ops.minimize(ops.complement(self.unary())))
        return self.primary()

    def primary(self) -> DFA:
        """primary := REGEX | NAME | NAME '(' args ')' | '(' expr ')'"""
        c = self.peek()
        start = self.pos
        if c == "(":
            self.pos += 1
            inner = self.expr()
            self.expect(")")
            return inner
        if c == "/":
            return self.record(start, "regex → ε-NFA → DFA → min-DFA", self.regex_literal())
        if c.isalpha() or c == "_":
            name = self.ident()
            if self.peek() == "(":
                return self.call(name, start)
            return self.record(start, f"named constraint {name}", self.lookup(name, start))
        raise AlgebraError(
            "expected a regex /.../, a name, '!' or '('" if c else "unexpected end of expression", self.pos
        )

    def regex_literal(self) -> DFA:
        """REGEX := '/' ... '/' with \\/ for a literal slash (passed on to the regex parser)."""
        start = self.pos
        self.pos += 1
        i = self.pos
        while i < len(self.text) and self.text[i] != "/":
            i += 2 if self.text[i] == "\\" else 1
        if i >= len(self.text):
            raise AlgebraError("unterminated regex literal", start)
        body = self.text[self.pos : i]
        self.pos = i + 1
        try:
            return ops.regex_to_dfa(body, max_states=self.regex_limit)
        except RegexSyntaxError as e:
            raise AlgebraError(f"bad regex /{body}/: {e}", start) from None
        except ops.StateLimitError as e:
            raise AlgebraError(f"regex /{body}/ is too complex: {e}", start) from None

    def lookup(self, name: str, pos: int) -> DFA:
        """A named constraint, or the special name 'any' (one character)."""
        if name == "any":
            return length_dfa(1, 1)
        if name not in self.names:
            raise AlgebraError(f"unknown name {name!r}", pos)
        try:
            return ops.regex_to_dfa(self.names[name], max_states=self.regex_limit)
        except RegexSyntaxError as e:
            raise AlgebraError(f"bad regex for {name!r}: {e}", pos) from None
        except ops.StateLimitError as e:
            raise AlgebraError(f"regex for {name!r} is too complex: {e}", pos) from None

    def args(self) -> list[DFA]:
        """'(' expr (',' expr)* ')'"""
        self.expect("(")
        out = [self.expr()]
        while self.peek() == ",":
            self.pos += 1
            out.append(self.expr())
        self.expect(")")
        return out

    def call(self, name: str, start: int) -> DFA:
        """Built-in functions: length, contains, starts, ends, concat, star."""
        if name == "length":
            self.expect("(")
            n = self.number()
            m: int | None = n
            if self.peek() == ",":
                self.pos += 1
                m = self.number()
            self.expect(")")
            if n is None or (m is not None and m < n):
                raise AlgebraError("length(n, m) needs 0 <= n <= m", start)
            return self.record(start, "length counter automaton", length_dfa(n, m))
        arity = {"contains": 1, "starts": 1, "ends": 1, "star": 1, "concat": None}
        if name not in arity:
            raise AlgebraError(f"unknown function {name!r}", start)
        args = self.args()
        if arity[name] is not None and len(args) != arity[name]:
            raise AlgebraError(f"{name}() takes {arity[name]} argument", start)
        if name == "star":
            return self.record(start, "Kleene star (NFA)", ops.star(args[0]))
        if name == "concat":
            result = args[0]
            for nxt in args[1:]:
                result = ops.concat(result, nxt)
            return self.record(start, "concatenation (NFA)", result)
        anything = any_star()
        parts = {"contains": [anything, args[0], anything], "starts": [args[0], anything], "ends": [anything, args[0]]}
        result = parts[name][0]
        for nxt in parts[name][1:]:
            result = ops.concat(result, nxt)
        return self.record(start, f"{name}: concatenation with ANY*", result)


def evaluate(text: str, names: dict[str, str] | None = None, max_states: int = 2000) -> Result:
    """Compile a constraint expression to its minimal DFA.

    `names` adds user-defined constraints (name -> regex) on top of BUILTINS.
    """
    library = {**BUILTINS, **(names or {})}
    parser = _Parser(text, library, max_states)
    dfa = parser.parse()
    return Result(dfa, parser.steps)


def compile_constraint(text: str, names: dict[str, str] | None = None) -> DFA:
    """Convenience: just the minimal DFA of an expression."""
    return evaluate(text, names).dfa
