"""Server configuration, read from environment variables with the prefix GRAMMARGUARD_."""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """Every limit that keeps a public endpoint responsive lives here, so it can be tuned
    (or tightened for a demo) without touching code."""

    model_config = SettingsConfigDict(env_prefix="GRAMMARGUARD_")

    model_name: str = "gpt2"
    warm_start: bool = True  # load the model in the background when the server starts
    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]
    frontend_dir: Path = REPO_ROOT / "frontend" / "dist"

    # input limits (violations are a 422 with code "limit_exceeded")
    max_regex_len: int = 500
    max_expression_len: int = 2000
    max_text_len: int = 10_000
    max_prompt_len: int = 4_000
    max_states: int = 2_000  # algebra / minimal-DFA cap
    max_dfa_states: int = 5_000  # unminimized subset-construction cap
    max_regex_states: int = 40  # state elimination is only attempted on automata this small
    max_dot_states: int = 150  # larger automata are returned without a drawing
    max_count_length: int = 64
    max_tokens: int = 200
    max_batch: int = 50
    max_testgen_cases: int = 2_000
    learn_max_states: int = 40

    generation_timeout: float = 120.0  # seconds; a longer generation is stopped
    compile_cache_size: int = 128
