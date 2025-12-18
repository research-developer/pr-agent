"""
Railway Deployment Poller.

Polls Railway's GraphQL API for deployment status changes.

Implementation Status: STUB
Branch: feature/railway-poller
"""

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

        TODO: Implement in feature/railway-poller branch
        - Query deployments via GraphQL
        - Track state changes
        - Emit events only for terminal state transitions
        - Filter by service_ids and environments if specified
        """
        # STUB: Implementation goes in feature/railway-poller branch
        raise NotImplementedError(
            "RailwayPoller.poll() not yet implemented. "
            "See feature/railway-poller branch."
        )

    async def _fetch_deployments(self) -> list[dict]:
        """
        Fetch recent deployments from Railway API.

        Returns:
            List of deployment dicts from GraphQL response

        TODO: Implement
        - GraphQL query for deployments
        - Handle pagination if needed
        - Return normalized deployment data
        """
        raise NotImplementedError("See feature/railway-poller branch")

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

        TODO: Implement
        - Check if deployment matches filters (service_ids, environments)
        - Check if status changed from previous poll
        - Only emit for terminal states (SUCCESS, FAILED, CRASHED)
        - Create and return RailwayDeployEvent
        """
        raise NotImplementedError("See feature/railway-poller branch")

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
