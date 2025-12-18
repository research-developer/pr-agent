"""
Webhook router for detecting and routing webhooks from multiple platforms.

Uses a combination of headers, payload structure, and heuristics to identify
the source of incoming webhooks.
"""

import hashlib
import hmac
import logging
from datetime import datetime
from typing import Any, Callable, Optional

from pr_agent.webhooks.models import (
    WebhookSource,
    WebhookHeaders,
    WebhookPayload,
    WebhookEvent,
    WEBHOOK_EVENT_CLASSES,
)

logger = logging.getLogger(__name__)


# =============================================================================
# Source Detection Functions
# =============================================================================

def _detect_github(headers: WebhookHeaders, body: dict) -> tuple[bool, float]:
    """
    Detect if webhook is from GitHub.

    Indicators:
    - X-GitHub-Event header
    - X-GitHub-Delivery header
    - X-Hub-Signature-256 header
    - User-Agent starts with "GitHub-Hookshot/"
    - Body contains "repository", "sender" with specific GitHub structure
    """
    confidence = 0.0

    # Strong indicators (headers)
    if headers.github_event:
        confidence += 0.4
    if headers.github_delivery:
        confidence += 0.3
    if headers.github_signature:
        confidence += 0.2

    # User-Agent check
    if headers.user_agent and "GitHub-Hookshot" in headers.user_agent:
        confidence += 0.1

    # Payload structure checks
    if "repository" in body and isinstance(body.get("repository"), dict):
        if "full_name" in body.get("repository", {}):
            confidence += 0.1
    if "sender" in body and isinstance(body.get("sender"), dict):
        if "login" in body.get("sender", {}):
            confidence += 0.1

    return confidence >= 0.5, min(confidence, 1.0)


def _detect_vercel(headers: WebhookHeaders, body: dict) -> tuple[bool, float]:
    """
    Detect if webhook is from Vercel.

    Indicators:
    - x-vercel-signature header
    - User-Agent contains "Vercel"
    - Body has "type" field with deployment types
    - Body has Vercel-specific fields (deploymentId, projectId)
    """
    confidence = 0.0

    # Strong indicators
    if headers.vercel_signature:
        confidence += 0.5

    # User-Agent check
    if headers.user_agent and "Vercel" in headers.user_agent:
        confidence += 0.2

    # Payload structure
    vercel_types = ["deployment", "deployment.created", "deployment.succeeded",
                    "deployment.ready", "deployment.error", "deployment.canceled",
                    "project.created", "project.removed"]
    if body.get("type") in vercel_types:
        confidence += 0.3

    # Vercel-specific fields
    if "deploymentId" in body or ("payload" in body and "deployment" in body.get("payload", {})):
        confidence += 0.2

    return confidence >= 0.5, min(confidence, 1.0)


def _detect_linear(headers: WebhookHeaders, body: dict) -> tuple[bool, float]:
    """
    Detect if webhook is from Linear.

    Indicators:
    - Linear-Signature header
    - Linear-Event header
    - Body has "action", "data", "type" with Linear patterns
    - Body contains Linear-specific fields (identifier like "ENG-123")
    """
    confidence = 0.0

    # Strong indicators
    if headers.linear_signature:
        confidence += 0.4
    if headers.linear_event:
        confidence += 0.3

    # Payload structure
    linear_types = ["Issue", "Comment", "Project", "Cycle", "IssueLabel", "Reaction"]
    if body.get("type") in linear_types:
        confidence += 0.2

    if "action" in body and body.get("action") in ["create", "update", "remove"]:
        confidence += 0.1

    # Linear identifier pattern (ABC-123)
    data = body.get("data", {})
    identifier = data.get("identifier", "")
    if isinstance(identifier, str) and "-" in identifier:
        parts = identifier.split("-")
        if len(parts) == 2 and parts[0].isalpha() and parts[1].isdigit():
            confidence += 0.2

    # Linear-specific fields
    if "webhookId" in body:
        confidence += 0.1

    return confidence >= 0.5, min(confidence, 1.0)


def _detect_slack(headers: WebhookHeaders, body: dict) -> tuple[bool, float]:
    """
    Detect if webhook is from Slack.

    Indicators:
    - X-Slack-Signature header
    - X-Slack-Request-Timestamp header
    - Body has Slack-specific fields (team_id, api_app_id)
    - URL verification challenge
    - Slash command fields (command, response_url)
    """
    confidence = 0.0

    # Strong indicators
    if headers.slack_signature:
        confidence += 0.4
    if headers.slack_timestamp:
        confidence += 0.3

    # URL verification
    if body.get("type") == "url_verification" and "challenge" in body:
        confidence += 0.5

    # Slack-specific fields
    if "team_id" in body:
        confidence += 0.2
    if "api_app_id" in body:
        confidence += 0.1

    # Slash command detection
    if "command" in body and body.get("command", "").startswith("/"):
        confidence += 0.3
    if "response_url" in body and "slack.com" in body.get("response_url", ""):
        confidence += 0.2

    # Events API structure
    if body.get("type") == "event_callback" and "event" in body:
        confidence += 0.3

    return confidence >= 0.5, min(confidence, 1.0)


def _detect_discord(headers: WebhookHeaders, body: dict) -> tuple[bool, float]:
    """
    Detect if webhook is from Discord.

    Indicators:
    - X-Signature-Ed25519 header
    - X-Signature-Timestamp header
    - Body has interaction structure (type, application_id)
    - User-Agent contains "Discord"
    """
    confidence = 0.0

    # Strong indicators (Ed25519 signature headers)
    if headers.discord_signature:
        confidence += 0.4
    if headers.discord_timestamp:
        confidence += 0.3

    # User-Agent check
    if headers.user_agent and "Discord" in headers.user_agent:
        confidence += 0.1

    # Interaction structure
    if "type" in body and isinstance(body.get("type"), int):
        # Discord interaction types are integers: 1=PING, 2=APP_COMMAND, etc.
        if body.get("type") in [1, 2, 3, 4, 5]:
            confidence += 0.3

    if "application_id" in body:
        confidence += 0.2

    # Discord-specific fields
    if "guild_id" in body or "channel_id" in body:
        confidence += 0.1

    return confidence >= 0.5, min(confidence, 1.0)


def _detect_gmail_pubsub(headers: WebhookHeaders, body: dict) -> tuple[bool, float]:
    """
    Detect if webhook is from Gmail via Pub/Sub.

    Indicators:
    - Body has Pub/Sub message structure
    - Message data decodes to Gmail notification
    - User-Agent contains "Google" or "APIs-Google"
    """
    confidence = 0.0

    # User-Agent check
    if headers.user_agent:
        if "Google" in headers.user_agent or "APIs-Google" in headers.user_agent:
            confidence += 0.2

    # Pub/Sub structure
    if "message" in body and isinstance(body.get("message"), dict):
        message = body["message"]
        if "data" in message and "messageId" in message:
            confidence += 0.4

        # Try to decode and check for Gmail fields
        if "data" in message:
            import base64
            import json
            try:
                data = json.loads(base64.b64decode(message["data"]).decode())
                if "emailAddress" in data or "historyId" in data:
                    confidence += 0.4
            except Exception:
                pass

    if "subscription" in body and "projects/" in body.get("subscription", ""):
        confidence += 0.2

    return confidence >= 0.5, min(confidence, 1.0)


def _detect_railway(headers: WebhookHeaders, body: dict) -> tuple[bool, float]:
    """
    Detect if webhook is from Railway.

    Indicators:
    - User-Agent contains "Railway"
    - Body has Railway-specific fields
    """
    confidence = 0.0

    # User-Agent check
    if headers.user_agent and "Railway" in headers.user_agent:
        confidence += 0.4

    # Railway-specific fields
    if "type" in body:
        railway_types = ["DEPLOY", "deployment.started", "deployment.completed",
                        "deployment.failed", "deployment.crashed"]
        if body.get("type") in railway_types:
            confidence += 0.4

    # Railway payload structure
    if "deployment" in body and "service" in body:
        confidence += 0.3

    return confidence >= 0.5, min(confidence, 1.0)


# Detection function registry
DETECTORS: list[tuple[WebhookSource, Callable[[WebhookHeaders, dict], tuple[bool, float]]]] = [
    (WebhookSource.GITHUB, _detect_github),
    (WebhookSource.VERCEL, _detect_vercel),
    (WebhookSource.LINEAR, _detect_linear),
    (WebhookSource.SLACK, _detect_slack),
    (WebhookSource.DISCORD, _detect_discord),
    (WebhookSource.GMAIL, _detect_gmail_pubsub),
    (WebhookSource.RAILWAY, _detect_railway),
]


def detect_webhook_source(
    headers: dict[str, str],
    body: dict[str, Any],
) -> tuple[WebhookSource, float]:
    """
    Detect the source of a webhook based on headers and body.

    Args:
        headers: Raw HTTP headers
        body: Parsed JSON body

    Returns:
        Tuple of (WebhookSource, confidence_score)
    """
    parsed_headers = WebhookHeaders.from_raw(headers)

    best_source = WebhookSource.UNKNOWN
    best_confidence = 0.0

    for source, detector in DETECTORS:
        try:
            is_match, confidence = detector(parsed_headers, body)
            if is_match and confidence > best_confidence:
                best_source = source
                best_confidence = confidence
        except Exception as e:
            logger.warning(f"Detector for {source.value} failed: {e}")

    logger.info(f"Detected webhook source: {best_source.value} (confidence: {best_confidence:.2f})")
    return best_source, best_confidence


# =============================================================================
# Signature Verification
# =============================================================================

def verify_github_signature(payload: bytes, signature: str, secret: str) -> bool:
    """Verify GitHub webhook signature (X-Hub-Signature-256)."""
    if not signature.startswith("sha256="):
        return False

    expected = "sha256=" + hmac.new(
        secret.encode(),
        payload,
        hashlib.sha256
    ).hexdigest()

    return hmac.compare_digest(signature, expected)


def verify_slack_signature(
    payload: bytes,
    signature: str,
    timestamp: str,
    secret: str,
) -> bool:
    """Verify Slack webhook signature."""
    sig_basestring = f"v0:{timestamp}:{payload.decode()}"
    expected = "v0=" + hmac.new(
        secret.encode(),
        sig_basestring.encode(),
        hashlib.sha256
    ).hexdigest()

    return hmac.compare_digest(signature, expected)


def verify_linear_signature(payload: bytes, signature: str, secret: str) -> bool:
    """Verify Linear webhook signature."""
    expected = hmac.new(
        secret.encode(),
        payload,
        hashlib.sha256
    ).hexdigest()

    return hmac.compare_digest(signature, expected)


def verify_vercel_signature(payload: bytes, signature: str, secret: str) -> bool:
    """Verify Vercel webhook signature."""
    expected = hmac.new(
        secret.encode(),
        payload,
        hashlib.sha1
    ).hexdigest()

    return hmac.compare_digest(signature, expected)


def verify_discord_signature(
    payload: bytes,
    signature: str,
    timestamp: str,
    public_key: str,
) -> bool:
    """Verify Discord interaction signature (Ed25519)."""
    try:
        from nacl.signing import VerifyKey
        from nacl.exceptions import BadSignature

        verify_key = VerifyKey(bytes.fromhex(public_key))
        message = timestamp.encode() + payload
        verify_key.verify(message, bytes.fromhex(signature))
        return True
    except ImportError:
        logger.warning("PyNaCl not installed, cannot verify Discord signatures")
        return False
    except BadSignature:
        return False
    except Exception as e:
        logger.error(f"Discord signature verification failed: {e}")
        return False


# =============================================================================
# Webhook Router
# =============================================================================

class WebhookRouter:
    """
    Routes webhooks to appropriate handlers based on detected source.

    Usage:
        router = WebhookRouter()

        @router.on(WebhookSource.GITHUB)
        async def handle_github(event: GitHubWebhookEvent):
            print(f"GitHub event: {event.event_type}")

        # In your webhook endpoint:
        event = router.parse(headers, body, raw_body)
        await router.dispatch(event)
    """

    def __init__(self):
        self._handlers: dict[WebhookSource, list[Callable]] = {}
        self._secrets: dict[WebhookSource, str] = {}

    def set_secret(self, source: WebhookSource, secret: str) -> None:
        """Set the webhook secret for a source (used for verification)."""
        self._secrets[source] = secret

    def on(self, source: WebhookSource):
        """Decorator to register a handler for a webhook source."""

        def decorator(func: Callable) -> Callable:
            if source not in self._handlers:
                self._handlers[source] = []
            self._handlers[source].append(func)
            return func

        return decorator

    def register_handler(self, source: WebhookSource, handler: Callable) -> None:
        """Register a handler programmatically."""
        if source not in self._handlers:
            self._handlers[source] = []
        self._handlers[source].append(handler)

    def parse(
        self,
        headers: dict[str, str],
        body: dict[str, Any],
        raw_body: Optional[bytes] = None,
    ) -> WebhookPayload:
        """
        Parse an incoming webhook into a WebhookPayload.

        Args:
            headers: Raw HTTP headers
            body: Parsed JSON body
            raw_body: Raw body bytes (for signature verification)

        Returns:
            WebhookPayload with detected source
        """
        source, confidence = detect_webhook_source(headers, body)
        parsed_headers = WebhookHeaders.from_raw(headers)

        # Extract event ID and type based on source
        event_type = None
        event_id = None

        if source == WebhookSource.GITHUB:
            event_type = parsed_headers.github_event
            event_id = parsed_headers.github_delivery
        elif source == WebhookSource.LINEAR:
            event_type = body.get("type")
            event_id = body.get("webhookId")
        elif source == WebhookSource.SLACK:
            event_type = body.get("event", {}).get("type") or body.get("type")
            event_id = body.get("event_id")
        elif source == WebhookSource.VERCEL:
            event_type = body.get("type")
            event_id = body.get("id")
        elif source == WebhookSource.DISCORD:
            event_type = f"interaction_{body.get('type')}"
            event_id = body.get("id")

        return WebhookPayload(
            source=source,
            confidence=confidence,
            headers=parsed_headers,
            body=body,
            raw_body=raw_body,
            event_type=event_type,
            event_id=event_id,
            timestamp=datetime.now(),
        )

    def verify(self, payload: WebhookPayload) -> bool:
        """
        Verify the webhook signature if a secret is configured.

        Args:
            payload: The parsed webhook payload

        Returns:
            True if verified (or no secret configured), False if verification fails
        """
        secret = self._secrets.get(payload.source)
        if not secret or not payload.raw_body:
            return True  # No verification configured

        if payload.source == WebhookSource.GITHUB:
            sig = payload.headers.github_signature
            return sig and verify_github_signature(payload.raw_body, sig, secret)

        elif payload.source == WebhookSource.SLACK:
            sig = payload.headers.slack_signature
            ts = payload.headers.slack_timestamp
            return sig and ts and verify_slack_signature(payload.raw_body, sig, ts, secret)

        elif payload.source == WebhookSource.LINEAR:
            sig = payload.headers.linear_signature
            return sig and verify_linear_signature(payload.raw_body, sig, secret)

        elif payload.source == WebhookSource.VERCEL:
            sig = payload.headers.vercel_signature
            return sig and verify_vercel_signature(payload.raw_body, sig, secret)

        elif payload.source == WebhookSource.DISCORD:
            sig = payload.headers.discord_signature
            ts = payload.headers.discord_timestamp
            return sig and ts and verify_discord_signature(payload.raw_body, sig, ts, secret)

        return True  # Unknown source, skip verification

    def to_event(self, payload: WebhookPayload) -> Optional[WebhookEvent]:
        """
        Convert a WebhookPayload to a typed WebhookEvent.

        Args:
            payload: The parsed webhook payload

        Returns:
            Typed WebhookEvent subclass, or None if source unknown
        """
        event_class = WEBHOOK_EVENT_CLASSES.get(payload.source)
        if not event_class:
            logger.warning(f"No event class for source: {payload.source}")
            return None

        return event_class.from_payload(payload)

    async def dispatch(self, event: WebhookEvent) -> list[Any]:
        """
        Dispatch an event to registered handlers.

        Args:
            event: The typed webhook event

        Returns:
            List of handler results
        """
        handlers = self._handlers.get(event.source, [])
        if not handlers:
            logger.debug(f"No handlers for source: {event.source.value}")
            return []

        import asyncio
        import inspect

        results = []
        for handler in handlers:
            try:
                if inspect.iscoroutinefunction(handler):
                    result = await handler(event)
                else:
                    result = handler(event)
                results.append(result)
            except Exception as e:
                logger.error(f"Handler {handler.__name__} failed: {e}")
                results.append(e)

        return results

    async def handle_webhook(
        self,
        headers: dict[str, str],
        body: dict[str, Any],
        raw_body: Optional[bytes] = None,
    ) -> Optional[WebhookEvent]:
        """
        Full webhook handling pipeline: parse -> verify -> convert -> dispatch.

        Args:
            headers: Raw HTTP headers
            body: Parsed JSON body
            raw_body: Raw body bytes for verification

        Returns:
            The processed WebhookEvent, or None if handling failed
        """
        # Parse
        payload = self.parse(headers, body, raw_body)

        if payload.source == WebhookSource.UNKNOWN:
            logger.warning("Could not detect webhook source")
            return None

        # Verify
        if not self.verify(payload):
            logger.error(f"Webhook signature verification failed for {payload.source.value}")
            return None

        # Convert to typed event
        event = self.to_event(payload)
        if not event:
            return None

        # Dispatch
        await self.dispatch(event)

        return event
