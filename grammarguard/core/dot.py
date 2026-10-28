"""Graphviz DOT text for NFAs and DFAs (written by hand, no graphviz library).

A DOT file is just a list of nodes and labelled edges. Conventions used here
are the textbook ones: accepting states are double circles, and the start
state is marked by an arrow coming from an invisible node.
"""

from __future__ import annotations

from grammarguard.core.dfa import DFA
from grammarguard.core.nfa import NFA


def _esc(text: str) -> str:
    """Escape a string for use inside a double-quoted DOT label."""
    return text.replace("\\", "\\\\").replace('"', '\\"')


def _show(ch: str) -> str:
    """Printable form of one symbol (spaces and control characters made visible)."""
    names = {" ": "␣", "\n": "\\n", "\t": "\\t"}
    return names.get(ch, ch)


def compress_symbols(symbols) -> str:
    """Summarise a set of symbols as a label, merging runs: {0,1,2,3,a} -> '0-3,a'.

    Several parallel edges between the same two states are drawn as one edge.
    """
    codes = sorted({ord(c) for c in symbols})
    parts, i = [], 0
    while i < len(codes):
        j = i
        while j + 1 < len(codes) and codes[j + 1] == codes[j] + 1:
            j += 1
        if j - i >= 2:
            parts.append(f"{_show(chr(codes[i]))}-{_show(chr(codes[j]))}")
        else:
            parts.extend(_show(chr(c)) for c in codes[i : j + 1])
        i = j + 1
    return ",".join(parts)


def nfa_to_dot(nfa: NFA) -> str:
    """DOT for an epsilon-NFA; epsilon edges are labelled ε."""
    lines = ["digraph NFA {", "  rankdir=LR;", "  node [shape=circle, fontsize=11];", "  __start [shape=point];"]
    for q in sorted(nfa.states):
        shape = "doublecircle" if q in nfa.accept else "circle"
        lines.append(f'  q{q} [label="{q}", shape={shape}];')
    lines.append(f"  __start -> q{nfa.start};")
    grouped: dict[tuple[int, int], set[str | None]] = {}
    for src, sym, dst in nfa.edges():
        grouped.setdefault((src, dst), set()).add(sym)
    for (src, dst), syms in sorted(grouped.items()):
        label = compress_symbols(s for s in syms if s is not None)
        if None in syms:
            label = f"{label},ε" if label else "ε"
        lines.append(f'  q{src} -> q{dst} [label="{_esc(label)}"];')
    lines.append("}")
    return "\n".join(lines)


def dfa_to_dot(dfa: DFA, show_dead: bool = False, highlight: int | None = None, use_labels: bool = False) -> str:
    """DOT for a DFA.

    The dead state and every edge into it are hidden by default (they would
    clutter the picture); every missing edge implicitly goes to the dead state.
    `highlight` colours the current state during generation. With
    `use_labels` nodes show their subset (e.g. {0,1,4}) instead of a number.
    """
    lines = ["digraph DFA {", "  rankdir=LR;", "  node [shape=circle, fontsize=11];", "  __start [shape=point];"]
    live = dfa.live_states()
    for q in dfa.states:
        if q == dfa.dead and not show_dead:
            continue
        shape = "doublecircle" if q in dfa.accept else "circle"
        label = dfa.labels.get(q, str(q)) if use_labels else str(q)
        style = ', style=filled, fillcolor="#ffd966"' if q == highlight else ("" if q in live else ", style=dashed")
        lines.append(f'  q{q} [label="{_esc(label)}", shape={shape}{style}];')
    lines.append(f"  __start -> q{dfa.start};")
    grouped: dict[tuple[int, int], set[str]] = {}
    for (src, sym), dst in dfa.delta.items():
        if not show_dead and dfa.dead in (src, dst):
            continue
        grouped.setdefault((src, dst), set()).add(sym)
    for (src, dst), syms in sorted(grouped.items()):
        lines.append(f'  q{src} -> q{dst} [label="{_esc(compress_symbols(syms))}"];')
    lines.append("}")
    return "\n".join(lines)
