"""Token index: which LLM tokens may be emitted from each DFA state.

Theory: a language model emits TOKENS (multi-character strings), but the DFA
reads CHARACTERS. To bridge the two we run the DFA over each token's
characters: delta*(q, token_text). A token t is allowed in state q iff the DFA
ends in a LIVE state, i.e. the output so far can still be completed to a
string of the language. Tokens that end in the dead state, or in any state
from which no accepting state is reachable, are forbidden. This guarantees
the generation can never get stuck, and when it stops in an accepting state
the whole output is in L(M).

The index is computed once per (DFA, tokenizer) and cached on disk.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path

from grammarguard.core.dfa import DFA

CACHE_DIR = Path(os.environ.get("GRAMMARGUARD_CACHE_DIR", Path.home() / ".cache" / "grammarguard"))


def _bytes_to_unicode() -> dict[int, str]:
    """GPT-2's reversible byte -> printable-unicode map (used by its BPE vocab).

    The vocabulary stores tokens in this disguised form (e.g. a space byte is
    shown as 'Ġ'); to get the REAL text of a token we invert this map.
    """
    keep = list(range(ord("!"), ord("~") + 1)) + list(range(0xA1, 0xAD)) + list(range(0xAE, 0x100))
    mapping, extra = {}, 0
    for b in range(256):
        if b in keep:
            mapping[b] = chr(b)
        else:
            mapping[b] = chr(256 + extra)
            extra += 1
    return mapping


def vocab_strings(tokenizer) -> list[str | None]:
    """Decode every vocab id to its exact string, once.

    Index = token id. Byte-level BPE tokens can be fragments of a multi-byte
    UTF-8 character; such a token has no standalone text, so we map it to None
    (it can never be allowed because the regex alphabets are text). Special
    tokens such as <|endoftext|> also map to None: EOS is handled separately.
    """
    unicode_to_byte = {c: b for b, c in _bytes_to_unicode().items()}
    special = set(tokenizer.all_special_ids)
    out: list[str | None] = [None] * len(tokenizer)
    for token, tid in tokenizer.get_vocab().items():
        if tid in special:
            continue
        try:
            out[tid] = bytes(unicode_to_byte[c] for c in token).decode("utf-8")
        except (KeyError, UnicodeDecodeError):
            out[tid] = None
    return out


@dataclass
class TokenIndex:
    """allowed[q] = token ids legal in live state q; next_state[q][t] = delta*(q, text(t))."""

    allowed: dict[int, list[int]]
    next_state: dict[int, dict[int, int]]

    def allowed_for(self, q: int) -> list[int]:
        """Token ids legal in state q (empty for non-live states)."""
        return self.allowed.get(q, [])

    def next_for(self, q: int, tid: int) -> int:
        """delta*(q, text(tid)) for a token that is allowed in q."""
        return self.next_state[q][tid]

    def num_allowed(self, q: int) -> int:
        """How many vocabulary tokens are legal in state q (0 for non-live states)."""
        return len(self.allowed_for(q))


def build_index_loop(dfa: DFA, strings: list[str | None]) -> TokenIndex:
    """Reference implementation: for each live state q and token t, run the DFA over t.

    Cost: |live states| x |vocab| x |token length|. Kept as the obviously-correct
    version that the trie index is tested against.
    """
    live = dfa.live_states()
    allowed: dict[int, list[int]] = {}
    next_state: dict[int, dict[int, int]] = {}
    for q in sorted(live):
        allowed[q] = []
        next_state[q] = {}
        for tid, text in enumerate(strings):
            if not text:  # None (special/partial bytes) or the empty string
                continue
            r = dfa.run(text, q)
            if r in live:
                allowed[q].append(tid)
                next_state[q][tid] = r
    return TokenIndex(allowed, next_state)


class VocabTrie:
    """A character trie of the vocabulary: every token is a path from the root.

    children[n] maps a character to the child node; tokens_at[n] lists the token
    ids whose text ends exactly at node n. Tokens that share a prefix ("20",
    "201", "2019") share the path, so a walk reads each shared prefix once.
    """

    def __init__(self, strings: list[str | None]):
        """Insert every non-empty token text."""
        self.children: list[dict[str, int]] = [{}]
        self.tokens_at: list[list[int]] = [[]]
        for tid, text in enumerate(strings):
            if not text:
                continue
            node = 0
            for ch in text:
                nxt = self.children[node].get(ch)
                if nxt is None:
                    nxt = len(self.children)
                    self.children[node][ch] = nxt
                    self.children.append({})
                    self.tokens_at.append([])
                node = nxt
            self.tokens_at[node].append(tid)

    def __len__(self) -> int:
        """Number of trie nodes."""
        return len(self.children)


def _walk(dfa: DFA, trie: VocabTrie, live: frozenset[int], q: int) -> dict[int, int]:
    """Allowed tokens of ONE state: {token id: next state}, by a pruned DFS over trie x DFA.

    Reading a character moves both the trie node and the DFA state; if the DFA
    state is not live the whole subtree below is pruned, since a non-live state
    can only lead to non-live states -- every token in that subtree would be
    rejected. Tokens ending at a visited node are allowed.
    """
    found: dict[int, int] = {}
    stack = [(0, q)]
    while stack:
        node, state = stack.pop()
        for ch, child in trie.children[node].items():
            r = dfa.step(state, ch)
            if r not in live:
                continue  # prune the whole subtree
            for tid in trie.tokens_at[child]:
                found[tid] = r
            stack.append((child, r))
    return found


def build_index_trie(dfa: DFA, trie: VocabTrie) -> TokenIndex:
    """Eager index: walk the trie once for every live state.

    Same output as build_index_loop; much less work when the language is
    restrictive, because most of the trie is pruned after one or two characters.
    """
    live = dfa.live_states()
    allowed: dict[int, list[int]] = {}
    next_state: dict[int, dict[int, int]] = {}
    for q in sorted(live):
        found = _walk(dfa, trie, live, q)
        allowed[q] = sorted(found)
        next_state[q] = found
    return TokenIndex(allowed, next_state)


class LazyTokenIndex:
    """Token index that fills in a state's row the first time generation reaches it.

    Generation visits only a handful of the DFA's states (one per token), while
    a policy such as `email & !contains(..) & length(5,40)` has over a thousand.
    Walking the trie for all of them took ~19 s; for the few visited it takes
    milliseconds. Rows are memoised, so each state costs one walk at most.
    """

    def __init__(self, dfa: DFA, trie: VocabTrie):
        """Remember the automaton and the vocabulary trie; compute nothing yet."""
        self.dfa, self.trie, self.live = dfa, trie, dfa.live_states()
        self._rows: dict[int, tuple[list[int], dict[int, int]]] = {}

    def _row(self, q: int) -> tuple[list[int], dict[int, int]]:
        """(sorted allowed ids, next-state map) for q, computed on first use."""
        if q not in self._rows:
            found = _walk(self.dfa, self.trie, self.live, q) if q in self.live else {}
            self._rows[q] = (sorted(found), found)
        return self._rows[q]

    def allowed_for(self, q: int) -> list[int]:
        """Token ids legal in state q."""
        return self._row(q)[0]

    def next_for(self, q: int, tid: int) -> int:
        """delta*(q, text(tid))."""
        return self._row(q)[1][tid]

    def num_allowed(self, q: int) -> int:
        """How many tokens are legal in q."""
        return len(self.allowed_for(q))

    @property
    def rows_computed(self) -> int:
        """How many states have been expanded so far (for diagnostics)."""
        return len(self._rows)


_TRIES: dict[str, tuple[list[str | None], VocabTrie]] = {}


def vocab_trie(tokenizer) -> tuple[list[str | None], VocabTrie]:
    """(token texts, trie) for a tokenizer, built once per process."""
    key = getattr(tokenizer, "name_or_path", "") or str(id(tokenizer))
    if key not in _TRIES:
        strings = vocab_strings(tokenizer)
        _TRIES[key] = (strings, VocabTrie(strings))
    return _TRIES[key]


def build_index(dfa: DFA, strings: list[str | None], trie: VocabTrie | None = None) -> TokenIndex:
    """Token index for a DFA (trie-based; builds the trie from `strings` if none is given)."""
    return build_index_trie(dfa, trie or VocabTrie(strings))


def cache_key(dfa: DFA, model_name: str) -> str:
    """Hash of the minimized DFA's canonical table + tokenizer name.

    The minimal DFA is unique per language, so equivalent regexes share a key.
    """
    return hashlib.sha256((model_name + "\n" + dfa.signature()).encode()).hexdigest()[:24]


EAGER_LIMIT = 64  # DFAs with more live states than this get a lazy, in-memory index


def load_or_build_index(
    dfa: DFA, tokenizer, model_name: str = "gpt2", cache_dir: Path | None = None
) -> TokenIndex | LazyTokenIndex:
    """Return the token index for a DFA.

    Small DFAs (<= EAGER_LIMIT live states): full index, read from / written to
    the disk cache. Large ones: a LazyTokenIndex (rows computed on demand).
    """
    strings, trie = vocab_trie(tokenizer)
    if len(dfa.live_states()) > EAGER_LIMIT:
        return LazyTokenIndex(dfa, trie)
    cache_dir = cache_dir or CACHE_DIR
    path = cache_dir / f"token_index_{cache_key(dfa, model_name)}.json"
    if path.exists():
        raw = json.loads(path.read_text())
        allowed = {int(q): ids for q, ids in raw["allowed"].items()}
        nxt = {int(q): dict(zip(allowed[int(q)], states, strict=True)) for q, states in raw["next"].items()}
        return TokenIndex(allowed, nxt)
    index = build_index_trie(dfa, trie)
    cache_dir.mkdir(parents=True, exist_ok=True)
    raw = {
        "allowed": index.allowed,
        "next": {q: [index.next_state[q][t] for t in ids] for q, ids in index.allowed.items()},
    }
    path.write_text(json.dumps(raw))
    return index
