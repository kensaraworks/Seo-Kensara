# Phase 1 — Build Plan: News Scout + Blog Writer

## Goal
Automated weekly SEO blog draft generation. Every Monday morning, 1 keyword-targeted blog post (800–1200 words) lands in `drafts/blogs/` ready for Harjinder to review and publish.

## Success Criteria
- [ ] News scout fetches + scores relevant DPDPA/GDPR stories daily
- [ ] Blog writer generates complete, on-brand post targeting a keyword
- [ ] Output is valid Markdown with frontmatter (title, meta, keywords, date)
- [ ] Scheduler runs daily (news) and weekly (blog) automatically
- [ ] Human approval step: Harjinder reads `drafts/blogs/` → clicks publish (Phase 2)
- [ ] 0 hallucinated facts — all claims sourced from real news items

## Task Breakdown

### Task 1 — Config (30 min)
File: `src/config.py`
- pydantic-settings BaseSettings
- All env vars: AZURE_OPENAI_*, WORDPRESS_*, LINKEDIN_*
- `.env.example` with placeholder values

### Task 2 — RSS Scraper (1 hr)
File: `src/scrapers/rss_scraper.py`
- feedparser to read RSS feeds (ICO, EDPB, IAPP)
- httpx for MeitY (no RSS — scrape `meity.gov.in/whats-new/`)
- Returns: `list[NewsItem]` with title, url, summary, published_date, source
- Cache: save to `drafts/.cache/news_YYYY-MM-DD.json` (skip re-fetch if today's cache exists)

### Task 3 — News Scout Agent (2 hr)
File: `src/agents/news_scout.py`
- Input: `list[NewsItem]` from scraper
- Azure OpenAI call: score each item 0-10 for DPDPA/GDPR relevance
- Filter: only items score >= 7
- Pick top 3 for the week
- Output: `list[ScoredNewsItem]`

### Task 4 — Blog Writer Agent (3 hr)
File: `src/agents/blog_writer.py`
- Input: `ScoredNewsItem` + target keyword (from weekly keyword rotation)
- Multi-step generation:
  1. Outline generation (structure + H2 headings)
  2. Section-by-section writing (to stay within token limits)
  3. Meta description + title generation
  4. CTA injection (always points to kensara.in/request-demo)
- Output: `BlogPost` pydantic model
- Word count check: 800–1200 words (retry if outside range)

### Task 5 — File Publisher (30 min)
File: `src/publishers/file_publisher.py`
- Input: `BlogPost`
- Output: `drafts/blogs/YYYY-MM-DD-[slug].md`
- Frontmatter: title, date, keyword, meta_description, status: "draft"

### Task 6 — Scheduler (1 hr)
File: `src/main.py`
- APScheduler AsyncIOScheduler
- Job 1: `news_scan` — daily at 08:00 IST (02:30 UTC)
- Job 2: `blog_generate` — every Monday at 09:00 IST (03:30 UTC)
- Graceful shutdown on SIGTERM

### Task 7 — Tests (2 hr)
- `tests/test_news_scout.py` — mock Azure OpenAI, test scoring logic
- `tests/test_blog_writer.py` — mock Azure OpenAI, test output structure
- `tests/conftest.py` — fixtures
- Integration test flag: `INTEGRATION_TEST=true` makes real Azure OpenAI calls

## Keyword Weekly Rotation (Phase 1)

| Week | Primary Keyword | Secondary |
|------|----------------|-----------|
| Week 1 | DPDPA compliance software | DPDPA tool India, DPDPA software |
| Week 2 | DSAR automation India | DSAR software, data subject requests |
| Week 3 | consent management platform India | cookie consent India, DPDPA consent |
| Week 4 | data breach notification software | 72 hour breach clock, GDPR breach India |
| Repeat | ... | ... |

## Env Vars Needed

```
AZURE_OPENAI_ENDPOINT=https://kensarai-openai.openai.azure.com/
AZURE_OPENAI_KEY=<from Azure Portal>
AZURE_OPENAI_DEPLOYMENT=gpt-4o
WORDPRESS_URL=https://kensara.in
WORDPRESS_USER=<wp username>
WORDPRESS_APP_PASSWORD=<wp application password>
LINKEDIN_CLIENT_ID=<from LinkedIn app>
LINKEDIN_CLIENT_SECRET=<from LinkedIn app>
LINKEDIN_ACCESS_TOKEN=<OAuth2 token>
```

## Done = All boxes checked
- [ ] `pytest tests/` passes
- [ ] `src/main.py` runs without error
- [ ] `drafts/blogs/` has at least 1 generated blog post
- [ ] Blog post has correct structure (H1, H2s, CTA, 800–1200 words)
- [ ] No PII logged, no secrets in code
