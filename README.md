# KensaraAI Autonomous SEO Pipeline

A FastAPI application and background pipeline that watches Indian privacy
regulation, maintains a public **DPDPA Enforcement Tracker**, and drafts
SEO-optimised compliance content for [kensara.in](https://kensara.in).

Deploys to **Vercel** (serverless) with **Supabase** as the database.

---

## The public enforcement tracker

The tracker is the project's main link-earning asset. It is served from three
public endpoints, none of which require a login:

| Path | What it is |
| --- | --- |
| `/enforcement-tracker.html` | The rendered page |
| `/dpdpa-enforcement-tracker` | Canonical alias (matches the page's own `<link rel="canonical">`) |
| `/enforcement-tracker/data.json` | The open dataset, CC-BY 4.0, CORS-enabled |
| `/api/v1/enforcement/actions` | Filtered JSON API (`section`, `sector`, `authority`, `limit`, `offset`) |

Publishing the raw dataset is deliberate — a citable dataset is what earns
links from researchers and journalists.

### Verified rows only

Rows discovered by the weekly Tavily sweep are **leads, not facts**. They are
stored with `needs_review = true` and never appear on the public page, in the
dataset, in the API, or in the WordPress mirror. A row becomes public only once
a human has filled in its fields.

This matters twice over: publishing `[Unconfirmed — see source URL]` against a
named company is an accuracy problem, and dozens of near-identical scraped rows
are thin content on the page that is supposed to be earning links.

Review the queue at **Intelligence → Enforcement Review Queue**, then either
correct the row in the Supabase `enforcement_actions` table and clear
`needs_review`, or:

```bash
curl -X POST https://<your-app>/api/v1/enforcement/verify/IND-IT43A-012 \
  -H 'Content-Type: application/json' \
  -d '{"company":"Acme Pvt Ltd","authority":"CERT-In","sector":"Fintech",
       "violation_type":"Breach notification failure",
       "summary":"...","outcome":"Fine imposed","penalty_amount":"₹25 lakh"}'
```

Verification is refused while any placeholder field is still unfilled.

---

## Deploying to Vercel

### 1. Create the database

In the Supabase SQL editor, run [`schema_supabase.sql`](schema_supabase.sql).
It is idempotent, so re-running it is safe.

### 2. Set environment variables

In **Vercel → Project → Settings → Environment Variables** (see
[`.env.example`](.env.example) for the full list):

| Variable | Required | Purpose |
| --- | --- | --- |
| `SUPABASE_URL` | yes | Project URL |
| `SUPABASE_SERVICE_KEY` | yes | `service_role` secret — server-side only |
| `CRON_SECRET` | yes | Guards the cron endpoint; it returns 401 until set |
| `TAVILY_API_KEY` | for discovery | Weekly enforcement sweep |
| `WORDPRESS_URL`, `WORDPRESS_USER`, `WORDPRESS_APP_PASSWORD` | optional | Mirrors the tracker onto kensara.in |
| `WORDPRESS_ENFORCEMENT_TRACKER_SLUG` | optional | Also sets the canonical URL the page advertises |

Leave `DATA_DIR` unset — it resolves to `/tmp` on serverless automatically.

### 3. Deploy, then seed

```bash
vercel --prod

python scripts/seed_supabase.py --check   # connectivity, writes nothing
python scripts/seed_supabase.py           # loads data/enforcement_tracker.json
```

Seeding is additive and upserts by `id`, so it never destroys rows a reviewer
has corrected. It no-ops if the table already has rows; pass `--force` to
re-apply.

### 4. Verify

```bash
curl https://<your-app>/healthz
```

```json
{
  "status": "ok",
  "serverless": true,
  "supabase_configured": true,
  "router_errors": {},
  "enforcement_tracker": {
    "source": "supabase:table",
    "published_actions": 23,
    "pending_review": 62
  }
}
```

`source` tells you which store answered. `supabase:table` is the healthy state;
`file:bundled` means Supabase is unreachable or empty and the page is being
served from the repository seed.

If `status` is `degraded`, `router_errors` names the router that failed to
import and why — that is the diagnostic to read first when a page misbehaves.
If the whole app fails to import, `/healthz` returns `503` with
`"mode": "recovery"`; set `DEBUG_STARTUP=1` to have it include the traceback
(leave it unset in production — tracebacks name internal paths and settings).

---

## Serverless gotchas this codebase handles

Things that work locally and fail on Vercel. All four have caused a real
outage here, and each has a regression test in
`tests/test_serverless_runtime.py`:

| Trap | What happens | How it's handled |
| --- | --- | --- |
| **No tz database** | `ZoneInfo("Asia/Kolkata")` raises `ZoneInfoNotFoundError` at import, so *every* route 500s | `tzdata` is pinned in `requirements.txt`, and `src/runtime.py` falls back to a fixed UTC+05:30 |
| **CWD is `/var/task`** | `Path("drafts")` silently points at a read-only directory | Paths resolve from `__file__` or `settings.content_output_dir` |
| **Read-only bundle** | Writing uploads into `static/` raises `OSError` | Uploads go to the writable drafts tree, served by the `/uploads` mount |
| **Two entry points** | Zero-config picked the root `app.py` over `api/index.py` | Both import `app` from `src/asgi.py`, so they cannot diverge |

Anything written to disk on serverless lives in `/tmp`, is per-instance, and
disappears. That is why Supabase is the real store.

## How it fits together

```
src/asgi.py             Shared ASGI factory; falls back to a recovery app that
                        still serves the dataset if the real app won't import
api/index.py            Vercel entry point  -> src/asgi.py
app.py                  Root entry point    -> src/asgi.py
src/ui/app.py           App assembly. Registers routers defensively — one bad
                        router degrades one page instead of the whole site
src/ui/routers/tracker.py   Public tracker routes (registered first, on purpose)
src/ui/scheduler.py     APScheduler jobs; only runs on long-lived hosts
src/store/              Persistence. Supabase first, bundled JSON as a floor
src/agents/             The content and research agents
```

### Storage

Reads fall through in order, so the public page renders no matter what:

1. Supabase `enforcement_actions`
2. Supabase `platform_stats` legacy blob (pre-migration layout)
3. Writable JSON cache under `DATA_DIR`
4. `data/enforcement_tracker.json` bundled in the repo
5. An empty skeleton

Statistics are recomputed from whichever rows actually loaded, so the counters
on the page can never drift from its contents.

### Scheduling

Serverless functions do not stay alive, so **APScheduler does not run on
Vercel**. The weekly tracker refresh comes from Vercel Cron instead, declared in
`vercel.json`:

```json
{ "path": "/api/cron/enforcement-tracker", "schedule": "0 1 * * 4" }
```

On a long-running host (Docker, Render, a VM) `src/ui/scheduler.py` starts and
runs the full job set. A job whose dependencies are missing is skipped and
logged; the rest still run.

---

## Dependencies

Split three ways, because the serverless bundle has a hard size limit:

| File | Contents | Installed on |
| --- | --- | --- |
| `requirements.txt` | FastAPI, Jinja2, httpx, Pydantic | Vercel + everywhere |
| `requirements-pipeline.txt` | scraping, LLM and Google SDKs (~150MB) | long-running hosts |
| `requirements-dev.txt` | pytest and friends | development |

Every heavy import is lazy, so the web app — dashboard and tracker both — runs
on `requirements.txt` alone. Pipeline features degrade individually when their
packages are absent. `tests/test_serverless_import_discipline.py` enforces this.

---

## Local development

```bash
python -m venv .venv && source .venv/bin/activate

pip install -r requirements.txt            # web app only
pip install -r requirements-dev.txt        # everything, including the pipeline

cp .env.example .env                       # then fill in your keys

uvicorn src.ui.app:app --reload --port 8000
```

- Dashboard: <http://localhost:8000> (auth key `COO@Kensara`)
- Tracker: <http://localhost:8000/enforcement-tracker.html>
- Health: <http://localhost:8000/healthz>

Run the enforcement sweep by hand:

```bash
python -m src.agents.enforcement_tracker
```

## Tests

```bash
pytest tests/ -q
```

The tracker-specific suites are worth knowing about:

- `test_enforcement_store_supabase.py` — drives the real httpx code path against
  a local fake PostgREST, so header, parameter and upsert-semantics regressions
  get caught without needing a Supabase project
- `test_enforcement_tracker_routes.py` — public access, no unverified rows, cron
  auth, and graceful degradation
- `test_serverless_import_discipline.py` — no heavy module-level imports, and
  every router defines its `router` object

## Other deployment targets

`Dockerfile`, `render.yaml` and `start.sh` still describe a long-running
deployment, which runs the in-process scheduler and the full pipeline. Install
`requirements-pipeline.txt` there.
