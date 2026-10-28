PY ?= .venv/bin/python

.PHONY: install test test-fast lint typecheck api web dev

install:            ## install the package with every extra, editable
	$(PY) -m pip install -e ".[ml,api,ui,dev]"

test:               ## full suite (loads GPT-2)
	$(PY) -m pytest -q

test-fast:          ## core + API tests only, no model
	$(PY) -m pytest -q -m "not slow"

lint:
	$(PY) -m ruff check grammarguard tests
	$(PY) -m ruff format --check grammarguard tests

typecheck:
	$(PY) -m mypy

api:                ## backend on :8000
	$(PY) -m uvicorn grammarguard.api.main:app --reload --port 8000

web:                ## frontend dev server on :5173 (proxies /api to :8000)
	cd frontend && npm run dev
