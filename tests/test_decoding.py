"""Phase 2 tests: token index and constrained generation with real GPT-2 (CPU)."""

import pytest

from core.dfa import regex_to_dfa
from decoding.generate import collect, generate_constrained, generate_unconstrained, load_model
from decoding.token_index import build_index, cache_key, load_or_build_index, vocab_strings

REGEXES = [
    ("[0-9]{4}-[0-9]{2}-[0-9]{2}", "Date: "),
    ("\\([0-9]{3}\\) [0-9]{3}-[0-9]{4}", "Phone: "),
    ("EMP-[0-9]{3,5}", "Employee ID: "),
    ("(true|false|null)", "Value: "),
    ("[a-z]+@[a-z]+\\.(com|org)", "Email: "),
]


@pytest.fixture(scope="module")
def tokenizer():
    """GPT-2 tokenizer (loaded once)."""
    return load_model("gpt2")[0]


def test_vocab_strings_are_exact(tokenizer):
    """Our byte-level decoding agrees with the tokenizer on every ASCII-only token."""
    strings = vocab_strings(tokenizer)
    assert len(strings) == len(tokenizer)
    assert strings[tokenizer.eos_token_id] is None
    checked = 0
    for tid, s in enumerate(strings):
        if s is not None and s.isascii():
            assert s == tokenizer.decode([tid])
            checked += 1
    assert checked > 40000


def test_index_invariants(tokenizer):
    """Every allowed token ends in a live state, and nothing live is missed."""
    dfa = regex_to_dfa("[0-9]{4}-[0-9]{2}")
    strings = vocab_strings(tokenizer)
    index = build_index(dfa, strings)
    live = dfa.live_states()
    assert set(index.allowed) == set(live)
    for q in live:
        for t in index.allowed[q]:
            assert dfa.run(strings[t], q) == index.next_state[q][t] in live
        # completeness: any token that keeps the DFA live is allowed
        expected = {t for t, s in enumerate(strings) if s and dfa.run(s, q) in live}
        assert set(index.allowed[q]) == expected


def test_index_cache_roundtrip(tokenizer, tmp_path):
    """Second call reads the cache and returns an identical index; key depends on the language."""
    dfa = regex_to_dfa("EMP-[0-9]{3}")
    first = load_or_build_index(dfa, tokenizer, cache_dir=tmp_path)
    assert len(list(tmp_path.glob("token_index_*.json"))) == 1
    second = load_or_build_index(dfa, tokenizer, cache_dir=tmp_path)
    assert (first.allowed, first.next_state) == (second.allowed, second.next_state)
    assert cache_key(dfa, "gpt2") == cache_key(regex_to_dfa("EMP-[0-9]{3}"), "gpt2")
    assert cache_key(dfa, "gpt2") != cache_key(regex_to_dfa("EMP-[0-9]{4}"), "gpt2")


@pytest.mark.parametrize("regex,prompt", REGEXES)
@pytest.mark.parametrize("top_k,seed", [(None, 0), (20, 1), (20, 2)])
def test_constrained_output_is_accepted(regex, prompt, top_k, seed):
    """Every constrained output ends by EOS in an accepting state and is accepted by the DFA."""
    dfa = regex_to_dfa(regex)
    text, steps, why = collect(
        generate_constrained(dfa, prompt, max_tokens=40, top_k=top_k, seed=seed)
    )
    assert why == "eos"
    assert dfa.accepts(text), (regex, text)
    assert dfa.run(text) == steps[-1].dfa_state
    assert all(s.num_allowed >= 1 for s in steps)


def test_unconstrained_generator_runs():
    """Unconstrained generator yields tokens with no DFA state."""
    text, steps, why = collect(generate_unconstrained("Date: ", max_tokens=5))
    assert len(steps) == 5 and why == "max_tokens"
    assert all(s.dfa_state is None for s in steps)


def test_max_tokens_cutoff_gives_live_prefix():
    """If cut off early, the output is a prefix of a match: its state is live but not accepting."""
    dfa = regex_to_dfa("[0-9]{4}-[0-9]{2}-[0-9]{2}")
    text, steps, why = collect(generate_constrained(dfa, "Date: ", max_tokens=2))
    assert why == "max_tokens"
    assert dfa.run(text) in dfa.live_states()


def test_dead_end_stops():
    """A language with no non-empty string and a non-accepting start stops immediately as dead_end."""
    dfa = regex_to_dfa("[0-9]{2}")
    # Hand-build an unsatisfiable-by-vocab case: restrict the index to nothing.
    from decoding.token_index import TokenIndex

    empty = TokenIndex({q: [] for q in dfa.live_states()}, {q: {} for q in dfa.live_states()})
    text, steps, why = collect(generate_constrained(dfa, "x", index=empty))
    assert (text, why) == ("", "dead_end")
