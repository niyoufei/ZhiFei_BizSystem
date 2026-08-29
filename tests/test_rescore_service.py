from __future__ import annotations

from copy import deepcopy

import pytest

from app import rescore_service, storage


class MissingProjectError(Exception):
    pass


class StoreHarness:
    def __init__(self, data, *, fail_calls=None):
        self.data = deepcopy(data)
        self.original = deepcopy(data)
        self.fail_calls = fail_calls or {}
        self.writes = []
        self.write_counts = {name: 0 for name in data}
        self.history_calls = []

    def loader(self, name):
        def load():
            return deepcopy(self.data[name])

        return load

    def saver(self, name):
        def save(rows):
            self.write_counts[name] += 1
            call_number = self.write_counts[name]
            self.writes.append(name)
            self.data[name] = deepcopy(rows)
            if call_number in self.fail_calls.get(name, set()):
                raise OSError(f"controlled {name} save failure #{call_number}")

        return save

    def record_history(self, **history_args):
        name = "score_history"
        self.write_counts[name] += 1
        call_number = self.write_counts[name]
        self.writes.append(name)
        self.history_calls.append(deepcopy(history_args))
        self.data[name].append({"id": f"history-{call_number}", **deepcopy(history_args)})
        if call_number in self.fail_calls.get(name, set()):
            raise OSError(f"controlled {name} save failure #{call_number}")

    def service_kwargs(self):
        return {
            "load_projects": self.loader("projects"),
            "find_latest_project": _find_project,
            "load_expert_profiles": self.loader("expert_profiles"),
            "save_expert_profiles": self.saver("expert_profiles"),
            "load_submissions": self.loader("submissions"),
            "save_submissions": self.saver("submissions"),
            "load_score_reports": self.loader("score_reports"),
            "save_score_reports": self.saver("score_reports"),
            "load_evidence_units": self.loader("evidence_units"),
            "save_evidence_units": self.saver("evidence_units"),
            "load_score_history": self.loader("score_history"),
            "save_score_history": self.saver("score_history"),
            "save_projects": self.saver("projects"),
            "record_history_score": self.record_history,
            "replace_submission_evidence_units": _replace_evidence_units,
        }


def _find_project(project_id, projects):
    for project in projects:
        if str(project.get("id")) == project_id:
            return project
    raise MissingProjectError(project_id)


def _replace_evidence_units(all_units, *, submission_id, new_units):
    return [
        unit for unit in all_units if str(unit.get("submission_id") or "") != submission_id
    ] + deepcopy(new_units)


def _base_data():
    return {
        "projects": [{"id": "p1", "name": "old"}],
        "expert_profiles": [{"id": "profile-old", "project_id": "p1"}],
        "submissions": [
            {"id": "s1", "project_id": "p1", "total_score": 60.0},
            {"id": "s2", "project_id": "p1", "total_score": 70.0},
        ],
        "score_reports": [{"id": "report-old", "submission_id": "s2"}],
        "evidence_units": [{"id": "evidence-old", "submission_id": "s1"}],
        "score_history": [{"id": "history-old", "submission_id": "s2"}],
    }


def _computed_updates(*, include_history=True):
    history_args = None
    if include_history:
        history_args = {
            "project_id": "p1",
            "submission_id": "s1",
            "filename": "s1.txt",
            "total_score": 88.0,
            "dimension_scores": {"D01": 8.0},
            "penalty_count": 1,
        }
    return [
        {
            "submission_id": "s1",
            "report": {"total_score": 88.0},
            "total_score": 88.0,
            "updated_at": "2026-08-29T00:00:00Z",
            "expert_profile_id_used": "profile-new",
            "snapshot": {"id": "report-new", "submission_id": "s1"},
            "evidence_units": [{"id": "evidence-new", "submission_id": "s1"}],
            "history_args": history_args,
        },
        {
            "submission_id": "missing",
            "report": {"total_score": 99.0},
            "total_score": 99.0,
            "updated_at": "2026-08-29T00:00:00Z",
            "expert_profile_id_used": "profile-new",
            "snapshot": {"id": "report-missing", "submission_id": "missing"},
            "evidence_units": [],
            "history_args": {
                "project_id": "p1",
                "submission_id": "missing",
                "filename": "missing.txt",
                "total_score": 99.0,
                "dimension_scores": {},
                "penalty_count": 0,
            },
        },
    ]


def _call_service(stores, *, profile_created=True, include_history=True):
    return rescore_service.commit_rescore_batch(
        project_id="p1",
        project_patch={"name": "updated"},
        profile_created=profile_created,
        profile={"id": "profile-new", "project_id": "p1"},
        computed_updates=_computed_updates(include_history=include_history),
        **stores.service_kwargs(),
    )


def test_commit_rescore_batch_writes_matching_results_and_history_once():
    stores = StoreHarness(_base_data())

    committed_ids = _call_service(stores)

    assert committed_ids == {"s1"}
    assert stores.data["projects"][0]["name"] == "updated"
    assert [row["id"] for row in stores.data["expert_profiles"]] == [
        "profile-old",
        "profile-new",
    ]
    submissions = {row["id"]: row for row in stores.data["submissions"]}
    assert submissions["s1"]["total_score"] == 88.0
    assert submissions["s2"]["total_score"] == 70.0
    assert [row["id"] for row in stores.data["score_reports"]] == [
        "report-old",
        "report-new",
    ]
    assert [row["id"] for row in stores.data["evidence_units"]] == ["evidence-new"]
    assert [row["submission_id"] for row in stores.data["score_history"]] == [
        "s2",
        "s1",
    ]
    assert len(stores.history_calls) == 1


def test_commit_rescore_batch_skips_history_when_update_is_blocked():
    stores = StoreHarness(_base_data())

    committed_ids = _call_service(stores, profile_created=False, include_history=False)

    assert committed_ids == {"s1"}
    assert stores.data["score_history"] == stores.original["score_history"]
    assert stores.history_calls == []


def test_commit_rescore_batch_missing_project_performs_no_writes():
    data = _base_data()
    data["projects"] = []
    stores = StoreHarness(data)

    with pytest.raises(MissingProjectError):
        _call_service(stores)

    assert stores.writes == []
    assert stores.data == stores.original


@pytest.mark.parametrize(
    "failing_store",
    [
        "expert_profiles",
        "submissions",
        "score_reports",
        "evidence_units",
        "projects",
        "score_history",
    ],
)
def test_commit_rescore_batch_restores_all_stores_after_ambiguous_failure(failing_store):
    stores = StoreHarness(_base_data(), fail_calls={failing_store: {1}})

    with pytest.raises(OSError, match=f"controlled {failing_store} save failure #1"):
        _call_service(stores)

    assert stores.data == stores.original


def test_commit_rescore_batch_preserves_primary_error_if_rollback_fails():
    stores = StoreHarness(
        _base_data(),
        fail_calls={"score_history": {1}, "submissions": {2}},
    )

    with pytest.raises(
        OSError,
        match="controlled score_history save failure #1",
    ) as exc_info:
        _call_service(stores)

    assert any(
        "controlled submissions save failure #2" in note
        for note in getattr(exc_info.value, "__notes__", [])
    )


def test_commit_rescore_batch_supports_nested_real_score_history_write(monkeypatch, tmp_path):
    from app.engine.history import record_score

    store_paths = {
        "PROJECTS_PATH": tmp_path / "projects.json",
        "EXPERT_PROFILES_PATH": tmp_path / "expert_profiles.json",
        "SUBMISSIONS_PATH": tmp_path / "submissions.json",
        "SCORE_REPORTS_PATH": tmp_path / "score_reports.json",
        "EVIDENCE_UNITS_PATH": tmp_path / "evidence_units.json",
        "HISTORY_PATH": tmp_path / "score_history.json",
    }
    for attribute, path in store_paths.items():
        monkeypatch.setattr(storage, attribute, path)

    storage.save_projects([{"id": "p1", "name": "old"}])
    storage.save_expert_profiles([])
    storage.save_submissions([{"id": "s1", "project_id": "p1"}])
    storage.save_score_reports([])
    storage.save_evidence_units([])
    storage.save_score_history([])

    @storage.atomic_json_transaction(
        "evidence_units",
        "expert_profiles",
        "projects",
        "score_history",
        "score_reports",
        "submissions",
    )
    def commit():
        return rescore_service.commit_rescore_batch(
            project_id="p1",
            project_patch={"name": "updated"},
            profile_created=False,
            profile={"id": "profile-existing"},
            computed_updates=_computed_updates()[:1],
            load_projects=storage.load_projects,
            find_latest_project=_find_project,
            load_expert_profiles=storage.load_expert_profiles,
            save_expert_profiles=storage.save_expert_profiles,
            load_submissions=storage.load_submissions,
            save_submissions=storage.save_submissions,
            load_score_reports=storage.load_score_reports,
            save_score_reports=storage.save_score_reports,
            load_evidence_units=storage.load_evidence_units,
            save_evidence_units=storage.save_evidence_units,
            load_score_history=storage.load_score_history,
            save_score_history=storage.save_score_history,
            save_projects=storage.save_projects,
            record_history_score=record_score,
            replace_submission_evidence_units=_replace_evidence_units,
        )

    assert commit() == {"s1"}
    assert storage.load_projects()[0]["name"] == "updated"
    assert storage.load_submissions()[0]["total_score"] == 88.0
    assert storage.load_score_reports()[0]["id"] == "report-new"
    assert storage.load_evidence_units()[0]["id"] == "evidence-new"
    assert storage.load_score_history()[0]["submission_id"] == "s1"
