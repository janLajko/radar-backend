from __future__ import annotations

from urllib.parse import urlencode

from radar_backend import config


def monitoring_url(user_action_id: int) -> str:
    query = urlencode({"user_action_id": str(user_action_id)})
    return f"{_frontend_base_url()}/compliance-radar?{query}"


def policy_impact_review_url(policy_update_id: int) -> str:
    return f"{_frontend_base_url()}/compliance-radar/review/{policy_update_id}"


def reclassify_url() -> str:
    query = urlencode({"tab": "compliance_radar_alert"})
    return f"{_frontend_base_url()}/classifier?{query}"


def recalculate_url() -> str:
    query = urlencode({"tab": "compliance_radar_alert"})
    return f"{_frontend_base_url()}/product-sandbox?{query}"


def unsubscribe_url(token: str) -> str:
    query = urlencode({"token": token})
    return f"{_frontend_base_url()}/compliance-radar/unsubscribe?{query}"


def view_details_url(user_action_id: int) -> str:
    query = urlencode(
        {
            "user_action_id": str(user_action_id),
            "open_type": "view_details",
        }
    )
    return f"{_frontend_base_url()}/compliance-radar?{query}"


def _frontend_base_url() -> str:
    return config.frontend_base_url()
