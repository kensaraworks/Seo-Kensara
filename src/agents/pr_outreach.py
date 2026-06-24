"""PR Outreach Agent — drafts personalized media pitch emails for significant privacy stories.

Targets Indian and global media outlets covering compliance, tech regulation, and legal news.
Uses NVIDIA NIM (mistralai/mistral-medium-3.5-128b) for pitch generation.
"""
import json
from datetime import date
from pathlib import Path

import structlog
from openai import AsyncOpenAI
from pydantic import BaseModel

from src.config import settings

log = structlog.get_logger()

MEDIA_TARGETS = [
    {
        "name": "YourStory",
        "email": "editorial@yourstory.com",
        "beat": "Indian startups, tech, compliance",
    },
    {
        "name": "Inc42",
        "email": "tips@inc42.com",
        "beat": "Indian startups, funding, regulation",
    },
    {
        "name": "Entrackr",
        "email": "tips@entrackr.com",
        "beat": "Indian internet companies, compliance",
    },
    {
        "name": "Moneycontrol",
        "email": "digitalnews@moneycontrol.com",
        "beat": "Finance, business, compliance",
    },
    {
        "name": "Bar and Bench",
        "email": "news@barandbench.com",
        "beat": "Legal news, regulatory",
    },
    {
        "name": "IAPP",
        "email": "editorial@iapp.org",
        "beat": "Privacy professionals, global",
    },
    {
        "name": "MediaNama",
        "email": "tips@medianama.com",
        "beat": "Indian digital policy, tech regulation",
    },
]


class PitchEmail(BaseModel):
    outlet: str
    subject: str
    body: str
    angle: str
    urgency: str  # "breaking" | "timely" | "evergreen"


def _classify_urgency(story_title: str, story_summary: str) -> str:
    """Classify story urgency based on keywords — no LLM needed."""
    text = (story_title + " " + story_summary).lower()
    breaking_signals = ["fined", "penalty", "enforcement", "breach", "violation", "crackdown"]
    timely_signals = ["new rule", "amendment", "regulation", "compliance deadline", "guidance", "draft"]

    for signal in breaking_signals:
        if signal in text:
            return "breaking"
    for signal in timely_signals:
        if signal in text:
            return "timely"
    return "evergreen"


async def _draft_single_pitch(
    client: AsyncOpenAI,
    outlet: dict,
    story_title: str,
    story_summary: str,
    story_url: str,
    kensarai_angle: str,
    urgency: str,
) -> PitchEmail:
    """Draft one personalized pitch email for a specific outlet."""
    prefix = "EXCLUSIVE" if urgency == "breaking" else "PITCH"

    prompt = f"""You are writing a press pitch email on behalf of Prince Raj, COO & Co-Founder of KensaraAI.

Target outlet: {outlet["name"]}
Outlet beat: {outlet["beat"]}
Story: {story_title}
Story summary: {story_summary}
Story URL: {story_url}
KensaraAI angle: {kensarai_angle}
Urgency: {urgency}

Write a pitch email with this exact structure:
- Subject line: [{prefix}] <compelling angle + hook, max 60 chars>
- Body (150-200 words total):
  Line 1: Hook — the news + why it matters NOW for the outlet's audience
  Lines 2-3: Why KensaraAI (founded by CEO Rudraksh Tatwal) is the right expert source on this
  Line 4: Draft a relevant, quotable expert quote from CEO Rudraksh Tatwal or COO Prince Raj (1-2 sentences, factual)
  Line 5: One concrete data point or fact from the compliance/enforcement context
  Line 6: CTA — offer more context, interview, or a platform demo
  Signature: Prince Raj, COO KensaraAI | prince@kensara.in | +91-XXXXXXXXXX

Rules:
- Professional journalist tone — NOT sales-y
- Tailored to this outlet's beat ({outlet["beat"]})
- Never mention Anthropic, or any AI model
- Facts only — no made-up statistics
- Concise — journalists delete long emails

Return JSON:
{{
  "subject": "<subject line>",
  "body": "<full email body>",
  "angle": "<1-sentence angle summary>"
}}"""

    try:
        response = await client.chat.completions.create(
            model="mistralai/mistral-medium-3.5-128b",
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=0.7,
            max_tokens=600,
            timeout=30.0,
        )
        data = json.loads(response.choices[0].message.content)
        return PitchEmail(
            outlet=outlet["name"],
            subject=data["subject"],
            body=data["body"],
            angle=data["angle"],
            urgency=urgency,
        )
    except Exception as exc:
        log.error(
            "pr_pitch_draft_failed",
            outlet=outlet["name"],
            error=str(exc),
        )
        # Graceful fallback — return a minimal template so the batch doesn't abort
        return PitchEmail(
            outlet=outlet["name"],
            subject=f"[PITCH] KensaraAI expert comment: {story_title[:40]}",
            body=(
                f"Hi {outlet['name']} team,\n\n"
                f"{story_title}\n\n"
                f"{kensarai_angle}\n\n"
                "Happy to provide expert comment or a platform demo.\n\n"
                "Prince Raj, COO KensaraAI | prince@kensara.in"
            ),
            angle=kensarai_angle,
            urgency=urgency,
        )


async def draft_pr_pitches(
    story_title: str,
    story_summary: str,
    story_url: str,
    kensarai_angle: str,
) -> list[PitchEmail]:
    """
    Draft personalized pitch emails for each media target.

    Each pitch is customized for the outlet's beat and audience.
    Uses NVIDIA NIM (mistralai/mistral-medium-3.5-128b) via OpenAI-compatible API.

    If NVIDIA_API_KEY is not set: returns empty list with a warning log.
    """
    if not settings.nvidia_api_key:
        log.warning(
            "pr_outreach_skipped",
            reason="NVIDIA_API_KEY not set",
            action="set NVIDIA_API_KEY to enable PR pitch generation",
        )
        return []

    client = AsyncOpenAI(
        base_url="https://integrate.api.nvidia.com/v1",
        api_key=settings.nvidia_api_key,
    )

    urgency = _classify_urgency(story_title, story_summary)
    log.info(
        "pr_pitches_start",
        story=story_title[:60],
        urgency=urgency,
        outlets=len(MEDIA_TARGETS),
    )

    pitches: list[PitchEmail] = []
    for outlet in MEDIA_TARGETS:
        pitch = await _draft_single_pitch(
            client=client,
            outlet=outlet,
            story_title=story_title,
            story_summary=story_summary,
            story_url=story_url,
            kensarai_angle=kensarai_angle,
            urgency=urgency,
        )
        pitches.append(pitch)
        log.debug("pr_pitch_drafted", outlet=outlet["name"], subject=pitch.subject[:60])

    log.info("pr_pitches_done", total=len(pitches), urgency=urgency)
    return pitches


async def save_pitch_drafts(pitches: list[PitchEmail], story_slug: str) -> str:
    """
    Save pitch drafts to drafts/pr/YYYY-MM-DD-{story-slug}-pitches.md

    Returns the path of the saved file.
    """
    if not pitches:
        log.warning("pr_save_skipped", reason="no pitches to save")
        return ""

    today = date.today().isoformat()
    safe_slug = story_slug.lower().replace(" ", "-").replace("/", "-")[:60]
    filename = f"{today}-{safe_slug}-pitches.md"

    output_dir = Path(settings.content_output_dir) / "pr"
    output_dir.mkdir(parents=True, exist_ok=True)
    filepath = output_dir / filename

    lines = [
        f"# PR Pitch Drafts — {today}",
        f"**Story slug:** {story_slug}",
        f"**Total pitches:** {len(pitches)}",
        "",
        "---",
        "",
    ]

    for pitch in pitches:
        lines += [
            f"## {pitch.outlet}",
            f"**Urgency:** {pitch.urgency}",
            f"**Angle:** {pitch.angle}",
            "",
            f"**Subject:** {pitch.subject}",
            "",
            "**Body:**",
            "",
            pitch.body,
            "",
            "---",
            "",
        ]

    filepath.write_text("\n".join(lines), encoding="utf-8")
    log.info("pr_pitches_saved", path=str(filepath), count=len(pitches))
    return str(filepath)
