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


settings = Settings()

# ── Dynamic Persistence Resolution ───────────────────────────────────────────
from pathlib import Path
import shutil

persistent_base = Path(settings.data_dir).resolve()

# Update content_output_dir to point to persistent directory
settings.content_output_dir = str(persistent_base / "drafts")

# Ensure all subdirectories inside drafts exist
for sub in ("blogs", "linkedin", "newsletters", "reports", ".cache"):
    Path(settings.content_output_dir, sub).mkdir(parents=True, exist_ok=True)

# Centralize database path
settings_database_path = str(Path(settings.content_output_dir) / ".cache" / "jobs.db")

# Setup persistent enforcement tracker path
tracker_dir = persistent_base / "data"
tracker_dir.mkdir(parents=True, exist_ok=True)
settings_enforcement_tracker_path = str(tracker_dir / "enforcement_tracker.json")

# Seed/copy default enforcement tracker if not present
if not Path(settings_enforcement_tracker_path).exists():
    default_tr = Path("data/enforcement_tracker.json")
    if default_tr.exists():
        shutil.copy(default_tr, settings_enforcement_tracker_path)
    else:
        Path(settings_enforcement_tracker_path).write_text("{}", encoding="utf-8")

