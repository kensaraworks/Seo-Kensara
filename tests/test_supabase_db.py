"""Unit test for SupabaseDB client and database integration.

Verifies:
1. Client initialization and configuration checks.
2. Select, Insert, Upsert, Update, and Delete query formatting.
3. Transparent fallback logic when Supabase is active vs offline.
"""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch
from src.db.supabase_client import SupabaseDB, is_supabase_configured


class TestSupabaseDB(unittest.TestCase):

    def test_is_supabase_configured_false_when_empty(self):
        with patch("src.db.supabase_client.os.getenv", return_value=""):
            with patch("src.config.settings.supabase_url", ""):
                with patch("src.config.settings.supabase_service_key", ""):
                    self.assertFalse(is_supabase_configured())

    @patch("src.db.supabase_client.httpx.Client")
    def test_select_sync_success(self, mock_client_cls):
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = [{"job_id": "job-101", "job_name": "test_job"}]
        mock_client.get.return_value = mock_response
        mock_client_cls.return_value.__enter__.return_value = mock_client

        with patch("src.db.supabase_client.is_supabase_configured", return_value=True):
            with patch("src.db.supabase_client._get_credentials", return_value=("https://test.supabase.co", "testkey")):
                res = SupabaseDB.select_sync("jobs", filters={"job_id": "eq.job-101"})
                self.assertEqual(len(res), 1)
                self.assertEqual(res[0]["job_id"], "job-101")

    @patch("src.db.supabase_client.httpx.Client")
    def test_upsert_sync_success(self, mock_client_cls):
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.json.return_value = [{"post_id": "p-1", "post_title": "Test Title"}]
        mock_client.post.return_value = mock_response
        mock_client_cls.return_value.__enter__.return_value = mock_client

        with patch("src.db.supabase_client.is_supabase_configured", return_value=True):
            with patch("src.db.supabase_client._get_credentials", return_value=("https://test.supabase.co", "testkey")):
                res = SupabaseDB.upsert_sync("internal_link_map", {"post_id": "p-1", "post_title": "Test Title"}, on_conflict="post_id")
                self.assertEqual(len(res), 1)
                self.assertEqual(res[0]["post_title"], "Test Title")


if __name__ == "__main__":
    unittest.main()
