from __future__ import annotations

import json
import logging
import math
import time
import urllib.error
import urllib.request
from typing import Any

from radar_backend import config
from radar_backend.domain import WebhookEventModel

_LARK_TIMEOUT_SECONDS = 10
_RETRY_BACKOFF_SECONDS = (10, 30)
_MAX_ATTEMPT_COUNT = len(_RETRY_BACKOFF_SECONDS) + 1
_MAX_RESPONSE_BODY_BYTES = 64 * 1024
_MAX_ERROR_BODY_LENGTH = 1000

logger = logging.getLogger(__name__)


class WebhookSendError(RuntimeError):
    pass


class WebhookService:
    def send_webhook_event(self, event: WebhookEventModel) -> None:
        _post_lark_message_with_retry(event)


def _build_lark_message(event: WebhookEventModel) -> str:
    lines = [
        "Compliance Radar Alert",
        "",
        f"event_type: {event['event_type']}",
        f"entity_type: {event['entity_type']}",
        f"entity_id: {event['entity_id']}",
        f"event_id: {event['id']}",
        "",
        "payload:",
    ]

    payload = event["payload"]
    if not payload:
        lines.append("\t{}")
        return "\n".join(lines)

    for key, value in payload.items():
        lines.append(f"\t{key}: {_format_payload_value(value)}")

    return "\n".join(lines)


def _build_lark_request_body(message: str) -> dict[str, object]:
    return {
        "msg_type": "text",
        "content": {
            "text": message,
        },
    }


def _post_lark_message_with_retry(event: WebhookEventModel) -> None:
    lark_webhook_url = config.lark_webhook_url()
    message = _build_lark_message(event)
    body = _build_lark_request_body(message)
    last_error: WebhookSendError | None = None

    for attempt_index in range(_MAX_ATTEMPT_COUNT):
        if attempt_index > 0:
            time.sleep(_RETRY_BACKOFF_SECONDS[attempt_index - 1])

        try:
            _post_lark_message_once(lark_webhook_url, body)
            return
        except WebhookSendError as exc:
            last_error = exc
            if attempt_index < _MAX_ATTEMPT_COUNT - 1:
                logger.warning(
                    "lark webhook attempt failed: "
                    "id=%s event_type=%s entity_type=%s entity_id=%s "
                    "attempt=%s/%s error=%s",
                    event["id"],
                    event["event_type"],
                    event["entity_type"],
                    event["entity_id"],
                    attempt_index + 1,
                    _MAX_ATTEMPT_COUNT,
                    exc,
                )

    if last_error is not None:
        raise last_error
    raise WebhookSendError("Lark webhook failed without captured error")


def _post_lark_message_once(url: str, body: dict[str, object]) -> None:
    try:
        request_body = json.dumps(body, ensure_ascii=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise WebhookSendError(
            f"Lark webhook body is not JSON serializable: {exc}"
        ) from exc

    request = urllib.request.Request(
        url,
        data=request_body,
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "Accept": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=_LARK_TIMEOUT_SECONDS) as response:
            response_body = _read_response_body(response)
            status = response.getcode()
    except urllib.error.HTTPError as exc:
        response_body = _read_response_body(exc)
        raise WebhookSendError(
            f"Lark webhook HTTP {exc.code}: {_truncate(response_body)}"
        ) from exc
    except OSError as exc:
        raise WebhookSendError(f"Lark webhook request failed: {exc}") from exc

    if status < 200 or status >= 300:
        raise WebhookSendError(f"Lark webhook HTTP {status}: {_truncate(response_body)}")

    try:
        response_json = json.loads(response_body)
    except json.JSONDecodeError as exc:
        raise WebhookSendError(
            f"Lark webhook returned non-JSON response: {_truncate(response_body)}"
        ) from exc

    if _is_lark_success_response(response_json):
        return

    raise WebhookSendError(
        f"Lark webhook returned non-zero code: {_truncate(_compact_json(response_json))}"
    )


def _is_lark_success_response(response_json: object) -> bool:
    if not isinstance(response_json, dict):
        return False

    if "code" in response_json:
        return response_json.get("code") == 0
    if "StatusCode" in response_json:
        return response_json.get("StatusCode") == 0
    return False


def _read_response_body(response: Any) -> str:
    raw_body = response.read(_MAX_RESPONSE_BODY_BYTES + 1)
    response_body = raw_body[:_MAX_RESPONSE_BODY_BYTES].decode(
        "utf-8",
        errors="replace",
    )
    if len(raw_body) > _MAX_RESPONSE_BODY_BYTES:
        return response_body + "..."
    return response_body


def _format_payload_value(value: object) -> str:
    if isinstance(value, str):
        return value.replace("\r", "\\r").replace("\n", "\\n")
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if math.isnan(value):
            return "NaN"
        if math.isinf(value):
            return "Infinity" if value > 0 else "-Infinity"
        return str(value)
    return _compact_json(value)


def _compact_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def _truncate(value: str) -> str:
    if len(value) <= _MAX_ERROR_BODY_LENGTH:
        return value
    return value[:_MAX_ERROR_BODY_LENGTH] + "..."
