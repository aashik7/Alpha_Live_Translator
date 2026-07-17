"""Thread-safe in-process event bus for decoupling backend from UI."""

import threading
import traceback
from collections import defaultdict
from typing import Any, Callable, DefaultDict, List

from alpha.utils.logging_utils import get_logger

logger = get_logger(__name__)

Handler = Callable[[Any], None]


class EventBus:
    """Simple publish/subscribe bus safe for multi-threaded desktop use."""

    def __init__(self) -> None:
        self._handlers: DefaultDict[str, List[Handler]] = defaultdict(list)
        self._lock = threading.RLock()

    def subscribe(self, event_type, handler: Handler) -> None:
        """Register a handler for an event type (EventType or string)."""
        key = self._event_key(event_type)
        with self._lock:
            if handler not in self._handlers[key]:
                self._handlers[key].append(handler)

    def unsubscribe(self, event_type, handler: Handler) -> None:
        """Remove a previously registered handler."""
        key = self._event_key(event_type)
        with self._lock:
            try:
                self._handlers[key].remove(handler)
            except ValueError:
                pass

    def publish(self, event_type, payload: Any = None) -> None:
        """Notify subscribers; handler exceptions are logged, never propagated."""
        key = self._event_key(event_type)
        with self._lock:
            handlers = list(self._handlers.get(key, ()))

        for handler in handlers:
            try:
                handler(payload)
            except Exception as exc:
                logger.error("EventBus handler error for %s: %s", key, exc)
                traceback.print_exc()

    @staticmethod
    def _event_key(event_type) -> str:
        return getattr(event_type, "value", str(event_type))
