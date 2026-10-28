"""Test-suite synthesis for a validator, guided by its minimal DFA.

A validator (a regex used to accept or reject user input) is a DFA. Graph
coverage criteria from software testing apply directly to it:

    Positive tests (must be ACCEPTED), one per useful transition p -a-> r:
        access(p) + a + completion(r)
    where access(p) is a shortest string from q0 to p and completion(r) a
    shortest string from r to an accepting state. Together they exercise
    every edge of the automaton that can take part in an accepted string
    (100% transition coverage).

    Boundary negatives (must be REJECTED), one per edge into the dead region:
        access(p) + a           where p is useful but delta(p, a) is not live
    A correct prefix followed by the first character that breaks it, at
    every position where the input can go wrong.

    Substitutions (REJECTED unless the validator really accepts the character): a valid
    string with the character at some position replaced by a likely-wrong one such as
    a space or underscore. This catches validators that are too permissive about
    separators, which boundary negatives (which stop at the bad character) cannot.

    Deletions (REJECTED unless the character was optional): a valid string with one
    required character left out, for validators that are too short-tempered about
    lengths ({2,3} where {3} was meant).

    Truncations (must be REJECTED): access(p) for each useful non-accepting p,
    i.e. inputs that stop too early.

Limits: "any other character" is infinitely many inputs, so boundary negatives
try a few likely offenders (space, _, -, ., @, #) rather than all of them; a
validator that wrongly accepts some other specific character can still escape.

Every case records WHY it exists, so the suite explains itself.
"""

from __future__ import annotations

from collections import deque
from dataclasses import asdict, dataclass

from grammarguard.core.dfa import DFA
from grammarguard.core.nfa import OTHER, other_char


@dataclass(frozen=True)
class TestCase:
    """One generated test: the input, whether a correct validator accepts it, and why."""

    __test__ = False  # stop pytest from trying to collect this class

    text: str
    expected: bool
    kind: str  # "transition" | "boundary" | "substitution" | "deletion" | "truncation"
    reason: str


JUNK = " _-.@#"  # characters that sloppy validators often let through by mistake


def junk_chars(alphabet: frozenset[str], k: int = 3) -> list[str]:
    """Up to k characters outside Sigma worth testing as 'wrong' input (space, _, -, ., @, #)."""
    return [c for c in JUNK if c not in alphabet][:k]


def _access_strings(dfa: DFA, sym) -> dict[int, str]:
    """access(p): a shortest string from q0 to each reachable state (BFS)."""
    access = {dfa.start: ""}
    queue = deque([dfa.start])
    while queue:
        q = queue.popleft()
        for a in dfa.symbols():
            r = dfa.delta[(q, a)]
            if r not in access:
                access[r] = access[q] + sym(a)
                queue.append(r)
    return access


def _completions(dfa: DFA, sym) -> dict[int, str]:
    """completion(r): a shortest string from r to an accepting state, for every live r.

    Backward BFS from F: if delta(p, a) = r and r already has a completion w,
    then a w is a shortest completion for p (when p is first discovered)."""
    reverse: dict[int, list[tuple[int, str]]] = {q: [] for q in dfa.states}
    for (p, a), r in dfa.delta.items():
        reverse[r].append((p, a))
    comp = {q: "" for q in dfa.accept}
    queue = deque(sorted(dfa.accept))
    while queue:
        r = queue.popleft()
        for p, a in sorted(reverse[r], key=lambda e: (e[0], e[1] == OTHER, e[1])):
            if p not in comp:
                comp[p] = sym(a) + comp[r]
                queue.append(p)
    return comp


def generate_tests(dfa: DFA) -> list[TestCase]:
    """Build positives with full transition coverage, boundary negatives and truncations."""
    stand_in = other_char(dfa.alphabet)

    def sym(a: str) -> str:
        """A concrete character for a symbol (OTHER -> a character outside Sigma)."""
        return stand_in if a == OTHER else a

    def show(a: str) -> str:
        return f"a character outside Σ ({stand_in!r})" if a == OTHER else repr(a)

    access = _access_strings(dfa, sym)
    comp = _completions(dfa, sym)
    useful = set(access) & set(comp)
    cases: list[TestCase] = []
    seen: set[str] = set()

    def add(case: TestCase) -> None:
        if case.text not in seen:
            seen.add(case.text)
            cases.append(case)

    for p in sorted(useful):
        for a in dfa.symbols():
            r = dfa.delta[(p, a)]
            if r in useful:
                add(TestCase(access[p] + sym(a) + comp[r], True, "transition", f"covers q{p} --{show(a)}--> q{r}"))
            else:
                # OTHER stands for infinitely many characters; test a few likely offenders, not just one.
                for ch in junk_chars(dfa.alphabet) if a == OTHER else [a]:
                    why = f"valid up to position {len(access[p])}, then {ch!r}" + (" (outside Σ)" if a == OTHER else "")
                    add(TestCase(access[p] + ch, False, "boundary", why))
        if p not in dfa.accept:
            add(TestCase(access[p], False, "truncation", f"stops early in non-accepting q{p}"))
        if comp[p]:  # omit the next required character, keep the rest
            text = access[p] + comp[p][1:]
            add(
                TestCase(text, dfa.accepts(text), "deletion", f"position {len(access[p])}: required character left out")
            )
        if comp[p]:  # swap the next valid character for a likely-wrong one, keep the rest valid
            for ch in junk_chars(dfa.alphabet):
                text = access[p] + ch + comp[p][1:]
                add(
                    TestCase(
                        text,
                        dfa.accepts(text),
                        "substitution",
                        f"position {len(access[p])}: {ch!r} instead of a valid character",
                    )
                )
    return cases


def coverage(dfa: DFA, texts: list[str]) -> dict[str, float | int]:
    """Transition and state coverage achieved by a set of ACCEPTED strings.

    Only useful transitions count (edges that can appear in an accepted run)."""
    access = _access_strings(dfa, lambda a: a)
    comp = _completions(dfa, lambda a: a)
    useful = set(access) & set(comp)
    total = {(p, a) for p in useful for a in dfa.symbols() if dfa.delta[(p, a)] in useful}
    hit_edges, hit_states = set(), set()
    for text in texts:
        if not dfa.accepts(text):
            continue
        q = dfa.start
        hit_states.add(q)
        for ch in text:
            a = ch if ch in dfa.alphabet else OTHER
            hit_edges.add((q, a))
            q = dfa.delta[(q, a)]
            hit_states.add(q)
    return {
        "transitions_total": len(total),
        "transitions_covered": len(hit_edges & total),
        "transition_coverage": len(hit_edges & total) / len(total) if total else 1.0,
        "states_total": len(useful),
        "states_covered": len(hit_states & useful),
    }


def to_pytest(pattern: str, cases: list[TestCase]) -> str:
    """Render the suite as a standalone pytest file that checks a validator
    implemented with Python's re (the user's code, not ours)."""
    rows = "\n".join(f"    ({c.text!r}, {c.expected}),  # {c.kind}: {c.reason}" for c in cases)
    return (
        '"""Generated by GrammarGuard from the minimal DFA of the validator."""\n'
        "import re\n\nimport pytest\n\n"
        f"PATTERN = {pattern!r}\n\nCASES = [\n{rows}\n]\n\n\n"
        '@pytest.mark.parametrize("text,expected", CASES)\n'
        "def test_validator(text, expected):\n"
        "    assert (re.fullmatch(PATTERN, text) is not None) == expected\n"
    )


def to_dicts(cases: list[TestCase]) -> list[dict]:
    """Plain dicts for JSON/CSV export."""
    return [asdict(c) for c in cases]
