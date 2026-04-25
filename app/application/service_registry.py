from __future__ import annotations

from dataclasses import dataclass

from app.application.services import (
    AgentApplicationService,
    CliApplicationService,
    GovernanceApplicationService,
    LearningApplicationService,
    MaterialApplicationService,
    OpsApplicationService,
    ProjectApplicationService,
    ScoringApplicationService,
)
from app.bootstrap.storage import get_storage_access

_STORAGE = get_storage_access()


@dataclass(frozen=True)
class ApplicationServices:
    projects: ProjectApplicationService
    materials: MaterialApplicationService
    scoring: ScoringApplicationService
    governance: GovernanceApplicationService
    learning: LearningApplicationService
    ops: OpsApplicationService
    agents: AgentApplicationService
    cli: CliApplicationService


_SERVICES = ApplicationServices(
    projects=ProjectApplicationService(storage=_STORAGE),
    materials=MaterialApplicationService(storage=_STORAGE),
    scoring=ScoringApplicationService(storage=_STORAGE),
    governance=GovernanceApplicationService(storage=_STORAGE),
    learning=LearningApplicationService(storage=_STORAGE),
    ops=OpsApplicationService(storage=_STORAGE),
    agents=AgentApplicationService(storage=_STORAGE),
    cli=CliApplicationService(storage=_STORAGE),
)


def get_application_services() -> ApplicationServices:
    return _SERVICES
