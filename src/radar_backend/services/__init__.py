from radar_backend.services.email_service import EmailSendError, EmailService
from radar_backend.services.webhook_service import WebhookSendError, WebhookService

email_service = EmailService()
webhook_service = WebhookService()

__all__ = ["email_service", "webhook_service", "EmailSendError", "WebhookSendError"]
