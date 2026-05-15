from radar_backend.services.email_service import EmailService
from radar_backend.services.webhook_service import WebhookService

email_service = EmailService()
webhook_service = WebhookService()

__all__ = ["email_service", "webhook_service"]
