"""Agent capability registry used by the AI planner and runtime.

The registry is deliberately small and data driven.  An LLM can select an
agent by name, but it can never invent an endpoint or an execution primitive.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional


class UnknownAgentError(ValueError):
    """Raised when a plan references an agent that is not registered."""


@dataclass(frozen=True)
class AgentSpec:
    name: str
    endpoint: str = ""
    capabilities: List[str] = field(default_factory=list)
    input_schema: str = "paper_review"
    output_schema: str = "audit_result"
    cost: float = 1.0
    timeout: float = 600.0
    can_consult: List[str] = field(default_factory=list)
    config: Dict[str, Any] = field(default_factory=dict, compare=False)

    def as_dict(self) -> Dict[str, Any]:
        """Return a JSON-safe description while retaining service settings."""
        result = dict(self.config)
        result.update(
            {
                "name": self.name,
                "endpoint": self.endpoint,
                "capabilities": list(self.capabilities),
                "input_schema": self.input_schema,
                "output_schema": self.output_schema,
                "cost": self.cost,
                "timeout": self.timeout,
                "can_consult": list(self.can_consult),
            }
        )
        return result


class AgentRegistry:
    """Allow-list of callable agents and the capabilities they expose."""

    def __init__(self, specs: Iterable[AgentSpec]) -> None:
        self._specs = {spec.name: spec for spec in specs}
        if not self._specs:
            raise ValueError("at least one agent must be registered")

    @classmethod
    def from_endpoints(cls, endpoints: Mapping[str, Mapping[str, Any]]) -> "AgentRegistry":
        specs: List[AgentSpec] = []
        default_capabilities = {
            "logic_agent": ["logic", "argument", "consistency"],
            "citation_agent": ["citation", "literature", "relevance"],
            "format_agent": ["format", "structure", "style"],
            "experiment_agent": ["experiment", "statistics", "reproducibility"],
        }
        for name, raw in endpoints.items():
            cfg = dict(raw or {})
            capabilities = cfg.get("capabilities") or default_capabilities.get(name, [cfg.get("type", name)])
            can_consult = cfg.get("can_consult")
            if can_consult is None:
                can_consult = [other for other in endpoints if other != name]
            specs.append(
                AgentSpec(
                    name=name,
                    endpoint=str(cfg.get("url", cfg.get("endpoint", ""))),
                    capabilities=[str(item) for item in capabilities],
                    input_schema=str(cfg.get("input_schema", "paper_review")),
                    output_schema=str(cfg.get("output_schema", "audit_result")),
                    cost=float(cfg.get("cost", 1.0)),
                    timeout=float(cfg.get("timeout", 600)),
                    can_consult=[str(item) for item in can_consult],
                    config=cfg,
                )
            )
        return cls(specs)

    def names(self) -> List[str]:
        return list(self._specs)

    def get(self, name: str) -> AgentSpec:
        try:
            return self._specs[name]
        except KeyError as exc:
            raise UnknownAgentError(f"unknown agent: {name}") from exc

    def contains(self, name: str) -> bool:
        return name in self._specs

    def describe(self) -> List[Dict[str, Any]]:
        return [self._specs[name].as_dict() for name in self._specs]

