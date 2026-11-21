# Trio progress

Last updated: 2026-11-21. Status words mean: **Done** = built and covered by tests that were run
and passed; **Partial** = works but with stated gaps; **Mocked** = only exercised against the mock
provider or recorded API formats; **Blocked** = needs something outside the code; **Not started**.

## Phase 1 acceptance criteria

| Criterion | Status | Evidence |
|---|---|---|
| A user can register and sign in | Done | `backend/tests/test_auth.py`, e2e "register…", "sign out and sign back in" |
| A user can create and revisit conversations | Done | `test_chat.py::test_streaming_reply_persists`, e2e reload test |
| A user can select an AI provider | Done | Model picker + Auto; `test_auto_routing…`, `test_unconfigured_and_unknown_models_rejected` |
| The app can send a real request to at least one configured provider | **Mocked / Blocked** | Adapters for Anthropic, OpenAI, xAI and Gemini are implemented and tested against each vendor's recorded streaming wire format (`test_providers.py`), but **no live call has been made**: no API keys were available in the build environment. Needs one key in `backend/.env` to verify. |
| Responses stream into the chat interface | Done | SSE end to end; checked through the Next.js proxy (first chunk at ~20 ms of a 540 ms reply), e2e streaming test |
| Conversation history persists | Done | Postgres; e2e reload test |
| File uploads work with validation | Done | `test_upload_*`, e2e upload test (valid CSV accepted, fake PNG rejected) |
| Artifact workspace renders a generated artifact | Done | e2e: preview renders, button clicks work inside sandbox, code edit, error capture, save version |
| Model comparison works for configured providers | Done (mock) | `test_compare_*`, e2e compare + synthesis. Only mock models were configured. |
| Usage and errors are recorded | Done | `usage_records` + `audit_events`; `test_usage_summary`, `test_provider_failure…`, e2e usage page |
| Core automated tests pass | Done | 50 backend tests, 14 Playwright tests passed on 2026-11-21 |
| Project starts from documented commands | Done | README commands were the ones used to run it here |

## Feature detail

### Done
- Email/password accounts (Argon2), JWT session cookie, logout, sign out everywhere, login rate limit, audit log of auth events
- Conversations: create, list, search (title + content), folders field, rename, delete, export (Markdown/JSON)
- Chat: SSE streaming, stop button (partial reply saved as "stopped"), regenerate, edit, branch navigation (1/2 ‹ ›), ratings, copy
- Providers: common adapter interface; Anthropic, OpenAI, Gemini, xAI adapters; provider error normalization (auth, rate limit, not found, outage); clear "not configured" messages; Mock provider labeled in every reply
- Auto routing by reasoning mode (Fast/Balanced/Deep) and prompt complexity; Anthropic effort / OpenAI reasoning_effort mapped from mode
- Context-window management (drops oldest turns to fit; refuses oversize single messages)
- Attachments: text/code/CSV/JSON, PDF (text extracted), images (sent to vision models); validation and tenant isolation
- Artifacts: detection of `html`/`svg` blocks, sandboxed live preview, code editing, version history, restore (non-destructive), download, full screen, runtime error panel
- Model comparison: 2–4 models in parallel, per-model latency, time to first token, tokens, estimated cost, preference vote (stored as subjective feedback), combined answer with model attribution
- Usage dashboard: per-model totals, errors, recent calls, 24 h spend vs daily cap; spend cap enforced
- UI: responsive (phone drawer), light/dark/system theme without flash, command palette (Ctrl+K), keyboard shortcuts (Enter, Shift+Enter, Ctrl+Shift+O), Markdown, tables, KaTeX math, syntax highlighting, axe accessibility checks on key pages
- Infra: Alembic migration, Dockerfiles, docker-compose, GitHub Actions CI, `.env.example`, dependency audits (pip-audit, npm audit both clean on 2026-11-21)

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
Google/Microsoft sign-in, password reset, MFA, Stripe billing, voice, image generation, RAG/pgvector,
memory, research/web search, coding agent, office documents, email/calendar, agents and automations,
shareable artifact links, Monaco editor, Redis, object storage (S3/GCS), centralized logging/monitoring,
deployment.

## Blockers
1. **No provider API key** in the build environment, so a real model call is unverified. Add any one key to `backend/.env` and send a message.
2. **No GitHub repository** yet. Create an empty repo and the code can be pushed and CI will run.

## Next step
Phase 1 wrap-up: verify one live provider with a real key, push to GitHub and get CI green.
Then Phase 2: full artifact editor (Monaco), document generation, web research with citations, RAG
with pgvector, image generation and voice.
