# Trio development progress

Last updated: 2026-11-21. Status words mean: **Done** = built and covered by tests that were run
and passed; **Partial** = works but with stated gaps; **Mocked** = only exercised against the mock
provider or recorded API formats; **Blocked** = needs something outside the code; **Not started**.

## Phase 1 acceptance criteria

| Criterion | Status | Evidence |
|---|---|---|
| A user can register and sign in | Done | `backend/tests/test_auth.py`, e2e "register…", "sign out and sign back in" |
| A user can create and revisit conversations | Done | `test_chat.py::test_streaming_reply_persists`, e2e reload test |
| A user can select an AI provider | Done | Model picker + Auto (Trio Router); `test_auto_routing…`, `test_router_health.py`, `test_unconfigured_and_unknown_models_rejected` |
| The app can send a real request to at least one configured provider | **Mocked / Blocked** | Adapters for Anthropic, OpenAI, xAI and Gemini are implemented and tested against each vendor's recorded streaming wire format (`test_providers.py`), but **no live call has been made**: no API keys were available in the build environment. Needs one key in `backend/.env` to verify. |
| Responses stream into the chat interface | Done | SSE end to end; checked through the Next.js proxy (first chunk at ~20 ms of a 540 ms reply), e2e streaming test |
| Conversation history persists | Done | Postgres; e2e reload test |
| File uploads work with validation | Done | `test_upload_*`, e2e upload test (valid CSV accepted, fake PNG rejected) |
| Artifact workspace renders a generated artifact | Done | e2e: preview renders, button clicks work inside sandbox, code edit, error capture, save version |
| Model comparison works for configured providers | Done (mock) | `test_compare_*`, e2e compare + synthesis. Only mock models were configured. |
| Usage and errors are recorded | Done | `usage_records` + `audit_events`; `test_usage_summary`, `test_provider_failure…`, e2e usage page |
| Subscription checkout works in test mode | **Partial / Blocked** | Plans, credits, Checkout, portal, webhooks and billing history are built and tested with Stripe calls faked and webhooks signed locally (`test_billing.py`, 14 tests; e2e billing page). No Stripe test key was available, so a real test-mode checkout has not been run. |
| Core automated tests pass | Done | 79 backend tests, 16 Playwright tests passed on 2026-11-21 |
| Project starts from documented commands | Done | README commands were the ones used to run it here |

## Feature detail

### Done
- Email/password accounts (Argon2), JWT session cookie, logout, sign out everywhere, login rate limit, audit log of auth events
- Conversations: create, list, search (title + content), folders field, rename, delete, export (Markdown/JSON)
- Chat: SSE streaming, stop button (partial reply saved as "stopped"), regenerate, edit, branch navigation (1/2 ‹ ›), ratings, copy
- Providers: common adapter interface; Anthropic, OpenAI, Gemini, xAI adapters; provider error normalization (auth, rate limit, not found, outage); clear "not configured" messages; Mock provider labeled in every reply
- Anthropic effort / OpenAI reasoning_effort mapped from the reasoning mode (Fast/Balanced/Deep)
- Context-window management (drops oldest turns to fit; refuses oversize single messages)
- Attachments: text/code/CSV/JSON, PDF (text extracted), images (sent to vision models); validation and tenant isolation
- Artifacts: detection of `html`/`svg` blocks, sandboxed live preview, code editing, version history, restore (non-destructive), download, full screen, runtime error panel
- Model comparison: 2–4 models in parallel, per-model latency, time to first token, tokens, estimated cost, preference vote (stored as subjective feedback), combined answer with model attribution
- Usage dashboard: per-model totals, errors, recent calls, 24 h spend vs daily cap; spend cap enforced
- UI: responsive (phone drawer), light/dark/system theme without flash, command palette (Ctrl+K), keyboard shortcuts (Enter, Shift+Enter, Ctrl+Shift+O), Markdown, tables, KaTeX math, syntax highlighting, axe accessibility checks on key pages
- Infra: Alembic migration, Dockerfiles, docker-compose, GitHub Actions CI, `.env.example`, dependency audits (pip-audit, npm audit both clean on 2026-11-21)

- Subscriptions: plans Free/Plus/Pro/Business/Enterprise/Developer from `backend/plans.json` (no code change to edit prices, credits or limits); per-plan monthly credits and daily spend cap; credits charged per call (failed calls free); 402 with a clear message when credits run out; Stripe Checkout (subscription mode), customer portal (upgrade, downgrade, cancel, payment methods, invoices), signed webhooks with replay protection and idempotency, failed-payment handling (past_due keeps access, unpaid drops to Free), billing history; live Stripe keys refused unless `STRIPE_ALLOW_LIVE=true`; billing page

### Engine: router, fallback, health, evaluation (Done, mock-verified)
- **Trio Router** (`backend/app/services/router.py`): classifies each prompt (code, math, writing, research, extraction, analysis, chat), then scores every available model on expected quality for that task type, estimated cost, expected latency (live p50 once known), provider health, the user's allowed and preferred providers, and evidence from evaluation runs and thumbs-up/down ratings (Beta-style weighting so a few votes cannot swing it). The mode sets the weights. The ranked list is also the fallback order. The reason is shown on every routed reply.
- **Model fallback**: if a model fails before producing any text with an outage, rate-limit, auth or not-found error, the next ranked model answers (up to 3 attempts). The reply carries a visible notice ("X was unavailable, so Y answered instead"), each failed attempt is recorded in usage at 0 credits, and it never falls back from a real model to the mock. Users can turn fallback off and choose allowed/preferred providers in Settings.
- **Provider health**: per-provider circuit breaker (3 consecutive retryable failures opens it for 60 s, then one trial request), degraded state at 30% recent failures, p50 latency per model. Shown in Settings and at `GET /api/status`. In-process; moves to Redis with several API instances.
- **Evaluation framework** (`backend/evals/`): `python -m evals.run --suite routing|quality`. Routing suite: 40 labeled prompts for the task classifier. Quality suite: 15 cases graded deterministically (exact answers, JSON equality, citation validity against given sources, bullet count, refusal vs over-refusal, prompt-injection resistance, optional sandboxed execution of generated code). Metrics: task completion, quality, citation accuracy, tool accuracy, safety, error rate, p50 latency, cost; satisfaction from ratings at `GET /api/evals/summary`. Paid models need `--allow-spend` and a `--max-usd` cap checked against a worst-case estimate; code execution needs `--exec-code` and must not run on the production server. `--save` stores runs, which feed the router.
- **Measured so far** (2026-11-21): classifier accuracy 97.5% (39/40) on routing_v1. Caveat: the dataset is small and was written by the same author as the classifier, so treat this as a regression check, not a benchmark. The quality suite has only been run against the mock models (7% task completion, as expected for canned text); **no real model has been evaluated yet** (no API keys).

### Partial
- **Model catalog**: Anthropic ids/prices are from Anthropic's current reference; OpenAI, Gemini and xAI ids/prices are editable defaults that must be verified (Settings → "Check live model lists" does this once keys exist).
- **Rate limiting**: in-process; needs Redis for more than one API instance.
- **Folders**: stored and filterable via API; no folder management UI yet.
- **Tenant model**: personal organization per user; team workspaces and RBAC UI come in Phase 6.
- **Attachments in UI**: shown as a count on the message, not as previews.

### Not verified here
- **Docker images / docker compose up**: files written and `docker compose config` validates, but no Docker daemon was available, so images were not built.
- **GitHub Actions workflow**: written, YAML validated, never run (no repository yet).
- **Live provider calls**: see the acceptance table.

### Not started (later phases)
Google/Microsoft sign-in, password reset, MFA, refunds workflow (Stripe dashboard for now), metered Developer billing reporting, voice, image generation, RAG/pgvector,
memory, research/web search, coding agent, office documents, email/calendar, agents and automations,
shareable artifact links, Monaco editor, Redis, object storage (S3/GCS), centralized logging/monitoring,
deployment.

## Blockers
1. **No Stripe test key**: add `STRIPE_SECRET_KEY=sk_test_...`, `STRIPE_WEBHOOK_SECRET` and price ids to run a real test checkout.
2. **No provider API key** in the build environment, so a real model call is unverified. Add any one key to `backend/.env` and send a message.
3. **No GitHub repository** yet. Create an empty repo and the code can be pushed and CI will run.

## Next step
Phase 2: deep research with citations, document generation, image generation, voice, knowledge base
(RAG), personal agents. Still wanted from outside: one provider key (live call plus the first real
quality eval), a Stripe test key, and a GitHub repository.
