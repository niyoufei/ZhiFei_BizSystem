from __future__ import annotations

from copy import deepcopy

import pytest

from app import qingtian_result_service


class StoreHarness:
    def __init__(self, *, status="scoring_preparation", fail_calls=None):
        self.data = {
            "submissions": [{"id": "s1", "project_id": "p1"}],
            "projects": [
                {
                    "id": "p1",
                    "status": status,
                    "qingtian_model_version": "project-model",
                }
            ],
            "qingtian_results": [{"id": "existing", "submission_id": "s0"}],
        }
        self.original = deepcopy(self.data)
        self.fail_calls = fail_calls or {}
        self.writes = []
        self.write_counts = {"projects": 0, "qingtian_results": 0}

    def loader(self, name):
        return lambda: deepcopy(self.data[name])

    def saver(self, name):
        def save(rows):
            self.write_counts[name] += 1
            call_number = self.write_counts[name]
            self.writes.append(name)
            self.data[name] = deepcopy(rows)
            if call_number in self.fail_calls.get(name, set()):
                raise OSError(f"controlled {name} save failure #{call_number}")

        return save

    def kwargs(self, transaction_factory=None):
        return {
            "submission_id": "s1",
            "qingtian_model_version": None,
            "default_model_version": "default-model",
            "qt_total_score": 88.5,
            "qt_dim_scores": {"01": 8.5},
            "qt_reasons": [{"kind": "missing", "text": "缺少节点"}],
            "raw_payload": {"raw": True},
            "atomic_json_transaction": transaction_factory or _direct_transaction,
            "load_submissions": self.loader("submissions"),
            "find_submission": _find_submission,
            "load_projects": self.loader("projects"),
            "save_projects": self.saver("projects"),
            "find_project": _find_project,
            "load_qingtian_results": self.loader("qingtian_results"),
            "save_qingtian_results": self.saver("qingtian_results"),
            "record_id_factory": lambda: "result-1",
            "now_iso": lambda: "2026-08-29T00:00:00+00:00",
        }


def _direct_transaction(*_store_names):
    def decorate(func):
        return func

    return decorate


def _find_submission(submission_id, submissions):
    result = next((row for row in submissions if row.get("id") == submission_id), None)
    if result is None:
        raise LookupError("submission missing")
    return result


def _find_project(project_id, projects):
    result = next((row for row in projects if row.get("id") == project_id), None)
    if result is None:
        raise LookupError("project missing")
    return result


def test_commit_qingtian_result_appends_result_and_advances_project_status():
    stores = StoreHarness()

    record = qingtian_result_service.commit_qingtian_result(**stores.kwargs())

    assert record == {
        "id": "result-1",
        "submission_id": "s1",
        "qingtian_model_version": "project-model",
        "qt_total_score": 88.5,
        "qt_dim_scores": {"01": 8.5},
        "qt_reasons": [{"kind": "missing", "text": "缺少节点"}],
        "raw_payload": {"raw": True},
        "created_at": "2026-08-29T00:00:00+00:00",
    }
    assert [row["id"] for row in stores.data["qingtian_results"]] == [
        "existing",
        "result-1",
    ]
    assert stores.data["projects"][0]["status"] == "submitted_to_qingtian"
    assert stores.writes == ["qingtian_results", "projects"]


def test_commit_qingtian_result_does_not_rewrite_project_when_status_is_already_advanced():
    stores = StoreHarness(status="active")

    qingtian_result_service.commit_qingtian_result(**stores.kwargs())

    assert stores.writes == ["qingtian_results"]
    assert stores.data["projects"] == stores.original["projects"]


def test_commit_qingtian_result_locks_read_and_write_stores_together():
    stores = StoreHarness()
    captured = []

    def capture_transaction(*store_names):
        captured.append(store_names)
        return _direct_transaction(*store_names)

    qingtian_result_service.commit_qingtian_result(
        **stores.kwargs(transaction_factory=capture_transaction)
    )

    assert captured == [("projects", "qingtian_results", "submissions")]


def test_commit_qingtian_result_missing_submission_has_zero_writes():
    stores = StoreHarness()
    stores.data["submissions"] = []

    with pytest.raises(LookupError, match="submission missing"):
        qingtian_result_service.commit_qingtian_result(**stores.kwargs())

    assert stores.writes == []
    assert stores.data == {**stores.original, "submissions": []}


def test_commit_qingtian_result_missing_project_has_zero_writes():
    stores = StoreHarness()
    stores.data["projects"] = []

    with pytest.raises(LookupError, match="project missing"):
        qingtian_result_service.commit_qingtian_result(**stores.kwargs())

    assert stores.writes == []
    assert stores.data == {**stores.original, "projects": []}


@pytest.mark.parametrize("failing_store", ["qingtian_results", "projects"])
def test_commit_qingtian_result_restores_both_stores_after_ambiguous_failure(
    failing_store,
):
    stores = StoreHarness(fail_calls={failing_store: {1}})

    with pytest.raises(OSError, match=f"controlled {failing_store} save failure #1"):
        qingtian_result_service.commit_qingtian_result(**stores.kwargs())

    assert stores.data == stores.original


def test_commit_qingtian_result_preserves_primary_error_and_adds_rollback_note():
    stores = StoreHarness(
        fail_calls={"projects": {1}, "qingtian_results": {2}},
    )

    with pytest.raises(
        OSError,
        match="controlled projects save failure #1",
    ) as exc_info:
        qingtian_result_service.commit_qingtian_result(**stores.kwargs())

    assert any(
        "controlled qingtian_results save failure #2" in note
        for note in getattr(exc_info.value, "__notes__", [])
    )
