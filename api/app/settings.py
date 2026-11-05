"""Application settings loaded from environment variables / .env.

All LLM access and other environment-specific configuration must flow through
this module so swapping providers or environments is a config change only.
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    session_secret: str
    app_base_url: str = "http://localhost:8080"
    # Fail-safe default: anything other than an explicit "development" is
    # treated as production (Secure cookies on, test-auth guard active).
    # Dev opts in via ENVIRONMENT=development (set in
    # docker-compose.override.yml and .env.example); a production deployment
    # that forgets to set ENVIRONMENT gets the safe behavior by default.
    environment: str = "production"

    # SMTP for magic-link emails. Dev points at the Mailpit service from
    # docker-compose.override.yml; prod swaps these to a real relay via env,
    # no code change (see .env.example).
    smtp_host: str = "localhost"
    smtp_port: int = 1025

    # SFU CAS (primary auth path). cas_base_url must be the CAS
    # server base WITHOUT a trailing slash (login/serviceValidate paths are
    # appended). The 5s validation timeout is the degraded-mode guard:
    # on timeout the callback returns a distinct 503 instead of hanging, and
    # the login page's always-visible magic-link form remains the fallback.
    cas_base_url: str = "https://cas.sfu.ca/cas"
    cas_validate_timeout_seconds: float = 5.0

    # Gates the test-only /test-auth/login route (Playwright login bypass).
    # main.py registers the router at startup based on this env
    # var, so a production container with ENABLE_TEST_AUTH=true WOULD
    # expose a total auth bypass on this flag alone - which is why main.py
    # also refuses to start when it is set while ENVIRONMENT is
    # "production" (defense in depth).
    enable_test_auth: bool = False

    # Admin bootstrap. When set,
    # the user whose email matches this value is promoted to admin on their
    # first sign-in - but only while zero admins exist yet
    # (`app/users/repo.py::maybe_bootstrap_admin`). The guard never
    # re-grants admin once any admin exists, so leaving this env var set
    # after initial setup is safe.
    bootstrap_admin_email: str | None = None

    # Upload/ingestion. Both are safe defaults - no env required to
    # boot - but are overridable via BOOK_STORAGE_DIR / MAX_UPLOAD_BYTES so
    # the storage path and cap are config, not hardcoded in the upload read
    # loop. `book_storage_dir` is a shared Docker volume mounted on both the
    # api and worker services (docker-compose.yml `bookdata` volume).
    book_storage_dir: str = "/data/books"
    max_upload_bytes: int = 300 * 1024 * 1024

    # Ingestion pipeline. Both are env-configurable so
    # swapping the OCR backend or tuning the parse-stage timeout is a config
    # change, never a code change. `ocr_backend` selects RapidOcrOptions
    # (default) vs a Tesseract backend in app/ingestion/parsing.py; the
    # Tesseract path also needs its escape-hatch apt packages uncommented in
    # api/Dockerfile. `ingest_parse_timeout_seconds` is the wall-clock ceiling
    # for the parse stage: app/ingestion/pipeline.py runs the parse in a child
    # process and KILLS it when this elapses (a thread could not be stopped).
    # Finalized to 3600s (60 min) from a load test: a real
    # 769-page dense STEM textbook parsed in 32.5 min on CPU-only hardware,
    # so 3600 gives ~1.85x margin - well clear of any
    # target-range (300-500pg) book while still turning a truly pathological
    # hang into a readable [parse] timeout. The prior
    # 1800s placeholder was disproven by that measurement (it was LOWER than a
    # legitimate parse). Override per-deployment via INGEST_PARSE_TIMEOUT_SECONDS.
    ocr_backend: str = "rapidocr"
    ingest_parse_timeout_seconds: float = 3600.0

    # Chat. The ONE env-configured
    # OpenAI-compatible endpoint both LLM calls in app/chat/llm_client.py
    # read from - this is the entire provider-swap surface (base_url +
    # model, config only, never a code change). llm_condense_model falls
    # back to llm_model when unset; only worth overriding to a smaller/
    # faster model if profiling later shows condensation latency matters.
    llm_base_url: str = "http://llm:11434/v1"
    llm_model: str = "llama3.1:8b"
    llm_condense_model: str | None = None

    # Token budget for the retrieved source blocks sent to the model. Sized for an 8192-token Ollama
    # context (OLLAMA_CONTEXT_LENGTH on the compose llm service): about 150
    # system prompt + up to about 2,700 history (3 pairs, assistant answers
    # capped at 800 tokens) + 3,500 sources + about 200 question and source
    # tags + 800 answer, leaving headroom. The sum is Chunk.token_count (the
    # bge-m3 tokenizer count stored at ingestion) rather than a tiktoken
    # count: no new dependency, and the serving model's own tokenizer is not
    # queryable through the OpenAI-compatible endpoint either way, so any
    # count here is a proxy. Whole chunks only: lower-ranked chunks that do
    # not fit are dropped, never truncated.
    llm_source_token_budget: int = 3500


@lru_cache
def get_settings() -> Settings:
    return Settings()
