"""Model Router — Module 2.9: Multi-Model Routing & Fallback Architecture.

2.9.A — Task-Specific Model Routing
    Every pipeline step maps to an explicit (preferred_model, temperature) pair.
    Groq llama-3.3-70b-versatile: primary for outline, body, assembly, metadata.
    NVIDIA mistralai/mistral-medium-3.5-128b: primary for pillar pages and
        regulatory_section steps (spec 2.9.A: better legal reasoning).
    Tier 3 posts: Groq ONLY, no NVIDIA fallback (latency requirement, spec 2.3).

2.9.B — Token Budget Management
    Per-job token budget by tier:
        Tier 1 — 12,000 tokens
        Tier 2 —  8,000 tokens
        Tier 3 —  4,000 tokens
        Pillar —  40,000 tokens (tier=0 sentinel)
    Every LLM call appends a row to the SQLite token_cost_log table.
    BudgetExceededError is raised if generate() is called after exhausting budget.
    When budget drops below 25% remaining, a concise-mode note is injected into
    the final user message so the LLM adapts (spec 2.9.B assembly note).

2.9.C — Prompt Hardening (Anti-Hallucination)
    ANTI_HALLUCINATION_SYSTEM_PROMPT is the canonical system prompt for EVERY LLM
    call in the pipeline — blog writer, pillar generator, content refresher alike.
    ModelRouter warns (does not block) if messages arrive without a system role,
    since each caller is responsible for construction, but omission is caught early.

Usage
-----
    router = ModelRouter(job_id="abc12", tier=2, cluster_id="consent")

    # Resilient: preferred model with automatic fallback
    text, fallback_used = await router.generate_with_fallback("section", messages)

    # Direct: preferred model only, raises on failure
    text = await router.generate("outline", messages, json_mode=True)
"""

from __future__ import annotations

import os
import datetime
import sqlite3
import structlog
from dataclasses import dataclass, field
from typing import Optional

log = structlog.get_logger()


# ──────────────────────────────────────────────────────────────────────────────
# 2.9.C — Canonical Anti-Hallucination System Prompt
# Imported by blog_writer, pillar_generator, and content_refresher.
# Never duplicate this string in agent files.
# ──────────────────────────────────────────────────────────────────────────────

ANTI_HALLUCINATION_SYSTEM_PROMPT: str = (
    "You are a senior Indian DPDPA compliance content expert writing for KensaraAI.\n\n"
    "You MUST NOT:\n"
    "- Invent DPDPA section numbers not provided in your context\n"
    "- Claim a specific ₹ penalty amount not provided in your context\n"
    "- Name a real Indian company as a compliance violator\n"
    "- Reference any certification or award for Kensara not in your context\n"
    "- State enforcement dates as certain facts — use 'projected', 'expected', "
    "or 'as per current rules'\n"
    "- Mention any competitor negatively\n"
    "- Use first-person plural ('we', 'our') except in the CTA section\n"
    "- Use any of these blocked phrases: \"in today's digital world\", "
    "\"in the ever-evolving landscape\", \"it is no secret that\", "
    "\"it goes without saying\", \"needless to say\", \"in conclusion\", "
    "\"in summary\", \"to summarize\", \"as we can see\", \"in this blog post\", "
    "\"we will explore\", \"let us dive into\", \"without further ado\", "
    "\"at the end of the day\", \"to further understand the implications of\", "
    "\"moving forward, it is essential\", \"it is worth noting\", "
    "\"as we have seen\", \"as discussed above\", \"as mentioned earlier\", "
    "\"as noted previously\"\n"
    "- Produce content in American English — use Indian English throughout "
    "(organisation not organization, authorised not authorized, recognised not recognized)\n"
    "- Make legal over-claims: never write \"100% compliant\", \"fully compliant\", "
    "\"guarantees compliance\", \"ensures compliance\", \"zero risk\", "
    "\"legally guaranteed\", \"fully protected\"\n\n"
    "You MUST:\n"
    "- Express financial penalties in ₹ first, then USD in parentheses\n"
    "- Name the specific Indian regulatory body (DPBI, MeitY, RBI, SEBI, IRDAI, "
    "CERT-In, TRAI) — never say 'the regulator' without naming it\n"
    "- Write in Indian English throughout\n\n"
    "─────────────────────────────────────────────────────────────\n"
    "APPROVED DPDPA PENALTY AMOUNTS — USE ONLY THESE FIGURES\n"
    "Do not use any other penalty figure. Do not recall a penalty figure from\n"
    "training-data memory of earlier draft bills — only the enacted Act's figures below.\n"
    "─────────────────────────────────────────────────────────────\n"
    "Failure to implement reasonable security safeguards (Section 8(5)): up to ₹250 crore\n"
    "Failure to notify DPBI of a personal data breach (Section 8(6)): up to ₹200 crore\n"
    "Failure to notify affected data principals of a breach (Section 8(7)): up to ₹200 crore\n"
    "Failure to fulfil data principal rights — access, correction, erasure: up to ₹50 crore\n"
    "Failure of Consent Manager obligations: up to ₹50 crore\n"
    "Failure of Significant Data Fiduciary (SDF) obligations: up to ₹250 crore\n"
    "General / residual non-compliance: up to ₹50 crore\n"
    "BANNED FIGURES (draft-bill era, NOT in the enacted Act — never write these): "
    "₹5 crore, ₹25 crore, ₹500 crore, ₹2,500 crore\n"
    "If the figure you need is not listed above and not given to you in context, "
    "write 'as per the applicable penalty provisions of the DPDPA' instead of a number.\n\n"
    "─────────────────────────────────────────────────────────────\n"
    "DPDPA SECTION NUMBER GUARD\n"
    "The Digital Personal Data Protection Act 2023 has exactly 40 sections (1-40).\n"
    "Rules under the Act run from Rule 1 to approximately Rule 22.\n"
    "NEVER cite Section 41 or above, or Rule 23 or above — they do not exist.\n"
    "Known correct mappings — use ONLY these when citing a specific section:\n"
    "  Section 4: grounds for processing personal data | Section 5: notice requirements\n"
    "  Section 6: consent requirements | Section 7: legitimate use without consent\n"
    "  Section 8: obligations of data fiduciary (8(5) security safeguards, 8(6) breach notification)\n"
    "  Section 9: children's data | Section 10: Significant Data Fiduciary obligations\n"
    "  Section 11: data principal rights (access, correction, erasure, nomination)\n"
    "  Section 16: exemptions | Section 25: penalties (general) | Section 26: power to make rules\n"
    "  Section 40: repeal and savings\n"
    "If you need to cite a section for a concept not listed above, write 'as per the DPDPA' "
    "without a specific section number — never invent a plausible-sounding one.\n"
    "─────────────────────────────────────────────────────────────"
)


# ──────────────────────────────────────────────────────────────────────────────
# 2.9.A — Task-Specific Routing Table
# ──────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class TaskConfig:
    """Immutable routing config for one pipeline task type."""
    preferred_model: str    # "groq" | "nvidia_blog" | "nvidia_analytical" | "nvidia_fast"
    temperature: float
    json_mode: bool = False
    # tier3_safe=False means this task MUST fall back to Groq for Tier 3 posts
    # (latency requirement: spec 2.3 and 2.9.A).
    tier3_safe: bool = True


# Every task name used in blog_writer.py, pillar_generator.py, and
# content_refresher.py must appear here.  Unknown tasks default to "section".
TASK_ROUTING: dict[str, TaskConfig] = {
    # ── blog pipeline ─────────────────────────────────────────────────────
    # Step 1: JSON outline — low temp for structural precision
    "outline": TaskConfig(
        preferred_model="groq",
        temperature=0.3,
        json_mode=True,
        tier3_safe=True,
    ),
    # Step 2 generic section body — moderate creativity, Groq speed
    "section": TaskConfig(
        preferred_model="groq",
        temperature=0.5,
        tier3_safe=True,
    ),
    # Step 2 regulatory explainer sections
    # Spec 2.9.A: "consider mistralai/mistral-medium-3.5-128b for regulatory sections
    # (better at legal reasoning) even on primary path".
    # NVIDIA Mistral is set as preferred; Groq becomes the fallback.
    # For Tier 3: tier3_safe=False forces override to Groq (latency).
    "regulatory_section": TaskConfig(
        preferred_model="nvidia_blog",
        temperature=0.3,
        tier3_safe=False,
    ),
    # Step 3: Assembly / continuity editing pass — this is an EDITING task
    # (delete redundant/filler text, do not generate new prose), so temperature
    # is kept near-zero (spec CHANGE-A2: "creativity must be suppressed").
    "assembly": TaskConfig(
        preferred_model="groq",
        temperature=0.1,
        tier3_safe=True,
    ),
    # Step 5: Metadata generation — plain text (meta desc is a string, not JSON)
    "metadata": TaskConfig(
        preferred_model="groq",
        temperature=0.1,
        json_mode=False,
        tier3_safe=True,
    ),
    # FAQ answers — generated AFTER the body is assembled and must stay 100%
    # consistent with it (no new ₹ figures / section numbers / dates). NVIDIA
    # Mistral is measurably better at honouring "don't introduce new facts"
    # constraints than Groq at this task (spec CHANGE-C1/C2).
    "faq": TaskConfig(
        preferred_model="nvidia_blog",
        temperature=0.3,
        tier3_safe=False,
    ),
    # ── pillar pipeline ───────────────────────────────────────────────────
    # Spec 2.9.A: "NVIDIA NIM as PRIMARY (not fallback) for pillar pages".
    # Pillar pages are long; NVIDIA's larger context window benefits them.
    "pillar": TaskConfig(
        preferred_model="nvidia_blog",
        temperature=0.4,
        tier3_safe=False,
    ),
    # Stage 1 cluster synthesis (structured JSON, Groq is faster here)
    "cluster_synthesis": TaskConfig(
        preferred_model="groq",
        temperature=0.2,
        json_mode=True,
        tier3_safe=True,
    ),
    # Stage 5 defined-term extraction for DefinedTermSet schema
    "term_extraction": TaskConfig(
        preferred_model="groq",
        temperature=0.1,
        json_mode=True,
        tier3_safe=True,
    ),
    # ── content refresh ───────────────────────────────────────────────────
    # Spec 2.9.A: "Groq llama-3.3-70b. Temperature: 0.4."
    "refresh": TaskConfig(
        preferred_model="groq",
        temperature=0.4,
        tier3_safe=True,
    ),
}


# ──────────────────────────────────────────────────────────────────────────────
# 2.9.B — Per-Tier Token Budgets
# ──────────────────────────────────────────────────────────────────────────────

# tier=0 is the sentinel for pillar pages (budget 40,000 per spec).
TIER_TOKEN_BUDGET: dict[int, int] = {
    0: 40_000,   # pillar pages
    1: 30_000,   # Tier 1 Regulatory Deep Dive (outline×2retry+sections+assembly+meta ≈ 22-26k)
    2: 12_000,   # Tier 2 Industry Playbook
    3: 40_000,   # Tier 3 Newsjack
}

# Approximate pricing in USD per token (not per 1M — stored as per-token for
# easy multiplication).  Update as provider rates change.
# Source: Groq public pricing / NVIDIA NIM estimates as of 2025.
_PRICE: dict[str, dict[str, float]] = {
    "groq":              {"input": 0.59e-6, "output": 0.79e-6},
    "nvidia_blog":       {"input": 0.40e-6, "output": 0.40e-6},
    "nvidia_analytical": {"input": 0.50e-6, "output": 0.50e-6},
    "nvidia_fast":       {"input": 0.15e-6, "output": 0.15e-6},
}


class BudgetExceededError(RuntimeError):
    """Raised when a job attempts an LLM call after its tier budget is exhausted."""


@dataclass
class TokenLedger:
    """Accumulates token usage and cost for a single generation job."""

    job_id: str
    tier: int
    cluster_id: str
    budget_limit: int = field(init=False)
    tokens_spent: int = field(default=0, init=False)
    cost_usd: float = field(default=0.0, init=False)
    fallback_used: bool = field(default=False, init=False)

    def __post_init__(self) -> None:
        self.budget_limit = TIER_TOKEN_BUDGET.get(self.tier, TIER_TOKEN_BUDGET[1])

    @property
    def remaining(self) -> int:
        return max(0, self.budget_limit - self.tokens_spent)

    @property
    def pct_used(self) -> float:
        if not self.budget_limit:
            return 0.0
        return self.tokens_spent / self.budget_limit

    def is_over_budget(self) -> bool:
        return self.tokens_spent >= self.budget_limit

    def record(
        self,
        *,
        input_tokens: int,
        output_tokens: int,
        model_key: str,
        model_name: str,
        task: str,
    ) -> None:
        prices = _PRICE.get(model_key, _PRICE["groq"])
        call_cost = (
            input_tokens * prices["input"]
            + output_tokens * prices["output"]
        )
        self.tokens_spent += input_tokens + output_tokens
        self.cost_usd += call_cost

        _write_token_cost_log(
            job_id=self.job_id,
            model_used=model_name,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=call_cost,
            tier=self.tier,
            cluster_id=self.cluster_id,
            task=task,
        )

        log.debug(
            "token_usage_recorded",
            job_id=self.job_id,
            task=task,
            model=model_name,
            in_tokens=input_tokens,
            out_tokens=output_tokens,
            total_spent=self.tokens_spent,
            budget=self.budget_limit,
            pct_used=f"{self.pct_used:.0%}",
        )


# ──────────────────────────────────────────────────────────────────────────────
# SQLite helper — token_cost_log table (spec 2.9.B DATABASE ADDITIONS)
# ──────────────────────────────────────────────────────────────────────────────

def _db_path() -> str:
    from src.config import settings
    cache_dir = os.path.join(settings.content_output_dir, ".cache")
    os.makedirs(cache_dir, exist_ok=True)
    return os.path.join(cache_dir, "jobs.db")


def _write_token_cost_log(
    *,
    job_id: str,
    model_used: str,
    input_tokens: int,
    output_tokens: int,
    cost_usd: float,
    tier: int,
    cluster_id: str,
    task: str,
) -> None:
    """Append one row to token_cost_log.

    Schema per spec 2.9.B:
        (job_id, model_used, input_tokens, output_tokens, cost_usd,
         timestamp, tier, cluster_id)
    Extended with 'task' for debugging which pipeline step consumed what.
    """
    try:
        conn = sqlite3.connect(_db_path())
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS token_cost_log (
                job_id        TEXT    NOT NULL,
                model_used    TEXT    NOT NULL,
                input_tokens  INTEGER NOT NULL,
                output_tokens INTEGER NOT NULL,
                cost_usd      REAL    NOT NULL,
                timestamp     TEXT    NOT NULL,
                tier          INTEGER NOT NULL,
                cluster_id    TEXT    NOT NULL,
                task          TEXT    NOT NULL
            )
        """)
        cur.execute(
            "INSERT INTO token_cost_log VALUES (?,?,?,?,?,?,?,?,?)",
            (
                job_id,
                model_used,
                input_tokens,
                output_tokens,
                round(cost_usd, 8),
                datetime.datetime.now(datetime.timezone.utc).isoformat(),
                tier,
                cluster_id,
                task,
            ),
        )
        conn.commit()
    except Exception as exc:
        log.error("token_cost_log_write_failed", error=str(exc))
    finally:
        try:
            conn.close()
        except Exception:
            pass


def get_job_cost_summary(job_id: str) -> dict:
    """Read aggregate token usage for a completed job from the SQLite log.

    Returns a dict with: job_id, call_count, total_input_tokens,
    total_output_tokens, total_cost_usd, cost_inr (at approximate rate).
    """
    try:
        conn = sqlite3.connect(_db_path())
        cur = conn.cursor()
        cur.execute(
            """
            SELECT
                COUNT(*)          AS call_count,
                SUM(input_tokens) AS total_input,
                SUM(output_tokens) AS total_output,
                SUM(cost_usd)     AS total_cost
            FROM token_cost_log
            WHERE job_id = ?
            """,
            (job_id,),
        )
        row = cur.fetchone()
        cost_usd = round(row[3] or 0.0, 6)
        return {
            "job_id": job_id,
            "call_count": row[0] or 0,
            "total_input_tokens": row[1] or 0,
            "total_output_tokens": row[2] or 0,
            "total_cost_usd": cost_usd,
            # Spec target: < ₹5 per blog post (≈ $0.06). Report both currencies.
            "total_cost_inr": round(cost_usd * 84, 2),
        }
    except Exception as exc:
        log.error("get_job_cost_summary_failed", job_id=job_id, error=str(exc))
        return {}
    finally:
        try:
            conn.close()
        except Exception:
            pass


# ──────────────────────────────────────────────────────────────────────────────
# Lazy singleton LLM clients — one instance per process, not per job
# ──────────────────────────────────────────────────────────────────────────────

_groq_client = None
_nvidia_client = None


def _get_groq():
    global _groq_client
    if _groq_client is None:
        from groq import AsyncGroq
        from src.config import settings
        _groq_client = AsyncGroq(api_key=settings.groq_api_key)
    return _groq_client


def _get_nvidia():
    global _nvidia_client
    if _nvidia_client is None:
        from openai import AsyncOpenAI
        from src.config import settings
        _nvidia_client = AsyncOpenAI(
            api_key=settings.nvidia_api_key,
            base_url="https://integrate.api.nvidia.com/v1",
        )
    return _nvidia_client


# ──────────────────────────────────────────────────────────────────────────────
# ModelRouter — public interface
# ──────────────────────────────────────────────────────────────────────────────

class ModelRouter:
    """Routes LLM calls to the right model/temperature per pipeline task.

    Instantiate once per generation job in the entry-point function and thread
    the router instance through every step / stage function that needs an LLM.

    Example
    -------
    router = ModelRouter(job_id="8f3a", tier=2, cluster_id="consent")

    # Resilient call (preferred model → automatic fallback on failure):
    text, fallback_used = await router.generate_with_fallback("section", messages)

    # Direct call (preferred model only, raises on failure):
    text = await router.generate("outline", messages, json_mode=True)

    # For pillar pages (tier=0 = 40k budget, NVIDIA primary):
    router = ModelRouter(job_id="p1", tier=0, cluster_id="dsar")
    text, _ = await router.generate_with_fallback("pillar", messages)
    """

    def __init__(
        self,
        job_id: str,
        tier: int,
        cluster_id: str = "general",
    ) -> None:
        self._ledger = TokenLedger(
            job_id=job_id,
            tier=tier,
            cluster_id=cluster_id,
        )
        log.info(
            "model_router_initialised",
            job_id=job_id,
            tier=tier,
            cluster_id=cluster_id,
            token_budget=self._ledger.budget_limit,
        )

    # ── public read-only properties ──────────────────────────────────────────

    @property
    def job_id(self) -> str:
        return self._ledger.job_id

    @property
    def tokens_spent(self) -> int:
        return self._ledger.tokens_spent

    @property
    def budget_remaining(self) -> int:
        return self._ledger.remaining

    @property
    def cost_usd(self) -> float:
        return self._ledger.cost_usd

    @property
    def fallback_used(self) -> bool:
        return self._ledger.fallback_used

    # ── primary public methods ───────────────────────────────────────────────

    async def generate(
        self,
        task: str,
        messages: list,
        json_mode: bool = False,
        tier_override: Optional[int] = None,
    ) -> str:
        """Call the preferred model for this task — no automatic fallback.

        Use generate_with_fallback() for resilience.
        Raises BudgetExceededError if the job's token budget is already exhausted.
        """
        self._guard_budget(task)
        config = self._resolve_config(task, tier_override)
        self._warn_if_no_system_message(messages, task)
        messages = self._inject_budget_pressure_if_needed(messages)

        if config.preferred_model == "groq":
            return await self._call_groq(
                task=task,
                messages=messages,
                temperature=config.temperature,
                json_mode=json_mode or config.json_mode,
            )
        return await self._call_nvidia(
            task=task,
            messages=messages,
            temperature=config.temperature,
            model_key=config.preferred_model,
        )

    async def generate_with_fallback(
        self,
        task: str,
        messages: list,
        json_mode: bool = False,
        tier_override: Optional[int] = None,
    ) -> tuple[str, bool]:
        """Call the preferred model; fall back to the secondary on any exception.

        Returns (content, fallback_was_used: bool).

        Routing rules:
        - Tier 3 posts: Groq only, never falls back (latency req, spec 2.3 / 2.9.A).
        - regulatory_section / pillar: NVIDIA primary → Groq fallback.
        - All other tasks: Groq primary → NVIDIA fallback.
        """
        self._guard_budget(task)
        effective_tier = (
            tier_override if tier_override is not None else self._ledger.tier
        )
        config = self._resolve_config(task, effective_tier)
        self._warn_if_no_system_message(messages, task)
        messages = self._inject_budget_pressure_if_needed(messages)

        # ── Tier 3: Groq ONLY — no fallback ─────────────────────────────────
        if effective_tier == 3:
            content = await self._call_groq(
                task=task,
                messages=messages,
                temperature=config.temperature,
                json_mode=json_mode or config.json_mode,
            )
            return content, False

        # ── NVIDIA-preferred tasks (regulatory_section, pillar) ──────────────
        # NVIDIA primary → Groq fallback
        if config.preferred_model != "groq":
            try:
                content = await self._call_nvidia(
                    task=task,
                    messages=messages,
                    temperature=config.temperature,
                    model_key=config.preferred_model,
                )
                return content, False
            except Exception as nvidia_exc:
                log.warning(
                    "nvidia_primary_failed_groq_fallback",
                    task=task,
                    model=config.preferred_model,
                    error=str(nvidia_exc),
                )
                content = await self._call_groq(
                    task=task,
                    messages=messages,
                    temperature=config.temperature,
                    json_mode=json_mode or config.json_mode,
                )
                self._ledger.fallback_used = True
                return content, True

        # ── Groq-preferred tasks — Groq primary → NVIDIA fallback ───────────
        try:
            content = await self._call_groq(
                task=task,
                messages=messages,
                temperature=config.temperature,
                json_mode=json_mode or config.json_mode,
            )
            return content, False
        except Exception as groq_exc:
            log.warning(
                "groq_failed_nvidia_fallback",
                task=task,
                error=str(groq_exc),
            )
            try:
                content = await self._call_nvidia(
                    task=task,
                    messages=messages,
                    temperature=config.temperature,
                    model_key="nvidia_blog",
                )
                self._ledger.fallback_used = True
                return content, True
            except Exception as nvidia_exc:
                raise RuntimeError(
                    f"Both Groq and NVIDIA NIM failed for task '{task}': "
                    f"Groq={groq_exc!s} | NVIDIA={nvidia_exc!s}"
                ) from nvidia_exc

    # ── internal: config resolution ──────────────────────────────────────────

    def _resolve_config(self, task: str, tier_override: Optional[int]) -> TaskConfig:
        """Return the effective TaskConfig, enforcing Tier 3 Groq-only rule."""
        config = TASK_ROUTING.get(task, TASK_ROUTING["section"])
        effective_tier = (
            tier_override if tier_override is not None else self._ledger.tier
        )
        # Spec 2.3 + 2.9.A: Tier 3 must ALWAYS use Groq, never NVIDIA NIM
        if effective_tier == 3 and not config.tier3_safe:
            log.info(
                "tier3_model_override_to_groq",
                task=task,
                original_preferred=config.preferred_model,
            )
            return TaskConfig(
                preferred_model="groq",
                temperature=config.temperature,
                json_mode=config.json_mode,
                tier3_safe=True,
            )
        return config

    # ── internal: guard & instrumentation ───────────────────────────────────

    def _guard_budget(self, task: str) -> None:
        if self._ledger.is_over_budget():
            raise BudgetExceededError(
                f"Job '{self._ledger.job_id}' exhausted its token budget "
                f"({self._ledger.tokens_spent}/{self._ledger.budget_limit} tokens). "
                f"Task '{task}' was blocked. Tier={self._ledger.tier}."
            )

    def _warn_if_no_system_message(self, messages: list, task: str) -> None:
        """Spec 2.9.C: every LLM call must carry the anti-hallucination system prompt."""
        has_system = any(m.get("role") == "system" for m in messages)
        if not has_system:
            log.warning(
                "missing_system_message",
                task=task,
                job_id=self._ledger.job_id,
                hint="Add ANTI_HALLUCINATION_SYSTEM_PROMPT as the system role message.",
            )

    def _inject_budget_pressure_if_needed(self, messages: list) -> list:
        """Append a concise-mode note when token budget falls below 25% remaining.

        Spec 2.9.B: 'if approaching token budget, the assembly step is instructed
        to prioritise completeness over depth.'  We apply this universally — the
        model adapts its verbosity when it receives the budget note.
        """
        if self._ledger.pct_used < 0.75:
            return messages

        note = (
            "\n\n[BUDGET NOTE: This generation job is approaching its token budget. "
            "Prioritise completeness and key facts over depth. "
            "Aim for the lower end of any word count target. Be concise.]"
        )
        messages = list(messages)  # shallow copy — never mutate the caller's list
        if messages and messages[-1].get("role") == "user":
            messages[-1] = {
                "role": "user",
                "content": messages[-1]["content"] + note,
            }
        return messages

    # ── internal: LLM calls ──────────────────────────────────────────────────

    async def _call_groq(
        self,
        task: str,
        messages: list,
        temperature: float,
        json_mode: bool = False,
    ) -> str:
        from src.config import settings

        client = _get_groq()
        kwargs: dict = {
            "model": settings.groq_model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": self._max_output_tokens(),
        }
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}

        response = await client.chat.completions.create(**kwargs)

        if response.usage:
            self._ledger.record(
                input_tokens=response.usage.prompt_tokens,
                output_tokens=response.usage.completion_tokens,
                model_key="groq",
                model_name=settings.groq_model,
                task=task,
            )

        return (response.choices[0].message.content or "").strip()

    async def _call_nvidia(
        self,
        task: str,
        messages: list,
        temperature: float,
        model_key: str = "nvidia_blog",
    ) -> str:
        from src.config import settings

        model_name_map = {
            "nvidia_blog":       settings.nvidia_model_blog,
            "nvidia_analytical": settings.nvidia_model_analytical,
            "nvidia_fast":       settings.nvidia_model_fast,
        }
        model_name = model_name_map.get(model_key, settings.nvidia_model_blog)

        client = _get_nvidia()
        response = await client.chat.completions.create(
            model=model_name,
            messages=messages,
            temperature=temperature,
            max_tokens=self._max_output_tokens(),
        )

        if response.usage:
            self._ledger.record(
                input_tokens=response.usage.prompt_tokens,
                output_tokens=response.usage.completion_tokens,
                model_key=model_key,
                model_name=model_name,
                task=task,
            )

        return (response.choices[0].message.content or "").strip()

    def _max_output_tokens(self) -> int:
        """Scale max output tokens down as the budget depletes.

        Preserves budget headroom for the remaining pipeline steps
        (assembly, metadata) that must always run after body generation.
        """
        remaining = self._ledger.remaining
        if remaining > 8_000:
            return 4_096
        elif remaining > 3_000:
            return 2_048
        else:
            return 1_024
