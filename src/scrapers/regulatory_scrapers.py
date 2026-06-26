"""Indian regulatory scrapers and monitors — MeitY, DPBI, CERT-In, SEBI, IRDAI, and Court Judgments."""
import asyncio
from datetime import date
from bs4 import BeautifulSoup
import httpx
import structlog
from src.config import settings
from pydantic import BaseModel

class NewsItem(BaseModel):
    title: str
    url: str
    summary: str
    published_date: str
    source: str

log = structlog.get_logger()

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

async def _tavily_fallback_search(query: str, source_name: str) -> list[NewsItem]:
    """Fallback search using Tavily API if direct scraping fails."""
    if not settings.tavily_api_key:
        log.debug("tavily_fallback_skipped", reason="no key", source=source_name)
        return []

    log.info("tavily_fallback_triggered", source=source_name, query=query)
    payload = {
        "api_key": settings.tavily_api_key,
        "query": query,
        "max_results": 5,
        "search_depth": "basic",
        "include_answer": False,
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post("https://api.tavily.com/search", json=payload)
            response.raise_for_status()
            data = response.json()

        items = []
        for result in data.get("results", []):
            items.append(
                NewsItem(
                    title=result.get("title", "").strip(),
                    url=result.get("url", ""),
                    summary=result.get("content", "")[:500].strip(),
                    published_date=str(date.today()),
                    source=source_name,
                )
            )
        return items
    except Exception as exc:
        log.error("tavily_fallback_failed", source=source_name, error=str(exc))
        return []

async def fetch_meity_gazette() -> list[NewsItem]:
    """Scrape MeitY gazette notifications from meity.gov.in/notifications."""
    log.info("fetch_meity_gazette_start")
    url = "https://meity.gov.in/notifications"
    try:
        async with httpx.AsyncClient(headers=HEADERS, timeout=10.0, follow_redirects=True) as client:
            r = await client.get(url)
            r.raise_for_status()
            soup = BeautifulSoup(r.text, "lxml")
            
            items = []
            for row in soup.select("table tr")[1:10]:
                cols = row.select("td")
                if len(cols) >= 2:
                    title = cols[1].text.strip()
                    link_elem = cols[1].find("a")
                    link = link_elem["href"] if link_elem else url
                    if not link.startswith("http"):
                        link = "https://meity.gov.in" + link
                    
                    items.append(
                        NewsItem(
                            title=title,
                            url=link,
                            summary=title,
                            published_date=str(date.today()),
                            source="MeitY Gazette",
                        )
                    )
            if items:
                log.info("fetch_meity_gazette_success", count=len(items))
                return items
    except Exception as exc:
        log.warn("fetch_meity_gazette_scrape_failed", error=str(exc))

    # Fallback to Tavily search
    query = "site:meity.gov.in/notifications DPDPA OR 'Digital Personal Data' OR 'Data Protection Board'"
    return await _tavily_fallback_search(query, "MeitY Gazette")

async def fetch_meity_press_releases() -> list[NewsItem]:
    """Scrape MeitY press releases from meity.gov.in/press-releases."""
    log.info("fetch_meity_press_releases_start")
    url = "https://meity.gov.in/press-releases"
    try:
        async with httpx.AsyncClient(headers=HEADERS, timeout=10.0, follow_redirects=True) as client:
            r = await client.get(url)
            r.raise_for_status()
            soup = BeautifulSoup(r.text, "lxml")
            items = []
            for row in soup.select(".views-row")[:10]:
                link_elem = row.find("a")
                if link_elem:
                    title = link_elem.text.strip()
                    link = link_elem["href"]
                    if not link.startswith("http"):
                        link = "https://meity.gov.in" + link
                    items.append(
                        NewsItem(
                            title=title,
                            url=link,
                            summary=title,
                            published_date=str(date.today()),
                            source="MeitY Press Releases",
                        )
                    )
            if items:
                log.info("fetch_meity_press_releases_success", count=len(items))
                return items
    except Exception as exc:
        log.warn("fetch_meity_press_releases_scrape_failed", error=str(exc))

    query = "site:meity.gov.in/press-releases DPDPA OR 'data protection'"
    return await _tavily_fallback_search(query, "MeitY Press Releases")

async def fetch_dpbi_orders() -> list[NewsItem]:
    """Scrape dpboard.gov.in for penalty orders or board updates."""
    log.info("fetch_dpbi_orders_start")
    query = "site:dpboard.gov.in DPDPA penalty order guidance notification"
    return await _tavily_fallback_search(query, "DPBI")

async def fetch_cert_in_advisories() -> list[NewsItem]:
    """Scrape CERT-In advisories from cert-in.org.in."""
    log.info("fetch_cert_in_advisories_start")
    url = "https://www.cert-in.org.in/s2cMainServlet?pageid=PUBENTIC"
    try:
        async with httpx.AsyncClient(headers=HEADERS, timeout=10.0, follow_redirects=True) as client:
            r = await client.get(url)
            r.raise_for_status()
            soup = BeautifulSoup(r.text, "lxml")
            items = []
            for link_elem in soup.select("a")[:30]:
                href = link_elem.get("href", "")
                if "pageid=PUBADVISORY" in href or "PUBADVISORIES" in href:
                    title = link_elem.text.strip()
                    if title:
                        if not href.startswith("http"):
                            href = "https://www.cert-in.org.in/" + href
                        items.append(
                            NewsItem(
                                title=title,
                                url=href,
                                summary=title,
                                published_date=str(date.today()),
                                source="CERT-In",
                            )
                        )
            if items:
                log.info("fetch_cert_in_advisories_success", count=len(items))
                return items
    except Exception as exc:
        log.warn("fetch_cert_in_advisories_scrape_failed", error=str(exc))

    query = "site:cert-in.org.in advisory breach"
    return await _tavily_fallback_search(query, "CERT-In")

async def fetch_sebi_circulars() -> list[NewsItem]:
    """Scrape SEBI circulars listing."""
    log.info("fetch_sebi_circulars_start")
    query = "site:sebi.gov.in circular data privacy OR cybersecurity"
    return await _tavily_fallback_search(query, "SEBI")

async def fetch_irdai_circulars() -> list[NewsItem]:
    """Scrape IRDAI circulars page."""
    log.info("fetch_irdai_circulars_start")
    query = "site:irdai.gov.in circular data protection OR privacy OR cyber insurance"
    return await _tavily_fallback_search(query, "IRDAI")

async def fetch_india_kanoon_judgments() -> list[NewsItem]:
    """Search IndiaKanoon or Tavily for privacy and DPDPA judgments."""
    log.info("fetch_india_kanoon_judgments_start")
    query = "site:indiankanoon.org DPDPA OR 'personal data protection' OR 'privacy law' judgment 2025 2026"
    return await _tavily_fallback_search(query, "Indian Court Judgments")

async def fetch_ico_enforcement() -> list[NewsItem]:
    """Scrape ICO enforcement actions and news from ico.org.uk.

    ICO officially discontinued RSS feeds after their 2024 website redesign.
    We now scrape the enforcement actions listing and news search pages directly.
    Falls back to a Tavily site-search if scraping fails.
    """
    log.info("fetch_ico_enforcement_start")
    BASE = "https://ico.org.uk"

    # Primary: scrape the enforcement actions listing page
    try:
        async with httpx.AsyncClient(headers=HEADERS, timeout=15.0, follow_redirects=True) as client:
            r = await client.get(f"{BASE}/action-weve-taken/enforcement/")
            r.raise_for_status()
            soup = BeautifulSoup(r.text, "lxml")

            items: list[NewsItem] = []
            # ICO renders enforcement results as anchor elements with descriptive text blocks
            for card in soup.select("article, .search-result, li.result, .enforcement-item"):
                link_elem = card.find("a", href=True)
                heading = card.find(["h2", "h3", "h4"])
                if not link_elem:
                    continue
                title = (heading.get_text(strip=True) if heading else link_elem.get_text(strip=True))
                href = link_elem["href"]
                if not href.startswith("http"):
                    href = BASE + href
                summary_elem = card.find("p")
                summary = summary_elem.get_text(strip=True)[:400] if summary_elem else title
                items.append(NewsItem(
                    title=title,
                    url=href,
                    summary=summary,
                    published_date=str(date.today()),
                    source="ICO",
                ))
            if items:
                log.info("fetch_ico_enforcement_success", count=len(items))
                return items

            # Fallback: extract any /action-weve-taken internal links with labels
            for a in soup.select("a[href]")[:50]:
                href = a["href"]
                if "/action-weve-taken/" in href and href != "/action-weve-taken/":
                    if not href.startswith("http"):
                        href = BASE + href
                    title = a.get_text(strip=True)
                    if title and len(title) > 10:
                        items.append(NewsItem(
                            title=title,
                            url=href,
                            summary=title,
                            published_date=str(date.today()),
                            source="ICO",
                        ))
            if items:
                log.info("fetch_ico_enforcement_links_success", count=len(items))
                return items[:20]

    except Exception as exc:
        log.warning("fetch_ico_enforcement_scrape_failed", error=str(exc))

    # Tavily fallback — searches ICO for recent enforcement news
    query = "site:ico.org.uk enforcement penalty fine action data protection 2025 2026"
    return await _tavily_fallback_search(query, "ICO")


async def fetch_iapp_resources() -> list[NewsItem]:
    """Scrape IAPP news articles and daily dashboard items from iapp.org.

    IAPP does not maintain a public RSS feed. We scrape the news listing
    page directly with a browser-like User-Agent, then fall back to Tavily.
    """
    log.info("fetch_iapp_news_start")
    BASE = "https://iapp.org"

    try:
        async with httpx.AsyncClient(headers=HEADERS, timeout=15.0, follow_redirects=True) as client:
            r = await client.get(f"{BASE}/news/")
            r.raise_for_status()
            soup = BeautifulSoup(r.text, "lxml")

            items: list[NewsItem] = []
            # IAPP news cards are rendered in article/li elements with heading+link
            for card in soup.select("article, .news-item, li.item, .resource-item, .post"):
                link_elem = card.find("a", href=True)
                heading = card.find(["h2", "h3", "h4"])
                if not link_elem:
                    continue
                title = (heading.get_text(strip=True) if heading else link_elem.get_text(strip=True))
                href = link_elem["href"]
                if not href.startswith("http"):
                    href = BASE + href
                summary_elem = card.find("p")
                summary = summary_elem.get_text(strip=True)[:400] if summary_elem else title
                if title and len(title) > 10:
                    items.append(NewsItem(
                        title=title,
                        url=href,
                        summary=summary,
                        published_date=str(date.today()),
                        source="IAPP",
                    ))
            if items:
                log.info("fetch_iapp_news_success", count=len(items))
                return items[:20]

            # Generic fallback: collect any iapp.org/news/ or iapp.org/resources/ links
            for a in soup.select("a[href]")[:60]:
                href = a["href"]
                if not href.startswith("http"):
                    href = BASE + href
                if "iapp.org/news/" in href or "iapp.org/resources/" in href:
                    title = a.get_text(strip=True)
                    if title and len(title) > 10:
                        items.append(NewsItem(
                            title=title,
                            url=href,
                            summary=title,
                            published_date=str(date.today()),
                            source="IAPP",
                        ))
            if items:
                log.info("fetch_iapp_links_success", count=len(items))
                return items[:20]

    except Exception as exc:
        log.warning("fetch_iapp_news_scrape_failed", error=str(exc))

    # Tavily fallback
    query = "site:iapp.org data privacy GDPR DPDPA compliance news 2025 2026"
    return await _tavily_fallback_search(query, "IAPP")

async def fetch_privacy_enforcement_press() -> list[NewsItem]:
    """Search Privacy Enforcement press releases."""
    log.info("fetch_privacy_enforcement_press_start")
    query = "site:privacyenforcement.net/press-releases privacy enforcement penalty fine action"
    return await _tavily_fallback_search(query, "Privacy Enforcement Press Releases")

async def fetch_appa_forum() -> list[NewsItem]:
    """Search APPA Forum website."""
    log.info("fetch_appa_forum_start")
    query = "site:appaforum.org privacy data protection authority APPA"
    return await _tavily_fallback_search(query, "APPA Forum")

async def fetch_data_guidance() -> list[NewsItem]:
    """Search DataGuidance website."""
    log.info("fetch_data_guidance_start")
    query = "site:dataguidance.com data protection privacy compliance regulation"
    return await _tavily_fallback_search(query, "DataGuidance")

async def fetch_dsci_news() -> list[NewsItem]:
    """Search Data Security Council of India news and reports."""
    log.info("fetch_dsci_news_start")
    query = "site:dsci.in data protection privacy framework guidelines report"
    return await _tavily_fallback_search(query, "Data Security Council of India")
