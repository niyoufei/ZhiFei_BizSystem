from __future__ import annotations

from copy import deepcopy

import pytest

from app import project_lifecycle_service


def _recovery_kwargs(data, saved, ensure):
    return {
        "load_projects": lambda: deepcopy(data["projects"]),
        "load_submissions": lambda: deepcopy(data["submissions"]),
        "load_materials": lambda: deepcopy(data["materials"]),
        "load_ground_truth": lambda: deepcopy(data["ground_truth"]),
        "load_evolution_reports": lambda: deepcopy(data["evolution_reports"]),
        "save_projects": lambda rows: saved.append(deepcopy(rows)),
        "ensure_project_v2_fields": ensure,
        "now_iso": lambda: "2026-08-29T00:00:00+00:00",
        "default_score_scale_max": 100,
        "default_region": "CN",
        "default_qingtian_model_version": "q1",
        "default_scoring_engine_locked": "v2",
        "default_calibrator_locked": "c1",
    }


def test_missing_project_without_artifacts_is_not_recovered():
    data = {
        "projects": [],
        "submissions": [],
        "materials": [],
        "ground_truth": [],
        "evolution_reports": {},
    }
    projects = [{"id": "stale"}]
    saved = []

    recovered = project_lifecycle_service.recover_missing_project_from_artifacts(
        "missing",
        projects,
        **_recovery_kwargs(data, saved, lambda _project: False),
    )

    assert recovered is None
    assert projects == []
    assert saved == []


def test_missing_project_is_recovered_from_all_artifact_evidence():
    data = {
        "projects": [],
        "submissions": [
            {
                "id": "s1",
                "project_id": "p1",
                "filename": "submission.txt",
                "created_at": "2026-08-29T02:00:00+00:00",
                "updated_at": "2026-08-29T04:00:00+00:00",
                "report": {"meta": {"score_scale_max": 5}},
            }
        ],
        "materials": [
            {
                "id": "m1",
                "project_id": "p1",
                "filename": "site-plan.pdf",
                "created_at": "2026-08-29T01:00:00+00:00",
            }
        ],
        "ground_truth": [
            {
                "id": "g1",
                "project_id": "p1",
                "created_at": "2026-08-29T03:00:00+00:00",
            }
        ],
        "evolution_reports": {"p1": {"updated_at": "2026-08-29T05:00:00+00:00"}},
    }
    projects = []
    saved = []
    ensured = []

    recovered = project_lifecycle_service.recover_missing_project_from_artifacts(
        "p1",
        projects,
        **_recovery_kwargs(
            data,
            saved,
            lambda project: ensured.append(project["id"]) or False,
        ),
    )

    assert recovered is not None
    assert recovered["name"] == "site-plan（恢复）"
    assert recovered["meta"]["score_scale_max"] == 5
    assert recovered["created_at"] == "2026-08-29T01:00:00+00:00"
    assert recovered["updated_at"] == "2026-08-29T05:00:00+00:00"
    assert recovered["region"] == "CN"
    assert ensured == ["p1"]
    assert projects == [recovered]
    assert saved == [projects]


def test_latest_orphan_recovery_selects_newest_artifact_project():
    recovered = []

    result = project_lifecycle_service.recover_latest_orphan_project(
        [{"id": "existing"}],
        load_submissions=lambda: [
            {"project_id": "p2", "updated_at": "2026-08-29T02:00:00+00:00"},
            {"project_id": "existing", "updated_at": "2026-08-29T09:00:00+00:00"},
        ],
        load_materials=lambda: [{"project_id": "p3", "created_at": "2026-08-29T03:00:00+00:00"}],
        recover_missing_project=lambda project_id, _projects: recovered.append(project_id)
        or {"id": project_id},
    )

    assert result == {"id": "p3"}
    assert recovered == ["p3"]


def test_create_project_record_applies_defaults_and_saves_once():
    store = []
    timestamps = iter(["created", "updated"])

    record = project_lifecycle_service.create_project_record(
        name="project",
        meta={"key": "value"},
        load_projects=lambda: deepcopy(store),
        save_projects=lambda rows: store.__setitem__(slice(None), deepcopy(rows)),
        duplicate_name_error=lambda: ValueError("duplicate"),
        new_id=lambda: "p1",
        now_iso=lambda: next(timestamps),
        ensure_project_v2_fields=lambda project: project["meta"].update(
            {"enforce_material_gate": True}
        )
        or True,
        default_region="CN",
        default_qingtian_model_version="q1",
        default_scoring_engine_locked="v2",
        default_calibrator_locked="c1",
    )

    assert record["id"] == "p1"
    assert record["created_at"] == "created"
    assert record["updated_at"] == "updated"
    assert record["meta"] == {"key": "value", "enforce_material_gate": True}
    assert store == [record]


def test_create_project_record_rejects_duplicate_without_write():
    writes = []

    with pytest.raises(ValueError, match="duplicate"):
        project_lifecycle_service.create_project_record(
            name="project",
            meta=None,
            load_projects=lambda: [{"id": "p1", "name": "project"}],
            save_projects=lambda rows: writes.append(rows),
            duplicate_name_error=lambda: ValueError("duplicate"),
            new_id=lambda: "unused",
            now_iso=lambda: "unused",
            ensure_project_v2_fields=lambda _project: False,
            default_region="CN",
            default_qingtian_model_version="q1",
            default_scoring_engine_locked="v2",
            default_calibrator_locked="c1",
        )

    assert writes == []


def test_list_projects_backfills_legacy_records_once():
    store = [{"id": "p1", "name": "legacy"}]
    recovery_calls = []

    rows = project_lifecycle_service.list_project_records(
        load_projects=lambda: deepcopy(store),
        save_projects=lambda value: store.__setitem__(slice(None), deepcopy(value)),
        ensure_project_v2_fields=lambda project: project.setdefault("created_at", "backfilled")
        == "backfilled",
        recovery_enabled=False,
        recover_latest_orphan_project=lambda projects: recovery_calls.append(projects),
    )

    assert rows[0]["created_at"] == "backfilled"
    assert store == rows
    assert recovery_calls == []


def test_list_projects_reloads_store_after_orphan_recovery():
    store = [{"id": "p1", "name": "fixture"}]
    recovery_calls = []

    def recover(projects):
        recovery_calls.append(deepcopy(projects))
        store.append({"id": "p2", "name": "recovered", "created_at": "now"})
        return store[-1]

    rows = project_lifecycle_service.list_project_records(
        load_projects=lambda: deepcopy(store),
        save_projects=lambda _value: None,
        ensure_project_v2_fields=lambda _project: False,
        recovery_enabled=True,
        recover_latest_orphan_project=recover,
    )

    assert [row["id"] for row in rows] == ["p1", "p2"]
    assert len(recovery_calls) == 1


def test_list_projects_does_not_recover_when_active_project_exists():
    recovery_calls = []

    rows = project_lifecycle_service.list_project_records(
        load_projects=lambda: [{"id": "p2", "name": "active", "created_at": "now"}],
        save_projects=lambda _value: None,
        ensure_project_v2_fields=lambda _project: False,
        recovery_enabled=True,
        recover_latest_orphan_project=lambda projects: recovery_calls.append(projects),
    )

    assert [row["id"] for row in rows] == ["p2"]
    assert recovery_calls == []
