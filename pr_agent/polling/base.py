"""
Abstract base class for polling event sources.

Adapted from pr_agent/servers/github_polling.py patterns.
"""

from abc import ABC, abstractmethod
from typing import AsyncIterator, Optional
from datetime import datetime
import asyncio
import logging

from pr_agent.polling.events import Event

logger = logging.getLogger(__name__)


class BasePoller(ABC):
    """
    Abstract base class for notification pollers.

    Subclasses implement poll() to fetch events from their respective sources
    (GitHub notifications, Railway deployments, etc.).

    Usage:
        class MyPoller(BasePoller):
            async def poll(self) -> list[Event]:
                # Fetch and return events
                ...

        poller = MyPoller(poll_interval=30)
        async for event in poller.run():
            await handle(event)
    """

    def __init__(
        self,
        poll_interval: int = 30,
        name: str = "poller",
        max_consecutive_errors: int = 5,
        error_backoff_multiplier: float = 2.0,
    ):
        """
        Initialize the poller.

        Args:
            poll_interval: Seconds between polls (default: 30)
            name: Identifier for logging
            max_consecutive_errors: Errors before entering backoff mode
            error_backoff_multiplier: Multiplier for backoff duration
        """
        self.poll_interval = poll_interval
        self.name = name
        self.max_consecutive_errors = max_consecutive_errors
        self.error_backoff_multiplier = error_backoff_multiplier

        self._running = False
        self._last_poll: Optional[datetime] = None
        self._error_count = 0
        self._total_events = 0
        self._total_polls = 0

    @abstractmethod
    async def poll(self) -> list[Event]:
        """
        Poll for new events.

        Subclasses must implement this method to fetch events from their
        respective sources. Should return an empty list if no new events.

        Returns:
            List of Event objects (may be empty)

        Raises:
            Exception: On polling errors (will be caught by run())
        """
        pass

    async def setup(self) -> None:
        """
        Optional setup hook called before polling starts.

        Override to perform initialization (e.g., verify credentials,
        establish connections).
        """
        pass

    async def teardown(self) -> None:
        """
        Optional teardown hook called when polling stops.

        Override to perform cleanup (e.g., close connections).
        """
        pass

    async def run(self) -> AsyncIterator[Event]:
        """
        Run the polling loop, yielding events as they arrive.

        This is an async generator that:
        1. Calls setup() once
        2. Repeatedly calls poll() at poll_interval
        3. Yields events as they arrive
        4. Handles errors with exponential backoff
        5. Calls teardown() when stopped

        Yields:
            Event objects from the poll source

        Example:
            async for event in poller.run():
                await process(event)
        """
        self._running = True
        logger.info(f"[{self.name}] Starting polling (interval={self.poll_interval}s)")

        try:
            await self.setup()

            while self._running:
                try:
                    events = await self.poll()
                    self._last_poll = datetime.utcnow()
                    self._total_polls += 1
                    self._error_count = 0  # Reset on success

                    for event in events:
                        self._total_events += 1
                        logger.debug(f"[{self.name}] Yielding event: {event.type.value}")
                        yield event

                except Exception as e:
                    self._error_count += 1
                    logger.error(
                        f"[{self.name}] Poll error ({self._error_count}/{self.max_consecutive_errors}): {e}"
                    )

                    if self._error_count >= self.max_consecutive_errors:
                        backoff = self.poll_interval * self.error_backoff_multiplier
                        logger.warning(
                            f"[{self.name}] Max errors reached, backing off for {backoff}s"
                        )
                        await asyncio.sleep(backoff)
                        self._error_count = 0

                await asyncio.sleep(self.poll_interval)

        finally:
            await self.teardown()
            logger.info(
                f"[{self.name}] Stopped. "
                f"Total polls: {self._total_polls}, events: {self._total_events}"
            )

    def stop(self) -> None:
        """Stop the polling loop gracefully."""
        self._running = False
        logger.info(f"[{self.name}] Stop requested")

    @property
    def is_running(self) -> bool:
        """Check if the poller is currently running."""
        return self._running

    @property
    def last_poll(self) -> Optional[datetime]:
        """Get the timestamp of the last successful poll."""
        return self._last_poll

    @property
    def stats(self) -> dict:
        """Get polling statistics."""
        return {
            "name": self.name,
            "running": self._running,
            "poll_interval": self.poll_interval,
            "last_poll": self._last_poll.isoformat() if self._last_poll else None,
            "total_polls": self._total_polls,
            "total_events": self._total_events,
            "consecutive_errors": self._error_count,
        }
