"""
Event models for polling-based PR-Agent.

Defines structured Pydantic models for events from different sources
(GitHub notifications, Railway deployments).
"""

from enum import Enum
from datetime import datetime
from typing import Any, Optional
from pydantic import BaseModel, Field


class EventType(str, Enum):
    """Types of events the polling agent can process."""

    # GitHub events
    GITHUB_PR_MENTION = "github.pr.mention"
    GITHUB_PR_OPENED = "github.pr.opened"
    GITHUB_PR_UPDATED = "github.pr.updated"
    GITHUB_PR_REVIEW_REQUESTED = "github.pr.review_requested"
    GITHUB_ISSUE_MENTION = "github.issue.mention"

    # Railway events
    RAILWAY_DEPLOY_SUCCESS = "railway.deploy.success"
    RAILWAY_DEPLOY_FAILED = "railway.deploy.failed"
    RAILWAY_DEPLOY_BUILDING = "railway.deploy.building"
    RAILWAY_SERVICE_CRASHED = "railway.service.crashed"

    # Cloudflare events
    CLOUDFLARE_PAGES_SUCCESS = "cloudflare.pages.success"
    CLOUDFLARE_PAGES_FAILED = "cloudflare.pages.failed"
    CLOUDFLARE_PAGES_BUILDING = "cloudflare.pages.building"
    CLOUDFLARE_WORKERS_DEPLOYED = "cloudflare.workers.deployed"
    CLOUDFLARE_WORKERS_FAILED = "cloudflare.workers.failed"


class Event(BaseModel):
    """
    Base event model with common fields.

    All event types inherit from this base class.
    """

    id: str = Field(..., description="Unique event identifier")
    type: EventType = Field(..., description="Event type")
    timestamp: datetime = Field(
        default_factory=datetime.utcnow, description="When the event occurred"
    )
    source: str = Field(..., description="Event source (github, railway)")
    raw_data: dict[str, Any] = Field(
        default_factory=dict, description="Original payload from source"
    )

    model_config = {"extra": "allow"}


class GitHubMentionEvent(Event):
    """
    Event for @mentions on GitHub PRs or Issues.

    Triggered when the bot is mentioned in a comment with an optional command.
    """

    source: str = "github"

    # Repository info
    repo_full_name: str = Field(..., description="Full repo name (owner/repo)")

    # PR/Issue info (one will be set)
    pr_number: Optional[int] = Field(None, description="PR number if mention is on PR")
    issue_number: Optional[int] = Field(
        None, description="Issue number if mention is on issue"
    )

    # Comment info
    comment_id: int = Field(..., description="GitHub comment ID")
    comment_body: str = Field(..., description="Full comment text")
    comment_author: str = Field(..., description="GitHub username of commenter")
    html_url: str = Field(..., description="URL to the comment")

    # Parsed command info
    command: Optional[str] = Field(
        None, description="Parsed command (e.g., '/review', '/describe')"
    )
    command_args: list[str] = Field(
        default_factory=list, description="Arguments to the command"
    )

    @property
    def pr_url(self) -> Optional[str]:
        """Get the PR URL if this is a PR mention."""
        if self.pr_number:
            return f"https://github.com/{self.repo_full_name}/pull/{self.pr_number}"
        return None

    @property
    def is_pr_mention(self) -> bool:
        """Check if this mention is on a PR (vs an issue)."""
        return self.pr_number is not None


class RailwayDeployEvent(Event):
    """
    Event for Railway deployment status changes.

    Triggered when a deployment succeeds, fails, or crashes.
    """

    source: str = "railway"

    # Service info
    service_id: str = Field(..., description="Railway service ID")
    service_name: str = Field(..., description="Human-readable service name")

    # Environment info
    environment_id: str = Field(..., description="Railway environment ID")
    environment_name: str = Field(
        ..., description="Environment name (e.g., 'production', 'staging')"
    )

    # Deployment info
    deploy_id: str = Field(..., description="Railway deployment ID")
    status: str = Field(
        ..., description="Deployment status (SUCCESS, FAILED, CRASHED, BUILDING)"
    )

    # Git info (if available)
    commit_sha: Optional[str] = Field(None, description="Git commit SHA")
    commit_message: Optional[str] = Field(None, description="Git commit message")

    # Error info (if failed/crashed)
    error_message: Optional[str] = Field(
        None, description="Error message if deployment failed"
    )
    logs_url: Optional[str] = Field(None, description="URL to deployment logs")

    @property
    def is_failure(self) -> bool:
        """Check if this is a failure event."""
        return self.status in ("FAILED", "CRASHED")

    @property
    def is_success(self) -> bool:
        """Check if this is a success event."""
        return self.status == "SUCCESS"


class GitHubPROpenedEvent(Event):
    """
    Event for newly opened or reopened PRs.

    Triggered when auto-review is enabled for a repository.
    """

    source: str = "github"

    repo_full_name: str = Field(..., description="Full repo name (owner/repo)")
    pr_number: int = Field(..., description="PR number")
    pr_title: str = Field(..., description="PR title")
    pr_author: str = Field(..., description="GitHub username of PR author")
    pr_url: str = Field(..., description="URL to the PR")
    is_draft: bool = Field(False, description="Whether the PR is a draft")

    # Auto-commands to run
    auto_commands: list[str] = Field(
        default_factory=list,
        description="Commands to run automatically (e.g., ['/review', '/describe'])",
    )


class CloudflarePagesEvent(Event):
    """
    Event for Cloudflare Pages deployment status changes.

    Triggered when a Pages deployment succeeds, fails, or starts building.
    """

    source: str = "cloudflare"

    # Project info
    project_name: str = Field(..., description="Cloudflare Pages project name")
    account_id: str = Field(..., description="Cloudflare account ID")

    # Deployment info
    deployment_id: str = Field(..., description="Deployment ID")
    status: str = Field(
        ..., description="Deployment status (active, success, failed, building)"
    )
    url: Optional[str] = Field(None, description="Deployment URL")
    preview_url: Optional[str] = Field(None, description="Preview URL for branch deploys")

    # Environment
    environment: str = Field(
        "production", description="Environment (production, preview)"
    )
    branch: Optional[str] = Field(None, description="Git branch name")

    # Git info
    commit_sha: Optional[str] = Field(None, description="Git commit SHA")
    commit_message: Optional[str] = Field(None, description="Git commit message")

    # Build info
    build_duration_ms: Optional[int] = Field(None, description="Build duration in ms")
    error_message: Optional[str] = Field(None, description="Error message if failed")

    @property
    def is_failure(self) -> bool:
        """Check if this is a failure event."""
        return self.status == "failed"

    @property
    def is_success(self) -> bool:
        """Check if this is a success event."""
        return self.status in ("active", "success")


class CloudflareWorkersEvent(Event):
    """
    Event for Cloudflare Workers deployment status changes.

    Triggered when a Worker is deployed or fails to deploy.
    """

    source: str = "cloudflare"

    # Worker info
    worker_name: str = Field(..., description="Worker script name")
    account_id: str = Field(..., description="Cloudflare account ID")

    # Deployment info
    deployment_id: Optional[str] = Field(None, description="Deployment ID")
    status: str = Field(..., description="Deployment status (deployed, failed)")

    # Version info
    version_id: Optional[str] = Field(None, description="Worker version ID")
    routes: list[str] = Field(default_factory=list, description="Worker routes")

    # Error info
    error_message: Optional[str] = Field(None, description="Error message if failed")

    @property
    def is_failure(self) -> bool:
        """Check if this is a failure event."""
        return self.status == "failed"

    @property
    def is_success(self) -> bool:
        """Check if this is a success event."""
        return self.status == "deployed"
