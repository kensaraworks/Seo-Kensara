from pydantic_settings import BaseSettings
from pydantic import Field
import structlog

log = structlog.get_logger()


class Settings(BaseSettings):
    # Azure OpenAI — used for news scoring only (complex JSON)
    azure_openai_endpoint: str = Field("", env="AZURE_OPENAI_ENDPOINT")
    azure_openai_key: str = Field("", env="AZURE_OPENAI_KEY")
    azure_openai_deployment: str = Field("gpt-4o", env="AZURE_OPENAI_DEPLOYMENT")

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

    # WordPress — kensara.in
    wordpress_url: str = Field("https://kensara.in", env="WORDPRESS_URL")
    wordpress_user: str = Field("", env="WORDPRESS_USER")
    wordpress_app_password: str = Field("", env="WORDPRESS_APP_PASSWORD")

    # LinkedIn
    linkedin_access_token: str = Field("", env="LINKEDIN_ACCESS_TOKEN")
    linkedin_organization_id: str = Field("", env="LINKEDIN_ORGANIZATION_ID")

    # Mailchimp — newsletter
    mailchimp_api_key: str = Field("", env="MAILCHIMP_API_KEY")
    mailchimp_list_id: str = Field("", env="MAILCHIMP_LIST_ID")

    # Content
    content_output_dir: str = Field("drafts", env="CONTENT_OUTPUT_DIR")
    blog_cadence: str = Field("daily", env="BLOG_CADENCE")  # daily | weekly
    integration_test: bool = Field(False, env="INTEGRATION_TEST")

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"


settings = Settings()
