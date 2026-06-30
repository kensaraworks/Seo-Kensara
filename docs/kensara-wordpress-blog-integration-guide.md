# Kensara Blog & WordPress Integration Architecture Guide

## 1. Overview
This document outlines the target page structure, URL routing, slug generation logic, WordPress settings, and REST API payload contract required to successfully bridge the **Kensara Autonomous SEO Agent** and the live site at `https://www.kensara.in/blogs`.

Currently, the SEO Agent successfully generates SEO-optimized compliance blog drafts (using multi-agent orchestration, key semantic clusters, internal links, and JSON Schema metadata) but saves them as local Markdown files in the `drafts/blogs` folder. This guide details how to implement the missing link—publishing these blogs live to WordPress—and how to structure the blogs index and detail pages.

---

## 2. Slugs & Routing Strategy

To achieve the clean and SEO-friendly URL structure required by the site, all blogs must resolve under `https://www.kensara.in/blogs/{slug}`.

### 2.1 How the SEO Agent Generates Slugs
The SEO Agent automatically generates URL slugs using the following strict rules:
1. **Lowercase Only**: Slugs must be entirely in lowercase.
2. **Hyphen Separated**: Spaces and special characters are replaced by a single hyphen (`-`).
3. **No Stop Words**: Commonly occurring non-descriptive words (e.g., `the`, `and`, `a`, `for`, `of`, `in`, `to`, `is`, `are`, `with`, `on`, `at`) are stripped to keep URLs short and targeted.
4. **Length Limit**: Slugs are capped at **60 characters** to ensure they remain crawlable and easy to read.
5. **Keyword-Rich**: Slugs are built from the primary SEO keyword.

The exact implementation in [blog_writer.py](file:///c:/Users/hp/SEO-Agent/src/agents/blog_writer.py) is:
```python
def _slugify(text: str) -> str:
    """Convert text to URL-safe slug. Removes stop words."""
    stop_words = {"the", "and", "a", "for", "of", "in", "to", "is", "are", "with", "on", "at"}
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    words = [w for w in text.split() if w not in stop_words]
    slug = re.sub(r"[-\s]+", "-", " ".join(words))
    return slug[:60].rstrip("-")

def _clean_slug(raw_slug: str) -> str:
    """Ensure slug is clean: lowercase, hyphenated, no special chars, max 60 chars."""
    slug = raw_slug.lower().strip()
    slug = re.sub(r"[^\w-]", "-", slug)
    slug = re.sub(r"-+", "-", slug).strip("-")
    return slug[:60]
```

### 2.2 WordPress Permalinks Configuration
To ensure WordPress maps posts to `kensara.in/blogs/{slug}`, the following configuration must be made in the WordPress Admin Dashboard:
1. Navigate to **Settings** > **Permalinks**.
2. Select **Custom Structure**.
3. Set the structure to: `/blogs/%postname%/`
4. Click **Save Changes**.

*This ensures that WordPress will automatically route any post to `https://www.kensara.in/blogs/{slug}`.*

### 2.3 Redirect and Host Canonicalization Rules
To maximize search engine ranking power (link equity) and prevent duplicate content issues, the following server redirects should be configured (via `.htaccess` or server middleware):
- **Trailing Slash / Path Normalization**:
  - `/blog` $\rightarrow$ `301 Redirect` $\rightarrow$ `/blogs`
  - `/blogs/` $\rightarrow$ `301 Redirect` $\rightarrow$ `/blogs`
  - Any uppercase characters in a slug $\rightarrow$ `301 Redirect` $\rightarrow$ lowercase slug.
- **Apex to WWW Canonicalization**:
  - `https://kensara.in/*` $\rightarrow$ `301 Redirect` $\rightarrow$ `https://www.kensara.in/*`

---

## 3. WordPress REST API BlogPost Integration Contract

To enable automated publishing, the SEO Agent needs to send a request to the WordPress REST API.

### 3.1 Endpoint & Authentication
- **Endpoint**: `POST https://www.kensara.in/wp-json/wp/v2/posts`
- **Authentication**: HTTP Basic Auth using WordPress **Application Passwords**.
  *(Generate this via wp-admin $\rightarrow$ Users $\rightarrow$ Your Profile $\rightarrow$ Application Passwords).*

### 3.2 Resolving the `403 Forbidden` / `wp-login.php` Error
If the agent experiences a `403 Forbidden` or `401 Unauthorized` when calling the REST API (or when looking at the login page):
1. **Security Firewalls**: Security plugins (like Wordfence, iThemes Security) or Cloudflare WAF rules might block direct access to `/wp-json/` or basic auth headers. Ensure the server's IP address is whitelisted.
2. **REST API Restriction**: Ensure the REST API is not disabled globally. You can test reachability by visiting `https://www.kensara.in/wp-json/`.
3. **Application Password Validity**: Application passwords will fail if basic authentication headers are stripped by the web server (common on Apache/Nginx configurations without `CGIPassAuth On` or custom header passes).

### 3.3 Target JSON API Payload
When publishing a `BlogPost`, the SEO Agent must map its fields to the WordPress post schema:
```json
{
  "title": "BlogPost.title",
  "content": "BlogPost.content_markdown (converted to HTML or passed as Gutenberg block markdown)",
  "excerpt": "BlogPost.meta_description",
  "slug": "BlogPost.slug",
  "status": "draft", // Or "publish" / "pending" based on approval rules
  "meta": {
    "meta_title": "BlogPost.title",
    "meta_description": "BlogPost.meta_description",
    "canonical_url": "https://www.kensara.in/blogs/{slug}",
    "primary_keyword": "BlogPost.primary_keyword",
    "risk_badge": "BlogPost.risk_level",
    "author_credentials": "BlogPost.author_credentials"
  }
}
```

---

## 4. Blogs Index Page (`/blogs`) Structure

The blogs index page acts as the central hub. It must follow a highly structured hierarchy designed for both readability and crawlability.

### 4.1 Section Components (Top to Bottom)
1. **Global Header**: Shared with the main Kensara website.
2. **Blogs Hero Section (`#blogs-hero`)**:
   - **H1**: `DPDPA and Compliance Insights for Indian Businesses`
   - **Subheading**: *Focus on DPDPA, AI governance, implementation playbooks, and regulatory updates.*
   - **Trust Chips**: *Expert-led*, *India-first*, *Audit-focused*, *Updated weekly*.
   - **CTA buttons**: Primary: "Book a Demo" $\rightarrow$ `/book-demo`, Secondary: "View Latest Regulatory Update" (anchors to featured post card).
3. **Quick Intent Tabs (`#blogs-intent-tabs`)**:
   - Filter options (left-to-right): **All | Regulatory Deep Dives | Industry Playbooks | Breaking Updates | Guides and Checklists**.
   - Reflects the active tab in the URL query string: `?intent=regulatory|industry|breaking|guide|all`.
4. **Featured Post Block (`#blogs-featured`)**:
   - Showcases the single most critical or latest post.
   - Contains: Badge (`BREAKING`, `DEEP DIVE`, etc.), H2 title, 40-60 word answer-first summary, meta metadata row (date, read time, author, Risk level badge).
5. **Blog Grid & Sidebar Split (`#blogs-grid` & `#blogs-sidebar`)**:
   - **Grid (3-columns on desktop)**: Lists regular blog post cards.
   - **Sidebar (Collapsible on Mobile)**: Contains search input, Category filter, Industry filter (Fintech, Healthtech, SaaS, etc.), Regulatory body filter (DPBI, MeitY, RBI, SEBI, etc.), and a Download DPDPA checklist CTA.
6. **Pagination (`#blogs-pagination`)**:
   - Page size of **12 cards**. Numbers with Prev/Next buttons. Query parameter `page=X` is preserved.
7. **Bottom Conversion Band (`#blogs-bottom-cta`)**:
   - Headline: *Need DPDPA compliance without legal guesswork?*
   - Subcopy: *Book a free compliance assessment with certified experts.*
   - Primary CTA: "Book Demo", Secondary: "Contact on WhatsApp".
8. **Global Footer**: Shared with the main site.

---

## 5. Blog Detail Page (`/blogs/{slug}`) Structure

The single blog post page details the content of a specific blog and feeds search engine crawlers with deep semantic markup.

### 5.1 Content Layout & SEO Structure
- **Breadcrumbs**: Home $\rightarrow$ Blogs $\rightarrow$ Category $\rightarrow$ Post Title.
- **H1**: The exact title of the blog post.
- **Metadata Row**: Published date, Last updated date (highly critical for DPDPA timelines), Author (Harjinder Singh, CIPP/E), and Read time.
- **Featured Image**: High-quality, compressed image with descriptive ALT tag (`BlogPost.featured_image_alt`).
- **Body Content**:
  - Implements an **answer-first layout** (conclusions stated in the first 2 paragraphs).
  - Clear heading hierarchy (`<h2>` $\rightarrow$ `<h3>`).
  - Dynamic **Internal Links**: Injected dynamically by the SEO Agent linking related compliance events, guidelines, and terms to build semantic authority.
- **Author Bio Box**: Showcases credentials to establish **EEAT** (Experience, Expertise, Authoritativeness, Trustworthiness).
- **FAQ Section**: Collapsible accordion containing FAQ schema when available.
- **Sidebar or Inline CTA**: A contextual CTA based on the post category (e.g. "Download SaaS Playbook" for a SaaS blog post).

---

## 6. SEO & Schema Data Specs

Every page must serve structured JSON-LD schema to search engines to capture rich snippets.

### 6.1 Listing Page (`/blogs`) Schema
Inject a `CollectionPage` schema with an `ItemList` nested element containing the active posts:
```json
{
  "@context": "https://schema.org",
  "@type": "CollectionPage",
  "name": "DPDPA Blogs and Compliance Insights for India | Kensara AI",
  "description": "Expert-led DPDPA deep dives, industry playbooks, and breaking regulatory updates for Indian businesses.",
  "url": "https://www.kensara.in/blogs",
  "mainEntity": {
    "@type": "ItemList",
    "numberOfItems": 12,
    "itemListElement": [
      {
        "@type": "ListItem",
        "position": 1,
        "url": "https://www.kensara.in/blogs/slug-1"
      }
    ]
  }
}
```

### 6.2 Individual Blog Post Page Schema
Inject `BlogPosting` and `BreadcrumbList` on every post:
```json
{
  "@context": "https://schema.org",
  "@type": "BlogPosting",
  "headline": "Post Title",
  "datePublished": "ISO-date",
  "dateModified": "ISO-date",
  "author": {
    "@type": "Person",
    "name": "Mr Rudraksh Tatwal",
    "jobTitle": "Founder & CEO"
  },
  "publisher": {
    "@type": "Organization",
    "name": "KensaraAI",
    "url": "https://www.kensara.in"
  }
}
```

---

## 7. Next Steps for Implementation in SEO Agent

To enable the SEO Agent to automatically post approved blogs to WordPress, the following changes are planned:
1. **Create `WordPressPublisher`** under `src/publishers/wordpress_publisher.py`.
2. **Add a WordPress Publishing Service** that is triggered when a draft is approved in the SEO Agent dashboard.
3. **Configure API Credentials**: Populate `WORDPRESS_USER` and `WORDPRESS_APP_PASSWORD` in the local `.env` file once application credentials are set.
