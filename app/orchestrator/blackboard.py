"""Thread-safe shared blackboard for agent findings and consultations."""

from __future__ import annotations

import copy
import uuid
from datetime import datetime, timezone
from threading import RLock
from typing import Any, Dict, List, Optional


class SharedBlackboard:
    """Controlled collaboration state for one paper-review request.

    Agents can append typed findings or messages, but cannot mutate an
    existing finding in place.  Every returned value is a deep copy.
    """

    def __init__(self, *, paper_id: str, request_id: str, paper_context: Optional[Dict[str, Any]] = None) -> None:
        self.paper_id = paper_id
        self.request_id = request_id
        self._lock = RLock()
        self._state: Dict[str, Any] = {
            "paper_context": dict(paper_context or {}),
            "agent_findings": [],
            "evidence": [],
            "open_questions": [],
            "consultation_requests": [],
            "decisions": [],
            "revisions": [],
        }

    @staticmethod
    def _stamp() -> str:
        return datetime.now(timezone.utc).isoformat()

    def add_finding(
        self,
        *,
        agent: str,
        task_id: str,
        finding: Dict[str, Any],
        evidence: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        record = {
            "finding_id": str(uuid.uuid4()),
            "agent": agent,
            "task_id": task_id,
            "finding": copy.deepcopy(finding),
            "evidence": copy.deepcopy(evidence or finding.get("evidence", [])),
            "created_at": self._stamp(),
        }
        with self._lock:
            self._state["agent_findings"].append(record)
            self._state["evidence"].extend(record["evidence"])
        return copy.deepcopy(record)

    def request_consultation(
        self,
        *,
        from_agent: str,
        to_agent: str,
        topic: str,
        question: str,
        payload: Optional[Dict[str, Any]] = None,
        task_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        request = {
            "request_id": str(uuid.uuid4()),
            "paper_id": self.paper_id,
            "from_agent": from_agent,
            "to_agent": to_agent,
            "task_id": task_id,
            "type": "consultation_request",
            "topic": topic[:200],
            "question": question[:2000],
            "payload": copy.deepcopy(payload or {}),
            "status": "open",
            "created_at": self._stamp(),
        }
        with self._lock:
            self._state["consultation_requests"].append(request)
            self._state["open_questions"].append(copy.deepcopy(request))
        return copy.deepcopy(request)

    def respond_consultation(
        self,
        *,
        request_id: str,
        from_agent: str,
        answer: Dict[str, Any],
    ) -> Dict[str, Any]:
        response = {
            "message_id": str(uuid.uuid4()),
            "request_id": request_id,
            "from_agent": from_agent,
            "type": "consultation_response",
            "answer": copy.deepcopy(answer),
            "created_at": self._stamp(),
        }
        with self._lock:
            for request in self._state["consultation_requests"]:
                if request["request_id"] == request_id:
                    request["status"] = "answered"
                    break
            self._state["revisions"].append(response)
        return copy.deepcopy(response)

    def record_decision(self, *, actor: str, decision: str, rationale: str, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        item = {
            "decision_id": str(uuid.uuid4()),
            "actor": actor,
            "decision": decision,
            "rationale": rationale,
            "payload": copy.deepcopy(payload or {}),
            "created_at": self._stamp(),
        }
        with self._lock:
            self._state["decisions"].append(item)
        return copy.deepcopy(item)

    def context_for_agent(self, agent: str, *, max_findings: int = 30) -> Dict[str, Any]:
        with self._lock:
            findings = [item for item in self._state["agent_findings"] if item.get("agent") != agent]
            requests = [
                item for item in self._state["consultation_requests"]
                if item.get("to_agent") == agent and item.get("status") == "open"
            ]
            return {
                "paper_id": self.paper_id,
                "request_id": self.request_id,
                "agent_findings": copy.deepcopy(findings[-max_findings:]),
                "open_questions": copy.deepcopy(requests[-max_findings:]),
                "decisions": copy.deepcopy(self._state["decisions"][-max_findings:]),
            }

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return copy.deepcopy(self._state)

