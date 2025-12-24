"""
Modular webhook models for multi-platform support.

Designed to identify webhook sources and normalize payloads across platforms.
"""

from abc import ABC, abstractmethod
from datetime import datetime
from enum import Enum
from typing import Any, Optional
from pydantic import BaseModel, Field


class WebhookSource(str, Enum):
    """Supported webhook sources."""

    GITHUB = "github"
    VERCEL = "vercel"
    NOTION = "notion"
    LINEAR = "linear"
    SLACK = "slack"
    GMAIL = "gmail"  # Via Pub/Sub
    DISCORD = "discord"
    RAILWAY = "railway"
    UNKNOWN = "unknown"


class WebhookSignature(BaseModel):
    """Signature/verification info from webhook."""

    algorithm: Optional[str] = Field(None, description="Signing algorithm (sha256, ed25519, etc.)")
    signature: Optional[str] = Field(None, description="The signature value")
    timestamp: Optional[str] = Field(None, description="Timestamp used in signing")


class WebhookHeaders(BaseModel):
    """Normalized webhook headers for source detection."""

    # Raw headers (lowercased keys)
    raw: dict[str, str] = Field(default_factory=dict)

    # Common identifiers extracted from headers
    user_agent: Optional[str] = None
    content_type: Optional[str] = None

    # Platform-specific headers
    github_event: Optional[str] = Field(None, description="X-GitHub-Event")
    github_delivery: Optional[str] = Field(None, description="X-GitHub-Delivery")
    github_signature: Optional[str] = Field(None, description="X-Hub-Signature-256")

    vercel_signature: Optional[str] = Field(None, description="x-vercel-signature")

    linear_signature: Optional[str] = Field(None, description="Linear-Signature")
    linear_event: Optional[str] = Field(None, description="Linear-Event")

    slack_signature: Optional[str] = Field(None, description="X-Slack-Signature")
    slack_timestamp: Optional[str] = Field(None, description="X-Slack-Request-Timestamp")

    discord_signature: Optional[str] = Field(None, description="X-Signature-Ed25519")
    discord_timestamp: Optional[str] = Field(None, description="X-Signature-Timestamp")

    @classmethod
    def from_raw(cls, headers: dict[str, str]) -> "WebhookHeaders":
        """Create from raw headers dict, normalizing keys to lowercase."""
        normalized = {k.lower(): v for k, v in headers.items()}

        return cls(
            raw=normalized,
            user_agent=normalized.get("user-agent"),
            content_type=normalized.get("content-type"),
            # GitHub
            github_event=normalized.get("x-github-event"),
            github_delivery=normalized.get("x-github-delivery"),
            github_signature=normalized.get("x-hub-signature-256"),
            # Vercel
            vercel_signature=normalized.get("x-vercel-signature"),
            # Linear
            linear_signature=normalized.get("linear-signature"),
            linear_event=normalized.get("linear-event"),
            # Slack
            slack_signature=normalized.get("x-slack-signature"),
            slack_timestamp=normalized.get("x-slack-request-timestamp"),
            # Discord
            discord_signature=normalized.get("x-signature-ed25519"),
            discord_timestamp=normalized.get("x-signature-timestamp"),
        )


class WebhookPayload(BaseModel):
    """
    Universal webhook payload container.

    Wraps the raw payload with metadata for routing and verification.
    """

    # Detection results
    source: WebhookSource = Field(WebhookSource.UNKNOWN, description="Detected source")
    confidence: float = Field(0.0, description="Detection confidence 0-1")

    # Raw data
    headers: WebhookHeaders = Field(default_factory=WebhookHeaders)
    body: dict[str, Any] = Field(default_factory=dict, description="Parsed JSON body")
    raw_body: Optional[bytes] = Field(None, description="Raw body for signature verification")

    # Extracted common fields
    event_type: Optional[str] = Field(None, description="Normalized event type")
    event_id: Optional[str] = Field(None, description="Unique event/delivery ID")
    timestamp: Optional[datetime] = Field(None, description="Event timestamp")

    # Source-specific metadata
    metadata: dict[str, Any] = Field(default_factory=dict)

    class Config:
        arbitrary_types_allowed = True


class WebhookEvent(BaseModel, ABC):
    """
    Base class for normalized webhook events.

    Each platform implements its own subclass with typed fields.
    """

    source: WebhookSource
    event_type: str
    event_id: str
    timestamp: datetime
    raw_payload: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    @abstractmethod
    def from_payload(cls, payload: WebhookPayload) -> "WebhookEvent":
        """Parse a WebhookPayload into a typed event."""
        pass


# =============================================================================
# Platform-Specific Event Models
# =============================================================================

class GitHubWebhookEvent(WebhookEvent):
    """GitHub webhook event."""

    source: WebhookSource = WebhookSource.GITHUB

    # GitHub-specific fields
    action: Optional[str] = None
    repository: Optional[str] = None  # owner/repo
    sender: Optional[str] = None  # username

    # PR-specific
    pr_number: Optional[int] = None
    pr_url: Optional[str] = None

    # Issue-specific
    issue_number: Optional[int] = None

    # Comment-specific
    comment_id: Optional[int] = None
    comment_body: Optional[str] = None

    @classmethod
    def from_payload(cls, payload: WebhookPayload) -> "GitHubWebhookEvent":
        body = payload.body
        repo = body.get("repository", {})
        sender = body.get("sender", {})
        pr = body.get("pull_request", {})
        issue = body.get("issue", {})
        comment = body.get("comment", {})

        return cls(
            event_type=payload.headers.github_event or "unknown",
            event_id=payload.headers.github_delivery or "",
            timestamp=payload.timestamp or datetime.now(),
            raw_payload=body,
            action=body.get("action"),
            repository=repo.get("full_name"),
            sender=sender.get("login"),
            pr_number=pr.get("number") or issue.get("number"),
            pr_url=pr.get("html_url"),
            issue_number=issue.get("number"),
            comment_id=comment.get("id"),
            comment_body=comment.get("body"),
        )


class VercelWebhookEvent(WebhookEvent):
    """Vercel webhook event."""

    source: WebhookSource = WebhookSource.VERCEL

    # Vercel-specific fields
    deployment_id: Optional[str] = None
    deployment_url: Optional[str] = None
    project_id: Optional[str] = None
    project_name: Optional[str] = None
    team_id: Optional[str] = None

    # Deployment status
    status: Optional[str] = None  # BUILDING, READY, ERROR, CANCELED

    # Git info
    git_commit_sha: Optional[str] = None
    git_commit_message: Optional[str] = None
    git_branch: Optional[str] = None

    @classmethod
    def from_payload(cls, payload: WebhookPayload) -> "VercelWebhookEvent":
        body = payload.body
        deployment = body.get("payload", body)  # Vercel wraps in "payload"

        return cls(
            event_type=body.get("type", "deployment"),
            event_id=body.get("id", ""),
            timestamp=payload.timestamp or datetime.now(),
            raw_payload=body,
            deployment_id=deployment.get("deployment", {}).get("id") or deployment.get("id"),
            deployment_url=deployment.get("deployment", {}).get("url") or deployment.get("url"),
            project_id=deployment.get("project", {}).get("id") or deployment.get("projectId"),
            project_name=deployment.get("project", {}).get("name") or deployment.get("name"),
            team_id=deployment.get("team", {}).get("id"),
            status=deployment.get("deployment", {}).get("state") or deployment.get("state"),
            git_commit_sha=deployment.get("deployment", {}).get("meta", {}).get("githubCommitSha"),
            git_commit_message=deployment.get("deployment", {}).get("meta", {}).get("githubCommitMessage"),
            git_branch=deployment.get("deployment", {}).get("meta", {}).get("githubCommitRef"),
        )


class LinearWebhookEvent(WebhookEvent):
    """Linear webhook event."""

    source: WebhookSource = WebhookSource.LINEAR

    # Linear-specific fields
    action: Optional[str] = None  # create, update, remove

    # Issue fields
    issue_id: Optional[str] = None
    issue_identifier: Optional[str] = None  # e.g., "ENG-123"
    issue_title: Optional[str] = None
    issue_url: Optional[str] = None

    # State
    state_name: Optional[str] = None
    priority: Optional[int] = None

    # Relations
    team_key: Optional[str] = None
    assignee: Optional[str] = None

    # Comment
    comment_id: Optional[str] = None
    comment_body: Optional[str] = None

    @classmethod
    def from_payload(cls, payload: WebhookPayload) -> "LinearWebhookEvent":
        body = payload.body
        data = body.get("data", {})

        return cls(
            event_type=body.get("type", "Issue"),
            event_id=body.get("webhookId", ""),
            timestamp=payload.timestamp or datetime.now(),
            raw_payload=body,
            action=body.get("action"),
            issue_id=data.get("id"),
            issue_identifier=data.get("identifier"),
            issue_title=data.get("title"),
            issue_url=data.get("url"),
            state_name=data.get("state", {}).get("name"),
            priority=data.get("priority"),
            team_key=data.get("team", {}).get("key"),
            assignee=data.get("assignee", {}).get("name"),
            comment_id=data.get("comment", {}).get("id") if "comment" in body.get("type", "").lower() else None,
            comment_body=data.get("body") if "comment" in body.get("type", "").lower() else None,
        )


class SlackWebhookEvent(WebhookEvent):
    """Slack webhook/event."""

    source: WebhookSource = WebhookSource.SLACK

    # Slack-specific fields
    team_id: Optional[str] = None
    channel_id: Optional[str] = None
    channel_name: Optional[str] = None
    user_id: Optional[str] = None
    user_name: Optional[str] = None

    # Message/event data
    text: Optional[str] = None
    thread_ts: Optional[str] = None

    # Slash command specific
    command: Optional[str] = None
    response_url: Optional[str] = None
    trigger_id: Optional[str] = None

    # Challenge (for URL verification)
    challenge: Optional[str] = None

    @classmethod
    def from_payload(cls, payload: WebhookPayload) -> "SlackWebhookEvent":
        body = payload.body
        event = body.get("event", {})

        # Handle URL verification challenge
        if body.get("type") == "url_verification":
            return cls(
                event_type="url_verification",
                event_id=body.get("token", ""),
                timestamp=datetime.now(),
                raw_payload=body,
                challenge=body.get("challenge"),
            )

        # Handle slash commands (form-encoded, different structure)
        if "command" in body:
            return cls(
                event_type="slash_command",
                event_id=body.get("trigger_id", ""),
                timestamp=datetime.now(),
                raw_payload=body,
                team_id=body.get("team_id"),
                channel_id=body.get("channel_id"),
                channel_name=body.get("channel_name"),
                user_id=body.get("user_id"),
                user_name=body.get("user_name"),
                text=body.get("text"),
                command=body.get("command"),
                response_url=body.get("response_url"),
                trigger_id=body.get("trigger_id"),
            )

        # Handle Events API
        return cls(
            event_type=event.get("type", body.get("type", "unknown")),
            event_id=body.get("event_id", ""),
            timestamp=datetime.now(),
            raw_payload=body,
            team_id=body.get("team_id"),
            channel_id=event.get("channel"),
            user_id=event.get("user"),
            text=event.get("text"),
            thread_ts=event.get("thread_ts"),
        )


class DiscordWebhookEvent(WebhookEvent):
    """Discord interaction/webhook event."""

    source: WebhookSource = WebhookSource.DISCORD

    # Discord-specific fields
    interaction_type: Optional[int] = None  # 1=PING, 2=APPLICATION_COMMAND, 3=MESSAGE_COMPONENT
    guild_id: Optional[str] = None
    channel_id: Optional[str] = None
    user_id: Optional[str] = None
    user_name: Optional[str] = None

    # Command data
    command_name: Optional[str] = None
    command_options: list[dict] = Field(default_factory=list)

    # Message data
    message_id: Optional[str] = None
    message_content: Optional[str] = None

    @classmethod
    def from_payload(cls, payload: WebhookPayload) -> "DiscordWebhookEvent":
        body = payload.body
        user = body.get("member", {}).get("user", {}) or body.get("user", {})
        data = body.get("data", {})

        return cls(
            event_type=f"interaction_{body.get('type', 0)}",
            event_id=body.get("id", ""),
            timestamp=payload.timestamp or datetime.now(),
            raw_payload=body,
            interaction_type=body.get("type"),
            guild_id=body.get("guild_id"),
            channel_id=body.get("channel_id"),
            user_id=user.get("id"),
            user_name=user.get("username"),
            command_name=data.get("name"),
            command_options=data.get("options", []),
            message_id=body.get("message", {}).get("id"),
            message_content=body.get("message", {}).get("content"),
        )


class GmailPubSubEvent(WebhookEvent):
    """Gmail Pub/Sub push notification event."""

    source: WebhookSource = WebhookSource.GMAIL

    # Gmail-specific fields
    email_address: Optional[str] = None
    history_id: Optional[str] = None

    @classmethod
    def from_payload(cls, payload: WebhookPayload) -> "GmailPubSubEvent":
        body = payload.body

        # Pub/Sub wraps the message
        message = body.get("message", {})
        import base64
        import json

        # Decode the data field
        data_b64 = message.get("data", "")
        try:
            data = json.loads(base64.b64decode(data_b64).decode())
        except Exception:
            data = {}

        return cls(
            event_type="gmail_push",
            event_id=message.get("messageId", ""),
            timestamp=payload.timestamp or datetime.now(),
            raw_payload=body,
            email_address=data.get("emailAddress"),
            history_id=data.get("historyId"),
        )


# Registry for easy lookup
WEBHOOK_EVENT_CLASSES: dict[WebhookSource, type[WebhookEvent]] = {
    WebhookSource.GITHUB: GitHubWebhookEvent,
    WebhookSource.VERCEL: VercelWebhookEvent,
    WebhookSource.LINEAR: LinearWebhookEvent,
    WebhookSource.SLACK: SlackWebhookEvent,
    WebhookSource.DISCORD: DiscordWebhookEvent,
    WebhookSource.GMAIL: GmailPubSubEvent,
}
