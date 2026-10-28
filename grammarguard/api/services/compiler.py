"""Turn request constraints into automata (with limits and a cache) and automata into JSON views."""

from __future__ import annotations

import json
import threading
from collections import OrderedDict
from dataclasses import dataclass, field

from grammarguard.api.errors import ApiError, LimitError
from grammarguard.api.schemas import AutomatonView, Constraint, Transition
from grammarguard.api.settings import Settings
from grammarguard.core import algebra, regex_parser
from grammarguard.core.dfa import DFA, minimize, regex_to_dfa, subset_construction
from grammarguard.core.dot import compress_symbols, dfa_to_dot, nfa_to_dot
from grammarguard.core.nfa import NFA, OTHER, regex_to_nfa


def as_expression(constraint: Constraint) -> str:
    """The constraint as one algebra expression (a regex becomes /regex/). Certificates store this,
    so a verifier can rebuild the automaton from the text alone."""
    if constraint.kind == "expression":
        return constraint.text
    return "/" + constraint.text.replace("/", "\\/") + "/"


@dataclass
class Compiled:
    """A compiled constraint: its minimal DFA and how the algebra built it."""

    dfa: DFA
    steps: list[algebra.Step] = field(default_factory=list)


@dataclass
class Pipeline:
    """regex -> AST -> ε-NFA -> DFA -> minimal DFA, all kept for display."""

    ast: regex_parser.Node
    nfa: NFA
    dfa: DFA
    min_dfa: DFA


class CompileService:
    """Compiles constraints under the configured limits; results are kept in an LRU cache."""

    def __init__(self, settings: Settings):
        """Empty caches."""
        self.settings = settings
        self._lock = threading.Lock()
        self._compiled: OrderedDict[tuple, Compiled] = OrderedDict()
        self._pipelines: OrderedDict[str, Pipeline] = OrderedDict()

    # -- limits
    def check_regex(self, text: str) -> None:
        """Reject over-long regexes."""
        if len(text) > self.settings.max_regex_len:
            raise LimitError(f"regex is longer than {self.settings.max_regex_len} characters")

    def check_text(self, text: str, what: str = "text") -> None:
        """Reject over-long input strings."""
        if len(text) > self.settings.max_text_len:
            raise LimitError(f"{what} is longer than {self.settings.max_text_len} characters")

    def check_prompt(self, text: str) -> None:
        """Reject over-long prompts."""
        if len(text) > self.settings.max_prompt_len:
            raise LimitError(f"prompt is longer than {self.settings.max_prompt_len} characters")

    def _remember(self, cache: OrderedDict, key, value) -> None:
        """Insert into an LRU cache, evicting the oldest entries."""
        with self._lock:
            cache[key] = value
            cache.move_to_end(key)
            while len(cache) > self.settings.compile_cache_size:
                cache.popitem(last=False)

    def _recall(self, cache: OrderedDict, key):
        """LRU lookup."""
        with self._lock:
            if key in cache:
                cache.move_to_end(key)
                return cache[key]
        return None

    # -- compiling
    def pipeline(self, regex: str) -> Pipeline:
        """Every stage of the pipeline for one regex (cached)."""
        self.check_regex(regex)
        hit = self._recall(self._pipelines, regex)
        if hit:
            return hit
        ast = regex_parser.parse(regex)
        nfa = regex_to_nfa(regex)
        dfa = subset_construction(nfa, max_states=self.settings.max_dfa_states)
        min_dfa = minimize(dfa)
        result = Pipeline(ast, nfa, dfa, min_dfa)
        self._remember(self._pipelines, regex, result)
        return result

    def resolve(self, constraint: Constraint) -> Compiled:
        """Minimal DFA of a regex or expression constraint (cached)."""
        key = (constraint.kind, constraint.text, tuple(sorted(constraint.names.items())))
        hit = self._recall(self._compiled, key)
        if hit:
            return hit
        if constraint.kind == "regex":
            self.check_regex(constraint.text)
            compiled = Compiled(regex_to_dfa(constraint.text, max_states=self.settings.max_dfa_states))
        else:
            if not constraint.text.strip():
                raise ApiError("algebra", "the expression is empty", position=0)
            if len(constraint.text) > self.settings.max_expression_len:
                raise LimitError(f"expression is longer than {self.settings.max_expression_len} characters")
            result = algebra.evaluate(constraint.text, constraint.names, max_states=self.settings.max_states)
            compiled = Compiled(result.dfa, result.steps)
        if compiled.dfa.num_states() > self.settings.max_states:
            raise LimitError(f"the automaton has {compiled.dfa.num_states()} states (limit {self.settings.max_states})")
        self._remember(self._compiled, key, compiled)
        return compiled

    def dfa_for_expression(self, expression: str, names: dict[str, str]) -> DFA:
        """Rebuild a DFA from a certificate's stored expression."""
        return self.resolve(Constraint(kind="expression", text=expression, names=names)).dfa

    # -- views
    def dfa_view(self, dfa: DFA, *, labels: bool = False, highlight: int | None = None) -> AutomatonView:
        """JSON-friendly description of a DFA (drawing omitted if it is too big)."""
        grouped: dict[tuple[int, int], set[str]] = {}
        for (src, sym), dst in dfa.delta.items():
            if dfa.dead is not None and dfa.dead in (src, dst):
                continue
            grouped.setdefault((src, dst), set()).add(sym)
        transitions = [
            Transition(source=s, target=t, label=compress_symbols(syms)) for (s, t), syms in sorted(grouped.items())
        ]
        small = dfa.num_states() <= self.settings.max_dot_states
        return AutomatonView(
            states=dfa.num_states(),
            live_states=len(dfa.live_states()),
            start=dfa.start,
            accepting=sorted(dfa.accept),
            dot=dfa_to_dot(dfa, use_labels=labels, highlight=highlight) if small else None,
            transitions=transitions,
            state_labels=dict(dfa.labels) if labels else {},
        )

    def nfa_view(self, nfa: NFA) -> AutomatonView:
        """JSON-friendly description of an ε-NFA."""
        grouped: dict[tuple[int, int], set[str | None]] = {}
        for src, sym, dst in nfa.edges():
            grouped.setdefault((src, dst), set()).add(sym)
        transitions = []
        for (s, t), syms in sorted(grouped.items()):
            label = compress_symbols(x for x in syms if x is not None and x != OTHER)
            if None in syms:
                label = f"{label},ε" if label else "ε"
            transitions.append(Transition(source=s, target=t, label=label))
        small = len(nfa.states) <= self.settings.max_dot_states
        return AutomatonView(
            states=len(nfa.states),
            start=nfa.start,
            accepting=sorted(nfa.accept),
            dot=nfa_to_dot(nfa) if small else None,
            transitions=transitions,
        )


def schema_key(schema: dict | None) -> str:
    """Certificate constraint string for a PDA: 'json' or 'schema:<canonical JSON>'."""
    return "json" if schema is None else "schema:" + json.dumps(schema, sort_keys=True, separators=(",", ":"))
