"""
Event dispatcher for routing polling events to handlers.

Aggregates events from multiple pollers and routes them to appropriate
handlers based on event type.
"""

import asyncio
from collections import defaultdict
from typing import Awaitable, Callable, Optional
import logging

from pr_agent.polling.base import BasePoller
from pr_agent.polling.events import Event, EventType, GitHubMentionEvent

logger = logging.getLogger(__name__)

# Type alias for event handlers
EventHandler = Callable[[Event], Awaitable[None]]


class EventDispatcher:
    """
    Aggregates events from multiple pollers and dispatches to handlers.

    Usage:
        dispatcher = EventDispatcher()

        # Add pollers
        dispatcher.add_poller(github_poller)
        dispatcher.add_poller(railway_poller)

        # Register handlers
        @dispatcher.on(EventType.GITHUB_PR_MENTION)
        async def handle_mention(event: GitHubMentionEvent):
            await pr_agent.handle_request(event.pr_url, event.command)

        # Run
        await dispatcher.run()
    """

    def __init__(self):
        self.pollers: list[BasePoller] = []
        self.handlers: dict[EventType, list[EventHandler]] = defaultdict(list)
        self._running = False
        self._tasks: list[asyncio.Task] = []

    def add_poller(self, poller: BasePoller) -> None:
        """
        Add a poller to the dispatcher.

        Args:
            poller: BasePoller instance to add
        """
        self.pollers.append(poller)
        logger.info(f"Added poller: {poller.name}")

    def on(self, event_type: EventType) -> Callable[[EventHandler], EventHandler]:
        """
        Decorator to register an event handler.

        Args:
            event_type: Type of event to handle

        Returns:
            Decorator function

        Example:
            @dispatcher.on(EventType.GITHUB_PR_MENTION)
            async def handle_mention(event: GitHubMentionEvent):
                ...
        """

        def decorator(func: EventHandler) -> EventHandler:
            self.handlers[event_type].append(func)
            logger.debug(f"Registered handler for {event_type.value}: {func.__name__}")
            return func

        return decorator

    def register_handler(
        self, event_type: EventType, handler: EventHandler
    ) -> None:
        """
        Register an event handler programmatically.

        Args:
            event_type: Type of event to handle
            handler: Async function to call when event occurs
        """
        self.handlers[event_type].append(handler)
        logger.debug(f"Registered handler for {event_type.value}: {handler.__name__}")

    async def dispatch(self, event: Event) -> None:
        """
        Dispatch an event to registered handlers.

        Args:
            event: Event to dispatch
        """
        handlers = self.handlers.get(event.type, [])

        if not handlers:
            logger.debug(f"No handlers for event type: {event.type.value}")
            return

        logger.info(f"Dispatching {event.type.value} to {len(handlers)} handler(s)")

        # Run handlers concurrently
        tasks = [self._safe_call(handler, event) for handler in handlers]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Log any errors
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.error(
                    f"Handler {handlers[i].__name__} failed for {event.type.value}: {result}"
                )

    async def _safe_call(self, handler: EventHandler, event: Event) -> None:
        """
        Safely call a handler with error handling.

        Args:
            handler: Handler function to call
            event: Event to pass to handler
        """
        try:
            await handler(event)
        except Exception as e:
            logger.exception(f"Handler {handler.__name__} raised exception: {e}")
            raise

    async def _run_poller(self, poller: BasePoller) -> None:
        """
        Run a single poller and dispatch its events.

        Args:
            poller: Poller to run
        """
        try:
            async for event in poller.run():
                await self.dispatch(event)
        except asyncio.CancelledError:
            logger.info(f"Poller {poller.name} cancelled")
            raise
        except Exception as e:
            logger.error(f"Poller {poller.name} crashed: {e}")
            raise

    async def run(self) -> None:
        """
        Run all pollers concurrently and dispatch events.

        This is the main entry point. Runs until stop() is called
        or all pollers complete.
        """
        if not self.pollers:
            logger.warning("No pollers configured, nothing to do")
            return

        self._running = True
        logger.info(f"Starting dispatcher with {len(self.pollers)} poller(s)")

        # Create tasks for each poller
        self._tasks = [
            asyncio.create_task(self._run_poller(poller), name=f"poller-{poller.name}")
            for poller in self.pollers
        ]

        try:
            # Wait for all pollers (they run indefinitely unless stopped)
            await asyncio.gather(*self._tasks)
        except asyncio.CancelledError:
            logger.info("Dispatcher cancelled")
        finally:
            self._running = False

    async def stop(self) -> None:
        """
        Stop all pollers gracefully.
        """
        logger.info("Stopping dispatcher...")

        # Signal all pollers to stop
        for poller in self.pollers:
            poller.stop()

        # Cancel all tasks
        for task in self._tasks:
            task.cancel()

        # Wait for tasks to complete
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)

        self._tasks = []
        self._running = False
        logger.info("Dispatcher stopped")

    @property
    def is_running(self) -> bool:
        """Check if the dispatcher is currently running."""
        return self._running

    @property
    def stats(self) -> dict:
        """Get aggregated statistics from all pollers."""
        return {
            "running": self._running,
            "poller_count": len(self.pollers),
            "handler_count": sum(len(h) for h in self.handlers.values()),
            "pollers": [p.stats for p in self.pollers],
        }


def create_pr_agent_handler() -> EventHandler:
    """
    Create a handler that integrates with existing PR-Agent tools.

    Returns:
        EventHandler that routes to PRAgent.handle_request()
    """
    from pr_agent.agent.pr_agent import PRAgent

    agent = PRAgent()

    async def handle_github_mention(event: Event) -> None:
        """Route GitHub mentions to PR-Agent."""
        if not isinstance(event, GitHubMentionEvent):
            return

        if not event.command:
            logger.debug(f"No command in mention, skipping: {event.id}")
            return

        if not event.pr_url:
            logger.debug(f"Not a PR mention, skipping: {event.id}")
            return

        logger.info(f"Processing {event.command} for PR: {event.pr_url}")

        try:
            # Build request string from command and args
            request = event.command
            if event.command_args:
                request += " " + " ".join(event.command_args)

            result = agent.handle_request(
                pr_url=event.pr_url,
                request=request,
            )
            if asyncio.iscoroutine(result):
                await result
        except Exception as e:
            logger.error(f"PR-Agent failed to process {event.command}: {e}")
            raise

    return handle_github_mention
