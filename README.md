# Student Course Library

A self-hosted web platform where professors publish course reading libraries (PDF textbooks and materials) and students study from them through hybrid search and a chat assistant grounded in the course materials.
Every search result and chat answer cites the exact book and page, and clicking a citation opens the PDF at that page.

## Features

- **SFU sign-in** through CAS, with an `@sfu.ca` magic-link fallback.
- **Roles**: students, professors (granted through an admin-approved role request), and admins.
- **Courses**: professors create courses, students browse and self-join.
- **PDF uploads**: professors upload PDFs, which are parsed in the background with layout-aware parsing and automatic OCR for scanned pages.
- **Hybrid search**: vector similarity plus Postgres full-text search, fused with Reciprocal Rank Fusion, scoped to one course.
- **Grounded chat**: answers stream in token by token, cite their sources as `[n]` markers, and refuse when the course materials don't cover the question.
- **Built-in PDF viewer** that deep-links to a page.
  PDFs are served only through an authenticated, enrollment-checked API route, never a public file URL.

## Tech stack

| Layer | Technology |
|-------|------------|
| Frontend | React 19, Vite, TypeScript, Tailwind CSS, shadcn/ui, react-pdf |
| Backend | FastAPI, SQLAlchemy (async), Alembic, Pydantic |
| Database | PostgreSQL 16 with pgvector (HNSW) and full-text search |
| Job queue | procrastinate (Postgres-backed, no Redis) |
| Ingestion | Docling with RapidOCR, bge-m3 embeddings via sentence-transformers |
| LLM | Any OpenAI-compatible endpoint, Ollama by default |
| Serving | nginx, Docker Compose |

Everything runs on CPU, embeddings included.
A GPU is optional and only speeds up the LLM.

## Architecture

```
browser ──> web (nginx: SPA + /api reverse proxy) ──> api (FastAPI) ──>     db (Postgres + pgvector)
                                                          │                  ^
                                                          │ defers jobs      │
                                                          v                  │
                                                     worker (procrastinate: parse -> chunk -> embed -> index)

api ──> llm (Ollama or any OpenAI-compatible endpoint)
```

| Service | Role |
|---------|------|
| `web` | Serves the built SPA and proxies `/api/` to the api, with buffering off so chat streaming works |
| `api` | REST and SSE API; runs database migrations on startup |
| `worker` | Ingests one book at a time: Docling parse, chunking, bge-m3 embedding, and indexing |
| `db` | Postgres with pgvector; also holds the job queue |
| `llm` | Optional Ollama container, enabled with the `llm` Compose profile |

## Getting started

### Prerequisites

- Docker with Docker Compose v2.
- Roughly 15 GB of free disk space: the api image bakes in the ML models so ingestion never downloads anything at runtime.
- Node.js 22+, only for running frontend tests outside Docker.

### 1. Configure the environment

```bash
cp .env.example .env
```

Then edit `.env`.
At minimum, set `POSTGRES_PASSWORD` and `SESSION_SECRET` to strong random values, for example with `openssl rand -hex 32`.
The `.env` file is gitignored and must never be committed.

| Variable | Required | Description |
|----------|----------|-------------|
| `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD` | Yes | Database name and credentials |
| `SESSION_SECRET` | Yes | Key for signing session cookies and magic-link tokens |
| `APP_BASE_URL` | Yes | Public URL of the app, used in magic-link emails |
| `ENVIRONMENT` | No | `development` disables Secure cookies for local http; anything else is treated as production |
| `CAS_BASE_URL` | No | CAS server base URL, defaults to `https://cas.sfu.ca/cas` |
| `BOOTSTRAP_ADMIN_EMAIL` | No | `@sfu.ca` email promoted to admin on first sign-in, only while no admin exists yet |
| `LLM_BASE_URL` | No | OpenAI-compatible endpoint, defaults to the bundled Ollama (`http://llm:11434/v1`) |
| `LLM_MODEL` | No | Chat model name, defaults to `llama3.1:8b` |
| `LLM_CONDENSE_MODEL` | No | Model for rewriting follow-up questions, defaults to `LLM_MODEL` |
| `LLM_SOURCE_TOKEN_BUDGET` | No | Token budget for retrieved sources in the prompt, defaults to `3500` |
| `OLLAMA_CONTEXT_LENGTH` | No | Ollama context window, defaults to `8192`; raise it together with `LLM_SOURCE_TOKEN_BUDGET` |
| `INGEST_PARSE_TIMEOUT_SECONDS` | No | Wall-clock limit for parsing one PDF, defaults to `3600` |
| `WORKER_NUM_THREADS` | No | CPU threads for the worker's ML libraries, defaults to `2` |

Switching LLM providers is a config change only: point `LLM_BASE_URL` and `LLM_MODEL` at any OpenAI-compatible API.

### 2. Start the stack

```bash
docker compose --profile llm up -d --wait
docker compose exec llm ollama pull llama3.1:8b
```

Open http://localhost:8080.

Leave out `--profile llm` if `LLM_BASE_URL` points at an external endpoint.

By default, `docker compose up` also applies `docker-compose.override.yml`, which gives you a development setup:

- hot reload for the api, worker, and Vite frontend;
- a Mailpit inbox at http://localhost:8025 that catches magic-link emails;
- a test-only login bypass used by the E2E suite.

To confirm the stack is healthy end to end:

```bash
./scripts/smoke-test.sh
```

### 3. Create the first admin

Set `BOOTSTRAP_ADMIN_EMAIL` to your `@sfu.ca` address before your first sign-in.
After that, professors request their role from the dashboard and an admin approves it.

## Deploying

Production uses only the base Compose file, so dev-only settings like the test-auth bypass never apply:

```bash
docker compose -f docker-compose.yml up -d --wait
```

Set `ENVIRONMENT=production` (or leave it unset), set a real `APP_BASE_URL`, and configure an SMTP relay for magic-link emails.
The api refuses to start if the test-auth bypass is enabled in production.

## Testing

### Backend

Backend tests use testcontainers to start a real Postgres with pgvector, so they need access to the Docker socket.
They run inside the api image that `docker compose build` produces.
Compose names it after the checkout folder (`<folder>-api`), so adjust `petproj-api` below if your folder is named differently:

```bash
docker run -d --name api-tests \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v "$PWD/api:/src" -w /src \
  -e PYTHONPATH=/src \
  -e TESTCONTAINERS_HOST_OVERRIDE=host.docker.internal \
  petproj-api sleep infinity
docker exec api-tests pip install -q pytest pytest-asyncio "testcontainers[postgres]" reportlab pillow
docker exec api-tests python -m pytest tests/ -q
docker rm -f api-tests
```

Lint and format checks:

```bash
docker run --rm -v "$PWD/api:/src" -w /src petproj-api \
  sh -c 'pip install -q ruff && ruff check . && ruff format --check .'
```

### Frontend

```bash
cd web
npm install
npm test          # Vitest unit tests
npm run lint      # ESLint
```

### End to end

The Playwright suite runs against the real nginx stack:

```bash
docker compose -f docker-compose.yml -f docker-compose.e2e.yml up -d --wait
cd web && BASE_URL=http://localhost:8080 npx playwright test
```

## Project structure

```
api/                    FastAPI backend and ingestion worker (one shared image)
  app/
    auth/               CAS, magic link, sessions, CSRF guard
    authz/              Role and enrollment authorization dependencies
    books/              PDF upload, storage, serving
    chat/               RAG chat: condense, retrieve, gate, stream, cite
    ingestion/          Docling parsing, chunking, embedding, job tasks
    search/             Hybrid search and Reciprocal Rank Fusion
    db/migrations/      Alembic migrations
  tests/                Unit, integration, and chat pipeline tests
web/                    React frontend
  src/features/         Pages grouped by feature
  e2e/                  Playwright tests
nginx/                  nginx config for the production web image
scripts/                Smoke test
docker-compose*.yml     Base, development override, and E2E overlay
```
