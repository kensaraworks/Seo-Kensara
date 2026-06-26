# Kensara Blogs Page Structure Specification

Version: 1.0
Date: 2026-06-25
Primary URL: https://www.kensara.in/blogs
Canonical URL: https://www.kensara.in/blogs

Host canonicalization rule:
- Choose one primary host for all public pages (recommended: www.kensara.in).
- 301 redirect non-primary host to primary host for all paths.

## 1) Current Live State (Observed)

- Live page title state is placeholder: Blogs are on the way.
- Route /blog returns 404.
- Route /blogs is the correct and active blog index route.
- Header and footer architecture is consistent with the main site.

This specification defines the exact target structure for replacing the current placeholder page.

## 2) Route and URL Rules

- Blog index route: /blogs
- Blog detail route: /blogs/{slug}
- Slug format: lowercase, hyphen-separated, max 60 chars
- Canonical for index: https://www.kensara.in/blogs
- Canonical for post: https://www.kensara.in/blogs/{slug}
- Redirect rules:
  - /blog -> 301 -> /blogs
  - /blogs/ -> 301 -> /blogs
  - https://kensara.in/* -> 301 -> https://www.kensara.in/*
  - Any uppercase in slug -> 301 lowercase version

## 3) Page Information Architecture (Top to Bottom)

### 3.1 Global Header (Existing Site Pattern)

Use existing Kensara header/navigation unchanged:
- Home
- DPDPA
- What we do (Benefits)
- Expertise
- Blogs (active state)
- Credibility and Resources
- Book Demo (primary CTA)

### 3.2 Blogs Hero Section

Section ID: blogs-hero

Required elements:
- H1: DPDPA and Compliance Insights for Indian Businesses
- Supporting copy (2 lines max):
  - Focus on DPDPA, AI governance, implementation playbooks, and regulatory updates.
- Utility chips under subcopy:
  - Expert-led
  - India-first
  - Audit-focused
  - Updated weekly
- Primary hero CTA: Book a Demo -> /book-demo
- Secondary hero CTA: View Latest Regulatory Update -> anchor to featured post card

### 3.3 Quick Intent Tabs (Primary Filtering)

Section ID: blogs-intent-tabs

Tabs (exact order):
- All
- Regulatory Deep Dives
- Industry Playbooks
- Breaking Updates
- Guides and Checklists

Behavior:
- All is default active.
- Tabs filter cards client-side if data is preloaded, server-side if paginated API.
- Active tab reflected in query param: ?intent=regulatory|industry|breaking|guide|all

### 3.4 Featured Post Block

Section ID: blogs-featured

Placement:
- Immediately after intent tabs.

Card structure:
- Badge (one): BREAKING, DEEP DIVE, PLAYBOOK, GUIDE
- H2 title (clickable)
- One-sentence answer-first summary (40-60 words)
- Meta row: date, read time, author
- Trust row:
  - Last Updated date
  - Risk badge (LOW, MEDIUM, HIGH REVIEWED)
- Primary CTA: Read Full Analysis
- Secondary CTA: Book Compliance Consultation

Selection rule:
- Highest priority post in pending-publish order after approval, else newest approved post.

### 3.5 Blog Grid Section

Section ID: blogs-grid

Desktop layout:
- 3-column grid at >=1200px
- 2-column grid at 768-1199px
- 1-column stack at <768px

Card content (every card must include):
- Category badge
- Title (max 80 chars on card line-wrap)
- Summary (max 180 chars)
- Meta: published date, updated date, read time
- Author mini-line: Harjinder Singh, CIPP/E
- Internal cluster tag (not visually noisy, but present)
- Read More link

Tier labels (from Module 2 model):
- Tier 1 -> Regulatory Deep Dive
- Tier 2 -> Industry Playbook
- Tier 3 -> Breaking Update

### 3.6 Sidebar (Desktop) / Collapsible Panel (Mobile)

Section ID: blogs-sidebar

Widgets in exact order:
1. Search posts
2. Category filter
3. Industry filter
4. Regulatory body filter
5. Most read (last 30 days)
6. Latest updates (last 7 days)
7. Download DPDPA checklist CTA

Filter values:
- Category: Deep Dive, Playbook, Breaking, Guide
- Industry: Fintech, Healthtech, EdTech, SaaS, Ecommerce, BFSI, HR Tech, Telecom
- Regulatory body: DPBI, MeitY, RBI, SEBI, IRDAI, CERT-In, TRAI

### 3.7 Pagination and Sorting

Section ID: blogs-pagination

- Default sort: newest date_modified
- Alternate sorts:
  - Newest published
  - Most read
  - Most cited (if available)
- Page size: 12 cards
- Pagination style: numbered with previous and next
- Query params:
  - page=1
  - sort=newest|popular|cited
  - intent=...
  - category=...
  - industry=...
  - regulator=...
  - q=...

### 3.8 Bottom Conversion Band

Section ID: blogs-bottom-cta

- Headline: Need DPDPA compliance without legal guesswork?
- Subcopy: Book a free compliance assessment with certified experts.
- Primary CTA: Book Demo
- Secondary CTA: Contact on WhatsApp

### 3.9 Global Footer (Existing Site Pattern)

Keep current footer architecture exactly as main site:
- Quick Links
- Expertise
- Resources
- Legal
- Contact info and social links

## 4) Blog Card Data Contract (Required Fields)

Each listed blog item must provide:
- id
- slug
- title
- meta_title
- meta_description
- canonical_url
- primary_keyword
- secondary_keywords
- cluster
- intent
- tier
- word_count
- qa_score
- geo_score
- risk_level
- approved
- status
- author
- author_credentials
- date_created
- date_published
- date_modified
- featured_image_alt
- internal_links_injected
- source_story_url

Mapping note:
These fields are already aligned with your Module 2 frontmatter schema.

## 5) SEO and Structured Data Requirements

### 5.1 Index Page SEO

- Title format:
  DPDPA Blogs and Compliance Insights for India | Kensara AI
- Meta description:
  Expert-led DPDPA deep dives, industry playbooks, and breaking regulatory updates for Indian businesses.
- Robots: index, follow
- Canonical: /blogs

### 5.2 Index Page Schema

Inject on /blogs page:
- CollectionPage
- ItemList (ordered list of visible cards)
- BreadcrumbList

### 5.3 Post Page Schema

Every blog detail page should include:
- Article (required)
- FAQPage (when FAQ exists)
- HowTo (when how-to section exists)
- BreadcrumbList (required)
- Speakable (if generated)

## 6) UX and Readability Rules

- No card should show unexplained legal certainty language.
- Show Last Updated on every card and post detail.
- Keep excerpt scannable and answer-first.
- Ensure descriptive anchor text for all links.
- Breaking posts display BREAKING badge for 48 hours after publish.

## 7) Performance and Technical Rules

- LCP target under 2.5s on mobile 4G.
- CLS target under 0.1.
- Initial JS for blogs listing under 180KB gzip where practical.
- Lazy-load below-the-fold cards and images.
- Preload first featured image and primary font.

## 8) Accessibility Rules

- One H1 only on index page.
- Filter controls keyboard-operable.
- Visible focus ring for all interactive elements.
- Minimum contrast ratio 4.5:1 for text.
- ARIA labels on search and pagination controls.

## 9) Tracking and Analytics Events

Fire these events:
- blogs_tab_click
- blogs_filter_change
- blogs_search_submit
- blogs_card_click
- blogs_pagination_click
- blogs_bottom_cta_click

Event payload minimum:
- page_path
- intent
- category
- industry
- regulator
- post_slug (if applicable)

## 10) Publishing and Governance Logic

- Show only posts where approved=true and status in published or ready.
- Tier 1 always requires explicit review before visible.
- Tier 3 marked as Breaking for first 48 hours, then normal category styling.
- If no approved posts exist, show a soft-empty state with one CTA and one latest update request form.

## 11) Exact Section Order Checklist (Implementation Sequence)

1. Global Header
2. Blogs Hero
3. Intent Tabs
4. Featured Post Block
5. Blog Grid + Sidebar
6. Pagination
7. Bottom Conversion Band
8. Global Footer

## 12) Acceptance Criteria (Go-Live)

- /blog properly redirects to /blogs.
- /blogs has no placeholder text.
- At least 12 cards render from real post data.
- Filters and pagination preserve URL query params.
- CollectionPage + ItemList schema validates.
- Mobile and desktop layouts match section order.
- Existing site header and footer remain visually consistent.
- All blog links resolve to /blogs/{slug} and return 200.
