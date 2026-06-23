"""KensaraAI SEO Agent — main scheduler entry point."""
import asyncio
from datetime import date

import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from src.agents.blog_writer import KEYWORD_ROTATION, generate_blog_post
from src.agents.news_scout import score_news_items
from src.publishers.file_publisher import save_blog_draft
from src.scrapers.rss_scraper import fetch_rss_feeds

log = structlog.get_logger()

# Week number → keyword index rotation
def _current_keyword() -> str:
    week = date.today().isocalendar()[1]
    return KEYWORD_ROTATION[week % len(KEYWORD_ROTATION)]


async def run_news_scan() -> None:
    """Daily job: fetch + score news. Cache results."""
    log.info("job_news_scan_start")
    try:
        items = await fetch_rss_feeds()
        scored = await score_news_items(items)
        log.info("job_news_scan_done", top_stories=len(scored))
    except Exception as exc:
        log.error("job_news_scan_failed", error=str(exc))


async def run_blog_generate() -> None:
    """Daily job: generate SEO blog post from top news + current keyword."""
    log.info("job_blog_generate_start")
    try:
        items = await fetch_rss_feeds()
        scored = await score_news_items(items)
        if not scored:
            log.warning("job_blog_no_news_found")
            return

        keyword = _current_keyword()
        log.info("job_blog_keyword", keyword=keyword)

        post = await generate_blog_post(scored[0], keyword)
        path = await save_blog_draft(post)

        log.info("job_blog_generate_done", path=str(path), word_count=post.word_count)
    except Exception as exc:
        log.error("job_blog_generate_failed", error=str(exc))


def main() -> None:
    scheduler = AsyncIOScheduler(timezone="Asia/Kolkata")

    # Daily news scan at 08:00 IST
    scheduler.add_job(
        run_news_scan,
        CronTrigger(hour=8, minute=0),
        id="news_scan",
        name="Daily news scan",
    )

    # Daily blog generation at 08:15 IST (15 min after news scan completes)
    scheduler.add_job(
        run_blog_generate,
        CronTrigger(hour=8, minute=15),
        id="blog_generate",
        name="Daily blog generation",
    )

    scheduler.start()
    log.info("seo_agent_started", jobs=scheduler.get_jobs())

    loop = asyncio.get_event_loop()
    try:
        loop.run_forever()
    except (KeyboardInterrupt, SystemExit):
        scheduler.shutdown()
        log.info("seo_agent_stopped")


if __name__ == "__main__":
    main()
