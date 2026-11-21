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
| Core automated tests pass | Done | 123 backend tests, 30 Playwright tests (incl. axe checks on 9 pages) passed on 2026-11-21 |
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

### Phase 2: research, documents, images, voice, knowledge base, agents (Done, mock/demo-verified)
| Feature | Status | What exists | Gaps |
|---|---|---|---|
| Knowledge base (RAG) | Done | Collections; add uploaded files (text, code, CSV, JSON, PDF) or pasted text; paragraph-aware chunking; PostgreSQL full-text search with OR-ranking; pick knowledge bases per message in chat; retrieved passages go to the model as untrusted, numbered sources and the reply cites [n]; sources stored with the message so regenerations see the same ones; per-user isolation (`test_knowledge.py`, e2e) | Keyword search only: pgvector is not installed here, so semantic (embedding) search is not built. No OCR for scanned PDFs. |
| Deep research | Done (demo search) | Plan queries, web search (Brave or Tavily adapters), safe page fetching (SSRF guard: public IPs only, ports 80/443, redirects re-checked, size and time caps), excerpt selection, cited report, citation check (invalid numbers marked [?], coverage measured), progress steps, cancel, Markdown export (`test_research.py`, e2e) | **No search key was available**, so only the labeled demo search (4 built-in example pages) has been run. Brave/Tavily adapters are tested against recorded response shapes only. Runs in the API process; needs a worker queue at scale. |
| Documents | Done | Generate reports, memos, letters, slide decks, spreadsheets from a prompt; edit Markdown; download as Word, PDF, PowerPoint, Excel or Markdown; any chat reply can be downloaded; spreadsheet cells starting with `=` are neutralized (`test_documents.py`, e2e) | Layout is plain (no templates or branding). PDF uses DejaVu fonts when installed, otherwise Latin-1 only. |
| Image generation | Done (demo) | OpenAI and xAI adapters (tested against recorded API shapes), size validation, safety-block errors mapped, images stored as the user's files, credits charged; labeled placeholder generator without keys (`test_images.py`, e2e) | No real image has been generated (no keys). Model ids and per-image prices are defaults to verify. Gemini image models not added yet. |
| Voice | Done | Dictation with the browser's speech recognition (or server transcription through OpenAI when a key is set), read-aloud with the browser's speech synthesis, server text-to-speech endpoint (`test_voice.py`, e2e for read-aloud) | Server STT/TTS tested with recorded API shapes only. In Chrome, browser dictation sends audio to Google; the button tooltip says so. No real-time voice conversation mode. |
| Personal agents | Done | Reusable assistants with instructions, default model and mode, knowledge bases (searched automatically), conversation starters, memory toggle; "Chat" starts a conversation with the agent (`test_agents_memory.py`, e2e) | Agents cannot run tools or act on a schedule yet (automations are in a later phase). |
| Memory | Done | Explicit only: things the user adds in Settings or saves with the bookmark button on their own message; added to the system prompt; global on/off; delete one or all | No automatic memory extraction (deliberately, for privacy). |

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
Google/Microsoft sign-in, password reset, MFA, refunds workflow (Stripe dashboard for now), metered Developer billing reporting,
semantic search (pgvector), email/calendar connectors, scheduled automations, coding studio (Phase 3), shareable artifact links,
Monaco editor, Redis, object storage (S3/GCS), centralized logging/monitoring, deployment.

## Blockers
1. **No web search key**: add `BRAVE_SEARCH_API_KEY` or `TAVILY_API_KEY` to run Deep research on the real web.
1. **No Stripe test key**: add `STRIPE_SECRET_KEY=sk_test_...`, `STRIPE_WEBHOOK_SECRET` and price ids to run a real test checkout.
2. **No provider API key** in the build environment, so a real model call is unverified. Add any one key to `backend/.env` and send a message.
3. **No GitHub repository** yet. Create an empty repo and the code can be pushed and CI will run.

## Next step
Phase 3: AI Coding Studio (code workspace and editor, sandboxed execution in an isolated runner, live
preview, automated tests, GitHub integration). Still wanted from outside: one provider key (live call
and first real quality eval), a web search key, a Stripe test key, and a GitHub repository.
