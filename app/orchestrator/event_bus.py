from __future__ import annotations

import uuid
from datetime import datetime, timezone
from threading import RLock
from typing import Any, Dict, List, Optional, Set


class EventBus:
    """Lightweight in-process pub/sub bus for orchestrated agent collaboration."""

    def __init__(
        self,
        subscriptions: Optional[Dict[str, Set[str]]] = None,
        max_events_per_request: int = 500,
    ) -> None:
        self._subscriptions: Dict[str, Set[str]] = subscriptions or {}
        self._max_events_per_request = max_events_per_request
        self._events_by_request: Dict[str, List[Dict[str, Any]]] = {}
        self._lock = RLock()

    def reset_request(self, request_id: str) -> None:
        with self._lock:
            self._events_by_request[request_id] = []

    def publish(
        self,
        *,
        event_type: str,
        request_id: str,
        paper_id: str,
        producer_agent: str,
        payload: Dict[str, Any],
        trace_id: Optional[str] = None,
        idempotency_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        event = {
            "event_id": str(uuid.uuid4()),
            "event_type": event_type,
            "request_id": request_id,
            "paper_id": paper_id,
            "producer_agent": producer_agent,
            "payload": payload,
            "trace_id": trace_id or request_id,
            "idempotency_key": idempotency_key or f"{request_id}:{producer_agent}:{event_type}",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        with self._lock:
            queue = self._events_by_request.setdefault(request_id, [])
            queue.append(event)
            if len(queue) > self._max_events_per_request:
                del queue[: len(queue) - self._max_events_per_request]
        return event

    def get_events_for_agent(self, agent_name: str, request_id: str) -> List[Dict[str, Any]]:
        allowed_types = self._subscriptions.get(agent_name, set())
        with self._lock:
            events = list(self._events_by_request.get(request_id, []))

        visible_events: List[Dict[str, Any]] = []
        for event in events:
            if event.get("producer_agent") == agent_name:
                continue
            if allowed_types and event.get("event_type") not in allowed_types:
                continue
            visible_events.append(event)
        return visible_events

    def snapshot(self, request_id: str) -> List[Dict[str, Any]]:
        with self._lock:
            return list(self._events_by_request.get(request_id, []))
