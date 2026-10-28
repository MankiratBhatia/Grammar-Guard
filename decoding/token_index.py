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
from dataclasses import dataclass
from pathlib import Path

from core.dfa import DFA

CACHE_DIR = Path(__file__).resolve().parent.parent / ".cache"


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

    def num_allowed(self, q: int) -> int:
        """How many vocabulary tokens are legal in state q (0 for non-live states)."""
        return len(self.allowed.get(q, ()))


def build_index(dfa: DFA, strings: list[str | None]) -> TokenIndex:
    """For each live state q and token t: run the DFA over t; keep t if it ends live.

    Cost: |live states| x |vocab| x |token length|, paid once and then cached.
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


def cache_key(dfa: DFA, model_name: str) -> str:
    """Hash of the minimized DFA's canonical table + tokenizer name.

    The minimal DFA is unique per language, so equivalent regexes share a key.
    """
    return hashlib.sha256((model_name + "\n" + dfa.signature()).encode()).hexdigest()[:24]


def load_or_build_index(dfa: DFA, tokenizer, model_name: str = "gpt2", cache_dir: Path | None = None) -> TokenIndex:
    """Return the token index, reading it from disk if this DFA was indexed before."""
    cache_dir = cache_dir or CACHE_DIR
    path = cache_dir / f"token_index_{cache_key(dfa, model_name)}.json"
    if path.exists():
        raw = json.loads(path.read_text())
        allowed = {int(q): ids for q, ids in raw["allowed"].items()}
        nxt = {int(q): dict(zip(allowed[int(q)], states)) for q, states in raw["next"].items()}
        return TokenIndex(allowed, nxt)
    index = build_index(dfa, vocab_strings(tokenizer))
    cache_dir.mkdir(parents=True, exist_ok=True)
    raw = {
        "allowed": index.allowed,
        "next": {q: [index.next_state[q][t] for t in ids] for q, ids in index.allowed.items()},
    }
    path.write_text(json.dumps(raw))
    return index
