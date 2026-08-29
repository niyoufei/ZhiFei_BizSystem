from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from app import project_delete_service


def _direct_transaction(*_store_names):
    return lambda func: func


def _store_data(material_path: Path):
    return {
        "projects": [
            {"id": "p1", "name": "target", "expert_profile_id": "ep1"},
            {"id": "p2", "name": "kept", "expert_profile_id": "ep2"},
        ],
        "materials": [
            {"id": "m1", "project_id": "p1", "path": str(material_path)},
            {"id": "m2", "project_id": "p2"},
        ],
        "submissions": [
            {"id": "s1", "project_id": "p1"},
            {"id": "s2", "project_id": "p2"},
        ],
        "score_reports": [
            {"id": "r1", "project_id": "p1"},
            {"id": "r2", "project_id": "p2"},
        ],
        "evidence_units": [
            {"id": "e1", "submission_id": "s1"},
            {"id": "e2", "submission_id": "s2"},
        ],
        "qingtian_results": [
            {"id": "q1", "submission_id": "s1"},
            {"id": "q2", "submission_id": "s2"},
        ],
        "delta_cases": [
            {"id": "d1", "project_id": "p1", "submission_id": "s1"},
            {"id": "d2", "project_id": "p2", "submission_id": "s2"},
        ],
        "calibration_samples": [
            {"id": "c1", "project_id": "p1", "submission_id": "s1"},
            {"id": "c2", "project_id": "p2", "submission_id": "s2"},
        ],
        "calibration_models": [
            {"id": "cm1", "train_filter": {"project_id": "p1"}},
            {"id": "cm2", "train_filter": {"project_id": "p2"}},
        ],
        "patch_packages": [
            {"id": "pp1", "project_id": "p1"},
            {"id": "pp2", "project_id": "p2"},
        ],
        "patch_deployments": [
            {"id": "pd1", "project_id": "p1", "patch_id": "pp1"},
            {"id": "pd2", "project_id": "p2", "patch_id": "pp2"},
        ],
        "project_anchors": [
            {"id": "a1", "project_id": "p1"},
            {"id": "a2", "project_id": "p2"},
        ],
        "project_requirements": [
            {"id": "pr1", "project_id": "p1"},
            {"id": "pr2", "project_id": "p2"},
        ],
        "learning_profiles": [
            {"id": "lp1", "project_id": "p1"},
            {"id": "lp2", "project_id": "p2"},
        ],
        "score_history": [
            {"id": "sh1", "project_id": "p1"},
            {"id": "sh2", "project_id": "p2"},
        ],
        "project_context": {"p1": {"value": 1}, "p2": {"value": 2}},
        "ground_truth": [
            {"id": "gt1", "project_id": "p1"},
            {"id": "gt2", "project_id": "p2"},
        ],
        "evolution_reports": {"p1": {"value": 1}, "p2": {"value": 2}},
        "expert_profiles": [{"id": "ep1"}, {"id": "ep2"}],
    }


class StoreHarness:
    def __init__(self, materials_dir, material_path, *, fail_calls=None):
        self.materials_dir = materials_dir
        self.data = deepcopy(_store_data(material_path))
        self.original = deepcopy(self.data)
        self.fail_calls = fail_calls or {}
        self.write_counts = {name: 0 for name in self.data}
        self.writes = []
        self.invalidations = []

    def loader(self, name):
        return lambda: deepcopy(self.data[name])

    def saver(self, name):
        def save(value):
            self.write_counts[name] += 1
            call_number = self.write_counts[name]
            self.writes.append(name)
            self.data[name] = deepcopy(value)
            if call_number in self.fail_calls.get(name, set()):
                raise OSError(f"controlled {name} save failure #{call_number}")

        return save

    def invoke(self, *, transaction_factory=_direct_transaction, project_id="p1"):
        kwargs = {
            "project_id": project_id,
            "atomic_json_transaction": transaction_factory,
            "materials_dir": self.materials_dir,
            "ensure_data_dirs": lambda: None,
            "invalidate_material_index_cache": self.invalidations.append,
            "project_not_found_error": lambda: LookupError("project missing"),
        }
        for name in self.data:
            kwargs[f"load_{name}"] = self.loader(name)
            kwargs[f"save_{name}"] = self.saver(name)
        return project_delete_service.delete_project_cascade(**kwargs)


def _make_harness(tmp_path, *, fail_calls=None):
    materials_dir = tmp_path / "materials"
    project_dir = materials_dir / "p1"
    project_dir.mkdir(parents=True)
    material_path = project_dir / "document.txt"
    material_path.write_text("evidence", encoding="utf-8")
    return StoreHarness(
        materials_dir,
        material_path,
        fail_calls=fail_calls,
    ), project_dir


def test_delete_locks_complete_store_set_and_removes_json_and_files(tmp_path):
    stores, project_dir = _make_harness(tmp_path)
    captured = []

    def transaction_factory(*names):
        captured.append(names)
        return lambda func: func

    result = stores.invoke(transaction_factory=transaction_factory)

    assert captured == [project_delete_service.PROJECT_DELETE_STORES]
    assert result["project_id"] == "p1"
    assert result["removed_counts"]["calibration_models"] == 1
    assert not project_dir.exists()
    assert stores.invalidations == ["p1"]
    for name, value in stores.data.items():
        if isinstance(value, dict):
            assert list(value) == ["p2"]
        else:
            assert len(value) == 1
            if name == "calibration_models":
                assert value[0]["train_filter"]["project_id"] == "p2"
            elif name == "expert_profiles":
                assert value[0]["id"] == "ep2"
            else:
                assert value[0].get("project_id", "p2") != "p1"


@pytest.mark.parametrize("failing_store", project_delete_service.PROJECT_DELETE_STORES)
def test_delete_restores_all_stores_and_files_after_ambiguous_failure(
    tmp_path,
    failing_store,
):
    stores, project_dir = _make_harness(tmp_path, fail_calls={failing_store: {1}})

    with pytest.raises(OSError, match=f"controlled {failing_store} save failure #1"):
        stores.invoke()

    assert stores.data == stores.original
    assert (project_dir / "document.txt").read_text(encoding="utf-8") == "evidence"
    assert stores.invalidations == []


def test_delete_missing_project_is_zero_write_and_keeps_files(tmp_path):
    stores, project_dir = _make_harness(tmp_path)

    with pytest.raises(LookupError, match="project missing"):
        stores.invoke(project_id="missing")

    assert stores.writes == []
    assert stores.data == stores.original
    assert project_dir.exists()


def test_delete_preserves_primary_error_and_adds_rollback_note(tmp_path):
    stores, _project_dir = _make_harness(
        tmp_path,
        fail_calls={"projects": {1, 2}},
    )

    with pytest.raises(OSError, match="controlled projects save failure #1") as exc_info:
        stores.invoke()

    assert any(
        "controlled projects save failure #2" in note
        for note in getattr(exc_info.value, "__notes__", [])
    )


def test_delete_does_not_remove_material_outside_managed_root(tmp_path):
    external_file = tmp_path / "outside.txt"
    external_file.write_text("external", encoding="utf-8")
    stores = StoreHarness(tmp_path / "materials", external_file)

    result = stores.invoke()

    assert external_file.read_text(encoding="utf-8") == "external"
    assert result["cleanup_warnings"] == [
        f"skipped material outside managed root: {external_file.resolve()}"
    ]


def test_file_staging_rejects_project_directory_outside_managed_root(tmp_path):
    materials_dir = tmp_path / "materials"
    outside_dir = tmp_path / "outside-project"
    outside_dir.mkdir()
    evidence = outside_dir / "evidence.txt"
    evidence.write_text("external", encoding="utf-8")

    quarantine, staged_paths, warnings = project_delete_service._stage_project_files(
        project_id="../outside-project",
        materials_dir=materials_dir,
        materials=[],
    )

    assert quarantine is None
    assert staged_paths == []
    assert warnings == [f"skipped project directory outside managed root: {outside_dir.resolve()}"]
    assert evidence.read_text(encoding="utf-8") == "external"
