from __future__ import annotations

from copy import deepcopy

import pytest

from app import data_hygiene_service


def _direct_transaction(*_store_names):
    return lambda func: func


def _store_data():
    def project_rows():
        return [
            {"id": "kept", "project_id": "p1"},
            {"id": "orphan", "project_id": "p2"},
        ]

    return {
        "projects": [{"id": "p1"}],
        "submissions": project_rows(),
        "materials": project_rows(),
        "learning_profiles": project_rows(),
        "score_history": project_rows(),
        "ground_truth": project_rows(),
        "project_anchors": project_rows(),
        "project_requirements": project_rows(),
        "delta_cases": project_rows(),
        "calibration_samples": project_rows(),
        "calibration_models": [
            {"id": "kept", "train_filter": {"project_id": "p1"}},
            {"id": "orphan", "train_filter": {"project_id": "p2"}},
        ],
        "patch_packages": project_rows(),
        "patch_deployments": [
            {"id": "kept", "project_id": "p1", "patch_id": "kept"},
            {"id": "orphan", "project_id": "p2", "patch_id": "orphan"},
        ],
        "score_reports": [
            {"id": "kept", "project_id": "p1", "submission_id": "kept"},
            {"id": "orphan", "project_id": "p2", "submission_id": "orphan"},
        ],
        "evidence_units": [
            {"id": "kept", "project_id": "p1", "submission_id": "kept"},
            {"id": "orphan", "project_id": "p2", "submission_id": "orphan"},
        ],
        "qingtian_results": [
            {"id": "kept", "project_id": "p1", "submission_id": "kept"},
            {"id": "orphan", "project_id": "p2", "submission_id": "orphan"},
        ],
        "project_context": {"p1": {"ok": True}, "p2": {"ok": False}},
        "evolution_reports": {"p1": {"ok": True}, "p2": {"ok": False}},
    }


class StoreHarness:
    def __init__(self, *, fail_calls=None):
        self.data = deepcopy(_store_data())
        self.original = deepcopy(self.data)
        self.fail_calls = fail_calls or {}
        self.write_counts = {name: 0 for name in self.data}
        self.writes = []

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

    def invoke(self, *, apply, transaction_factory=_direct_transaction):
        savable = tuple(name for name in self.data if name != "projects")
        return data_hygiene_service.build_data_hygiene_report(
            apply=apply,
            atomic_json_transaction=transaction_factory,
            loaders={name: self.loader(name) for name in self.data},
            savers={name: self.saver(name) for name in savable},
            now_iso=lambda: "2026-08-29T00:00:00+00:00",
        )


def test_audit_is_read_only_and_reports_calibration_model_scope():
    stores = StoreHarness()

    report = stores.invoke(apply=False)

    assert stores.writes == []
    assert stores.data == stores.original
    model_dataset = next(
        item for item in report["datasets"] if item["name"] == "calibration_models"
    )
    assert model_dataset == {
        "name": "calibration_models",
        "total": 2,
        "orphan_count": 1,
        "cleaned_count": 0,
        "mode": "train_filter.project_id",
    }


def test_repair_locks_complete_store_set_and_removes_every_orphan():
    stores = StoreHarness()
    captured = []

    def transaction_factory(*names):
        captured.append(names)
        return lambda func: func

    report = stores.invoke(apply=True, transaction_factory=transaction_factory)

    assert captured == [data_hygiene_service.DATA_HYGIENE_STORES]
    assert report["orphan_records_total"] == 17
    assert report["cleaned_records_total"] == 17
    for name, value in stores.data.items():
        if name == "projects":
            continue
        if isinstance(value, dict):
            assert list(value) == ["p1"]
        else:
            assert len(value) == 1
            if name == "calibration_models":
                assert value[0]["train_filter"]["project_id"] == "p1"
            else:
                assert value[0].get("project_id", "p1") == "p1"


@pytest.mark.parametrize(
    "failing_store",
    [name for name in _store_data() if name != "projects"],
)
def test_repair_restores_all_stores_after_ambiguous_write_failure(failing_store):
    stores = StoreHarness(fail_calls={failing_store: {1}})

    with pytest.raises(OSError, match=f"controlled {failing_store} save failure #1"):
        stores.invoke(apply=True)

    assert stores.data == stores.original


def test_repair_preserves_primary_error_and_adds_rollback_note():
    stores = StoreHarness(fail_calls={"submissions": {1, 2}})

    with pytest.raises(
        OSError,
        match="controlled submissions save failure #1",
    ) as exc_info:
        stores.invoke(apply=True)

    assert any(
        "controlled submissions save failure #2" in note
        for note in getattr(exc_info.value, "__notes__", [])
    )
