from __future__ import annotations

from app.application.agents import ControlledAgentRunner, build_default_agent_registry
from app.application.storage_access import StorageAccess


class AgentApplicationService:
    def __init__(self, *, storage: StorageAccess | None = None):
        self.registry = build_default_agent_registry(storage=storage)
        self.runner = ControlledAgentRunner()

    def list_agents(self) -> list[dict[str, object]]:
        rows = []
        for agent in self.registry.list_agents():
            rows.append(agent.spec.model_dump(mode="json"))
        return rows

    def dry_run(
        self,
        *,
        agent_name: str,
        payload: dict[str, object],
        reuse_cached: bool = True,
    ):
        agent = self.registry.get(agent_name)
        return self.runner.run(
            agent=agent,
            payload=dict(payload),
            reuse_cached=reuse_cached,
            dry_run=True,
        )
