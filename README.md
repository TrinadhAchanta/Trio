# Trio

Trio is one workspace for chatting with, comparing and building with several AI models
(OpenAI, Anthropic, Google Gemini and xAI) through their official APIs.

Built so far: accounts, streaming multi-provider chat with branching, file uploads, side-by-side
model comparison, sandboxed artifact previews, subscriptions and credits (Stripe test mode), the Trio
Router with model fallback and provider health, an evaluation framework, and Phase 2: knowledge bases
with cited answers, deep research, document generation and export (Word, PDF, PowerPoint, Excel),
image generation, voice, personal agents and memory. See [DEVELOPMENT_PROGRESS.md](DEVELOPMENT_PROGRESS.md) for exactly what is
done, partial, mocked and not started.

## Architecture

```
browser ──► Next.js (frontend/, :3000) ──/api/* proxy──► FastAPI (backend/, :8000) ──► PostgreSQL
                                                              │
                                                              └─► provider adapters ──► OpenAI / Anthropic / Gemini / xAI APIs
```

- **frontend/**: Next.js 16, React 19, TypeScript, Tailwind CSS 4. The browser only talks to its own
  origin; `/api/*` is proxied to the backend so the session cookie is first-party and no API key ever
  reaches the browser.
- **backend/**: FastAPI, SQLAlchemy 2, Alembic, PostgreSQL. Responses stream to the browser as
  server-sent events.
- **Provider layer** (`backend/app/providers/`): one `Provider` interface with adapters for
  Anthropic (official `anthropic` SDK), OpenAI (official `openai` SDK), xAI (OpenAI-compatible API via
  the `openai` SDK) and Gemini (official REST API). A clearly labeled **Mock** provider makes the app
  usable without keys and powers the automated tests.
- **Trio Router** (`services/router.py`) picks a model per message from the task type, reasoning mode,
  expected quality (priors updated by eval results and ratings), cost, latency and provider health,
  and falls back to the next model if one is unavailable (`services/health.py` circuit breaker).
- **Evaluations** (`backend/evals/`): `python -m evals.run --suite routing` (free) or
  `--suite quality --models <ids>` (paid models need `--allow-spend --max-usd N`).

Key tradeoffs: FastAPI was chosen over a TypeScript backend because later phases (RAG, document
processing, data analysis, sandboxed code execution) are strongest in the Python ecosystem. Rate
limiting is in-process for now (single instance); it moves to Redis when the API scales out.

## Run it locally

Prerequisites: Python 3.12+, Node 22+, PostgreSQL 16.

```bash
# 1. Database (once)
createuser -P trio            # password: trio   (or use your own and edit DATABASE_URL)
createdb -O trio trio
createdb -O trio trio_test    # only needed for the backend tests

# 2. Backend
cd backend
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env          # then add any provider API keys you have
alembic upgrade head
uvicorn app.main:app --reload --port 8000

# 3. Frontend (new terminal)
cd frontend
npm ci
npm run dev                   # http://localhost:3000
```

Open http://localhost:3000, create an account and start chatting. With no keys in `backend/.env`
the app runs in **demo mode** and every reply is from the Mock provider (the UI says so on every
message). Add e.g. `ANTHROPIC_API_KEY=...` and restart the backend to use a real model.

Or run everything in containers: `docker compose up --build`, then open http://localhost:3000.

### Adding provider keys

| Provider | Env var | Notes |
|---|---|---|
| Anthropic | `ANTHROPIC_API_KEY` | Claude Opus 5.5, Sonnet 5.5, Haiku 5.5 |
| OpenAI | `OPENAI_API_KEY` | GPT-5 family (catalog default, verify) |
| Google | `GEMINI_API_KEY` | Gemini 2.5 Pro / Flash (catalog default, verify) |
| xAI | `XAI_API_KEY` | Grok (catalog default, verify) |
| Brave Search or Tavily | `BRAVE_SEARCH_API_KEY` / `TAVILY_API_KEY` | Web search for Deep research (otherwise a labeled demo search) |

Model ids and prices change often. After adding a key, open **Settings → Check live model lists**
to see which catalog models your key can actually use, and override the catalog without code
changes by pointing `TRIO_MODEL_CATALOG` at a JSON file shaped like `backend/models.example.json`.

## Tests

```bash
# Backend: unit + API integration tests (needs the trio_test database)
cd backend && pytest -q && ruff check .

# Frontend: lint, types, production build
cd frontend && npm run lint && npm run typecheck && npm run build

# End-to-end browser tests: start the API (with a higher auth rate limit, since every test
# registers a user) and the built web app, then run Playwright
cd backend && RATE_LIMIT_AUTH_PER_MINUTE=500 uvicorn app.main:app --port 8000 &
cd frontend && npx next start -p 3000 &
cd frontend && npx playwright test
```

CI (`.github/workflows/ci.yml`) runs all of the above plus `pip-audit` and `npm audit` on every
push and pull request.

## Security notes

- Passwords hashed with Argon2; sessions are signed JWTs in an `HttpOnly`, `SameSite=Lax` cookie,
  revocable with "Sign out everywhere".
- Every query is scoped to the signed-in user; other users' ids return 404 (tested).
- Uploads: extension allowlist plus content sniffing, size limit, UTF-8 check, PDF text extraction,
  sanitized names, stored outside the web root, served with `nosniff` and a sandbox CSP.
- Artifacts run in an `<iframe sandbox="allow-scripts">` with an opaque origin and a CSP that blocks
  all network access, so generated code can't reach Trio's cookies, API or the internet.
- File contents are wrapped as untrusted data in prompts (basic prompt-injection hygiene).
- Per-user rate limits and a daily estimated-spend cap protect against runaway API costs.
- No compliance certification is claimed.
