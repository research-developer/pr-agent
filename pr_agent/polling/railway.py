"""
Railway Deployment Poller.

Polls Railway's GraphQL API for deployment status changes.
"""

from datetime import datetime
from typing import Optional
import logging

import httpx

from pr_agent.polling.base import BasePoller
from pr_agent.polling.events import Event, EventType, RailwayDeployEvent

logger = logging.getLogger(__name__)

# Railway API endpoint
RAILWAY_API_URL = "https://backboard.railway.app/graphql/v2"


class RailwayPoller(BasePoller):
    """
    Poll Railway API for deployment status changes.

    Tracks deployments across services and emits events when status changes
    to SUCCESS, FAILED, or CRASHED.

    Usage:
        poller = RailwayPoller(
            token="railway_xxx",
            project_id="abc123",
            poll_interval=60,
        )
        async for event in poller.run():
            if event.is_failure:
                await alert_team(event)
    """

    def __init__(
        self,
        token: str,
        project_id: str,
        poll_interval: int = 60,
        service_ids: Optional[list[str]] = None,
        environments: Optional[list[str]] = None,
    ):
        """
        Initialize the Railway poller.

        Args:
            token: Railway API token
            project_id: Railway project ID to monitor
            poll_interval: Seconds between polls (default: 60)
            service_ids: Optional list of service IDs to filter
                        If None, monitors all services in project
            environments: Optional list of environment names to filter
                         (e.g., ["production", "staging"])
        """
        super().__init__(poll_interval=poll_interval, name="railway")

        self.token = token
        self.project_id = project_id
        self.service_ids = set(service_ids) if service_ids else None
        self.environments = set(environments) if environments else None

        # Track deployment states for change detection
        self._deployment_states: dict[str, str] = {}  # deploy_id -> status

        # HTTP client (created in setup)
        self._client: Optional[httpx.AsyncClient] = None

    async def setup(self) -> None:
        """Initialize HTTP client."""
        self._client = httpx.AsyncClient(timeout=30.0)
        logger.info(f"[{self.name}] Initialized for project {self.project_id}")

    async def teardown(self) -> None:
        """Close HTTP client."""
        if self._client:
            await self._client.aclose()
            self._client = None

    @property
    def _headers(self) -> dict[str, str]:
        """Get headers for Railway API requests."""
        return {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }

    async def poll(self) -> list[Event]:
        """
        Poll Railway API for deployment status changes.

        Returns:
            List of RailwayDeployEvent objects for deployments that changed
            to terminal states (SUCCESS, FAILED, CRASHED)
        """
        if not self._client:
            logger.warning(f"[{self.name}] HTTP client not initialized")
            return []

        try:
            deployments = await self._fetch_deployments()
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 429:
                logger.warning(f"[{self.name}] Rate limited, backing off")
            else:
                logger.error(f"[{self.name}] HTTP error: {e}")
            return []
        except Exception as e:
            logger.error(f"[{self.name}] Failed to fetch deployments: {e}")
            return []

        events: list[Event] = []

        for deployment in deployments:
            event = self._process_deployment(deployment)
            if event:
                events.append(event)

        if events:
            logger.info(f"[{self.name}] Emitting {len(events)} deployment event(s)")

        return events

    async def _fetch_deployments(self) -> list[dict]:
        """
        Fetch recent deployments from Railway API.

        Returns:
            List of deployment dicts from GraphQL response
        """
        query = self._build_deployments_query()
        variables = {"projectId": self.project_id}

        response = await self._client.post(
            RAILWAY_API_URL,
            json={"query": query, "variables": variables},
            headers=self._headers,
        )
        response.raise_for_status()

        data = response.json()

        if "errors" in data:
            errors = data["errors"]
            logger.error(f"[{self.name}] GraphQL errors: {errors}")
            return []

        edges = data.get("data", {}).get("deployments", {}).get("edges", [])
        return [edge["node"] for edge in edges]

    def _build_deployments_query(self) -> str:
        """
        Build GraphQL query for fetching deployments.

        Returns:
            GraphQL query string

        Example query:
            query GetDeployments($projectId: String!) {
                deployments(input: { projectId: $projectId }, first: 20) {
                    edges {
                        node {
                            id
                            status
                            createdAt
                            service { id name }
                            environment { id name }
                            meta { commitHash commitMessage }
                        }
                    }
                }
            }
        """
        return """
        query GetDeployments($projectId: String!) {
            deployments(input: { projectId: $projectId }, first: 20) {
                edges {
                    node {
                        id
                        status
                        createdAt
                        service { id name }
                        environment { id name }
                        meta { commitHash commitMessage }
                    }
                }
            }
        }
        """

    def _process_deployment(self, deployment: dict) -> Optional[RailwayDeployEvent]:
        """
        Process a deployment into an event if status changed to terminal state.

        Args:
            deployment: Deployment data from GraphQL

        Returns:
            RailwayDeployEvent if should emit, None otherwise
        """
        deploy_id = deployment.get("id")
        status = deployment.get("status")

        if not deploy_id or not status:
            return None

        # Get service and environment info
        service = deployment.get("service", {})
        service_id = service.get("id", "")
        service_name = service.get("name", "unknown")

        environment = deployment.get("environment", {})
        env_id = environment.get("id", "")
        env_name = environment.get("name", "unknown")

        # Apply filters
        if not self._should_process_service(service_id):
            return None
        if not self._should_process_environment(env_name):
            return None

        # Check if status changed
        previous_status = self._deployment_states.get(deploy_id)
        self._deployment_states[deploy_id] = status

        # Only emit for terminal states and state changes
        terminal_states = {"SUCCESS", "FAILED", "CRASHED"}
        if status not in terminal_states:
            return None

        # Skip if we already emitted for this terminal state
        if previous_status == status:
            return None

        # Get event type
        event_type = self._map_status_to_event_type(status)
        if not event_type:
            return None

        # Extract metadata
        meta = deployment.get("meta", {})
        commit_sha = meta.get("commitHash")
        commit_message = meta.get("commitMessage")

        # Build logs URL
        logs_url = (
            f"https://railway.app/project/{self.project_id}"
            f"/service/{service_id}/deployment/{deploy_id}"
        )

        # Parse timestamp
        created_at = deployment.get("createdAt")
        timestamp = datetime.utcnow()
        if created_at:
            try:
                timestamp = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                pass

        logger.info(
            f"[{self.name}] Deployment {deploy_id[:8]} -> {status} "
            f"({service_name}/{env_name})"
        )

        return RailwayDeployEvent(
            id=f"railway-{deploy_id}",
            type=event_type,
            timestamp=timestamp,
            raw_data=deployment,
            service_id=service_id,
            service_name=service_name,
            environment_id=env_id,
            environment_name=env_name,
            deploy_id=deploy_id,
            status=status,
            commit_sha=commit_sha,
            commit_message=commit_message,
            logs_url=logs_url,
        )

    def _should_process_service(self, service_id: str) -> bool:
        """
        Check if a service should be processed.

        Args:
            service_id: Railway service ID

        Returns:
            True if service should be processed
        """
        if self.service_ids is None:
            return True  # No filter, process all
        return service_id in self.service_ids

    def _should_process_environment(self, env_name: str) -> bool:
        """
        Check if an environment should be processed.

        Args:
            env_name: Environment name

        Returns:
            True if environment should be processed
        """
        if self.environments is None:
            return True  # No filter, process all
        return env_name in self.environments

    def _map_status_to_event_type(self, status: str) -> Optional[EventType]:
        """
        Map Railway deployment status to EventType.

        Args:
            status: Railway status string

        Returns:
            EventType or None if status should not trigger event
        """
        status_map = {
            "SUCCESS": EventType.RAILWAY_DEPLOY_SUCCESS,
            "FAILED": EventType.RAILWAY_DEPLOY_FAILED,
            "CRASHED": EventType.RAILWAY_SERVICE_CRASHED,
            "BUILDING": EventType.RAILWAY_DEPLOY_BUILDING,
        }
        return status_map.get(status)
