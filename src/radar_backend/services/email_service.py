from __future__ import annotations

import html
import logging
import smtplib
import ssl
import time
from email.message import EmailMessage
from email.utils import formataddr
from typing import NoReturn, cast

from radar_backend import config
from radar_backend.domain import (
    ActionType,
    EmailActionSummary,
    EmailAffectedProduct,
    EmailDeliveryModel,
    EmailDeliveryPayload,
    NotificationRecipientModel,
)
from radar_backend.services.frontend_urls import (
    monitoring_url,
    recalculate_url,
    reclassify_url,
    unsubscribe_url,
    view_details_url,
)
from radar_backend.services.rate_limit import TokenBucket

_SMTP_TIMEOUT_SECONDS = 30
_SMTP_EMAIL_RPM = 800
_SMTP_EMAIL_BURST = 40
_RETRY_BACKOFF_SECONDS = (10, 30)
_MAX_ATTEMPT_COUNT = len(_RETRY_BACKOFF_SECONDS) + 1
_SUBJECT_MAX_LENGTH = 70
_HEADLINE_SUBJECT_MAX_LENGTH = 50

logger = logging.getLogger(__name__)
_SMTP_EMAIL_BUCKET = TokenBucket(
    name="smtp_email",
    rpm=_SMTP_EMAIL_RPM,
    burst=_SMTP_EMAIL_BURST,
)


class EmailSendError(RuntimeError):
    pass


class EmailService:
    def missing_required_configuration(self) -> list[str]:
        return missing_required_configuration()

    def send_email_delivery(
        self,
        delivery: EmailDeliveryModel,
        recipient: NotificationRecipientModel,
    ) -> None:
        _send_email_delivery_with_retry(delivery, recipient)


def missing_required_configuration() -> list[str]:
    missing: list[str] = []
    for name, getter in (
        ("SMTP_HOST", config.smtp_host),
        ("SMTP_PORT", config.smtp_port),
        ("SMTP_USERNAME", config.smtp_username),
        ("SMTP_PASSWORD", config.smtp_password),
        ("SMTP_USE_TLS", config.smtp_use_tls),
        ("FROM_EMAIL", config.from_email),
        ("FROM_NAME", config.from_name),
        ("FRONTEND_BASE_URL", config.frontend_base_url),
    ):
        try:
            getter()
        except ValueError:
            missing.append(name)
    return missing


def _send_email_delivery_with_retry(
    delivery: EmailDeliveryModel,
    recipient: NotificationRecipientModel,
) -> None:
    message = _build_email_message(delivery, recipient)
    last_error: EmailSendError | None = None

    for attempt_index in range(_MAX_ATTEMPT_COUNT):
        if attempt_index > 0:
            time.sleep(_RETRY_BACKOFF_SECONDS[attempt_index - 1])

        try:
            _SMTP_EMAIL_BUCKET.acquire(1)
            _send_email_message_once(message)
            return
        except EmailSendError as exc:
            last_error = exc
            if attempt_index < _MAX_ATTEMPT_COUNT - 1:
                logger.warning(
                    "smtp email attempt failed: delivery_id=%s recipient_id=%s "
                    "recipient_email=%s user_action_id=%s attempt=%s/%s error=%s",
                    delivery["id"],
                    recipient["id"],
                    recipient["email"],
                    delivery["user_action_id"],
                    attempt_index + 1,
                    _MAX_ATTEMPT_COUNT,
                    exc,
                )

    if last_error is not None:
        raise last_error
    raise EmailSendError("SMTP email failed without captured error")


def _send_email_message_once(message: EmailMessage) -> None:
    try:
        with smtplib.SMTP(
            config.smtp_host(),
            config.smtp_port(),
            timeout=_SMTP_TIMEOUT_SECONDS,
        ) as server:
            if config.smtp_use_tls():
                server.starttls(context=ssl.create_default_context())
                server.ehlo()
            server.login(config.smtp_username(), config.smtp_password())
            server.send_message(message)
    except (OSError, smtplib.SMTPException) as exc:
        raise EmailSendError(f"SMTP email request failed: {exc}") from exc


def _build_email_message(
    delivery: EmailDeliveryModel,
    recipient: NotificationRecipientModel,
) -> EmailMessage:
    payload = _validated_payload(delivery["payload"])
    urls = _render_urls(delivery, recipient)
    subject = _build_subject(payload)
    text_body = _build_text_body(payload, urls)
    html_body = _build_html_body(payload, urls)

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = formataddr((config.from_name(), config.from_email()))
    message["To"] = recipient["email"]
    message.set_content(text_body)
    message.add_alternative(html_body, subtype="html")
    return message


def _validated_payload(payload: object) -> EmailDeliveryPayload:
    if not isinstance(payload, dict):
        _invalid_payload("payload must be an object")

    payload_dict = cast(dict[str, object], payload)
    account_owner_email = _require_optional_str(payload_dict, "account_owner_email")
    if account_owner_email is not None and not account_owner_email.strip():
        payload_dict.pop("account_owner_email", None)
    _require_str(payload_dict, "source_label")
    _require_optional_str(payload_dict, "reference_number")
    _require_str(payload_dict, "headline")
    _require_str(payload_dict, "summary")
    _require_str(payload_dict, "source_url")

    affected_products = payload_dict.get("affected_products")
    if not isinstance(affected_products, list) or not affected_products:
        _invalid_payload("affected_products must be a non-empty list")
    for product in affected_products:
        _validate_affected_product(product)

    action_summaries = payload_dict.get("action_summaries")
    if not isinstance(action_summaries, list) or not action_summaries:
        _invalid_payload("action_summaries must be a non-empty list")
    for action_summary in action_summaries:
        _validate_action_summary(action_summary)

    return cast(EmailDeliveryPayload, payload_dict)


def _validate_affected_product(value: object) -> None:
    if not isinstance(value, dict):
        _invalid_payload("affected product must be an object")
    product = cast(EmailAffectedProduct, value)
    _require_str(product, "product_name")
    _require_str(product, "hts_code")


def _validate_action_summary(value: object) -> None:
    if not isinstance(value, dict):
        _invalid_payload("action summary must be an object")
    action_summary = cast(EmailActionSummary, value)
    _require_action_type(action_summary, "action_type")
    _require_positive_int(action_summary, "product_count")
    _require_optional_str(action_summary, "effective_date")


def _require_str(payload: dict[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        _invalid_payload(f"{key} must be a non-empty string")
    return value


def _require_optional_str(payload: dict[str, object], key: str) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        _invalid_payload(f"{key} must be a string or null")
    return value


def _require_positive_int(payload: dict[str, object], key: str) -> int:
    value = payload.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        _invalid_payload(f"{key} must be an integer")
    if value <= 0:
        _invalid_payload(f"{key} must be positive")
    return value


def _require_action_type(payload: dict[str, object], key: str) -> ActionType:
    value = payload.get(key)
    if not isinstance(value, str):
        _invalid_payload(f"{key} must be a string")
    try:
        return ActionType(value)
    except ValueError as exc:
        raise EmailSendError(f"invalid email delivery payload: unknown {key}={value}") from exc


def _invalid_payload(message: str) -> NoReturn:
    raise EmailSendError(f"invalid email delivery payload: {message}")


type _EmailUrls = dict[str, str]


def _render_urls(
    delivery: EmailDeliveryModel,
    recipient: NotificationRecipientModel,
) -> _EmailUrls:
    user_action_id = delivery["user_action_id"]
    return {
        "monitoring": monitoring_url(user_action_id),
        "reclassify": reclassify_url(),
        "recalculate": recalculate_url(),
        "unsubscribe": unsubscribe_url(recipient["unsubscribe_token"]),
        "view_details": view_details_url(user_action_id),
    }


def _build_subject(payload: EmailDeliveryPayload) -> str:
    source_label = payload["source_label"]
    reference_number = payload.get("reference_number")
    headline = _truncate(payload["headline"], _HEADLINE_SUBJECT_MAX_LENGTH)
    if reference_number:
        subject = f"[GingerControl] {source_label} {reference_number}: {headline}"
    else:
        subject = f"[GingerControl] {source_label}: {headline}"
    return _truncate(subject, _SUBJECT_MAX_LENGTH)


def _build_text_body(payload: EmailDeliveryPayload, urls: _EmailUrls) -> str:
    products = payload["affected_products"]
    footer_lines = [
        f"Original source: {payload['source_url']}",
        f"Monitoring page: {urls['monitoring']}",
        f"Unsubscribe: {urls['unsubscribe']}",
    ]
    account_owner_email = payload.get("account_owner_email")
    if account_owner_email:
        footer_lines.append(f"Account owner: {account_owner_email}")

    lines = [
        f"A new {payload['source_label']} update affects {len(products)} products on your sandbox.",
        "",
        "What happened",
        payload["summary"],
        "",
        "Affected products",
    ]
    lines.extend(_format_product_text(product) for product in products)
    lines.extend(
        [
            "",
            "What you need to do",
        ]
    )
    lines.extend(
        _format_action_summary_text(action_summary, urls)
        for action_summary in payload["action_summaries"]
    )
    lines.extend(
        [
            "",
            f"View full briefing: {urls['view_details']}",
            "",
        ]
    )
    lines.extend(footer_lines)
    return "\n".join(lines)


def _build_html_body(payload: EmailDeliveryPayload, urls: _EmailUrls) -> str:
    product_items = "\n".join(
        f"<li>{html.escape(_format_product_label(product))}</li>"
        for product in payload["affected_products"]
    )
    action_items = "\n".join(
        _format_action_summary_html(action_summary, urls)
        for action_summary in payload["action_summaries"]
    )
    footer_lines = [
        f"Original source: <a href=\"{html.escape(payload['source_url'], quote=True)}\">"
        f"{html.escape(payload['source_url'])}</a><br>",
        f"Monitoring page: <a href=\"{html.escape(urls['monitoring'], quote=True)}\">"
        "Compliance Radar</a><br>",
        f"Unsubscribe: <a href=\"{html.escape(urls['unsubscribe'], quote=True)}\">"
        "unsubscribe</a>",
    ]
    account_owner_email = payload.get("account_owner_email")
    if account_owner_email:
        footer_lines[-1] = footer_lines[-1] + "<br>"
        footer_lines.append(f"Account owner: {html.escape(account_owner_email)}")

    return "\n".join(
        [
            "<!doctype html>",
            "<html>",
            "<body>",
            f"<p>A new {html.escape(payload['source_label'])} update affects "
            f"{len(payload['affected_products'])} products on your sandbox.</p>",
            "<h2>What happened</h2>",
            f"<p>{html.escape(payload['summary'])}</p>",
            "<h2>Affected products</h2>",
            f"<ul>{product_items}</ul>",
            "<h2>What you need to do</h2>",
            f"<ul>{action_items}</ul>",
            f"<p><a href=\"{html.escape(urls['view_details'], quote=True)}\">View full briefing</a></p>",
            "<hr>",
            "<p>",
            *footer_lines,
            "</p>",
            "</body>",
            "</html>",
        ]
    )


def _format_product_text(product: EmailAffectedProduct) -> str:
    return f"- {_format_product_label(product)}"


def _format_product_label(product: EmailAffectedProduct) -> str:
    return f"{product['product_name']} ({product['hts_code']})"


def _format_action_summary_text(
    action_summary: EmailActionSummary,
    urls: _EmailUrls,
) -> str:
    action_copy = _action_copy(ActionType(action_summary["action_type"]))
    effective = _effective_phrase(action_summary.get("effective_date"))
    return (
        f"- {action_summary['product_count']} product(s) need {action_copy['noun']}{effective}. "
        f"{action_copy['link_text']}: {urls[action_copy['url_key']]}"
    )


def _format_action_summary_html(
    action_summary: EmailActionSummary,
    urls: _EmailUrls,
) -> str:
    action_copy = _action_copy(ActionType(action_summary["action_type"]))
    effective = _effective_phrase(action_summary.get("effective_date"))
    url = urls[action_copy["url_key"]]
    return (
        f"<li>{action_summary['product_count']} product(s) need "
        f"{html.escape(action_copy['noun'])}{html.escape(effective)}. "
        f"<a href=\"{html.escape(url, quote=True)}\">"
        f"{html.escape(action_copy['link_text'])}</a></li>"
    )


type _ActionCopy = dict[str, str]


def _action_copy(action_type: ActionType) -> _ActionCopy:
    if action_type is ActionType.RECLASSIFY_PRODUCT:
        return {
            "noun": "reclassification",
            "link_text": "Reclassify now",
            "url_key": "reclassify",
        }
    if action_type is ActionType.RECALCULATE_TARIFF:
        return {
            "noun": "tariff recalculation",
            "link_text": "Recalculate now",
            "url_key": "recalculate",
        }
    raise EmailSendError(f"unsupported action type: {action_type}")


def _effective_phrase(effective_date: str | None) -> str:
    if not effective_date:
        return ""
    return f" (effective {effective_date})"


def _truncate(value: str, max_length: int) -> str:
    if len(value) <= max_length:
        return value
    if max_length <= 3:
        return value[:max_length]
    return value[: max_length - 3] + "..."
