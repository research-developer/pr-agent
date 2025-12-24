"""
Polling infrastructure for local PR-Agent daemon.

This module provides polling-based event sources as an alternative to webhooks,
enabling PR-Agent to run as a local service that monitors GitHub notifications,
Railway deployments, and Cloudflare deployments.
"""

from pr_agent.polling.base import BasePoller
from pr_agent.polling.events import (
    Event,
    EventType,
    GitHubMentionEvent,
    RailwayDeployEvent,
    CloudflarePagesEvent,
    CloudflareWorkersEvent,
)
from pr_agent.polling.dispatcher import EventDispatcher
from pr_agent.polling.github import GitHubPoller
from pr_agent.polling.railway import RailwayPoller
from pr_agent.polling.cloudflare import CloudflarePoller

__all__ = [
    # Base
    "BasePoller",
    "EventDispatcher",
    # Pollers
    "GitHubPoller",
    "RailwayPoller",
    "CloudflarePoller",
    # Events
    "Event",
    "EventType",
    "GitHubMentionEvent",
    "RailwayDeployEvent",
    "CloudflarePagesEvent",
    "CloudflareWorkersEvent",
]
