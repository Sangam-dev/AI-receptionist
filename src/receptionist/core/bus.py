import asyncio
import logging
from collections import defaultdict
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from receptionist.core.events import BaseEvent, HandlerFailed

logger = logging.getLogger(__name__)

E = TypeVar("E", bound=BaseEvent)

Handler = Callable[[E], Awaitable[None]]


def subscribe(event_type: type[E]) -> Callable[[Handler[E]], Handler[E]]:
    def decorator(func: Handler[E]) -> Handler[E]:
        if not hasattr(func, "_subscribed_events"):
            func._subscribed_events = []
        func._subscribed_events.append(event_type)
        return func

    return decorator


class EventBus:
    def __init__(self) -> None:
        self._handlers: dict[type, list[Handler]] = defaultdict(list)
        self._tasks: set[asyncio.Task] = set()
        self._closed = False
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            self._loop = None

    def subscribe(self, event_type: type[E], handler: Handler[E]) -> None:
        if self._closed:
            raise RuntimeError("bus is closed")
        if not asyncio.iscoroutinefunction(handler):
            raise TypeError(
                f"{getattr(handler, '__name__', handler)} must be async. Got: {type(handler)}"
            )
        self._handlers[event_type].append(handler)
        logger.debug("Subscribed %s → %s", event_type.__name__, getattr(handler, "__name__", handler))

    def unsubscribe(self, event_type: type[E], handler: Handler[E]) -> None:
        handlers = self._handlers.get(event_type, [])
        if handler in handlers:
            handlers.remove(handler)

    def handler_count(self, event_type: type) -> int:
        return len(self._handlers.get(event_type, []))

    def register_handlers(self, obj: Any) -> None:
        for attr_name in dir(obj):
            try:
                attr = getattr(obj, attr_name)
            except AttributeError:
                continue
            if hasattr(attr, "_subscribed_events"):
                for event_type in attr._subscribed_events:
                    self.subscribe(event_type, attr)

    def emit(self, event: BaseEvent) -> None:
        if self._closed:
            raise RuntimeError("bus is closed")
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError as exc:
            raise RuntimeError(
                f"emit({type(event).__name__}) called outside a running event loop"
            ) from exc

        event_type = type(event)
        handlers = self._handlers.get(event_type, [])

        if not handlers:
            logger.debug("No handlers for %s", event_type.__name__)
            return

        logger.debug(
            "emit %s [%s] → %d handler(s)",
            event_type.__name__,
            event.event_id[:8],
            len(handlers),
        )

        for handler in handlers:
            task = asyncio.create_task(
                self._run_handler(handler, event),
                name=f"{getattr(handler, '__name__', handler)}:{event.event_id[:8]}",
            )
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

    async def _run_handler(self, handler: Handler, event: BaseEvent) -> None:
        try:
            await handler(event)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            handler_name = getattr(handler, "__name__", repr(handler))
            logger.exception(
                "Handler %s failed on %s [session=%s turn=%s]",
                handler_name,
                type(event).__name__,
                event.session_id,
                event.turn_id,
            )
            if isinstance(event, HandlerFailed):
                return
            self.emit(
                HandlerFailed(
                    handler=handler_name,
                    failed_event_id=event.event_id,
                    failed_event_type=type(event).__name__,
                    error=f"{type(exc).__name__}: {exc}",
                    session_id=event.session_id,
                    turn_id=event.turn_id,
                )
            )

    async def emit_and_wait(self, event: BaseEvent) -> None:
        if self._closed:
            raise RuntimeError("bus is closed")
        handlers = self._handlers.get(type(event), [])
        if not handlers:
            logger.debug("No handlers for %s", type(event).__name__)
            return
        await asyncio.gather(
            *(self._run_handler(h, event) for h in handlers),
            return_exceptions=True,
        )

    def emit_threadsafe(self, event: BaseEvent) -> None:
        if self._loop is None:
            raise RuntimeError("no event loop; start the app before emit_threadsafe")
        self._loop.call_soon_threadsafe(self.emit, event)

    async def drain(self, timeout: float | None = None) -> None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout if timeout is not None else None
        while self._tasks:
            remaining = None
            if deadline is not None:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    logger.warning(
                        "drain(timeout=%s) expired with %d task(s) still running",
                        timeout,
                        len(self._tasks),
                    )
                    return
            await asyncio.wait(tuple(self._tasks), timeout=remaining)

    async def close(self) -> None:
        self._closed = True
        for task in list(self._tasks):
            if not task.done():
                task.cancel()
        await self.drain()
        self._handlers.clear()
