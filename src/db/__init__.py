"""Database module for KensaraAI SEO Pipeline.

Provides direct, unified Supabase client integration for all relational tables,
logs, queue items, vector embeddings, and persistent JSON stores.
"""
from src.db.supabase_client import (
    SupabaseDB,
    get_supabase_db,
    is_supabase_configured,
)

__all__ = [
    "SupabaseDB",
    "get_supabase_db",
    "is_supabase_configured",
]
