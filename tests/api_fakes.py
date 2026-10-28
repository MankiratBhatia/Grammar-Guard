"""A stand-in for decoding.engine.Engine that needs no language model.

It "generates" the k-th shortest accepted string, one character per token, so API behaviour
(SSE protocol, queueing, timeouts, cancellation, batch de-duplication) can be tested quickly and
deterministically. Same interface as Engine: constrained / unconstrained / json, busy, stats, warm_up.
"""

from __future__ import annotations

import threading
import time

from grammarguard.core import counting
from grammarguard.core.pda import JSON_PDA, format_stack
from grammarguard.decoding.generate import Step, Telemetry
from grammarguard.decoding.pda_generate import PDAStep


class FakeEngine:
    """Deterministic fake with one lock, like the real engine."""

    def __init__(self, delay: float = 0.0, json_text: str = '{"a":[1,2]}', explode: bool = False):
        """delay = seconds per token; json_text = what json() emits; explode = raise inside generation."""
        self.delay, self.json_text, self.explode = delay, json_text, explode
        self._lock = threading.Lock()
        self.tokens_emitted = 0
        self.runs_started = 0

    # -- Engine interface
    @property
    def busy(self) -> bool:
        return self._lock.locked()

    def warm_up(self) -> None:
        pass

    def stats(self) -> dict:
        return {
            "model": "fake",
            "loaded": True,
            "busy": self.busy,
            "cached_indexes": 0,
            "index_hits": 0,
            "index_misses": 0,
        }

    def _pace(self, cancel) -> bool:
        """Sleep one 'token time'; True if cancelled."""
        if self.explode:
            raise RuntimeError("boom")
        time.sleep(self.delay)
        return bool(cancel and cancel())

    def constrained(self, dfa, prompt, *, cancel=None, seed=0, **options):
        with self._lock:
            self.runs_started += 1
            strings = counting.shortest_strings(dfa, seed % 7 + 1, include_other=False)
            text = strings[seed % len(strings)] if strings else ""
            q = dfa.start
            for ch in text:
                if self._pace(cancel):
                    return "cancelled"
                q = dfa.step(q, ch)
                self.tokens_emitted += 1
                tel = Telemetry(0.5, 0.5, 1.0, "x", False, 0.4, 3.0, 2.0)
                yield Step(ch, q, 7, tel)
            return "eos"

    def unconstrained(self, prompt, *, cancel=None, max_tokens=3, **options):
        with self._lock:
            for i in range(min(max_tokens, 3)):
                if self._pace(cancel):
                    return "cancelled"
                yield Step("zq"[i % 2], None, 50257, None)
            return "max_tokens"

    def json(self, prompt, *, pda=JSON_PDA, cancel=None, **options):
        with self._lock:
            stack = pda.initial()
            for i, ch in enumerate(self.json_text):
                if self._pace(cancel):
                    return "cancelled"
                stack = pda.step(stack, ch)
                self.tokens_emitted += 1
                yield PDAStep(ch, format_stack(stack), 5, 100, i == 3, 0.6, i % 2 == 0, 0.3)
            return "eos"
