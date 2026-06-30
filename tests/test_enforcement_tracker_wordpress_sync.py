from src.agents.enforcement_tracker import build_wordpress_page_payload


def test_build_wordpress_page_payload_uses_tracker_slug_and_content():
    tracker_data = {
        "metadata": {"last_updated": "2026-06-29"},
        "statistics": {
            "total_enforcement_actions": 0,
            "total_pre_dpdpa_actions": 0,
            "total_cert_in_actions": 0,
            "total_all_sections": 0,
            "by_sector": {},
            "by_violation_type": {},
            "by_outcome": {},
        },
        "enforcement_actions": [],
        "cert_in_enforcement": [],
        "pre_dpdpa_actions": [],
    }

    payload = build_wordpress_page_payload(tracker_data)

    assert payload["slug"] == "enforcement-tracker"
    assert payload["title"] == "DPDPA Enforcement Tracker India"
    assert payload["status"] == "publish"
    assert "DPDPA Enforcement Tracker India" in payload["content"]
    assert "Updated:" in payload["content"]
