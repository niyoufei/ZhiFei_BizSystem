from __future__ import annotations

from copy import deepcopy

import pytest

from app import ground_truth_sync_service, ground_truth_write_service


class StoreHarness:
    def __init__(self, data, *, fail_calls=None):
        self.data = deepcopy(data)
        self.original = deepcopy(data)
        self.fail_calls = fail_calls or {}
        self.reads = []
        self.writes = []
        self.write_counts = {name: 0 for name in data}

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

    def service_kwargs(self, refresh):
        return {
            "load_ground_truth": self.loader("ground_truth"),
            "save_ground_truth": self.saver("ground_truth"),
            "load_qingtian_results": self.loader("qingtian_results"),
            "save_qingtian_results": self.saver("qingtian_results"),
            "load_submissions": self.loader("submissions"),
            "save_submissions": self.saver("submissions"),
            "load_score_reports": self.loader("score_reports"),
            "save_score_reports": self.saver("score_reports"),
            "load_evidence_units": self.loader("evidence_units"),
            "save_evidence_units": self.saver("evidence_units"),
            "load_score_history": self.loader("score_history"),
            "save_score_history": self.saver("score_history"),
            "load_calibration_samples": self.loader("calibration_samples"),
            "save_calibration_samples": self.saver("calibration_samples"),
            "load_delta_cases": self.loader("delta_cases"),
            "save_delta_cases": self.saver("delta_cases"),
            "refresh_project_reflection_objects": refresh,
        }

    def addition_kwargs(self, sync, *, transaction_factory=None):
        if transaction_factory is None:
            transaction_factory = _direct_transaction
        return {
            "atomic_json_transaction": transaction_factory,
            "load_projects": self.loader("projects"),
            "save_projects": self.saver("projects"),
            "load_ground_truth": self.loader("ground_truth"),
            "save_ground_truth": self.saver("ground_truth"),
            "load_submissions": self.loader("submissions"),
            "save_submissions": self.saver("submissions"),
            "load_score_reports": self.loader("score_reports"),
            "save_score_reports": self.saver("score_reports"),
            "load_evidence_units": self.loader("evidence_units"),
            "save_evidence_units": self.saver("evidence_units"),
            "load_score_history": self.loader("score_history"),
            "save_score_history": self.saver("score_history"),
            "load_qingtian_results": self.loader("qingtian_results"),
            "save_qingtian_results": self.saver("qingtian_results"),
            "load_high_score_features": self.loader("high_score_features"),
            "save_high_score_features": self.saver("high_score_features"),
            "load_calibration_samples": self.loader("calibration_samples"),
            "save_calibration_samples": self.saver("calibration_samples"),
            "load_delta_cases": self.loader("delta_cases"),
            "save_delta_cases": self.saver("delta_cases"),
            "sync_ground_truth_record": sync,
            "project_not_found_error": lambda: LookupError("project missing"),
            "source_submission_not_found_error": lambda: LookupError("source submission missing"),
        }


def _direct_transaction(*_store_names):
    def decorate(func):
        return func

    return decorate


def _addition_data():
    return {
        "projects": [{"id": "p1", "status": "scoring_preparation"}],
        "ground_truth": [{"id": "gt-existing", "project_id": "p1"}],
        "submissions": [{"id": "source-1", "project_id": "p1"}],
        "score_reports": [{"id": "report-existing"}],
        "evidence_units": [{"id": "evidence-existing"}],
        "score_history": [{"id": "history-existing"}],
        "qingtian_results": [{"id": "qingtian-existing"}],
        "high_score_features": [{"feature_id": "feature-existing"}],
        "calibration_samples": [{"id": "sample-existing"}],
        "delta_cases": [{"id": "delta-existing"}],
    }


def test_additions_commit_all_records_and_sync_each_once():
    stores = StoreHarness(_addition_data())
    synced = []

    def sync(project_id, record):
        synced.append((project_id, record["id"]))
        submissions = stores.loader("submissions")()
        submissions.append({"id": f"synthetic-{record['id']}", "project_id": project_id})
        stores.saver("submissions")(submissions)

    records = [
        {"id": "gt-1", "project_id": "p1"},
        {"id": "gt-2", "project_id": "p1"},
    ]
    ground_truth_write_service.commit_ground_truth_additions(
        "p1",
        records,
        **stores.addition_kwargs(sync),
    )

    assert [row["id"] for row in stores.data["ground_truth"]] == [
        "gt-existing",
        "gt-1",
        "gt-2",
    ]
    assert synced == [("p1", "gt-1"), ("p1", "gt-2")]


def test_additions_empty_batch_is_a_zero_write_noop():
    stores = StoreHarness(_addition_data())

    ground_truth_write_service.commit_ground_truth_additions(
        "p1",
        [],
        **stores.addition_kwargs(lambda *_args: None),
    )

    assert stores.reads == []
    assert stores.writes == []
    assert stores.data == stores.original


@pytest.mark.parametrize(
    "failing_store",
    [
        "projects",
        "submissions",
        "score_reports",
        "evidence_units",
        "score_history",
        "qingtian_results",
        "high_score_features",
        "calibration_samples",
        "delta_cases",
    ],
)
def test_additions_restore_every_store_after_sync_write_failure(failing_store):
    stores = StoreHarness(_addition_data(), fail_calls={failing_store: {1}})

    def failing_sync(_project_id, _record):
        updated = stores.loader(failing_store)()
        updated.append({"id": "partial-sync"})
        stores.saver(failing_store)(updated)

    with pytest.raises(OSError, match=f"controlled {failing_store} save failure #1"):
        ground_truth_write_service.commit_ground_truth_additions(
            "p1",
            [{"id": "gt-1", "project_id": "p1"}],
            **stores.addition_kwargs(failing_sync),
        )

    assert stores.data == stores.original


def test_additions_restore_ground_truth_after_ambiguous_initial_save_failure():
    stores = StoreHarness(_addition_data(), fail_calls={"ground_truth": {1}})

    with pytest.raises(OSError, match="controlled ground_truth save failure #1"):
        ground_truth_write_service.commit_ground_truth_additions(
            "p1",
            [{"id": "gt-1", "project_id": "p1"}],
            **stores.addition_kwargs(lambda *_args: None),
        )

    assert stores.data == stores.original


def test_additions_project_disappears_before_commit_without_writes():
    data = _addition_data()
    data["projects"] = []
    stores = StoreHarness(data)

    with pytest.raises(LookupError, match="project missing"):
        ground_truth_write_service.commit_ground_truth_additions(
            "p1",
            [{"id": "gt-1", "project_id": "p1"}],
            **stores.addition_kwargs(lambda *_args: None),
        )

    assert stores.writes == []
    assert stores.data == stores.original


def test_additions_source_submission_disappears_before_commit_without_writes():
    stores = StoreHarness(_addition_data())

    with pytest.raises(LookupError, match="source submission missing"):
        ground_truth_write_service.commit_ground_truth_additions(
            "p1",
            [
                {
                    "id": "gt-1",
                    "project_id": "p1",
                    "source_submission_id": "missing",
                }
            ],
            **stores.addition_kwargs(lambda *_args: None),
        )

    assert stores.writes == []
    assert stores.data == stores.original


def test_additions_preserve_primary_error_when_rollback_also_fails():
    stores = StoreHarness(
        _addition_data(),
        fail_calls={"submissions": {1}, "ground_truth": {2}},
    )

    def failing_sync(_project_id, _record):
        stores.saver("submissions")([{"id": "partial-sync"}])

    with pytest.raises(
        OSError,
        match="controlled submissions save failure #1",
    ) as exc_info:
        ground_truth_write_service.commit_ground_truth_additions(
            "p1",
            [{"id": "gt-1", "project_id": "p1"}],
            **stores.addition_kwargs(failing_sync),
        )

    assert any(
        "controlled ground_truth save failure #2" in note
        for note in getattr(exc_info.value, "__notes__", [])
    )


def test_additions_nested_history_write_respects_canonical_lock_order(monkeypatch, tmp_path):
    from app import storage

    store_names = (
        "calibration_samples",
        "delta_cases",
        "evidence_units",
        "ground_truth",
        "high_score_features",
        "project_anchors",
        "project_requirements",
        "projects",
        "qingtian_results",
        "score_history",
        "score_reports",
        "submissions",
    )
    for store_name in store_names:
        path_attribute = storage._STORE_PATH_ATTRIBUTES[store_name]
        monkeypatch.setattr(storage, path_attribute, tmp_path / f"{store_name}.json")

    storage.save_projects([{"id": "p1"}])

    @storage.atomic_json_transaction("score_history")
    def sync_with_history(_project_id, record):
        history = storage.load_score_history()
        history.append({"id": f"history-{record['id']}"})
        storage.save_score_history(history)

    ground_truth_write_service.commit_ground_truth_additions(
        "p1",
        [{"id": "gt-1", "project_id": "p1"}],
        atomic_json_transaction=storage.atomic_json_transaction,
        load_projects=storage.load_projects,
        save_projects=storage.save_projects,
        load_ground_truth=storage.load_ground_truth,
        save_ground_truth=storage.save_ground_truth,
        load_submissions=storage.load_submissions,
        save_submissions=storage.save_submissions,
        load_score_reports=storage.load_score_reports,
        save_score_reports=storage.save_score_reports,
        load_evidence_units=storage.load_evidence_units,
        save_evidence_units=storage.save_evidence_units,
        load_score_history=storage.load_score_history,
        save_score_history=storage.save_score_history,
        load_qingtian_results=storage.load_qingtian_results,
        save_qingtian_results=storage.save_qingtian_results,
        load_high_score_features=storage.load_high_score_features,
        save_high_score_features=storage.save_high_score_features,
        load_calibration_samples=storage.load_calibration_samples,
        save_calibration_samples=storage.save_calibration_samples,
        load_delta_cases=storage.load_delta_cases,
        save_delta_cases=storage.save_delta_cases,
        sync_ground_truth_record=sync_with_history,
        project_not_found_error=lambda: LookupError("project missing"),
        source_submission_not_found_error=lambda: LookupError("source submission missing"),
    )

    assert storage.load_score_history() == [{"id": "history-gt-1"}]
    assert [row["id"] for row in storage.load_ground_truth()] == ["gt-1"]


def _base_data():
    ground_truth_id = "12345678-target"
    return ground_truth_id, {
        "ground_truth": [
            {"id": ground_truth_id, "project_id": "p1"},
            {"id": "gt-other", "project_id": "p1"},
        ],
        "qingtian_results": [
            {
                "id": "qt-target",
                "submission_id": "user-linked",
                "raw_payload": {"ground_truth_record_id": ground_truth_id},
            },
            {
                "id": "qt-other-project",
                "submission_id": "other-project-synthetic",
                "raw_payload": {"ground_truth_record_id": ground_truth_id},
            },
            {"id": "qt-other", "submission_id": "other", "raw_payload": {}},
        ],
        "submissions": [
            {
                "id": "synthetic-explicit",
                "project_id": "p1",
                "source_ground_truth_id": ground_truth_id,
                "ground_truth_generated": True,
                "filename": "generated.txt",
            },
            {
                "id": "synthetic-legacy",
                "project_id": "p1",
                "source_ground_truth_id": ground_truth_id,
                "filename": "ground_truth_12345678.txt",
                "bidder_name": "GT_12345678",
            },
            {
                "id": "user-linked",
                "project_id": "p1",
                "source_ground_truth_id": ground_truth_id,
                "filename": "user-upload.docx",
                "bidder_name": "User Bidder",
            },
            {
                "id": "other-project-synthetic",
                "project_id": "p2",
                "source_ground_truth_id": ground_truth_id,
                "ground_truth_generated": True,
                "filename": "generated-p2.txt",
            },
            {"id": "other", "project_id": "p1", "filename": "other.docx"},
        ],
        "score_reports": [
            {"id": "r1", "submission_id": "synthetic-explicit"},
            {"id": "r2", "submission_id": "synthetic-legacy"},
            {"id": "r3", "submission_id": "user-linked"},
            {"id": "r4", "submission_id": "other"},
        ],
        "evidence_units": [
            {"id": "e1", "submission_id": "synthetic-explicit"},
            {"id": "e2", "submission_id": "synthetic-legacy"},
            {"id": "e3", "submission_id": "user-linked"},
            {"id": "e4", "submission_id": "other"},
        ],
        "score_history": [
            {"id": "h1", "submission_id": "synthetic-explicit"},
            {"id": "h2", "submission_id": "synthetic-legacy"},
            {"id": "h3", "submission_id": "user-linked"},
            {"id": "h4", "submission_id": "other"},
        ],
        "calibration_samples": [{"id": "c1", "project_id": "p1"}],
        "delta_cases": [{"id": "d1", "project_id": "p1"}],
    }


def test_delete_cascade_removes_only_synthetic_chain_and_preserves_user_submission():
    ground_truth_id, data = _base_data()
    stores = StoreHarness(data)
    refresh_calls = []

    deleted = ground_truth_write_service.delete_ground_truth_cascade(
        "p1",
        ground_truth_id,
        **stores.service_kwargs(lambda project_id: refresh_calls.append(project_id)),
    )

    assert deleted is True
    assert stores.data["ground_truth"] == [{"id": "gt-other", "project_id": "p1"}]
    assert stores.data["qingtian_results"] == [
        {
            "id": "qt-other-project",
            "submission_id": "other-project-synthetic",
            "raw_payload": {"ground_truth_record_id": ground_truth_id},
        },
        {"id": "qt-other", "submission_id": "other", "raw_payload": {}},
    ]
    assert stores.data["submissions"] == [
        {
            "id": "user-linked",
            "project_id": "p1",
            "filename": "user-upload.docx",
            "bidder_name": "User Bidder",
        },
        {
            "id": "other-project-synthetic",
            "project_id": "p2",
            "source_ground_truth_id": ground_truth_id,
            "ground_truth_generated": True,
            "filename": "generated-p2.txt",
        },
        {"id": "other", "project_id": "p1", "filename": "other.docx"},
    ]
    for store_name in ("score_reports", "evidence_units", "score_history"):
        assert [row["submission_id"] for row in stores.data[store_name]] == [
            "user-linked",
            "other",
        ]
    assert refresh_calls == ["p1"]


def test_delete_cascade_not_found_does_not_load_or_write_related_stores():
    _ground_truth_id, data = _base_data()
    stores = StoreHarness(data)
    refresh_calls = []

    deleted = ground_truth_write_service.delete_ground_truth_cascade(
        "p1",
        "missing",
        **stores.service_kwargs(lambda project_id: refresh_calls.append(project_id)),
    )

    assert deleted is False
    assert stores.reads == ["ground_truth"]
    assert stores.writes == []
    assert refresh_calls == []
    assert stores.data == stores.original


@pytest.mark.parametrize(
    "failing_store",
    [
        "qingtian_results",
        "submissions",
        "score_reports",
        "evidence_units",
        "score_history",
        "ground_truth",
    ],
)
def test_delete_cascade_restores_all_attempted_stores_after_ambiguous_save_failure(
    failing_store,
):
    ground_truth_id, data = _base_data()
    stores = StoreHarness(data, fail_calls={failing_store: {1}})
    refresh_calls = []

    with pytest.raises(OSError, match=f"controlled {failing_store} save failure #1"):
        ground_truth_write_service.delete_ground_truth_cascade(
            "p1",
            ground_truth_id,
            **stores.service_kwargs(lambda project_id: refresh_calls.append(project_id)),
        )

    assert stores.data == stores.original
    assert refresh_calls == []


def test_delete_cascade_restores_reflection_stores_after_refresh_failure():
    ground_truth_id, data = _base_data()
    stores = StoreHarness(data)

    def failing_refresh(_project_id):
        stores.data["qingtian_results"].append({"id": "partial-refresh"})
        stores.data["calibration_samples"].append({"id": "partial-refresh"})
        stores.data["delta_cases"].append({"id": "partial-refresh"})
        raise RuntimeError("controlled reflection refresh failure")

    with pytest.raises(RuntimeError, match="controlled reflection refresh failure"):
        ground_truth_write_service.delete_ground_truth_cascade(
            "p1",
            ground_truth_id,
            **stores.service_kwargs(failing_refresh),
        )

    assert stores.data == stores.original


def test_delete_cascade_preserves_primary_error_when_rollback_also_fails():
    ground_truth_id, data = _base_data()
    stores = StoreHarness(
        data,
        fail_calls={"ground_truth": {1}, "submissions": {2}},
    )

    with pytest.raises(
        OSError,
        match="controlled ground_truth save failure #1",
    ) as exc_info:
        ground_truth_write_service.delete_ground_truth_cascade(
            "p1",
            ground_truth_id,
            **stores.service_kwargs(lambda _project_id: None),
        )

    assert any(
        "controlled submissions save failure #2" in note
        for note in getattr(exc_info.value, "__notes__", [])
    )


def test_sync_marks_new_submission_as_ground_truth_generated():
    ground_truth = {
        "id": "12345678-target",
        "project_id": "p1",
        "shigong_text": "ground truth text",
        "judge_scores": [80, 81, 82, 83, 84],
        "final_score": 82,
    }
    saved_submissions = []

    ground_truth_sync_service.sync_ground_truth_record_to_qingtian(
        "p1",
        ground_truth,
        default_qingtian_model_version="v1",
        load_projects=lambda: [{"id": "p1", "status": "active"}],
        find_project=lambda _project_id, projects: projects[0],
        load_config=lambda: object(),
        resolve_project_scoring_context=lambda _project_id: ({}, None, None),
        load_submissions=lambda: [],
        build_pending_submission_report=lambda **_kwargs: {"status": "scored"},
        now_iso=lambda: "2026-08-29T00:00:00+00:00",
        submission_is_scored=lambda _submission: True,
        score_submission_for_project=lambda **_kwargs: ({}, []),
        report_is_blocked=lambda _report: False,
        mark_report_scored=lambda *_args, **_kwargs: None,
        save_submissions=lambda rows: saved_submissions.extend(deepcopy(rows)),
        load_score_reports=lambda: [],
        build_score_report_snapshot=lambda **_kwargs: {},
        save_score_reports=lambda _rows: None,
        load_evidence_units=lambda: [],
        replace_submission_evidence_units=lambda rows, **_kwargs: rows,
        save_evidence_units=lambda _rows: None,
        record_history_score=lambda **_kwargs: None,
        load_qingtian_results=lambda: [],
        resolve_project_score_scale_max=lambda _project: 100,
        ground_truth_record_for_learning=lambda *_args, **_kwargs: {
            "final_score": 82,
            "final_score_raw": 82,
            "score_scale_max": 100,
        },
        auto_update_feature_confidence_on_ground_truth=lambda **_kwargs: {
            "updated": 0,
            "retired": 0,
        },
        load_ground_truth=lambda: [deepcopy(ground_truth)],
        save_ground_truth=lambda _rows: None,
        save_qingtian_results=lambda _rows: None,
        save_projects=lambda _rows: None,
        refresh_project_reflection_objects=lambda _project_id: None,
    )

    assert len(saved_submissions) == 1
    assert saved_submissions[0]["ground_truth_generated"] is True


def test_sync_propagates_feature_confidence_write_failure():
    ground_truth = {
        "id": "gt-1",
        "project_id": "p1",
        "shigong_text": "ground truth text",
        "judge_scores": [80, 81, 82, 83, 84],
        "final_score": 82,
    }
    submission = {
        "id": "s1",
        "project_id": "p1",
        "source_ground_truth_id": "gt-1",
        "text": "ground truth text",
        "report": {},
    }

    def fail_feature_update(**_kwargs):
        raise OSError("controlled feature confidence failure")

    with pytest.raises(OSError, match="controlled feature confidence failure"):
        ground_truth_sync_service.sync_ground_truth_record_to_qingtian(
            "p1",
            ground_truth,
            default_qingtian_model_version="v1",
            load_projects=lambda: [{"id": "p1", "status": "active"}],
            find_project=lambda _project_id, projects: projects[0],
            load_config=lambda: object(),
            resolve_project_scoring_context=lambda _project_id: ({}, None, None),
            load_submissions=lambda: [deepcopy(submission)],
            build_pending_submission_report=lambda **_kwargs: {},
            now_iso=lambda: "2026-08-29T00:00:00+00:00",
            submission_is_scored=lambda _submission: True,
            score_submission_for_project=lambda **_kwargs: ({}, []),
            report_is_blocked=lambda _report: False,
            mark_report_scored=lambda *_args, **_kwargs: None,
            save_submissions=lambda _rows: None,
            load_score_reports=lambda: [],
            build_score_report_snapshot=lambda **_kwargs: {},
            save_score_reports=lambda _rows: None,
            load_evidence_units=lambda: [],
            replace_submission_evidence_units=lambda rows, **_kwargs: rows,
            save_evidence_units=lambda _rows: None,
            record_history_score=lambda **_kwargs: None,
            load_qingtian_results=lambda: [],
            resolve_project_score_scale_max=lambda _project: 100,
            ground_truth_record_for_learning=lambda *_args, **_kwargs: {
                "final_score": 82,
                "final_score_raw": 82,
                "score_scale_max": 100,
            },
            auto_update_feature_confidence_on_ground_truth=fail_feature_update,
            load_ground_truth=lambda: [deepcopy(ground_truth)],
            save_ground_truth=lambda _rows: None,
            save_qingtian_results=lambda _rows: None,
            save_projects=lambda _rows: None,
            refresh_project_reflection_objects=lambda _project_id: None,
        )
