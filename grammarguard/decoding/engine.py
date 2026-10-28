"""Engine: the shared language model plus everything that must live as long as it does.

A web server must not reload GPT-2 per request, and GPT-2 on CPU is not safe to
drive from several threads at once. The Engine therefore owns
    * the model and tokenizer (loaded once, lazily),
    * the vocabulary trie,
    * an LRU cache of token indexes keyed by the DFA's canonical signature,
    * one lock: generations run one at a time, the rest wait their turn,
    * cooperative cancellation: a CancelToken is polled between tokens, so a
      client that disconnects stops its generation after at most one more token.
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from collections.abc import Iterator
from functools import lru_cache
from typing import Any

from grammarguard.core.dfa import DFA
from grammarguard.core.pda import JSON_PDA, PDA
from grammarguard.decoding import generate as gen
from grammarguard.decoding import pda_generate as pgen
from grammarguard.decoding.token_index import LazyTokenIndex, TokenIndex, load_or_build_index, vocab_trie


class CancelToken:
    """A flag that a request handler sets to stop a running generation.

    Calling the token returns True once it has been cancelled, which is exactly
    the `should_stop` callable the generators expect.
    """

    def __init__(self) -> None:
        """Start not cancelled."""
        self._event = threading.Event()

    def cancel(self) -> None:
        """Ask the generation to stop at its next token boundary."""
        self._event.set()

    def __call__(self) -> bool:
        """Has cancel() been called?"""
        return self._event.is_set()


class Engine:
    """Model, tokenizer, vocabulary trie, index cache and the single generation lock."""

    def __init__(self, model_name: str = "gpt2", max_cached_indexes: int = 32):
        """Nothing is loaded until first use (or warm_up())."""
        self.model_name = model_name
        self.max_cached_indexes = max_cached_indexes
        self._lock = threading.Lock()
        self._indexes: OrderedDict[str, TokenIndex | LazyTokenIndex] = OrderedDict()
        self._index_lock = threading.Lock()
        self.index_hits = 0
        self.index_misses = 0

    # -- loading
    def warm_up(self) -> None:
        """Load the model and build the vocabulary trie now (e.g. at server start-up)."""
        tokenizer, _ = gen.load_model(self.model_name)
        vocab_trie(tokenizer)

    @property
    def loaded(self) -> bool:
        """Has the model been loaded in this process?"""
        return gen.load_model.cache_info().currsize > 0

    @property
    def busy(self) -> bool:
        """Is a generation running right now?"""
        return self._lock.locked()

    # -- index cache
    def index_for(self, dfa: DFA) -> TokenIndex | LazyTokenIndex:
        """Token index for `dfa`, from the in-memory LRU cache, disk cache, or freshly built."""
        key = dfa.signature()
        with self._index_lock:
            if key in self._indexes:
                self._indexes.move_to_end(key)
                self.index_hits += 1
                return self._indexes[key]
        tokenizer, _ = gen.load_model(self.model_name)
        index = load_or_build_index(dfa, tokenizer, self.model_name)
        with self._index_lock:
            self.index_misses += 1
            self._indexes[key] = index
            while len(self._indexes) > self.max_cached_indexes:
                self._indexes.popitem(last=False)
        return index

    def stats(self) -> dict[str, Any]:
        """Diagnostics for the health endpoint."""
        return {
            "model": self.model_name,
            "loaded": self.loaded,
            "busy": self.busy,
            "cached_indexes": len(self._indexes),
            "index_hits": self.index_hits,
            "index_misses": self.index_misses,
        }

    # -- generation (each holds the lock for the whole run)
    def constrained(
        self, dfa: DFA, prompt: str, *, cancel: CancelToken | None = None, **options: Any
    ) -> Iterator[gen.Step]:
        """DFA-constrained generation; yields Steps, returns the stop reason."""
        index = self.index_for(dfa)
        with self._lock:
            return (
                yield from gen.generate_constrained(
                    dfa, prompt, model_name=self.model_name, index=index, should_stop=cancel, **options
                )
            )

    def unconstrained(self, prompt: str, *, cancel: CancelToken | None = None, **options: Any) -> Iterator[gen.Step]:
        """Plain generation for comparison."""
        with self._lock:
            return (
                yield from gen.generate_unconstrained(prompt, model_name=self.model_name, should_stop=cancel, **options)
            )

    def json(
        self, prompt: str, *, pda: PDA = JSON_PDA, cancel: CancelToken | None = None, **options: Any
    ) -> Iterator[pgen.PDAStep]:
        """PDA-constrained generation (JSON subset, or a schema PDA)."""
        with self._lock:
            return (
                yield from pgen.generate_json(
                    prompt, pda=pda, model_name=self.model_name, should_stop=cancel, **options
                )
            )


@lru_cache(maxsize=4)
def get_engine(model_name: str = "gpt2") -> Engine:
    """The process-wide Engine for a model name (a tiny registry)."""
    return Engine(model_name)
