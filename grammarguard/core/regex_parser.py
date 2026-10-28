"""Regex -> abstract syntax tree (AST).

Theory: a regular expression over an alphabet Sigma is built from
    epsilon, single symbols a in Sigma, and the three regular operations
    union (R|S), concatenation (RS) and Kleene star (R*).
Everything else is "syntactic sugar" that we rewrite (desugar) into those
three operations while parsing:
    R+      = R R*
    R?      = R | epsilon
    R{n}    = R R ... R            (n copies, R{0} = epsilon)
    R{n,m}  = R{n} (R?){m-n}       (n mandatory copies, m-n optional ones)
    R{n,}   = R{n} R*
    [a-z0-9] = a set of symbols; one edge per symbol in the automaton

So after parsing, the AST only contains:
    Epsilon, Literal, CharClass, Concat, Union, Star.

Pipeline: string --tokenize()--> tokens --Parser--> AST.

Grammar used by the recursive-descent parser (lowest precedence first):
    regex   := union
    union   := concat ('|' concat)*
    concat  := repeat*                       (empty sequence = epsilon)
    repeat  := atom ('*' | '+' | '?' | '{n}' | '{n,}' | '{n,m}')*
    atom    := CHAR | CLASS | '(' union ')'
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# --------------------------------------------------------------------------
# AST node types
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Epsilon:
    """The regex epsilon: matches only the empty string."""


@dataclass(frozen=True)
class Literal:
    """A single alphabet symbol `char`: matches exactly that one character."""

    char: str


@dataclass(frozen=True)
class CharClass:
    """A finite set of symbols, e.g. [0-9]. Equivalent to the union of its members."""

    chars: frozenset[str]


@dataclass(frozen=True)
class Concat:
    """Concatenation L(left) . L(right)."""

    left: Node
    right: Node


@dataclass(frozen=True)
class Union:
    """Union L(left) U L(right)."""

    left: Node
    right: Node


@dataclass(frozen=True)
class Star:
    """Kleene star L(child)*: zero or more concatenated copies."""

    child: Node


Node = Epsilon | Literal | CharClass | Concat | Union | Star


class RegexSyntaxError(ValueError):
    """Raised when the input is not a regex in our supported syntax."""


# --------------------------------------------------------------------------
# Tokenizer
# --------------------------------------------------------------------------

# Characters with special meaning outside a [...] class.
METACHARS = set("|*+?()[]{}\\")
# `.` and anchors are deliberately NOT supported (see DECISIONS.md), so we
# reject them rather than silently treating them differently from Python's re.
RESERVED = set(".^$")


@dataclass(frozen=True)
class Token:
    """One lexical unit. `kind` is one of:
    CHAR, CLASS, UNION, STAR, PLUS, QMARK, LPAREN, RPAREN, REPEAT.
    `value` holds the char (CHAR), frozenset (CLASS) or (n, m|None) (REPEAT)."""

    kind: str
    value: Any = None
    pos: int = 0


def _parse_class(text: str, i: int) -> tuple[frozenset[str], int]:
    """Lexical analysis of a character class body starting just after '['.

    Expands ranges like a-z into the explicit finite set of symbols they denote
    (a class is just shorthand for a union of single symbols).
    Returns (set_of_chars, index just after the closing ']').
    """
    start = i - 1
    chars: set[str] = set()
    if i < len(text) and text[i] == "^":
        raise RegexSyntaxError(f"negated classes [^...] are not supported (pos {i})")

    def read_one(j: int) -> tuple[str, int]:
        """Read one (possibly backslash-escaped) class member."""
        if text[j] == "\\":
            if j + 1 >= len(text):
                raise RegexSyntaxError("dangling backslash inside class")
            return text[j + 1], j + 2
        return text[j], j + 1

    while True:
        if i >= len(text):
            raise RegexSyntaxError(f"unterminated character class starting at pos {start}")
        if text[i] == "]":
            break
        lo, i = read_one(i)
        # A range a-b, unless '-' is the last char before ']' (then it is literal).
        if i + 1 < len(text) and text[i] == "-" and text[i + 1] != "]":
            hi, i = read_one(i + 1)
            if ord(lo) > ord(hi):
                raise RegexSyntaxError(f"bad range {lo}-{hi} in class at pos {start}")
            chars.update(chr(c) for c in range(ord(lo), ord(hi) + 1))
        else:
            chars.add(lo)
    if not chars:
        raise RegexSyntaxError(f"empty character class at pos {start}")
    return frozenset(chars), i + 1


def _parse_braces(text: str, i: int) -> tuple[tuple[int, int | None], int]:
    """Lexical analysis of a bounded repetition {n}, {n,} or {n,m} after '{'.

    Returns ((n, m), index after '}'), where m is None for the unbounded {n,}.
    """
    start = i - 1
    close = text.find("}", i)
    if close == -1:
        raise RegexSyntaxError(f"unterminated {{ at pos {start}")
    body = text[i:close]
    m: int | None
    parts = body.split(",")
    try:
        if len(parts) == 1:
            n = m = int(parts[0])
        elif len(parts) == 2:
            n = int(parts[0])
            m = int(parts[1]) if parts[1] != "" else None
        else:
            raise ValueError
    except ValueError:
        raise RegexSyntaxError(f"bad repetition {{{body}}} at pos {start}") from None
    if n < 0 or (m is not None and m < n):
        raise RegexSyntaxError(f"bad repetition bounds {{{body}}} at pos {start}")
    return (n, m), close + 1


def tokenize(text: str) -> list[Token]:
    """Lexer: turn the regex string into a flat list of tokens.

    Handles escapes (\\x means the literal symbol x), character classes and
    {n,m} repetition so the parser only has to deal with grammar structure.
    """
    tokens: list[Token] = []
    simple = {"|": "UNION", "*": "STAR", "+": "PLUS", "?": "QMARK", "(": "LPAREN", ")": "RPAREN"}
    i = 0
    while i < len(text):
        c = text[i]
        if c == "\\":
            if i + 1 >= len(text):
                raise RegexSyntaxError("dangling backslash at end of regex")
            tokens.append(Token("CHAR", text[i + 1], i))
            i += 2
        elif c in simple:
            tokens.append(Token(simple[c], None, i))
            i += 1
        elif c == "[":
            chars, j = _parse_class(text, i + 1)
            tokens.append(Token("CLASS", chars, i))
            i = j
        elif c == "{":
            bounds, j = _parse_braces(text, i + 1)
            tokens.append(Token("REPEAT", bounds, i))
            i = j
        elif c in "]}":
            raise RegexSyntaxError(f"unmatched {c!r} at pos {i}")
        elif c in RESERVED:
            raise RegexSyntaxError(f"{c!r} is not supported; escape it as '\\{c}' for a literal (pos {i})")
        else:
            tokens.append(Token("CHAR", c, i))
            i += 1
    return tokens


# --------------------------------------------------------------------------
# Desugaring helpers (smart constructors)
# --------------------------------------------------------------------------


def concat(left: Node, right: Node) -> Node:
    """Concatenation, simplifying with the identity epsilon.R = R.epsilon = R."""
    if isinstance(left, Epsilon):
        return right
    if isinstance(right, Epsilon):
        return left
    return Concat(left, right)


def repeat_exactly(node: Node, n: int) -> Node:
    """Desugar R{n} into R R ... R (n copies); R{0} = epsilon."""
    result: Node = Epsilon()
    for _ in range(n):
        result = concat(result, node)
    return result


def optional(node: Node) -> Node:
    """Desugar R? into R | epsilon."""
    return Union(node, Epsilon())


def plus(node: Node) -> Node:
    """Desugar R+ into R R* (one copy, then zero or more)."""
    return concat(node, Star(node))


def repeat_range(node: Node, n: int, m: int | None) -> Node:
    """Desugar R{n,m} into R{n} (R?){m-n}, and R{n,} into R{n} R*."""
    mandatory = repeat_exactly(node, n)
    if m is None:
        return concat(mandatory, Star(node))
    return concat(mandatory, repeat_exactly(optional(node), m - n))


# --------------------------------------------------------------------------
# Recursive-descent parser
# --------------------------------------------------------------------------


class Parser:
    """Recursive-descent parser: one method per grammar non-terminal.

    Precedence (loosest to tightest): union < concatenation < postfix operators.
    This is encoded by the call chain union -> concat -> repeat -> atom.
    """

    def __init__(self, tokens: list[Token]):
        """Store the token stream and a cursor into it."""
        self.tokens = tokens
        self.pos = 0

    def peek(self) -> Token | None:
        """Look-ahead of one token (the grammar is LL(1))."""
        return self.tokens[self.pos] if self.pos < len(self.tokens) else None

    def advance(self) -> Token:
        """Consume and return the current token."""
        tok = self.tokens[self.pos]
        self.pos += 1
        return tok

    def parse(self) -> Node:
        """regex := union, and the whole input must be consumed."""
        node = self.union()
        tok = self.peek()
        if tok is not None:
            raise RegexSyntaxError(f"unexpected {tok.kind} at pos {tok.pos}")
        return node

    def union(self) -> Node:
        """union := concat ('|' concat)*  -- builds a left-nested Union tree."""
        node = self.concat()
        while (tok := self.peek()) is not None and tok.kind == "UNION":
            self.advance()
            node = Union(node, self.concat())
        return node

    def concat(self) -> Node:
        """concat := repeat*  -- juxtaposition; an empty sequence denotes epsilon."""
        node: Node = Epsilon()
        while (tok := self.peek()) is not None and tok.kind in ("CHAR", "CLASS", "LPAREN"):
            node = concat(node, self.repeat())
        return node

    def repeat(self) -> Node:
        """repeat := atom postfix*  -- applies *, +, ?, {n,m} (desugared on the spot)."""
        node = self.atom()
        while (tok := self.peek()) is not None and tok.kind in ("STAR", "PLUS", "QMARK", "REPEAT"):
            self.advance()
            if tok.kind == "STAR":
                node = Star(node)
            elif tok.kind == "PLUS":
                node = plus(node)
            elif tok.kind == "QMARK":
                node = optional(node)
            else:
                n, m = tok.value
                node = repeat_range(node, n, m)
        return node

    def atom(self) -> Node:
        """atom := CHAR | CLASS | '(' union ')'."""
        tok = self.advance()
        if tok.kind == "CHAR":
            return Literal(tok.value)
        if tok.kind == "CLASS":
            chars = tok.value
            return Literal(next(iter(chars))) if len(chars) == 1 else CharClass(chars)
        # tok.kind == "LPAREN" (guaranteed by the caller's look-ahead check)
        node = self.union()
        close = self.peek()
        if close is None or close.kind != "RPAREN":
            raise RegexSyntaxError(f"missing ')' for '(' at pos {tok.pos}")
        self.advance()
        return node


def parse(text: str) -> Node:
    """Parse a regex string into a desugared AST (only epsilon/symbol/union/concat/star)."""
    tokens = tokenize(text)
    if tokens and tokens[0].kind in ("STAR", "PLUS", "QMARK", "REPEAT"):
        raise RegexSyntaxError("regex cannot start with a repetition operator")
    for prev, cur in zip(tokens, tokens[1:], strict=False):
        if cur.kind in ("STAR", "PLUS", "QMARK", "REPEAT") and prev.kind in ("LPAREN", "UNION"):
            raise RegexSyntaxError(f"nothing to repeat at pos {cur.pos}")
    return Parser(tokens).parse()


def alphabet(node: Node) -> frozenset[str]:
    """Sigma of the regex: every symbol that occurs in the AST."""
    if isinstance(node, Literal):
        return frozenset(node.char)
    if isinstance(node, CharClass):
        return node.chars
    if isinstance(node, (Concat, Union)):
        return alphabet(node.left) | alphabet(node.right)
    if isinstance(node, Star):
        return alphabet(node.child)
    return frozenset()


_CLASS_SPECIAL = set("]\\-^[")


def class_text(chars: frozenset[str]) -> str:
    """Print a character class with ranges, e.g. [0-9a-f_] (three or more consecutive characters become a range)."""
    codes = sorted(ord(c) for c in chars)
    parts: list[str] = []
    i = 0

    def e(c: int) -> str:
        ch = chr(c)
        return "\\" + ch if ch in _CLASS_SPECIAL else ch

    while i < len(codes):
        j = i
        while j + 1 < len(codes) and codes[j + 1] == codes[j] + 1:
            j += 1
        if j - i >= 2:
            parts.append(f"{e(codes[i])}-{e(codes[j])}")
        else:
            parts.extend(e(c) for c in codes[i : j + 1])
        i = j + 1
    return "[" + "".join(parts) + "]"


def to_string(node: Node) -> str:
    """Pretty-print a (desugared) AST back to regex syntax, with brackets only where they are needed."""
    if isinstance(node, Epsilon):
        return "ε"
    if isinstance(node, Literal):
        return node.char
    if isinstance(node, CharClass):
        return class_text(node.chars)
    if isinstance(node, Concat):
        return to_string(node.left) + to_string(node.right)
    if isinstance(node, Union):
        return "(" + to_string(node.left) + "|" + to_string(node.right) + ")"
    text = to_string(node.child)
    if isinstance(
        node.child, (Literal, CharClass, Epsilon, Union)
    ):  # already a single unit (a union prints its own brackets)
        return text + "*"
    return "(" + text + ")*"
