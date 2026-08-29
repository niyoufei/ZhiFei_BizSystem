from __future__ import annotations

from copy import deepcopy

import pytest

from app import calibration_model_service

CASES = {
    "train": {
        "private": "_train_calibration_model",
        "stores": ("calibration_samples", "projects", "calibration_models"),
        "locks": (
            "calibration_models",
            "calibration_samples",
            "projects",
            "qingtian_results",
            "score_reports",
            "submissions",
        ),
    },
    "deploy": {
        "private": "_deploy_calibration_model",
        "stores": ("calibration_models", "projects"),
        "locks": ("calibration_models", "projects"),
    },
    "predict": {
        "private": "_apply_calibration_prediction",
        "stores": ("score_reports", "submissions"),
        "locks": ("calibration_models", "projects", "score_reports", "submissions"),
    },
    "auto": {
        "private": "_run_auto_calibration_lifecycle",
        "stores": ("projects", "calibration_models", "score_reports", "submissions"),
        "locks": ("calibration_models", "projects", "score_reports", "submissions"),
    },
}


class StoreHarness:
    def __init__(self, *, fail_calls=None):
        self.data = {
            "calibration_models": [{"id": "model-original"}],
            "calibration_samples": [{"id": "sample-original"}],
            "projects": [{"id": "p1"}],
            "score_reports": [{"id": "report-original"}],
            "submissions": [{"id": "submission-original"}],
        }
        self.original = deepcopy(self.data)
        self.fail_calls = fail_calls or {}
        self.write_counts = {name: 0 for name in self.data}
        self.writes = []

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


def _find_project(project_id, projects):
    result = next((row for row in projects if row.get("id") == project_id), None)
    if result is None:
        raise LookupError("project missing")
    return result


def _operation_for(stores, harness):
    def operation(**kwargs):
        for name in stores:
            rows = harness.loader(name)()
            rows.append({"id": f"partial-{name}"})
            kwargs[f"save_{name}"](rows)
        return {"ok": True}

    return operation


def _invoke(case, harness, transaction_factory):
    loaders_and_savers = {
        f"load_{name}": harness.loader(name)
        for name in (
            "calibration_models",
            "calibration_samples",
            "projects",
            "score_reports",
            "submissions",
        )
    }
    loaders_and_savers.update(
        {
            f"save_{name}": harness.saver(name)
            for name in (
                "calibration_models",
                "calibration_samples",
                "projects",
                "score_reports",
                "submissions",
            )
        }
    )
    if case == "train":
        return calibration_model_service.train_calibration_model(
            atomic_json_transaction=transaction_factory,
            load_calibration_models=loaders_and_savers["load_calibration_models"],
            save_calibration_models=loaders_and_savers["save_calibration_models"],
            load_calibration_samples=loaders_and_savers["load_calibration_samples"],
            save_calibration_samples=loaders_and_savers["save_calibration_samples"],
            load_projects=loaders_and_savers["load_projects"],
            save_projects=loaders_and_savers["save_projects"],
        )
    if case == "deploy":
        return calibration_model_service.deploy_calibration_model(
            atomic_json_transaction=transaction_factory,
            load_calibration_models=loaders_and_savers["load_calibration_models"],
            save_calibration_models=loaders_and_savers["save_calibration_models"],
            load_projects=loaders_and_savers["load_projects"],
            save_projects=loaders_and_savers["save_projects"],
        )
    if case == "predict":
        return calibration_model_service.apply_calibration_prediction(
            project_id="p1",
            atomic_json_transaction=transaction_factory,
            load_projects=loaders_and_savers["load_projects"],
            find_project=_find_project,
            load_score_reports=loaders_and_savers["load_score_reports"],
            save_score_reports=loaders_and_savers["save_score_reports"],
            load_submissions=loaders_and_savers["load_submissions"],
            save_submissions=loaders_and_savers["save_submissions"],
        )
    return calibration_model_service.run_auto_calibration_lifecycle(
        project_id="p1",
        atomic_json_transaction=transaction_factory,
        load_projects=loaders_and_savers["load_projects"],
        find_project=_find_project,
        load_calibration_models=loaders_and_savers["load_calibration_models"],
        save_calibration_models=loaders_and_savers["save_calibration_models"],
        save_projects=loaders_and_savers["save_projects"],
        load_score_reports=loaders_and_savers["load_score_reports"],
        save_score_reports=loaders_and_savers["save_score_reports"],
        load_submissions=loaders_and_savers["load_submissions"],
        save_submissions=loaders_and_savers["save_submissions"],
    )


@pytest.mark.parametrize("case", CASES)
def test_lifecycle_transactions_lock_their_complete_store_sets(monkeypatch, case):
    stores = StoreHarness()
    captured = []

    def transaction_factory(*store_names):
        captured.append(store_names)

        def decorate(func):
            return func

        return decorate

    config = CASES[case]
    monkeypatch.setattr(
        calibration_model_service,
        config["private"],
        _operation_for(config["stores"], stores),
    )

    _invoke(case, stores, transaction_factory)

    assert captured == [config["locks"]]
    assert stores.writes == list(config["stores"])


@pytest.mark.parametrize(
    ("case", "failing_store"),
    [(case, failing_store) for case, config in CASES.items() for failing_store in config["stores"]],
)
def test_lifecycle_transactions_restore_all_attempted_stores_after_failure(
    monkeypatch,
    case,
    failing_store,
):
    stores = StoreHarness(fail_calls={failing_store: {1}})
    config = CASES[case]
    monkeypatch.setattr(
        calibration_model_service,
        config["private"],
        _operation_for(config["stores"], stores),
    )

    with pytest.raises(OSError, match=f"controlled {failing_store} save failure #1"):
        _invoke(case, stores, lambda *_names: lambda func: func)

    assert stores.data == stores.original


def test_lifecycle_transaction_preserves_primary_error_and_adds_rollback_note(monkeypatch):
    stores = StoreHarness(
        fail_calls={"calibration_models": {1}, "calibration_samples": {2}},
    )
    config = CASES["train"]
    monkeypatch.setattr(
        calibration_model_service,
        config["private"],
        _operation_for(config["stores"], stores),
    )

    with pytest.raises(
        OSError,
        match="controlled calibration_models save failure #1",
    ) as exc_info:
        _invoke("train", stores, lambda *_names: lambda func: func)

    assert any(
        "controlled calibration_samples save failure #2" in note
        for note in getattr(exc_info.value, "__notes__", [])
    )
