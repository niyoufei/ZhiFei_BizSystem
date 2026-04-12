from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from app.infrastructure.storage.sqlite_event_store import SQLiteEventStore
from app.ports.event_store import EventEnvelope


def _patch_storage_root(monkeypatch, tmp_path: Path):
    from app import storage

    monkeypatch.setattr(storage, "DATA_DIR", tmp_path)
    monkeypatch.setattr(storage, "MATERIALS_DIR", tmp_path / "materials")
    monkeypatch.setattr(storage, "VERSIONED_JSON_DIR", tmp_path / "versions")
    return storage


def test_save_projects_dual_writes_sqlite_when_mirror_enabled(monkeypatch, tmp_path: Path):
    storage = _patch_storage_root(monkeypatch, tmp_path)
    monkeypatch.setenv("ZHIFEI_STORAGE_ENABLE_SQLITE_MIRROR", "true")
    monkeypatch.setattr(storage, "PROJECTS_PATH", tmp_path / "projects.json")

    rows = [{"id": "p1", "name": "项目一"}]
    storage.save_projects(rows)

    assert json.loads((tmp_path / "projects.json").read_text(encoding="utf-8")) == rows
    summary = storage.validate_storage_sync(collections=["projects"])
    assert summary["matched_collections"] == 1
    assert summary["rows"][0]["matched"] is True


def test_restore_json_version_emits_rollback_event(monkeypatch, tmp_path: Path):
    storage = _patch_storage_root(monkeypatch, tmp_path)
    monkeypatch.setenv("ZHIFEI_STORAGE_ENABLE_EVENT_LOG", "true")
    monkeypatch.setattr(storage, "HIGH_SCORE_FEATURES_PATH", tmp_path / "high_score_features.json")

    storage.save_high_score_features([{"id": "f1", "name": "首版"}])
    storage.save_high_score_features([{"id": "f2", "name": "次版"}])
    versions = storage.list_json_versions(storage.HIGH_SCORE_FEATURES_PATH)

    storage.restore_json_version(storage.HIGH_SCORE_FEATURES_PATH, versions[-1]["version_id"])

    events = storage.list_domain_events(event_types=["RollbackApplied"])
    assert len(events) == 1
    assert events[0]["payload"]["collection"] == "high_score_features"


def test_sqlite_event_store_enforces_idempotency(tmp_path: Path):
    store = SQLiteEventStore(tmp_path / "events.sqlite3")
    event = EventEnvelope(
        event_id="evt-1",
        aggregate_type="project",
        aggregate_id="p1",
        event_type="ProjectCreated",
        event_version=1,
        payload={"project_id": "p1", "name": "项目一"},
        occurred_at="2026-04-12T00:00:00+00:00",
        idempotency_key="project-created:p1",
    )

    first = store.append(event)
    second = store.append(event)

    assert first.inserted is True
    assert second.inserted is False
    assert len(store.list_events()) == 1


def test_project_activity_projection_replays_events(monkeypatch, tmp_path: Path):
    storage = _patch_storage_root(monkeypatch, tmp_path)
    monkeypatch.setenv("ZHIFEI_STORAGE_ENABLE_EVENT_LOG", "true")

    storage.append_domain_event(
        event_type="ProjectCreated",
        aggregate_type="project",
        aggregate_id="p1",
        payload={"project_id": "p1", "name": "项目一"},
        idempotency_key="project-created:p1",
    )
    storage.append_domain_event(
        event_type="ArtifactUploaded",
        aggregate_type="project",
        aggregate_id="p1",
        payload={"project_id": "p1", "artifact_id": "m1", "artifact_type": "drawing"},
        idempotency_key="artifact-uploaded:m1",
    )
    storage.append_domain_event(
        event_type="ScoreComputed",
        aggregate_type="project",
        aggregate_id="p1",
        payload={"project_id": "p1", "submission_id": "s1", "total_score": 91.5},
        idempotency_key="score-computed:s1",
    )

    projection = storage.replay_project_activity_projection(persist=True)

    assert projection["project_count"] == 1
    assert projection["projects"][0]["artifact_upload_count"] == 1
    assert projection["projects"][0]["score_count"] == 1
    assert projection["projects"][0]["project_name"] == "项目一"


def test_projection_consistency_probe_matches_snapshot(monkeypatch, tmp_path: Path):
    storage = _patch_storage_root(monkeypatch, tmp_path)
    monkeypatch.setenv("ZHIFEI_STORAGE_ENABLE_EVENT_LOG", "true")

    storage.append_domain_event(
        event_type="ProjectCreated",
        aggregate_type="project",
        aggregate_id="p1",
        payload={"project_id": "p1", "name": "项目一"},
        idempotency_key="project-created:p1",
    )
    storage.replay_project_activity_projection(persist=True)

    probe = storage.probe_projection_consistency()

    assert probe["ok"] is True
    assert probe["projection_name"] == "project_activity"


def test_learning_artifact_versions_probe_reports_versioned_artifacts(monkeypatch, tmp_path: Path):
    storage = _patch_storage_root(monkeypatch, tmp_path)
    monkeypatch.setenv("ZHIFEI_STORAGE_ENABLE_EVENT_LOG", "true")
    monkeypatch.setattr(storage, "CALIBRATION_MODELS_PATH", tmp_path / "calibration_models.json")
    monkeypatch.setattr(storage, "HIGH_SCORE_FEATURES_PATH", tmp_path / "high_score_features.json")
    monkeypatch.setattr(storage, "EVOLUTION_REPORTS_PATH", tmp_path / "evolution_reports.json")

    storage.save_calibration_models([{"id": "c1", "version": "c1", "project_id": "p1"}])
    storage.save_high_score_features([{"id": "f1", "project_id": "p1", "name": "高分特征"}])
    storage.save_evolution_reports(
        {"p1": {"project_id": "p1", "updated_at": "2026-04-12T00:00:00+00:00"}}
    )

    probe = storage.probe_learning_artifact_versions()

    assert probe["ok"] is True
    by_name = {row["name"]: row for row in probe["rows"]}
    assert by_name["calibration_models"]["version_count"] >= 1
    assert by_name["high_score_features"]["version_count"] >= 1
    assert by_name["evolution_reports"]["version_count"] >= 1


def test_save_json_is_atomic_under_lock_contention(tmp_path: Path):
    from app.storage import load_json, save_json

    target = tmp_path / "contended.json"
    payloads = [{"version": index} for index in range(12)]

    with ThreadPoolExecutor(max_workers=6) as executor:
        list(executor.map(lambda row: save_json(target, row), payloads))

    data = load_json(target, {})
    assert data in payloads
    assert json.loads(target.read_text(encoding="utf-8")) == data


def test_artifact_store_copies_file_under_project_type(monkeypatch, tmp_path: Path):
    storage = _patch_storage_root(monkeypatch, tmp_path)
    source = tmp_path / "source.txt"
    source.write_text("artifact", encoding="utf-8")

    stored = storage.store_artifact_copy(
        project_id="p1",
        artifact_type="drawing",
        source_path=source,
        filename="drawing.txt",
    )

    assert stored.path == tmp_path / "materials" / "p1" / "drawing" / "drawing.txt"
    assert stored.path.read_text(encoding="utf-8") == "artifact"
    assert len(stored.content_hash) == 64
