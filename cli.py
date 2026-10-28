"""Command line: compare unconstrained vs DFA-constrained generation.

    python cli.py --regex "[0-9]{4}-[0-9]{2}-[0-9]{2}" --prompt "Date: "
"""

import argparse
import time

from core.dfa import regex_to_dfa
from decoding.generate import collect, generate_constrained, generate_unconstrained


def main() -> None:
    """Parse arguments, build the minimal DFA, and print both generations."""
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--regex", required=True)
    ap.add_argument("--prompt", default="")
    ap.add_argument("--max-tokens", type=int, default=40)
    ap.add_argument("--top-k", type=int, default=None, help="sample from the top k tokens (default: greedy)")
    ap.add_argument("--temperature", type=float, default=1.0)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--verbose", action="store_true", help="print every step (token, DFA state, #allowed)")
    args = ap.parse_args()

    dfa = regex_to_dfa(args.regex)  # minimal DFA
    print(f"regex: {args.regex}   minimal DFA: {dfa.num_states()} states, {len(dfa.live_states())} live")
    opts = dict(max_tokens=args.max_tokens, top_k=args.top_k, temperature=args.temperature, seed=args.seed)

    text, _, why = collect(generate_unconstrained(args.prompt, **opts))
    print(f"\nunconstrained [{why}]: {text!r}\n  matches regex: {dfa.accepts(text)}")

    t0 = time.time()
    gen = generate_constrained(dfa, args.prompt, **opts)
    text, steps, why = collect(gen)
    print(f"\nconstrained   [{why}]: {text!r}\n  matches regex: {dfa.accepts(text)}   ({time.time() - t0:.1f}s)")
    if args.verbose:
        for s in steps:
            print(f"  {s.token_text!r:12} -> state {s.dfa_state:<3} allowed={s.num_allowed}")


if __name__ == "__main__":
    main()
