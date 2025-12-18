"""
Polling infrastructure for local PR-Agent daemon.

This module provides polling-based event sources as an alternative to webhooks,
enabling PR-Agent to run as a local service that monitors GitHub notifications
and Railway deployments.
"""

from pr_agent.polling.base import BasePoller
from pr_agent.polling.events import Event, EventType, GitHubMentionEvent, RailwayDeployEvent
from pr_agent.polling.dispatcher import EventDispatcher

__all__ = [
    "BasePoller",
    "Event",
    "EventType",
    "GitHubMentionEvent",
    "RailwayDeployEvent",
    "EventDispatcher",
]
