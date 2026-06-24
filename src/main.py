"""KensaraAI SEO Agent — main scheduler entry point."""
import asyncio
from datetime import date
import hashlib
import json
import sqlite3

import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from src.agents.blog_writer import generate_blog_post
from src.agents.keyword_cluster_engine import run_cluster_gap_auto_queue
from src.agents.trending_monitor import (
    monitor_google_trends,
    monitor_google_autocomplete,
    monitor_reddit_quora,
    monitor_linkedin
)
from src.geo.geo_monitor import (
    monitor_ai_citations,
    monitor_ai_overviews,
    verify_crawler_access
)
from src.geo.entity_monitor import (
    check_knowledge_panel,
    monitor_brand_mentions,
    audit_third_party_listings,
    monitor_founder_brand
)
from src.geo.llms_txt_generator import write_llms_txt
from src.agents.intent_classifier import IntentType
from src.agents.news_scout import score_news_items, ScoredNewsItem
from src.publishers.file_publisher import save_blog_draft
from src.scrapers.rss_scraper import fetch_rss_feeds
from src.queue.job_queue import job_queue
from src.scrapers.deduplicator import get_word_frequencies
from src.analytics.feedback_loop import (
    evaluate_content_performance,
    run_source_health_analysis,
    run_seasonal_preload_check,
    seed_calendar_on_startup,
)

log = structlog.get_logger()


async def run_news_scan() -> None:
    """Daily job: fetch + score news. Cache results."""
    log.info("job_news_scan_start")
    try:
        items = await fetch_rss_feeds()
        scored = await score_news_items(items)
        
        # Record scored items as scanned in database to prevent re-processing
        for s in scored:
            story_id = hashlib.md5(s.item.url.encode("utf-8")).hexdigest()
            if not job_queue.is_story_processed(story_id):
                job_queue.record_processed_story(
                    story_id=story_id,
                    source=s.item.source,
                    headline=s.item.title,
                    url=s.item.url,
                    score=s.relevance_score,
                    intent_tag=s.suggested_angle,
                    fingerprint_vector=json.dumps(get_word_frequencies(s.item.title + " " + s.item.summary)),
                    action_taken="scanned"
                )
                
        log.info("job_news_scan_done", top_stories=len(scored))
    except Exception as exc:
        log.error("job_news_scan_failed", error=str(exc))


async def run_blog_generate(story: ScoredNewsItem | None = None) -> None:
    """Daily job: generate SEO blog post from top news + current keyword.
    If a specific story is passed (e.g. from an immediate newsjacking trigger), use it directly.
    """
    log.info("job_blog_generate_start", immediate=story is not None)
    try:
        if story is None:
            items = await fetch_rss_feeds()
            scored = await score_news_items(items)
            if not scored:
                log.warning("job_blog_no_news_found")
                return
            top_story = scored[0]
        else:
            top_story = story

        queued_item = job_queue.pop_content_queue()
        if queued_item:
            keyword = queued_item["keyword"]
            intent_type = queued_item.get("intent_type") or IntentType.INFORMATIONAL.value
            try:
                paa_questions = json.loads(queued_item.get("paa_questions", "[]"))
            except Exception:
                paa_questions = []
        else:
            keyword = "DPDPA compliance India"
            intent_type = IntentType.INFORMATIONAL.value
            paa_questions = []

        log.info("job_blog_keyword", keyword=keyword, intent=intent_type)

        post = await generate_blog_post(top_story, keyword, intent_type, paa_questions)
        path = await save_blog_draft(post)
        
        job_queue.mark_content_completed(keyword)

        # Record story as processed / blog generated in the database
        story_id = hashlib.md5(top_story.item.url.encode("utf-8")).hexdigest()
        job_queue.record_processed_story(
            story_id=story_id,
            source=top_story.item.source,
            headline=top_story.item.title,
            url=top_story.item.url,
            score=top_story.relevance_score,
            intent_tag=top_story.suggested_angle,
            fingerprint_vector=json.dumps(get_word_frequencies(top_story.item.title + " " + top_story.item.summary)),
            action_taken="blog_generated"
        )

        log.info("job_blog_generate_done", path=str(path), word_count=post.word_count)
    except Exception as exc:
        log.error("job_blog_generate_failed", error=str(exc))


async def run_regulatory_poll() -> None:
    """Poll regulatory feeds every 4 hours. If a story has score >= 12, trigger newsjacking immediately."""
    log.info("run_regulatory_poll_start")
    try:
        items = await fetch_rss_feeds()
        scored = await score_news_items(items)
        
        critical_stories = [s for s in scored if s.relevance_score >= 12]
        if critical_stories:
            for s in critical_stories:
                story_id = hashlib.md5(s.item.url.encode("utf-8")).hexdigest()
                
                # Double check to prevent duplicate newsjacks
                try:
                    with sqlite3.connect(str(job_queue.db_path)) as conn:
                        conn.row_factory = sqlite3.Row
                        row = conn.execute(
                            "SELECT action_taken FROM stories_processed WHERE story_id = ?",
                            (story_id,)
                        ).fetchone()
                        action = row["action_taken"] if row else None
                except Exception:
                    action = None
                
                if action in ("newsjacked", "blog_generated"):
                    log.debug("critical_story_already_newsjacked", url=s.item.url)
                    continue
                    
                log.info("critical_newsjack_triggered", title=s.item.title, score=s.relevance_score)
                # Trigger blog generation immediately
                await run_blog_generate(story=s)
                
                # Record it as newsjacked
                job_queue.record_processed_story(
                    story_id=story_id,
                    source=s.item.source,
                    headline=s.item.title,
                    url=s.item.url,
                    score=s.relevance_score,
                    intent_tag=s.suggested_angle,
                    fingerprint_vector=json.dumps(get_word_frequencies(s.item.title + " " + s.item.summary)),
                    action_taken="newsjacked"
                )
        else:
            log.info("run_regulatory_poll_no_critical_stories")
    except Exception as exc:
        log.error("run_regulatory_poll_failed", error=str(exc))


async def run_trending_monitor_daily() -> None:
    """Daily trending queries monitor (Google Trends)."""
    log.info("job_trending_monitor_daily_start")
    await monitor_google_trends()
    log.info("job_trending_monitor_daily_done")


async def run_trending_monitor_weekly() -> None:
    """Weekly trending signals monitor (Autocomplete, Reddit/Quora, LinkedIn)."""
    log.info("job_trending_monitor_weekly_start")
    await asyncio.gather(
        monitor_google_autocomplete(),
        monitor_reddit_quora(),
        monitor_linkedin()
    )
    log.info("job_trending_monitor_weekly_done")


async def run_geo_monitor_weekly() -> None:
    """Weekly GEO monitor for AI visibility."""
    log.info("job_geo_monitor_weekly_start")
    await asyncio.gather(
        monitor_ai_citations(),
        monitor_ai_overviews(),
        verify_crawler_access()
    )
    log.info("job_geo_monitor_weekly_done")


async def run_llms_txt_update() -> None:
    """Monthly generation of llms.txt."""
    log.info("job_llms_txt_update_start")
    await asyncio.to_thread(write_llms_txt, "drafts/llms.txt")
    log.info("job_llms_txt_update_done")


async def run_entity_monitor_weekly() -> None:
    """Weekly entity status and brand mentions monitor."""
    log.info("job_entity_monitor_weekly_start")
    await asyncio.gather(
        check_knowledge_panel(),
        monitor_brand_mentions(),
        monitor_founder_brand()
    )
    log.info("job_entity_monitor_weekly_done")


async def run_third_party_audit_monthly() -> None:
    """Monthly audit of third-party directories."""
    log.info("job_third_party_audit_monthly_start")
    await audit_third_party_listings()
    log.info("job_third_party_audit_monthly_done")


async def run_feedback_loop_monthly() -> None:
    """Monthly 1.8.A: evaluate 30-day content performance and feed results back into cluster intelligence."""
    log.info("job_feedback_loop_monthly_start")
    try:
        counts = await evaluate_content_performance()
        log.info("job_feedback_loop_monthly_done", **counts)
    except Exception as exc:
        log.error("job_feedback_loop_monthly_failed", error=str(exc))


async def run_source_health_weekly() -> None:
    """Weekly 1.8.B: analyse source rejection rates and auto-downgrade high-rejection feeds."""
    log.info("job_source_health_weekly_start")
    try:
        brief = await run_source_health_analysis()
        log.info("job_source_health_weekly_done", sources_checked=len(brief))
    except Exception as exc:
        log.error("job_source_health_weekly_failed", error=str(exc))


async def run_seasonal_preload_daily() -> None:
    """Daily 1.8.C: check if any seasonal enforcement window needs a pre-scheduled content burst."""
    log.info("job_seasonal_preload_daily_start")
    try:
        await run_seasonal_preload_check()
        log.info("job_seasonal_preload_daily_done")
    except Exception as exc:
        log.error("job_seasonal_preload_daily_failed", error=str(exc))


def main() -> None:
    # Seed seasonal calendar on every startup — idempotent
    seed_calendar_on_startup()

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

    # Periodic regulatory poll every 4 hours
    scheduler.add_job(
        run_regulatory_poll,
        CronTrigger(hour="*/4", minute=0),
        id="regulatory_poll",
        name="Regulatory feed poll",
    )

    # Weekly cluster gap auto-queue at 06:00 IST on Monday
    scheduler.add_job(
        run_cluster_gap_auto_queue,
        CronTrigger(day_of_week='mon', hour=6, minute=0),
        id="cluster_auto_queue",
        name="Weekly keyword cluster auto-queue",
    )

    # Daily trending monitor at 06:30 IST
    scheduler.add_job(
        run_trending_monitor_daily,
        CronTrigger(hour=6, minute=30),
        id="trending_monitor_daily",
        name="Daily trending monitor",
    )

    # Weekly trending monitor at 07:00 IST on Monday
    scheduler.add_job(
        run_trending_monitor_weekly,
        CronTrigger(day_of_week='mon', hour=7, minute=0),
        id="trending_monitor_weekly",
        name="Weekly trending monitor",
    )

    # Weekly GEO monitor at 07:30 IST on Tuesday
    scheduler.add_job(
        run_geo_monitor_weekly,
        CronTrigger(day_of_week='tue', hour=7, minute=30),
        id="geo_monitor_weekly",
        name="Weekly GEO visibility monitor",
    )

    # Weekly Entity monitor at 07:00 IST on Wednesday
    scheduler.add_job(
        run_entity_monitor_weekly,
        CronTrigger(day_of_week='wed', hour=7, minute=0),
        id="entity_monitor_weekly",
        name="Weekly Entity visibility monitor",
    )

    # Monthly Third-Party Audit on the 2nd of every month at 05:00 IST
    scheduler.add_job(
        run_third_party_audit_monthly,
        CronTrigger(day=2, hour=5, minute=0),
        id="third_party_audit",
        name="Monthly third-party directory audit",
    )

    # Monthly LLMs.txt update on the 1st of every month at 05:00 IST
    scheduler.add_job(
        run_llms_txt_update,
        CronTrigger(day=1, hour=5, minute=0),
        id="llms_txt_update",
        name="Monthly LLMs.txt generation",
    )

    # Monthly feedback loop — 1st of every month at 04:00 IST (before LLMs.txt job)
    scheduler.add_job(
        run_feedback_loop_monthly,
        CronTrigger(day=1, hour=4, minute=0),
        id="feedback_loop_monthly",
        name="Monthly content performance feedback loop (1.8.A)",
    )

    # Weekly source health analysis — Every Monday at 05:30 IST
    scheduler.add_job(
        run_source_health_weekly,
        CronTrigger(day_of_week='mon', hour=5, minute=30),
        id="source_health_weekly",
        name="Weekly source rejection health analysis (1.8.B)",
    )

    # Daily seasonal preload check — 07:15 IST (after cluster auto-queue at 06:00)
    scheduler.add_job(
        run_seasonal_preload_daily,
        CronTrigger(hour=7, minute=15),
        id="seasonal_preload_daily",
        name="Daily seasonal enforcement window preload check (1.8.C)",
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
