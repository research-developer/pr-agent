"""
Cloudflare Deployment Poller.

Polls Cloudflare's REST API for Pages and Workers deployment status changes.
"""

from datetime import datetime
from typing import Optional
import logging

import httpx

from pr_agent.polling.base import BasePoller
from pr_agent.polling.events import (
    Event,
    EventType,
    CloudflarePagesEvent,
    CloudflareWorkersEvent,
)

logger = logging.getLogger(__name__)

# Cloudflare API endpoint
CLOUDFLARE_API_URL = "https://api.cloudflare.com/client/v4"


class CloudflarePoller(BasePoller):
    """
    Poll Cloudflare API for Pages and Workers deployment status changes.

    Tracks deployments and emits events when status changes to
    success, failed, or active.

    Usage:
        poller = CloudflarePoller(
            token="cf_xxx",
            account_id="abc123",
            poll_interval=60,
        )
        async for event in poller.run():
            if event.is_failure:
                await alert_team(event)
    """

    def __init__(
        self,
        token: str,
        account_id: str,
        poll_interval: int = 60,
        pages_projects: Optional[list[str]] = None,
        workers_scripts: Optional[list[str]] = None,
        environments: Optional[list[str]] = None,
    ):
        """
        Initialize the Cloudflare poller.

        Args:
            token: Cloudflare API token
            account_id: Cloudflare account ID
            poll_interval: Seconds between polls (default: 60)
            pages_projects: Optional list of Pages project names to monitor
                           If None, monitors all projects
            workers_scripts: Optional list of Worker script names to monitor
                            If None, monitors all workers
            environments: Optional list of environment names to filter
                         (e.g., ["production", "preview"])
        """
        super().__init__(poll_interval=poll_interval, name="cloudflare")

        self.token = token
        self.account_id = account_id
        self.pages_projects = set(pages_projects) if pages_projects else None
        self.workers_scripts = set(workers_scripts) if workers_scripts else None
        self.environments = set(environments) if environments else None

        # Track deployment states for change detection
        self._pages_states: dict[str, str] = {}  # deploy_id -> status
        self._workers_states: dict[str, str] = {}  # version_id -> status

        # HTTP client (created in setup)
        self._client: Optional[httpx.AsyncClient] = None

    async def setup(self) -> None:
        """Initialize HTTP client."""
        self._client = httpx.AsyncClient(timeout=30.0)
        logger.info(f"[{self.name}] Initialized for account {self.account_id}")

    async def teardown(self) -> None:
        """Close HTTP client."""
        if self._client:
            await self._client.aclose()
            self._client = None

    @property
    def _headers(self) -> dict[str, str]:
        """Get headers for Cloudflare API requests."""
        return {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }

    async def poll(self) -> list[Event]:
        """
        Poll Cloudflare API for deployment status changes.

        Returns:
            List of CloudflarePagesEvent/CloudflareWorkersEvent objects
        """
        if not self._client:
            logger.warning(f"[{self.name}] HTTP client not initialized")
            return []

        events: list[Event] = []

        # Poll Pages deployments
        if self.pages_projects is None or len(self.pages_projects) > 0:
            try:
                pages_events = await self._poll_pages()
                events.extend(pages_events)
            except Exception as e:
                logger.error(f"[{self.name}] Failed to poll Pages: {e}")

        # Poll Workers deployments
        if self.workers_scripts is None or len(self.workers_scripts) > 0:
            try:
                workers_events = await self._poll_workers()
                events.extend(workers_events)
            except Exception as e:
                logger.error(f"[{self.name}] Failed to poll Workers: {e}")

        if events:
            logger.info(f"[{self.name}] Emitting {len(events)} deployment event(s)")

        return events

    async def _poll_pages(self) -> list[Event]:
        """Poll Cloudflare Pages for deployment changes."""
        events: list[Event] = []

        # Get list of projects to poll
        projects = await self._list_pages_projects()

        for project in projects:
            project_name = project.get("name")
            if not project_name:
                continue

            # Filter by configured projects
            if self.pages_projects and project_name not in self.pages_projects:
                continue

            try:
                deployments = await self._fetch_pages_deployments(project_name)
                for deployment in deployments:
                    event = self._process_pages_deployment(project_name, deployment)
                    if event:
                        events.append(event)
            except Exception as e:
                logger.error(f"[{self.name}] Failed to fetch deployments for {project_name}: {e}")

        return events

    async def _poll_workers(self) -> list[Event]:
        """Poll Cloudflare Workers for deployment changes."""
        events: list[Event] = []

        # Get list of workers to poll
        scripts = await self._list_workers_scripts()

        for script in scripts:
            script_name = script.get("id")
            if not script_name:
                continue

            # Filter by configured scripts
            if self.workers_scripts and script_name not in self.workers_scripts:
                continue

            try:
                deployments = await self._fetch_workers_deployments(script_name)
                for deployment in deployments:
                    event = self._process_workers_deployment(script_name, deployment)
                    if event:
                        events.append(event)
            except Exception as e:
                logger.error(f"[{self.name}] Failed to fetch deployments for {script_name}: {e}")

        return events

    async def _list_pages_projects(self) -> list[dict]:
        """List all Pages projects in the account."""
        url = f"{CLOUDFLARE_API_URL}/accounts/{self.account_id}/pages/projects"

        try:
            response = await self._client.get(url, headers=self._headers)
            response.raise_for_status()
            data = response.json()

            if not data.get("success"):
                logger.error(f"[{self.name}] Pages API error: {data.get('errors')}")
                return []

            return data.get("result", [])
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 429:
                logger.warning(f"[{self.name}] Rate limited on Pages projects")
            else:
                logger.error(f"[{self.name}] HTTP error listing projects: {e}")
            return []

    async def _list_workers_scripts(self) -> list[dict]:
        """List all Workers scripts in the account."""
        url = f"{CLOUDFLARE_API_URL}/accounts/{self.account_id}/workers/scripts"

        try:
            response = await self._client.get(url, headers=self._headers)
            response.raise_for_status()
            data = response.json()

            if not data.get("success"):
                logger.error(f"[{self.name}] Workers API error: {data.get('errors')}")
                return []

            return data.get("result", [])
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 429:
                logger.warning(f"[{self.name}] Rate limited on Workers scripts")
            else:
                logger.error(f"[{self.name}] HTTP error listing scripts: {e}")
            return []

    async def _fetch_pages_deployments(self, project_name: str) -> list[dict]:
        """Fetch recent deployments for a Pages project."""
        url = (
            f"{CLOUDFLARE_API_URL}/accounts/{self.account_id}"
            f"/pages/projects/{project_name}/deployments"
        )

        response = await self._client.get(url, headers=self._headers)
        response.raise_for_status()
        data = response.json()

        if not data.get("success"):
            logger.error(f"[{self.name}] Pages deployments error: {data.get('errors')}")
            return []

        return data.get("result", [])

    async def _fetch_workers_deployments(self, script_name: str) -> list[dict]:
        """Fetch recent deployments for a Workers script."""
        url = (
            f"{CLOUDFLARE_API_URL}/accounts/{self.account_id}"
            f"/workers/scripts/{script_name}/deployments"
        )

        response = await self._client.get(url, headers=self._headers)
        response.raise_for_status()
        data = response.json()

        if not data.get("success"):
            logger.error(f"[{self.name}] Workers deployments error: {data.get('errors')}")
            return []

        # Workers API nests deployments
        result = data.get("result", {})
        return result.get("deployments", []) if isinstance(result, dict) else []

    def _process_pages_deployment(
        self, project_name: str, deployment: dict
    ) -> Optional[CloudflarePagesEvent]:
        """Process a Pages deployment into an event."""
        deploy_id = deployment.get("id")
        if not deploy_id:
            return None

        # Get latest stage status
        latest_stage = deployment.get("latest_stage", {})
        stage_name = latest_stage.get("name", "")
        status = latest_stage.get("status", "")

        # Apply environment filter
        environment = deployment.get("environment", "production")
        if self.environments and environment not in self.environments:
            return None

        # Check if status changed
        previous_status = self._pages_states.get(deploy_id)
        current_state = f"{stage_name}:{status}"
        self._pages_states[deploy_id] = current_state

        # Only emit for terminal states and state changes
        if stage_name != "deploy":
            return None  # Not in final stage yet

        terminal_statuses = {"success", "active", "failure", "failed"}
        if status.lower() not in terminal_statuses:
            return None

        # Skip if we already emitted for this state
        if previous_status == current_state:
            return None

        # Determine event type
        if status.lower() in ("success", "active"):
            event_type = EventType.CLOUDFLARE_PAGES_SUCCESS
        else:
            event_type = EventType.CLOUDFLARE_PAGES_FAILED

        # Extract git info
        trigger = deployment.get("deployment_trigger", {})
        metadata = trigger.get("metadata", {})
        commit_sha = metadata.get("commit_hash")
        commit_message = metadata.get("commit_message")
        branch = metadata.get("branch")

        # Parse timestamp
        created_on = deployment.get("created_on")
        timestamp = datetime.utcnow()
        if created_on:
            try:
                timestamp = datetime.fromisoformat(created_on.replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                pass

        # Calculate build duration
        started = latest_stage.get("started_on")
        ended = latest_stage.get("ended_on")
        build_duration_ms = None
        if started and ended:
            try:
                start_dt = datetime.fromisoformat(started.replace("Z", "+00:00"))
                end_dt = datetime.fromisoformat(ended.replace("Z", "+00:00"))
                build_duration_ms = int((end_dt - start_dt).total_seconds() * 1000)
            except (ValueError, AttributeError):
                pass

        logger.info(
            f"[{self.name}] Pages {project_name}/{deploy_id[:8]} -> {status} "
            f"({environment})"
        )

        return CloudflarePagesEvent(
            id=f"cf-pages-{deploy_id}",
            type=event_type,
            timestamp=timestamp,
            raw_data=deployment,
            project_name=project_name,
            account_id=self.account_id,
            deployment_id=deploy_id,
            status=status,
            url=deployment.get("url"),
            preview_url=deployment.get("aliases", [None])[0] if deployment.get("aliases") else None,
            environment=environment,
            branch=branch,
            commit_sha=commit_sha,
            commit_message=commit_message,
            build_duration_ms=build_duration_ms,
        )

    def _process_workers_deployment(
        self, script_name: str, deployment: dict
    ) -> Optional[CloudflareWorkersEvent]:
        """Process a Workers deployment into an event."""
        deploy_id = deployment.get("id")
        if not deploy_id:
            return None

        # Check if this is a new deployment
        previous = self._workers_states.get(deploy_id)
        self._workers_states[deploy_id] = "deployed"

        # Skip if we already processed this deployment
        if previous == "deployed":
            return None

        # Parse timestamp
        created_on = deployment.get("created_on")
        timestamp = datetime.utcnow()
        if created_on:
            try:
                timestamp = datetime.fromisoformat(created_on.replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                pass

        # Get version info
        versions = deployment.get("versions", [])
        version_id = versions[0].get("version_id") if versions else None

        logger.info(f"[{self.name}] Workers {script_name} deployed ({deploy_id[:8]})")

        return CloudflareWorkersEvent(
            id=f"cf-workers-{deploy_id}",
            type=EventType.CLOUDFLARE_WORKERS_DEPLOYED,
            timestamp=timestamp,
            raw_data=deployment,
            worker_name=script_name,
            account_id=self.account_id,
            deployment_id=deploy_id,
            status="deployed",
            version_id=version_id,
        )
