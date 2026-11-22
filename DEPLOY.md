# Deploying Trio

## Render (free tier, current target)

Trio runs as **one Docker web service** (Next.js serves the site and proxies `/api/*` to FastAPI inside
the same container) plus **a PostgreSQL database**. Render allows one free database per account, so
Trio can share an existing one: with `DB_SCHEMA=trio`, every Trio table (and Trio's own migration
history) lives in a separate `trio` schema and the other app's tables are not touched. Uploaded files are stored in PostgreSQL
(`STORAGE_BACKEND=db`) because free Render services have no persistent disk.

1. Push this repository to GitHub.
2. In Render: **New > Blueprint**, pick the repository. Render reads `render.yaml` and proposes the
   `trio` web service on the free plan (in the same region as the database).
   Set `DATABASE_URL` to the database's **Internal Database URL** (database page > Connect).
3. When asked for secret values, enter at least one AI key (`ANTHROPIC_API_KEY` or `OPENAI_API_KEY`).
   Leave the others empty for now; they can be added later under the service's **Environment** tab.
4. Apply. The first build takes several minutes. Database migrations run automatically on every start.
5. Open the service URL, create an account and send a message.

After the first deploy, set `PUBLIC_APP_URL` to the site URL (used for Stripe redirects).

### Free tier limits (Render's rules, check their current docs)
- The service sleeps after a period without traffic; the next visit takes up to about a minute.
- The free PostgreSQL database expires after a limited time (30 days at the time of writing) unless
  upgraded. Back up or upgrade before then if real users sign up.
- 512 MB memory: the container uses about 180 MB idle in local tests.
- Coding Studio code execution is not available on this setup (it needs a separate, isolated Docker host).

### Cost safety
- `DAILY_COST_LIMIT_USD`: per-user cap on estimated AI spend per 24 hours (also limited by each plan).
- `SITE_DAILY_COST_LIMIT_USD`: cap across all users per 24 hours. When reached, chat returns
  "Trio has reached its daily AI usage limit" until spend drops. Set it to what you are willing to pay.
- Set a spending limit in each AI provider's own dashboard as well.

### Before announcing publicly
- Stripe stays in test mode unless `STRIPE_ALLOW_LIVE=true`.
- No compliance certification is claimed.
- Review the privacy policy and terms (not yet written) with someone qualified.

## Local production check
    docker build -t trio-app .
    docker run -p 10000:10000 -e ENVIRONMENT=production -e JWT_SECRET=$(openssl rand -hex 32) \
      -e COOKIE_SECURE=false -e DATABASE_URL=postgresql://USER:PASS@HOST:5432/DB trio-app
