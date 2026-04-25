from __future__ import annotations

from dataclasses import dataclass

from app.application.agents.evidence_completeness import EvidenceCompletenessAgent
from app.application.agents.ops_triage import OpsTriageAgent
from app.application.agents.runtime import ControlledAgent
from app.application.agents.score_deviation_analysis import ScoreDeviationAnalysisAgent
from app.application.storage_access import StorageAccess


@dataclass(frozen=True)
class AgentRegistry:
    _agents_by_name: dict[str, ControlledAgent]

    def get(self, agent_name: str) -> ControlledAgent:
        key = str(agent_name or "").strip().lower()
        try:
            return self._agents_by_name[key]
        except KeyError as exc:
            raise KeyError(f"unknown_agent:{agent_name}") from exc

    def list_agents(self) -> list[ControlledAgent]:
        return [self._agents_by_name[key] for key in sorted(self._agents_by_name.keys())]


def build_default_agent_registry(*, storage: StorageAccess | None = None) -> AgentRegistry:
    agents = {
        "evidence-completeness": EvidenceCompletenessAgent(storage=storage),
        "score-deviation-analysis": ScoreDeviationAnalysisAgent(storage=storage),
        "ops-triage": OpsTriageAgent(),
    }
    return AgentRegistry(_agents_by_name=agents)
