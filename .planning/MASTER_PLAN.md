# KensaraAI SEO Agent — Master Plan
**Owner:** Harjinder Singh (Tajmanor LLP)
**Updated:** 2026-06-07
**Goal:** Fully automated content + growth machine. CEO approves in UI. Everything else autopilot.
**Primary goals:** Rank #1 Google for DPDPA keywords + be cited in ChatGPT/Perplexity/Gemini (GEO)
**Cadence:** Daily blog posts (not weekly)

---

## The One-Line North Star

> Jab bhi koi India mein "DPDPA compliance software" sooche — KensaraAI pehle aaye.
> Google pe, ChatGPT pe, LinkedIn pe, everywhere. Automatically.

---

## Tool Stack (Free + Worth It)

### Content Generation (Zero Variable Cost)
| Tool | Free Tier | Use For | Model |
|------|-----------|---------|-------|
| **Groq API** | 1000 req/day, Llama 3.3 70B | Daily blogs + LinkedIn + newsletter | Primary |
| **NVIDIA NIM** | 40 RPM, Llama 405B | Quality-critical / fallback | Fallback |
| **Azure OpenAI** | Paid (use sparingly) | News scoring only (complex JSON) | Scoring |
| **Claude Opus 4.6** | Per-session | Monthly planning + content strategy | Thinking |
| **Claude Sonnet 4.6** | Per-session | All code implementation | Building |

### Search + Intelligence (Free/Cheap)
| Tool | Cost | Use For |
|------|------|---------|
| **Tavily API** | 1000 credits/month free | Real-time news beyond RSS |
| **RSS Feeds** | Free | ICO, EDPB, IAPP, MediaNama |
| **Serper.dev** | 2500 queries free, then $1/1K | Google rank tracking |
| **Google Search Console API** | Free | Impression/click data |
| **DataForSEO** | $50 prepaid, $0.0006/query | Keyword volume (Phase 3+) |

### Publishing (Free APIs)
| Tool | Cost | Use For |
|------|------|---------|
| **WordPress REST API** | Free | Blog publish to kensara.in |
| **LinkedIn API v2** | Free | LinkedIn posts |
| **Mailchimp API** | Free <500 contacts | Newsletter send |
| **Buffer API** | Free tier | Social scheduling fallback |

### GEO Monitoring
| Tool | Cost | Use For |
|------|------|---------|
| **Manual weekly test** | Free | Ask ChatGPT/Perplexity "best DPDPA tool" |
| **Peec AI / Otterly.ai** | Paid (Phase 4+) | Automated AI citation tracking |

---

## Blog Cadence: Daily (Not Weekly)

**Daily flow:**
```
08:00 IST → RSS + Tavily fetch news
08:05 IST → Azure OpenAI scores items (relevance 0-10)
08:15 IST → Groq Llama 3.3 70B generates 800-1200 word blog
08:20 IST → Auto quality check (word count, keyword, CTA, H2 structure)
           → Pass: queue in CEO UI
           → Fail: retry once with refined prompt
CEO: 10 min/week → bulk approve 7 drafts → auto-push to WP draft
```

**Result:** 365 blogs/year → covers all DPDPA keywords + long-tail → massive SEO footprint

---

## Model Assignment (MANDATORY)

| Task | Model | Reason |
|------|-------|--------|
| Planning, content strategy, competitor analysis | Claude Opus 4.6 | Thinking-heavy, monthly |
| All code, tests, debugging | Claude Sonnet 4.6 | Implementation |
| Blog drafts, LinkedIn posts, newsletter | Groq Llama 3.3 70B | Free, high-volume |
| Quality-critical content, complex compliance writing | NVIDIA NIM Llama 405B | Better reasoning |
| News relevance scoring | Azure OpenAI gpt-4o | JSON output, reliable |

---

## Marketing Skills Integration

Skills sourced from: https://github.com/coreyhaines31/marketingskills
Each skill = Claude Code skill (invoke manually) + Python agent wrapper (autopilot)

### Skills We Use (Priority Order)

| Skill | Agent Name | Schedule | Output |
|-------|-----------|---------|--------|
| `content-strategy` | ContentStrategyAgent | Monthly | Editorial calendar, content pillars |
| `seo-audit` | SEOAuditAgent | Weekly | kensara.in health report |
| `ai-seo` | AISEOAgent | Weekly | GEO optimization tasks (ChatGPT/Perplexity citations) |
| `social` | LinkedInAgent | 3x/week | LinkedIn posts repurposed from blogs |
| `competitor-profiling` | CompetitorAgent | Weekly | OneTrust/TrustArc/Seqrite monitoring report |
| `copywriting` | CopywritingAgent | On-demand | kensara.in landing page copy |
| `programmatic-seo` | ProgrammaticSEOAgent | Monthly | "DPDPA for [industry]" page variants |
| `emails` | NewsletterAgent | Monthly | KensaraAI Privacy Digest draft |
| `lead-magnets` | LeadMagnetAgent | Quarterly | DPDPA checklist PDF, DSAR guide |
| `schema` | SchemaAgent | On-demand | JSON-LD schema for all pages |
| `cro` | CROAgent | Monthly | Conversion optimization recommendations |

### How Skills Become Agents

```
marketingskills/skills/seo-audit/SKILL.md
  → Installed as Claude Code skill (manual invocation via /seo-audit)
  → Also: src/agents/seo_audit.py wraps the skill methodology
    → Runs on schedule via APScheduler
    → Output saved to drafts/reports/YYYY-MM-DD-seo-audit.md
    → CEO reviews in UI
```

---

## Full Architecture

```
External Sources
  ├── RSS feeds (ICO, EDPB, IAPP)        → rss_scraper.py
  ├── MeitY scrape                        → meity_scraper.py
  └── Competitor sites                    → competitor_scraper.py

Context Layer (src/context/)
  ├── kensarai_facts.py    ← brand facts, pricing, differentiators (UPDATE HERE)
  ├── platform_stats.py    ← DSARs processed, consents recorded, breach clocks started
  └── builder.py           ← assembles full context dict for all LLM calls

Marketing Agents (src/agents/)
  ├── news_scout.py        ← daily: score news 0-10, pick top 3
  ├── blog_writer.py       ← weekly: full SEO blog (800-1200 words)
  ├── linkedin_writer.py   ← 3x/week: posts repurposed from blogs
  ├── newsletter_writer.py ← monthly: KensaraAI Privacy Digest
  ├── seo_audit.py         ← weekly: kensara.in SEO health check
  ├── ai_seo.py            ← weekly: GEO optimization (ChatGPT/Perplexity)
  ├── competitor_agent.py  ← weekly: OneTrust/TrustArc/Seqrite monitoring
  ├── copywriting_agent.py ← on-demand: landing page copy
  └── programmatic_seo.py  ← monthly: industry-specific page variants

Publishers (src/publishers/)
  ├── file_publisher.py    ← always: saves to drafts/ for review
  ├── wordpress.py         ← Phase 4: pushes approved content to kensara.in
  └── linkedin.py          ← Phase 4: posts approved LinkedIn content

UI (src/ui/) — FastAPI + Tailwind
  ├── Content Queue        ← all drafts, approve/reject with one click
  ├── Schedule Overview    ← what runs when, last run status
  ├── Metrics Dashboard    ← rankings, traffic, leads (Google Search Console API)
  └── Context Editor       ← CEO can update KensaraAI stats, pricing, new facts

Scheduler (src/main.py)
  └── APScheduler orchestrates all agents on their schedules
```

---

## Phase Breakdown

### Phase 1 — Core Pipeline (CURRENT — finish this week)
**Goal:** Blog draft in `drafts/blogs/` every Monday. End-to-end working.

Tasks:
- [x] Config (pydantic-settings)
- [x] RSS scraper (ICO, EDPB, IAPP)
- [x] News scout (Azure OpenAI scoring)
- [x] Blog writer (3-step: outline → content → meta)
- [x] File publisher (save to drafts/)
- [x] Scheduler (daily news + weekly blog)
- [ ] `src/context/` layer (extract hardcoded context → updatable module)
- [ ] Fix rss_scraper: wrap feedparser in asyncio.to_thread() (blocks event loop now)
- [ ] Fix news_scout: parallel scoring with asyncio.gather (sequential now)
- [ ] Tests: pytest (real calls gated by INTEGRATION_TEST=true)
- [ ] End-to-end verify: 1 blog in drafts/blogs/

Done criteria:
- `python -m src.main` runs without error
- `drafts/blogs/` has 1 blog with H1, H2s, CTA, 800-1200 words
- No PII logged, no secrets in code

---

### Phase 2 — CEO Approval UI (build after Phase 1 done)
**Goal:** CEO opens browser → sees drafts → approves with one click.

Tech: FastAPI + Jinja2 + Tailwind CSS (no build step, runs on same Python stack)

UI screens:
1. **Content Queue** — list of all drafts (blogs, LinkedIn, newsletter)
   - Title, keyword, word count, date generated
   - Preview button (renders Markdown)
   - Approve ✓ / Reject ✗ / Edit (opens in editor)
   - Status: draft → approved → published
2. **Schedule View** — what runs when
   - Daily news scan: last run, next run, items found
   - Weekly blog: last generated, next Monday
   - LinkedIn: 3 posts this week (drafted/approved/posted)
3. **Context Editor** — update KensaraAI facts
   - DSARs processed this month
   - New modules launched
   - Pricing changes
   - New credentials (MeitY, IITG, etc.)

---

### Phase 3 — LinkedIn + Marketing Agent Fleet
**Goal:** LinkedIn posts auto-generated. SEO audit + competitor monitoring running.

Agents to build:
- `linkedin_writer.py` — 3 posts/week from blog + news
  - Post type 1: Fear-based (fine/enforcement news)
  - Post type 2: Educational (DPDPA vs GDPR, how-to)
  - Post type 3: Social proof (DPO story, platform stat)
- `seo_audit.py` — weekly kensara.in audit (using seo-audit skill methodology)
  - Checks: title tags, meta descriptions, H1s, page speed, internal links
  - Output: prioritized fix list in drafts/reports/
- `ai_seo.py` — weekly GEO optimization (using ai-seo skill methodology)
  - Tests: does ChatGPT/Perplexity cite KensaraAI for "DPDPA compliance software"?
  - Recommends: content structure changes for better AI citation
- `competitor_agent.py` — weekly OneTrust/TrustArc/Seqrite monitoring
  - Tracks: new content, pricing changes, feature announcements
  - Output: competitive intel report in drafts/reports/

---

### Phase 4 — WordPress Auto-Publish + LinkedIn Publisher
**Goal:** Approved content auto-publishes. No manual copy-paste.

WordPress flow:
```
Approve in UI → wordpress.py → POST /wp-json/wp/v2/posts (status: "draft")
→ CEO sees draft in wp-admin → clicks Publish → live on kensara.in/blog/
```

LinkedIn flow:
```
Approve in UI → linkedin.py → POST LinkedIn API v2 → live post
(or: schedule for optimal time — Tue/Wed/Thu 8-10am IST)
```

---

### Phase 5 — Newsletter + Programmatic SEO
**Goal:** Monthly newsletter auto-drafted. 50+ programmatic pages on kensara.in.

Newsletter (monthly):
- `newsletter_writer.py` — pulls top 3 compliance stories + platform stats + 1 KensaraAI feature
- Mailchimp/Substack API sends after CEO approval

Programmatic SEO (monthly):
- `programmatic_seo.py` — generates page variants for:
  - "DPDPA compliance for fintech" 
  - "DPDPA compliance for healthtech"
  - "DPDPA compliance for edtech"
  - "GDPR compliance India for SaaS"
  - 20+ more industry/use-case combos
- WordPress API creates these as pages

---

### Phase 6 — Analytics + CRO + Lead Magnets
**Goal:** Know what's working. Convert more visitors.

- `analytics.py` — Google Search Console API → weekly ranking report
  - Which keywords moved up/down
  - Which pages get impressions but low CTR
- `cro.py` — monthly conversion analysis
  - Page visit → demo request conversion rate
  - Which blog posts drive demo clicks
- `lead_magnets.py` — quarterly
  - Generate updated DPDPA compliance checklist (PDF)
  - DSAR response guide
  - Email capture form integration

---

## Updated Project Structure

```
kensarai-seo-agent/
├── CLAUDE.md
├── .env.example
├── .planning/
│   ├── MASTER_PLAN.md           ← THIS FILE
│   ├── PHASE_1_PLAN.md          ← Phase 1 detail (keep)
│   ├── GROWTH_STRATEGY.md       ← Full digital strategy
│   └── COMPETITOR_ANALYSIS.md   ← 13 competitors mapped
├── src/
│   ├── config.py
│   ├── main.py                  ← APScheduler entry point
│   ├── context/                 ← NEW: context injection layer
│   │   ├── __init__.py
│   │   ├── kensarai_facts.py    ← brand facts (updatable)
│   │   ├── platform_stats.py    ← product metrics (updatable)
│   │   └── builder.py           ← assembles context for LLM calls
│   ├── agents/
│   │   ├── news_scout.py
│   │   ├── blog_writer.py
│   │   ├── linkedin_writer.py   ← Phase 3
│   │   ├── newsletter_writer.py ← Phase 5
│   │   ├── seo_audit.py         ← Phase 3
│   │   ├── ai_seo.py            ← Phase 3
│   │   ├── competitor_agent.py  ← Phase 3
│   │   ├── copywriting_agent.py ← Phase 4
│   │   └── programmatic_seo.py  ← Phase 5
│   ├── publishers/
│   │   ├── base.py
│   │   ├── file_publisher.py
│   │   ├── wordpress.py         ← Phase 4
│   │   └── linkedin.py          ← Phase 4
│   ├── scrapers/
│   │   ├── rss_scraper.py
│   │   ├── meity_scraper.py     ← Phase 3
│   │   └── competitor_scraper.py ← Phase 3
│   └── ui/                      ← Phase 2: CEO dashboard
│       ├── app.py               ← FastAPI app
│       ├── routers/
│       │   ├── queue.py         ← content approval queue
│       │   ├── schedule.py      ← schedule status
│       │   └── context.py       ← context editor
│       └── templates/           ← Jinja2 + Tailwind HTML
├── drafts/
│   ├── blogs/
│   ├── linkedin/
│   ├── newsletters/
│   └── reports/                 ← SEO audit, competitor, GEO reports
└── tests/
    ├── conftest.py
    ├── test_news_scout.py
    ├── test_blog_writer.py
    └── test_linkedin_writer.py
```

---

## Marketing Skills Installation

Install skills from coreyhaines31/marketingskills for manual invocation in Claude Code:

```bash
# Clone skills into .claude/skills/marketing/
git clone https://github.com/coreyhaines31/marketingskills.git .marketing-skills

# Skills available as /seo-audit, /ai-seo, /content-strategy, etc.
# Each also wrapped in a Python agent for autopilot scheduling
```

**Invoke manually (Claude Code):**
- `/seo-audit` → run SEO audit on kensara.in
- `/ai-seo` → optimize for ChatGPT/Perplexity citations
- `/content-strategy` → generate monthly content calendar
- `/competitor-profiling` → deep dive on specific competitor

**Run on autopilot (APScheduler):**
- Same logic embedded in Python agents, runs on schedule

---

## CEO Dashboard — What the CEO Sees

```
┌─────────────────────────────────────────────┐
│  KensaraAI Content Hub                       │
│  Week of Jun 9, 2026                         │
├─────────────────────────────────────────────┤
│  PENDING APPROVAL (3)                        │
│  ─────────────────                           │
│  📄 Blog: "DPDPA Compliance Software..."    │
│     Keyword: DPDPA compliance software       │
│     Words: 1,142 | Generated: Mon 8am        │
│     [Preview] [✓ Approve] [✗ Reject]        │
│                                              │
│  📱 LinkedIn (3 posts this week)             │
│     Post 1: ICO fine angle [Approve] [Edit] │
│     Post 2: DPDPA checklist [Approve] [Edit]│
│     Post 3: Platform stat [Approve] [Edit]  │
├─────────────────────────────────────────────┤
│  THIS WEEK'S INTEL                           │
│  • SEO Audit: 3 issues found → [View]       │
│  • Competitor: OneTrust launched India tier  │
│  • GEO: KensaraAI now cited in 2 queries    │
├─────────────────────────────────────────────┤
│  SCHEDULE                                    │
│  • Daily news scan: ✓ ran today (7 items)   │
│  • Weekly blog: ✓ generated (Mon 9am)       │
│  • LinkedIn posts: scheduled Tue/Wed/Thu     │
└─────────────────────────────────────────────┘
```

---

## Timeline

| Week | Focus |
|------|-------|
| **Now** | Finish Phase 1 (context layer + tests + end-to-end verify) |
| **+1w** | Phase 2: CEO UI (content queue + schedule view) |
| **+2w** | Phase 3: LinkedIn agent + SEO audit agent + competitor agent |
| **+3w** | Phase 3: AI-SEO agent |
| **+4w** | Phase 4: WordPress publisher + LinkedIn publisher |
| **+6w** | Phase 5: Newsletter + programmatic SEO |
| **+8w** | Phase 6: Analytics + CRO + lead magnets |

---

## What's Automated vs Human-Approved

| Action | Automated | Human gate |
|--------|-----------|-----------|
| News scanning | ✓ daily | — |
| Blog draft generation | ✓ weekly | ← CEO approves |
| LinkedIn post drafts | ✓ 3x/week | ← CEO approves |
| Newsletter draft | ✓ monthly | ← CEO approves |
| SEO audit report | ✓ weekly | ← CEO reads |
| Competitor intel | ✓ weekly | ← CEO reads |
| GEO optimization tasks | ✓ weekly | ← CEO assigns |
| WordPress publish | ← after approval | ✓ CEO approves → auto |
| LinkedIn post | ← after approval | ✓ CEO approves → auto |
| Newsletter send | ← after approval | ✓ CEO approves → auto |
