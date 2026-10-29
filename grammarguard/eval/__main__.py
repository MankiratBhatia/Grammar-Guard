"""python -m grammarguard.eval [--quick] [--out eval/results/latest.json]"""

import argparse
import json
from pathlib import Path

from grammarguard.decoding.engine import Engine
from grammarguard.eval.report import markdown
from grammarguard.eval.runner import run_suite, selected_cases


def main() -> None:
    """Run the benchmark and write JSON + Markdown next to each other."""
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--quick", action="store_true", help="3 validators only (about a minute)")
    ap.add_argument("--model", default="gpt2")
    ap.add_argument("--max-tokens", type=int, default=30)
    ap.add_argument("--out", default="eval/results/latest.json")
    args = ap.parse_args()
    results = run_suite(
        Engine(args.model),
        selected_cases(args.quick),
        max_tokens=args.max_tokens,
        progress=lambda m: print(m, flush=True),
    )
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, indent=1))
    out.with_suffix(".md").write_text(markdown(results))
    print(f"wrote {out} and {out.with_suffix('.md')}")


if __name__ == "__main__":
    main()
