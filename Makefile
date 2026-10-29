PY ?= .venv/bin/python

.PHONY: install test test-fast lint typecheck api web web-install web-test web-build web-types serve

install:            ## install the package with every extra, editable
	$(PY) -m pip install -e ".[ml,api,dev]"

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

web-install:        ## install frontend dependencies (needs Node 20+)
	cd frontend && npm install

web:                ## frontend dev server on :5173 (proxies /api to :8000)
	cd frontend && npm run dev

web-test:           ## type-check and run the frontend tests
	cd frontend && npm run typecheck && npm test

web-build:          ## production build into frontend/dist (served by the API)
	cd frontend && npm run build

web-types:          ## regenerate frontend/src/api/schema.d.ts from the backend's OpenAPI
	cd frontend && npm run gen:api

serve: web-build    ## one process, one port: API + built frontend on :8000
	$(PY) -m grammarguard.api
