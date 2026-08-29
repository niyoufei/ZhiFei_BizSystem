from __future__ import annotations

from copy import deepcopy

import pytest

from app import storage, submission_scoring_service


class MissingProjectError(Exception):
    pass


class StoreHarness:
    def __init__(self, data, *, fail_calls=None):
        self.data = deepcopy(data)
        self.original = deepcopy(data)
        self.fail_calls = fail_calls or {}
        self.reads = []
        self.writes = []
        self.write_counts = {name: 0 for name in data}
        self.history_calls = []
        self.transactions = []

    def loader(self, name):
        def load():
            self.reads.append(name)
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
        self.data[name].append({"id": "history-new", **deepcopy(history_args)})
        if call_number in self.fail_calls.get(name, set()):
            raise OSError(f"controlled {name} save failure #{call_number}")

    def transaction(self, *names):
        self.transactions.append(names)

        def decorate(func):
            return func

        return decorate

    def service_kwargs(self):
        return {
            "atomic_json_transaction": self.transaction,
            "load_projects": self.loader("projects"),
            "load_submissions": self.loader("submissions"),
            "save_submissions": self.saver("submissions"),
            "load_score_reports": self.loader("score_reports"),
            "save_score_reports": self.saver("score_reports"),
            "load_evidence_units": self.loader("evidence_units"),
            "save_evidence_units": self.saver("evidence_units"),
            "load_score_history": self.loader("score_history"),
            "save_score_history": self.saver("score_history"),
            "record_history_score": self.record_history,
            "replace_submission_evidence_units": _replace_evidence_units,
            "project_not_found_error": MissingProjectError,
        }


def _replace_evidence_units(all_units, *, submission_id, new_units):
    return [
        unit for unit in all_units if str(unit.get("submission_id") or "") != submission_id
    ] + deepcopy(new_units)


def _base_data():
    return {
        "projects": [{"id": "p1"}],
        "submissions": [{"id": "submission-old", "project_id": "p1"}],
        "score_reports": [{"id": "report-old", "submission_id": "submission-old"}],
        "evidence_units": [{"id": "evidence-old", "submission_id": "submission-old"}],
        "score_history": [{"id": "history-old", "submission_id": "submission-old"}],
    }


def _call_service(stores, *, evidence_units=None):
    submission_scoring_service.commit_inline_scoring_result(
        project_id="p1",
        record={"id": "submission-new", "project_id": "p1", "filename": "inline"},
        snapshot={"id": "report-new", "submission_id": "submission-new"},
        evidence_units=evidence_units or [],
        history_args={
            "project_id": "p1",
            "submission_id": "submission-new",
            "filename": "inline",
            "total_score": 88.0,
            "dimension_scores": {"D01": 8.0},
            "penalty_count": 1,
        },
        **stores.service_kwargs(),
    )


def test_commit_inline_scoring_result_without_evidence_writes_history_once():
    stores = StoreHarness(_base_data())

    _call_service(stores)

    assert [row["id"] for row in stores.data["submissions"]] == [
        "submission-old",
        "submission-new",
    ]
    assert [row["id"] for row in stores.data["score_reports"]] == [
        "report-old",
        "report-new",
    ]
    assert stores.data["evidence_units"] == stores.original["evidence_units"]
    assert [row["id"] for row in stores.data["score_history"]] == [
        "history-old",
        "history-new",
    ]
    assert len(stores.history_calls) == 1
    assert "evidence_units" not in stores.writes
    assert stores.transactions == [
        (
            "evidence_units",
            "projects",
            "score_history",
            "score_reports",
            "submissions",
        )
    ]


def test_commit_inline_scoring_result_with_evidence_replaces_new_submission_units():
    data = _base_data()
    data["evidence_units"].append({"id": "stale-new", "submission_id": "submission-new"})
    stores = StoreHarness(data)

    _call_service(
        stores,
        evidence_units=[{"id": "evidence-new", "submission_id": "submission-new"}],
    )

    assert [row["id"] for row in stores.data["evidence_units"]] == [
        "evidence-old",
        "evidence-new",
    ]
    assert len(stores.history_calls) == 1


def test_commit_inline_scoring_result_missing_project_performs_no_store_writes():
    data = _base_data()
    data["projects"] = []
    stores = StoreHarness(data)

    with pytest.raises(MissingProjectError):
        _call_service(stores, evidence_units=[{"id": "evidence-new"}])

    assert stores.reads == ["projects"]
    assert stores.writes == []
    assert stores.data == stores.original


@pytest.mark.parametrize(
    "failing_store",
    ["submissions", "score_reports", "evidence_units", "score_history"],
)
def test_commit_inline_scoring_result_restores_every_store_after_ambiguous_failure(
    failing_store,
):
    stores = StoreHarness(_base_data(), fail_calls={failing_store: {1}})

    with pytest.raises(OSError, match=f"controlled {failing_store} save failure #1"):
        _call_service(
            stores,
            evidence_units=[{"id": "evidence-new", "submission_id": "submission-new"}],
        )

    assert stores.data == stores.original


def test_commit_inline_scoring_result_preserves_primary_error_if_rollback_fails():
    stores = StoreHarness(
        _base_data(),
        fail_calls={"score_history": {1}, "submissions": {2}},
    )

    with pytest.raises(
        OSError,
        match="controlled score_history save failure #1",
    ) as exc_info:
        _call_service(
            stores,
            evidence_units=[{"id": "evidence-new", "submission_id": "submission-new"}],
        )

    assert any(
        "controlled submissions save failure #2" in note
        for note in getattr(exc_info.value, "__notes__", [])
    )


def test_commit_inline_scoring_result_supports_nested_real_score_history_write(
    monkeypatch,
    tmp_path,
):
    from app.engine.history import record_score

    store_paths = {
        "PROJECTS_PATH": tmp_path / "projects.json",
        "SUBMISSIONS_PATH": tmp_path / "submissions.json",
        "SCORE_REPORTS_PATH": tmp_path / "score_reports.json",
        "EVIDENCE_UNITS_PATH": tmp_path / "evidence_units.json",
        "HISTORY_PATH": tmp_path / "score_history.json",
    }
    for attribute, path in store_paths.items():
        monkeypatch.setattr(storage, attribute, path)

    storage.save_projects([{"id": "p1"}])
    storage.save_submissions([])
    storage.save_score_reports([])
    storage.save_evidence_units([])
    storage.save_score_history([])

    submission_scoring_service.commit_inline_scoring_result(
        project_id="p1",
        record={"id": "submission-new", "project_id": "p1", "filename": "inline"},
        snapshot={"id": "report-new", "submission_id": "submission-new"},
        evidence_units=[{"id": "evidence-new", "submission_id": "submission-new"}],
        history_args={
            "project_id": "p1",
            "submission_id": "submission-new",
            "filename": "inline",
            "total_score": 88.0,
            "dimension_scores": {},
            "penalty_count": 0,
        },
        atomic_json_transaction=storage.atomic_json_transaction,
        load_projects=storage.load_projects,
        load_submissions=storage.load_submissions,
        save_submissions=storage.save_submissions,
        load_score_reports=storage.load_score_reports,
        save_score_reports=storage.save_score_reports,
        load_evidence_units=storage.load_evidence_units,
        save_evidence_units=storage.save_evidence_units,
        load_score_history=storage.load_score_history,
        save_score_history=storage.save_score_history,
        record_history_score=record_score,
        replace_submission_evidence_units=_replace_evidence_units,
        project_not_found_error=MissingProjectError,
    )

    assert [row["id"] for row in storage.load_submissions()] == ["submission-new"]
    assert [row["id"] for row in storage.load_score_reports()] == ["report-new"]
    assert [row["id"] for row in storage.load_evidence_units()] == ["evidence-new"]
    history = storage.load_score_history()
    assert len(history) == 1
    assert history[0]["submission_id"] == "submission-new"
