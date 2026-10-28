"""DFA -> regular expression by state elimination (Kleene's theorem, DFA => regex).

Together with Thompson's construction (regex => NFA) and subset construction
(NFA => DFA) this closes the circle: regexes, NFAs and DFAs describe exactly
the same class of languages.

Method (generalised NFA, edges labelled by regexes):
    1. Keep only useful states (reachable AND live); the dead state disappears.
    2. Add a new start s with an epsilon edge to q0, and a new final f with
       epsilon edges from every accepting state.
    3. Repeatedly remove a state q. For every pair p -> q -> r, add
           R(p, r)  :=  R(p, r) | R(p, q) R(q, q)* R(q, r)
    4. When only s and f are left, R(s, f) is the answer.

Edge labels are regex ASTs from regex_parser, combined with simplifying
constructors so the result stays readable, and printed with minimal brackets.
"""

from __future__ import annotations

from grammarguard.core import regex_parser as rp
from grammarguard.core.dfa import DFA
from grammarguard.core.nfa import OTHER


class NotExpressibleError(ValueError):
    """The language needs an 'any other character' edge, which our regex syntax lacks."""


# ------------------------------------------------------------- simplifying constructors


def _chars(node: rp.Node) -> frozenset[str] | None:
    """The symbol set of a single-character regex (Literal / CharClass), else None."""
    if isinstance(node, rp.Literal):
        return frozenset(node.char)
    if isinstance(node, rp.CharClass):
        return node.chars
    return None


def _symbol(chars: frozenset[str]) -> rp.Node:
    """Literal for one character, CharClass for several."""
    return rp.Literal(next(iter(chars))) if len(chars) == 1 else rp.CharClass(chars)


def nullable(node: rp.Node) -> bool:
    """Does L(node) contain the empty string?"""
    if isinstance(node, (rp.Epsilon, rp.Star)):
        return True
    if isinstance(node, rp.Union):
        return nullable(node.left) or nullable(node.right)
    if isinstance(node, rp.Concat):
        return nullable(node.left) and nullable(node.right)
    return False


def _alternatives(node: rp.Node) -> list[rp.Node]:
    """Flatten nested unions into a list of alternatives."""
    if isinstance(node, rp.Union):
        return _alternatives(node.left) + _alternatives(node.right)
    return [node]


def union(a: rp.Node | None, b: rp.Node | None) -> rp.Node | None:
    """R | S, with None meaning the empty language (no edge).

    Simplifications: identical alternatives are dropped; single-character
    alternatives merge into one class ([ab]); epsilon is dropped when another
    alternative is already nullable (R* | epsilon = R*).
    """
    if a is None:
        return b
    if b is None:
        return a
    alts: list[rp.Node] = []
    chars: set[str] = set()
    for alt in _alternatives(a) + _alternatives(b):
        cs = _chars(alt)
        if cs is not None:
            chars |= cs
        elif alt not in alts:
            alts.append(alt)
    if chars:
        alts.insert(0, _symbol(frozenset(chars)))
    if rp.Epsilon() in alts and any(nullable(x) for x in alts if x != rp.Epsilon()):
        alts.remove(rp.Epsilon())
    # keep epsilon last so it prints as R? rather than (|R)
    if rp.Epsilon() in alts:
        alts.remove(rp.Epsilon())
        alts.append(rp.Epsilon())
    result = alts[0]
    for alt in alts[1:]:
        result = rp.Union(result, alt)
    return result


def concat(a: rp.Node | None, b: rp.Node | None) -> rp.Node | None:
    """R S; the empty language annihilates (None), epsilon is the identity."""
    if a is None or b is None:
        return None
    return rp.concat(a, b)


def star(a: rp.Node | None) -> rp.Node:
    """R*, simplified: ()* = eps*, = eps; (R*)* = R*; (R | eps)* = R*."""
    if a is None or isinstance(a, rp.Epsilon):
        return rp.Epsilon()
    if isinstance(a, rp.Star):
        return a
    alts = [x for x in _alternatives(a) if x != rp.Epsilon()]
    inner = alts[0]
    for x in alts[1:]:
        inner = rp.Union(inner, x)
    return rp.Star(inner)


# ------------------------------------------------------------- printer

_SPECIAL = set("|*+?()[]{}\\.^$")
_CLASS_SPECIAL = set("]\\-^[")


def _esc(ch: str) -> str:
    """Escape a character that has a meaning in our regex syntax."""
    return "\\" + ch if ch in _SPECIAL else ch


def _class_text(chars: frozenset[str]) -> str:
    """Print a class with ranges, e.g. [0-9a-f_]."""
    codes = sorted(ord(c) for c in chars)
    parts, i = [], 0
    while i < len(codes):
        j = i
        while j + 1 < len(codes) and codes[j + 1] == codes[j] + 1:
            j += 1

        def e(c: int) -> str:
            ch = chr(c)
            return "\\" + ch if ch in _CLASS_SPECIAL else ch

        if j - i >= 2:
            parts.append(f"{e(codes[i])}-{e(codes[j])}")
        else:
            parts.extend(e(c) for c in codes[i : j + 1])
        i = j + 1
    return "[" + "".join(parts) + "]"


def to_regex(node: rp.Node, prec: int = 0) -> str:
    """Print an AST with as few brackets as precedence allows.

    Precedence: union (0) < concatenation (1) < postfix * + ? (2).
    R R* is printed as R+ and R | epsilon as R?.
    """
    if isinstance(node, rp.Epsilon):
        return "()"
    if isinstance(node, rp.Literal):
        return _esc(node.char)
    if isinstance(node, rp.CharClass):
        return _class_text(node.chars)
    if isinstance(node, rp.Star):
        return _postfix(node.child) + "*"
    if isinstance(node, rp.Union):
        alts = _alternatives(node)
        if alts[-1] == rp.Epsilon():
            rest = alts[0]
            for x in alts[1:-1]:
                rest = rp.Union(rest, x)
            return _postfix(rest) + "?"
        text = "|".join(to_regex(x, 1) for x in alts)
        return f"({text})" if prec > 0 else text
    # Concatenation: flatten, then compress runs. X X X -> X{3}, X X* -> X+, X{n} X* -> X{n,}
    items = _factors(node)
    out, i = [], 0
    while i < len(items):
        x, n = items[i], 1
        while i + n < len(items) and items[i + n] == x:
            n += 1
        i += n
        unbounded = i < len(items) and items[i] == rp.Star(x)
        if unbounded:
            i += 1
        if not unbounded and (n == 1 or (n == 2 and isinstance(x, rp.Literal))):
            out.append(to_regex(x, 1) * n)  # "ll" reads better than "l{2}"
        elif n == 1:
            out.append(_postfix(x) + "+")
        else:
            out.append(_postfix(x) + (f"{{{n},}}" if unbounded else f"{{{n}}}"))
    text = "".join(out)
    return f"({text})" if prec > 1 else text


def _factors(node: rp.Node) -> list[rp.Node]:
    """Flatten nested concatenations into a list of factors."""
    if isinstance(node, rp.Concat):
        return _factors(node.left) + _factors(node.right)
    return [node]


def _postfix(node: rp.Node) -> str:
    """Operand of a postfix operator: single symbols bare, everything else in one pair of brackets."""
    if isinstance(node, (rp.Literal, rp.CharClass)):
        return to_regex(node)
    if isinstance(node, rp.Union) and _alternatives(node)[-1] != rp.Epsilon():
        return to_regex(node, 1)  # already "(a|b)"
    return f"({to_regex(node)})"


# ------------------------------------------------------------- the algorithm


def node_count(node: rp.Node) -> int:
    """Number of AST nodes (a measure of how big a regex is)."""
    if isinstance(node, (rp.Union, rp.Concat)):
        return 1 + node_count(node.left) + node_count(node.right)
    if isinstance(node, rp.Star):
        return 1 + node_count(node.child)
    return 1


def dfa_to_regex_ast(dfa: DFA, max_nodes: int | None = 20_000) -> rp.Node | None:
    """State elimination on the useful part of `dfa`. None means L = empty set.

    The regex can grow exponentially with the number of states, so any edge label
    larger than `max_nodes` AST nodes aborts with NotExpressibleError (None = no limit).
    """
    useful = dfa.reachable_states() & dfa.live_states()
    if dfa.start not in useful:
        return None
    S, F = "s", "f"  # new start and final (strings never clash with int states)
    R: dict[tuple[object, object], rp.Node] = {}

    def add(p: object, r: object, label: rp.Node | None) -> None:
        """R(p, r) := R(p, r) | label."""
        if label is None:
            return
        new = union(R.get((p, r)), label)
        assert new is not None
        if max_nodes is not None and node_count(new) > max_nodes:
            raise NotExpressibleError(f"an equivalent regex would be larger than {max_nodes} nodes")
        R[(p, r)] = new

    for q in useful:
        by_target: dict[int, set[str]] = {}
        for a in dfa.symbols():
            r = dfa.delta[(q, a)]
            if r in useful:
                if a == OTHER:
                    raise NotExpressibleError(
                        "this language accepts characters outside its alphabet; "
                        "our regex syntax has no wildcard to write that"
                    )
                by_target.setdefault(r, set()).add(a)
        for r, chars in by_target.items():
            add(q, r, _symbol(frozenset(chars)))
    add(S, dfa.start, rp.Epsilon())
    for q in dfa.accept & useful:
        add(q, F, rp.Epsilon())

    remaining = set(useful)
    while remaining:
        # Heuristic: eliminate the state with the fewest in x out edges first.
        def cost(q: int) -> int:
            ins = sum(1 for (p, r) in R if r == q and p != q)
            outs = sum(1 for (p, r) in R if p == q and r != q)
            return ins * outs

        q = min(sorted(remaining), key=cost)
        remaining.remove(q)
        loop = star(R.pop((q, q), None))
        ins = [(src, lab) for (src, dst), lab in R.items() if dst == q]
        outs = [(dst, lab) for (src, dst), lab in R.items() if src == q]
        for src, _ in ins:
            del R[(src, q)]
        for dst, _ in outs:
            del R[(q, dst)]
        for src, lin in ins:
            for dst, lout in outs:
                add(src, dst, concat(concat(lin, loop), lout))
    return R.get((S, F))


def dfa_to_regex(dfa: DFA, max_nodes: int | None = 20_000) -> str:
    """The regex for L(dfa) as text in our syntax. The empty language prints as '[]'
    is not valid, so we raise NotExpressibleError for it instead."""
    ast = dfa_to_regex_ast(dfa, max_nodes)
    if ast is None:
        raise NotExpressibleError("the language is empty; our regex syntax has no symbol for ∅")
    return to_regex(ast)
