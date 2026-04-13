from __future__ import annotations

from typing import Any

from app.application.storage_access import StorageAccess
from app.bootstrap.storage import get_storage_access

_STORAGE_METHOD_MAP = {
    "load_projects": "load_projects",
    "save_projects": "save_projects",
    "load_submissions": "load_submissions",
    "save_submissions": "save_submissions",
    "load_materials": "load_materials",
    "save_materials": "save_materials",
    "load_ground_truth": "load_ground_truth",
    "save_ground_truth": "save_ground_truth",
    "load_qingtian_results": "load_qingtian_results",
    "save_qingtian_results": "save_qingtian_results",
    "load_score_reports": "load_score_reports",
    "save_score_reports": "save_score_reports",
    "load_evidence_units": "load_evidence_units",
    "save_evidence_units": "save_evidence_units",
    "list_json_versions": "list_json_versions",
    "load_json_version": "load_json_version",
    "restore_json_version": "restore_json_version",
}


class RuntimeModuleFacade:
    def __init__(self, runtime_module: Any, *, storage: StorageAccess | None = None):
        self._runtime_module = runtime_module
        self.storage = storage or get_storage_access()

    def __getattr__(self, name: str) -> Any:
        storage_name = _STORAGE_METHOD_MAP.get(name)
        if storage_name is not None:
            runtime_attr = getattr(self._runtime_module, name, None)
            runtime_module_name = getattr(runtime_attr, "__module__", None)
            if runtime_attr is not None and runtime_module_name not in {
                "app.storage",
                None,
            }:
                return runtime_attr
            return getattr(self.storage, storage_name)
        return getattr(self._runtime_module, name)
