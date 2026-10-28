"""Output certificates: a checkable proof that a text belongs to a language.

A certificate records the RUN of the automaton on the text:
    DFA:  the state after each prefix,  q0 -a1-> q1 -a2-> ... -an-> qn
    PDA:  the stack after each prefix.
plus a fingerprint (SHA-256) of the automaton it was produced with, and the
token boundaries of the generation. Checking it needs no language model and no
trust in the generator: the verifier rebuilds the automaton from the constraint
text itself, compares the fingerprint, and replays the run one transition at a time.

    certificate valid     the recorded run really is the automaton's run on the text,
                          and the 'accepted' claim agrees with its last state/stack.
    certificate accepted  the text is in the language.

This is the same idea as a proof-carrying result: a membership proof for a
regular language is just the sequence of states, checkable in linear time.
"""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from typing import Any

from grammarguard.core.dfa import DFA
from grammarguard.core.pda import PDA, format_stack


@dataclass
class Certificate:
    """A recorded run. `trace[i]` is the state (DFA) or stack (PDA, top first) after i characters."""

    kind: str  # "dfa" | "pda"
    constraint: str  # the regex / expression / schema the automaton was built from
    fingerprint: str  # SHA-256 of the automaton (canonical minimal-DFA table / parse table)
    text: str
    trace: list[Any]
    accepted: bool
    token_ends: list[int] = field(default_factory=list)  # character offset where each token ended

    def to_dict(self) -> dict[str, Any]:
        """Plain dict for JSON."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Certificate:
        """Inverse of to_dict (ignores unknown keys)."""
        names = {"kind", "constraint", "fingerprint", "text", "trace", "accepted", "token_ends"}
        return cls(**{k: v for k, v in data.items() if k in names})


@dataclass
class Verification:
    """Verdict of the checker: whether the certificate is truthful, and whether the text is accepted."""

    valid: bool
    accepted: bool
    reason: str


def _token_ends(text: str, tokens: list[str] | None) -> list[int]:
    """Character offsets at which each token ends (the tokens must concatenate to the text)."""
    if tokens is None:
        return []
    if "".join(tokens) != text:
        raise ValueError("tokens do not concatenate to the text")
    ends, pos = [], 0
    for t in tokens:
        pos += len(t)
        ends.append(pos)
    return ends


# ------------------------------------------------------------------ DFA


def dfa_fingerprint(dfa: DFA) -> str:
    """SHA-256 of the DFA's canonical table (a minimal DFA is unique per language)."""
    return hashlib.sha256(dfa.signature().encode()).hexdigest()


def make_dfa_certificate(dfa: DFA, constraint: str, text: str, tokens: list[str] | None = None) -> Certificate:
    """Record the DFA's run on `text`."""
    trace = [dfa.start]
    for ch in text:
        trace.append(dfa.step(trace[-1], ch))
    return Certificate(
        "dfa", constraint, dfa_fingerprint(dfa), text, trace, trace[-1] in dfa.accept, _token_ends(text, tokens)
    )


def verify_dfa_certificate(cert: Certificate, dfa: DFA) -> Verification:
    """Replay the recorded run against a DFA the VERIFIER built from `cert.constraint`."""

    def bad(reason: str) -> Verification:
        return Verification(False, False, reason)

    if cert.kind != "dfa":
        return bad(f"not a DFA certificate (kind={cert.kind!r})")
    if cert.fingerprint != dfa_fingerprint(dfa):
        return bad("fingerprint mismatch: the certificate was made with a different automaton")
    if len(cert.trace) != len(cert.text) + 1:
        return bad(f"trace has {len(cert.trace)} states, expected {len(cert.text) + 1}")
    if cert.trace[0] != dfa.start:
        return bad(f"run starts in q{cert.trace[0]}, the start state is q{dfa.start}")
    for i, ch in enumerate(cert.text):
        expected = dfa.step(cert.trace[i], ch)
        if cert.trace[i + 1] != expected:
            return bad(
                f"step {i + 1}: δ(q{cert.trace[i]}, {ch!r}) = q{expected}, certificate says q{cert.trace[i + 1]}"
            )
    accepted = cert.trace[-1] in dfa.accept
    if cert.accepted != accepted:
        return bad(
            f"final state q{cert.trace[-1]} is {'accepting' if accepted else 'not accepting'}, certificate disagrees"
        )
    if cert.token_ends != sorted(cert.token_ends) or any(not 0 < e <= len(cert.text) for e in cert.token_ends):
        return bad("token boundaries are not increasing offsets inside the text")
    return Verification(True, accepted, f"all {len(cert.text)} transitions check out")


# ------------------------------------------------------------------ PDA


def pda_fingerprint(pda: PDA) -> str:
    """SHA-256 of the PDA's LL(1) parse table (sorted, so it is canonical)."""
    rows = sorted(f"{a}|{c}|{' '.join(map(repr, rhs))}" for (a, c), rhs in pda.table.items())
    return hashlib.sha256(f"{pda.grammar.start}\n".encode() + "\n".join(rows).encode()).hexdigest()


def make_pda_certificate(pda: PDA, constraint: str, text: str, tokens: list[str] | None = None) -> Certificate:
    """Record the PDA's stack after every prefix of `text` (a rejected prefix ends the trace with None)."""
    stack = pda.initial()
    trace: list[Any] = [list(format_stack(stack))]
    accepted = True
    for ch in text:
        nxt = pda.step(stack, ch)
        if nxt is None:
            trace.append(None)
            accepted = False
            break
        stack = nxt
        trace.append(list(format_stack(stack)))
    accepted = accepted and pda.accepts_end(stack)
    return Certificate("pda", constraint, pda_fingerprint(pda), text, trace, accepted, _token_ends(text, tokens))


def verify_pda_certificate(cert: Certificate, pda: PDA) -> Verification:
    """Replay the recorded stacks against a PDA the verifier built from the constraint."""

    def bad(reason: str) -> Verification:
        return Verification(False, False, reason)

    if cert.kind != "pda":
        return bad(f"not a PDA certificate (kind={cert.kind!r})")
    if cert.fingerprint != pda_fingerprint(pda):
        return bad("fingerprint mismatch: the certificate was made with a different grammar")
    stack = pda.initial()
    if not cert.trace or cert.trace[0] != list(format_stack(stack)):
        return bad("initial stack is wrong")
    rejected = False
    for i, ch in enumerate(cert.text):
        if i + 1 >= len(cert.trace):
            return bad("trace is shorter than the text")
        nxt = pda.step(stack, ch)
        if nxt is None:
            if cert.trace[i + 1] is not None:
                return bad(f"step {i + 1}: the PDA rejects {ch!r} but the certificate shows a stack")
            rejected = True
            break
        stack = nxt
        if cert.trace[i + 1] != list(format_stack(stack)):
            return bad(f"step {i + 1}: stack after {ch!r} should be {list(format_stack(stack))}")
    if len(cert.trace) != (i + 2 if cert.text else 1):
        return bad("trace has the wrong length")
    accepted = not rejected and pda.accepts_end(stack)
    if cert.accepted != accepted:
        return bad("the accepted flag disagrees with the replayed run")
    return Verification(True, accepted, f"all {len(cert.text)} moves check out")
