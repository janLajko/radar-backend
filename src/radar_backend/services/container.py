from __future__ import annotations

from dataclasses import dataclass

from radar_backend.services.email_service import EmailService
from radar_backend.services.webhook_service import WebhookService


@dataclass(frozen=True)
class Services:
    email: EmailService
    webhook: WebhookService

    @classmethod
    def create(cls) -> "Services":
        return cls(
            email=EmailService(),
            webhook=WebhookService(),
        )
