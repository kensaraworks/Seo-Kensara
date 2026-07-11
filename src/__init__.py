import sys
import types
from pydantic import BaseModel, Field
from typing import List, Optional

def _create_stub_module(name: str, attrs: dict) -> types.ModuleType:
    module = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(module, k, v)
    sys.modules[name] = module
    return module

# Define a stub BlogPost model so downstream components can import/typecheck it.
class StubBlogPost(BaseModel):
    title: str = ""
    meta_description: str = ""
    slug: str = ""
    primary_keyword: str = ""
    secondary_keywords: List[str] = Field(default_factory=list)
    content_markdown: str = ""
    word_count: int = 0
    cta_url: str = "https://www.kensara.in/book-demo"
    cluster: str = "general"
    intent: str = "informational"
    tier: int = 1
    geo_score: int = 0
    qa_score: float = 0.0
    risk_level: str = "HIGH"
    approved: bool = False
    flagged: bool = False
    flag_reason: str = ""
    author: str = "Mr Rudraksh Tatwal"
    author_credentials: str = "Founder & CEO, KensaraAI"
    date_created: str = ""
    date_published: Optional[str] = None
    date_modified: Optional[str] = None
    schema_json: str = "{}"
    internal_links_injected: List[str] = Field(default_factory=list)
    source_story_url: Optional[str] = None
    featured_image_alt: str = ""
    wp_post_id: Optional[str] = None
    wp_post_url: Optional[str] = None
    image_url: Optional[str] = None

# Check if real blog_writer can be imported, otherwise stub it
try:
    import src.agents.blog_writer
except ImportError:
    _create_stub_module("src.agents.blog_writer", {
        "BlogPost": StubBlogPost,
        "generate_blog_post": None,
        "KEYWORD_ROTATION": [],
        "_get_keyword_rotation": lambda: [],
        "_step2_generate_sections": None,
    })

# Check if file_publisher can be imported, otherwise stub it
try:
    import src.publishers.file_publisher
except ImportError:
    _create_stub_module("src.publishers.file_publisher", {
        "save_blog_draft": lambda post: None,
    })

# Check if supabase_publisher can be imported, otherwise stub it
try:
    import src.publishers.supabase_publisher
except ImportError:
    _create_stub_module("src.publishers.supabase_publisher", {
        "publish_to_supabase": lambda post: {"status": "skipped", "reason": "stub"},
        "publish_to_supabase_sync": lambda post: {"status": "skipped", "reason": "stub"},
    })

# Check if quality.checker can be imported, otherwise stub it
try:
    import src.quality.checker
except ImportError:
    if "src.quality" not in sys.modules:
        _create_stub_module("src.quality", {})
    _create_stub_module("src.quality.checker", {
        "check_blog_quality": None,
    })

# Check if RAG retrieval can be imported, otherwise stub it
try:
    import src.rag.retrieval
except ImportError:
    if "src.rag" not in sys.modules:
        _create_stub_module("src.rag", {})
    _create_stub_module("src.rag.retrieval", {
        "retrieve": lambda *args, **kwargs: [],
    })

# Check if RAG query_library can be imported, otherwise stub it
try:
    import src.rag.query_library
except ImportError:
    if "src.rag" not in sys.modules:
        _create_stub_module("src.rag", {})
    _create_stub_module("src.rag.query_library", {
        "get_dpdpa_grounding": lambda *args, **kwargs: [],
    })

# Check if RAG context_builder can be imported, otherwise stub it
try:
    import src.rag.context_builder
except ImportError:
    if "src.rag" not in sys.modules:
        _create_stub_module("src.rag", {})
    _create_stub_module("src.rag.context_builder", {
        "build_context_block": lambda *args, **kwargs: "",
    })
