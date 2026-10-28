"""python -m grammarguard.api [--host H] [--port P] [--reload]"""

import argparse
import logging

import uvicorn


def main() -> None:
    """Serve the API (and the built frontend, if present)."""
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--reload", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    uvicorn.run("grammarguard.api.main:app", host=args.host, port=args.port, reload=args.reload)


if __name__ == "__main__":
    main()
