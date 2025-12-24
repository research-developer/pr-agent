"""
Webhook handling infrastructure.

Provides modular webhook parsing, routing, and verification for multiple platforms.
"""

from pr_agent.webhooks.models import (
    WebhookSource,
    WebhookPayload,
    WebhookEvent,
)
from pr_agent.webhooks.router import WebhookRouter, detect_webhook_source

__all__ = [
    "WebhookSource",
    "WebhookPayload",
    "WebhookEvent",
    "WebhookRouter",
    "detect_webhook_source",
]
