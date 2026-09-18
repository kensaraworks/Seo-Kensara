from pydantic_settings import BaseSettings
from pydantic import Field
import structlog
from dotenv import load_dotenv

load_dotenv()

log = structlog.get_logger()



class Settings(BaseSettings):

    # Groq — primary content generation (blogs, LinkedIn, newsletter) — FREE
    groq_api_key: str = Field("", env="GROQ_API_KEY")
    groq_model: str = Field("llama-3.3-70b-versatile", env="GROQ_MODEL")

    # NVIDIA NIM — primary content generation — FREE
    # Endpoint: https://integrate.api.nvidia.com/v1  (OpenAI-compatible)
    nvidia_api_key: str = Field("", env="NVIDIA_API_KEY")
    # Blog / long-form SEO content
    nvidia_model_blog: str = Field("mistralai/mistral-medium-3.5-128b", env="NVIDIA_MODEL_BLOG")
    # Analytical / comparison content (DPDPA vs GDPR etc.)
    nvidia_model_analytical: str = Field("qwen/qwen3-5-122b-a10b", env="NVIDIA_MODEL_ANALYTICAL")
    # Fast drafts — LinkedIn posts, newsletter bullets
    nvidia_model_fast: str = Field("deepseek-ai/deepseek-v4-flash", env="NVIDIA_MODEL_FAST")

    # Tavily — real-time news search (1000 credits/month free)
    tavily_api_key: str = Field("", env="TAVILY_API_KEY")

    # Serper.dev — Google rank tracking (2500 free queries)
    serper_api_key: str = Field("", env="SERPER_API_KEY")

    # Perplexity API — AI citation monitoring
    perplexity_api_key: str = Field("", env="PERPLEXITY_API_KEY")

    # Gemini API key (direct integration for Gemini GEO citation monitoring)
    gemini_api_key: str = Field("", env="GEMINI_API_KEY")

    # AllToken API — for GPT, Claude GEO monitoring
    alltoken_api_key: str = Field("", env="ALLTOKEN_API_KEY")
    alltoken_base_url: str = Field("https://api.openai.com/v1", env="ALLTOKEN_BASE_URL")

    # WordPress — kensara.in
    wordpress_url: str = Field("https://kensara.in", env="WORDPRESS_URL")
    wordpress_user: str = Field("", env="WORDPRESS_USER")
    wordpress_app_password: str = Field("", env="WORDPRESS_APP_PASSWORD")
    wordpress_enforcement_tracker_slug: str = Field("enforcement-tracker", env="WORDPRESS_ENFORCEMENT_TRACKER_SLUG")

    # LinkedIn
    linkedin_access_token: str = Field("", env="LINKEDIN_ACCESS_TOKEN")
    linkedin_organization_id: str = Field("", env="LINKEDIN_ORGANIZATION_ID")

    # Mailchimp — newsletter
    mailchimp_api_key: str = Field("", env="MAILCHIMP_API_KEY")
    mailchimp_list_id: str = Field("", env="MAILCHIMP_LIST_ID")

    # Supabase — blog publishing to public.blogs table
    # Get URL from: Supabase Dashboard → Project Settings → API → Project URL
    # Get key from: Supabase Dashboard → Project Settings → API → service_role secret
    supabase_url: str = Field("", env="SUPABASE_URL")
    supabase_service_key: str = Field("", env="SUPABASE_SERVICE_KEY")

    # Storage configuration (Azure Persistence)
    # On local, defaults to current directory (".").
    # On Azure, set DATA_DIR to "/home/kensara_data" to survive redeployments.
    data_dir: str = Field(".", env="DATA_DIR")

    # Content
    content_output_dir: str = Field("drafts", env="CONTENT_OUTPUT_DIR")
    blog_cadence: str = Field("daily", env="BLOG_CADENCE")  # daily | weekly
    integration_test: bool = Field(False, env="INTEGRATION_TEST")

    # News recency — RSS entries older than this many days are dropped before
    # entering the pipeline. Scored items also receive a recency penalty.
    # Override with NEWS_MAX_AGE_DAYS=180 for broader regulatory coverage.
    news_max_age_days: int = Field(90, env="NEWS_MAX_AGE_DAYS")

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"


#: Populated when an environment variable had to be discarded. Surfaced by
#: /healthz so a bad value is visible rather than silently defaulted.
SETTINGS_ERRORS: list[str] = []


def _load_settings() -> "Settings":
    """Build Settings, surviving a malformed environment variable.

    A single bad value — `NEWS_MAX_AGE_DAYS=` or a typo'd boolean — otherwise
    raises ValidationError at import and takes the whole application down with
    it. On a serverless host that surfaces as an opaque invocation failure with
    no indication that an env var is to blame. The offending variables are
    dropped, their defaults are used, and the problem is reported loudly at
    /healthz instead of fatally at import.
    """
    import os as _os

    from pydantic import ValidationError

    try:
        return Settings()
    except ValidationError as exc:
        discarded = []
        for error in exc.errors():
            for location in error.get("loc", ()):  # field name
                name = str(location)
                discarded.append(f"{name}: {error.get('msg', 'invalid')}")
                _os.environ.pop(name.upper(), None)
                _os.environ.pop(name, None)
        SETTINGS_ERRORS.extend(discarded)
        log.error("settings_invalid_env_discarded", fields=discarded)
        try:
            return Settings()
        except ValidationError:
            # Still unusable: fall back to the declared defaults wholesale
            # rather than refusing to start.
            SETTINGS_ERRORS.append("settings fell back to defaults entirely")
            log.error("settings_fallback_to_defaults")
            return Settings.model_construct()


settings = _load_settings()

# ── Dynamic Persistence Resolution ───────────────────────────────────────────
from pathlib import Path
import shutil
import tempfile
import os

# Auto-detect Vercel or AWS Lambda serverless execution environment
if os.getenv("VERCEL") == "1" or os.getenv("AWS_LAMBDA_FUNCTION_NAME"):
    if settings.data_dir == ".":
        settings.data_dir = "/tmp"

persistent_base = Path(settings.data_dir).resolve()

# Fallback to system temp directory (/tmp) if persistent_base is read-only
try:
    test_file = persistent_base / ".perm_check"
    test_file.write_text("ok", encoding="utf-8")
    test_file.unlink(missing_ok=True)
except Exception:
    persistent_base = Path(tempfile.gettempdir()).resolve()

# Update content_output_dir to point to persistent directory
settings.content_output_dir = str(persistent_base / "drafts")

# Ensure all subdirectories inside drafts exist
try:
    for sub in ("blogs", "linkedin", "newsletters", "reports", "flagged", ".cache"):
        Path(settings.content_output_dir, sub).mkdir(parents=True, exist_ok=True)
except Exception:
    pass

# Centralize database path
settings_database_path = str(Path(settings.content_output_dir) / ".cache" / "jobs.db")

# Setup persistent enforcement tracker path
try:
    tracker_dir = persistent_base / "data"
    tracker_dir.mkdir(parents=True, exist_ok=True)
    settings_enforcement_tracker_path = str(tracker_dir / "enforcement_tracker.json")

    # Seed/copy default enforcement tracker if not present
    if not Path(settings_enforcement_tracker_path).exists():
        # Resolved from this file: on Vercel the CWD is /var/task, not the
        # project root, so a relative path silently finds nothing.
        default_tr = Path(__file__).resolve().parent.parent / "data" / "enforcement_tracker.json"
        if default_tr.exists():
            shutil.copy(default_tr, settings_enforcement_tracker_path)
        else:
            Path(settings_enforcement_tracker_path).write_text("{}", encoding="utf-8")
except Exception:
    settings_enforcement_tracker_path = "data/enforcement_tracker.json"





# ── Credential resolution ────────────────────────────────────────────────────
# Values that mean "not configured". Copying .env.example into a dashboard is a
# common way to end up with a variable that is set but useless.
PLACEHOLDER_SECRETS = {"", "replace_me", "replace-me", "changeme", "your_key_here", "none", "null", "todo"}

#: Every credential the app reads, as ENV_VAR -> what it powers. Used for the
#: diagnostics report, so the list of things to check is never out of date.
CREDENTIAL_ENV_VARS = {
    "GROQ_API_KEY": "Groq — primary LLM",
    "NVIDIA_API_KEY": "NVIDIA NIM — blog/long-form generation",
    "TAVILY_API_KEY": "Tavily — news and enforcement search",
    "SERPER_API_KEY": "Serper — SERP and rank tracking",
    "PERPLEXITY_API_KEY": "Perplexity — GEO citation monitoring",
    "GEMINI_API_KEY": "Gemini — GEO citation monitoring",
    "ALLTOKEN_API_KEY": "AllToken — GPT/Claude GEO monitoring",
    "SUPABASE_URL": "Supabase — database",
    "SUPABASE_SERVICE_KEY": "Supabase — database",
    "WORDPRESS_USER": "WordPress — publishing",
    "WORDPRESS_APP_PASSWORD": "WordPress — publishing",
    "MAILCHIMP_API_KEY": "Mailchimp — newsletter",
    "MAILCHIMP_LIST_ID": "Mailchimp — newsletter",
    "CRON_SECRET": "Vercel Cron — guards the tracker refresh",
}


def _clean_secret(value: str | None) -> str:
    text = (value or "").strip()
    return "" if text.lower() in PLACEHOLDER_SECRETS else text


def get_secret(env_name: str) -> str:
    """Resolve a credential from settings first, then the raw environment.

    The health checks used to read os.getenv() directly while the rest of the
    app read `settings`, so a credential could be live for one and missing for
    the other. Placeholder values are treated as absent.
    """
    import os as _os

    value = _clean_secret(getattr(settings, env_name.lower(), ""))
    return value or _clean_secret(_os.getenv(env_name))


def credential_report() -> dict:
    """Which credentials the process can actually see. Never returns a value.

    Answers the question a dashboard cannot: is the variable missing because it
    was never set, or because the running deployment predates it? On Vercel,
    environment variables only reach a function that was deployed after they
    were saved.
    """
    import os as _os

    report = {}
    for env_name, purpose in CREDENTIAL_ENV_VARS.items():
        raw_env = _os.getenv(env_name)
        resolved = get_secret(env_name)
        if resolved:
            state = "configured"
        elif raw_env is not None and not _clean_secret(raw_env):
            state = "placeholder"  # set, but to something like "replace_me"
        else:
            state = "missing"
        report[env_name] = {
            "state": state,
            "purpose": purpose,
            "in_process_env": raw_env is not None,
            "length": len(resolved),
        }
    return report
