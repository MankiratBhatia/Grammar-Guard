# syntax=docker/dockerfile:1
# GrammarGuard: API + built frontend + GPT-2 (CPU) in one image. Build: docker build -t grammarguard .

# ---- stage 1: build the React frontend -------------------------------------------------------------
FROM node:20-slim AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json frontend/.npmrc ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# ---- stage 2: Python runtime -----------------------------------------------------------------------
FROM python:3.11-slim AS runtime
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HOME=/models \
    GRAMMARGUARD_CACHE_DIR=/cache \
    GRAMMARGUARD_FRONTEND_DIR=/app/frontend/dist \
    GRAMMARGUARD_BENCHMARKS_PATH=/app/eval/results/latest.json
WORKDIR /app

# CPU-only torch first (the default wheel bundles CUDA and is several times larger).
RUN pip install torch --index-url https://download.pytorch.org/whl/cpu

COPY pyproject.toml README.md ./
COPY grammarguard ./grammarguard
RUN pip install ".[ml,api]"

# Bake the model into the image so the container starts without internet access.
RUN python -c "from transformers import AutoModelForCausalLM, AutoTokenizer; \
AutoTokenizer.from_pretrained('gpt2'); AutoModelForCausalLM.from_pretrained('gpt2')"
# The weights are now local: never contact the Hugging Face hub at runtime (no update checks, no network needed).
ENV HF_HUB_OFFLINE=1

COPY --from=web /web/dist ./frontend/dist
COPY eval/results ./eval/results

# Run as an unprivileged user; /cache holds token indexes (mount a volume to keep them).
RUN useradd --create-home --uid 1000 app && mkdir -p /cache && chown -R app:app /cache /models
USER app

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=90s --retries=3 \
  CMD python -c "import sys,urllib.request as u; sys.exit(0 if u.urlopen('http://127.0.0.1:8000/api/v1/system/health', timeout=4).status == 200 else 1)"
CMD ["python", "-m", "grammarguard.api", "--host", "0.0.0.0", "--port", "8000"]
