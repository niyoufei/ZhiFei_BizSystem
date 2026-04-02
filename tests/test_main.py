"""Tests for app/main.py FastAPI endpoints."""

from __future__ import annotations

import copy
import os
import re
import subprocess
import sys
import tempfile
from collections import Counter, deque
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import app.main as app_main
from app.main import _build_score_self_awareness, app, create_app


@pytest.fixture
def client():
    """Create a test client."""
    return TestClient(app)


class TestCreateApp:
    """Tests for create_app factory function."""

    def test_create_app_returns_fastapi(self):
        """create_app should return the FastAPI app instance."""
        result = create_app()
        assert result is app


class TestAppLifespan:
    @patch("app.main._stop_material_parse_worker")
    @patch("app.main._start_material_parse_worker")
    def test_app_lifespan_starts_and_stops_parse_worker(
        self,
        mock_start_worker,
        mock_stop_worker,
    ):
        with TestClient(app):
            pass

        mock_start_worker.assert_called_once()
        mock_stop_worker.assert_called_once()


class TestStorageErrorHandling:
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    def test_storage_data_error_returns_readable_json(
        self,
        mock_ensure,
        mock_load_projects,
        client,
    ):
        from app.storage import StorageDataError

        mock_load_projects.side_effect = StorageDataError(
            Path("/tmp/projects.json"),
            "json_parse_failed",
            "数据文件 JSON 格式损坏：projects.json（第 1 行，第 3 列），请使用历史版本回滚。",
        )

        response = client.get("/api/v1/projects")

        assert response.status_code == 500
        data = response.json()
        assert data["error_code"] == "json_parse_failed"
        assert data["file"] == "projects.json"
        assert "历史版本回滚" in data["detail"]

    @patch("app.main.load_evidence_units")
    def test_load_evidence_units_safe_falls_back_on_corrupted_storage(
        self,
        mock_load_evidence_units,
    ):
        from app.main import _load_evidence_units_safe
        from app.storage import StorageDataError

        mock_load_evidence_units.side_effect = StorageDataError(
            Path("/tmp/evidence_units.json"),
            "json_parse_failed",
            "数据文件 JSON 格式损坏：evidence_units.json（第 1 行，第 1 列），请使用历史版本回滚。",
        )

        assert _load_evidence_units_safe() == []

    @patch("app.main.load_calibration_models")
    def test_load_calibration_models_safe_falls_back_on_corrupted_storage(
        self,
        mock_load_calibration_models,
    ):
        from app.main import _load_calibration_models_safe
        from app.storage import StorageDataError

        mock_load_calibration_models.side_effect = StorageDataError(
            Path("/tmp/calibration_models.json"),
            "json_parse_failed",
            "数据文件 JSON 格式损坏：calibration_models.json（第 1 行，第 1 列），请使用历史版本回滚。",
        )

        assert _load_calibration_models_safe() == []


class TestVersionedJsonHistoryRoutes:
    @patch("app.main.list_json_versions")
    @patch("app.main.ensure_data_dirs")
    def test_list_versioned_json_history(
        self,
        mock_ensure,
        mock_list_versions,
        client,
    ):
        mock_list_versions.return_value = [
            {
                "version_id": "20260314T010203000000Z",
                "filename": "expert_profiles_v20260314T010203000000Z.json",
                "created_at": "2026-03-14T01:02:03+00:00",
                "size_bytes": 128,
            }
        ]

        response = client.get("/api/v1/ops/versioned-json/expert_profiles")

        assert response.status_code == 200
        data = response.json()
        assert data["artifact"] == "expert_profiles"
        assert data["versions"][0]["version_id"] == "20260314T010203000000Z"

    @patch("app.main.restore_json_version")
    @patch("app.main.ensure_data_dirs")
    def test_rollback_versioned_json_history(
        self,
        mock_ensure,
        mock_restore_json_version,
        client,
    ):
        mock_restore_json_version.return_value = {
            "version_id": "20260314T010203000000Z",
            "restored_at": "2026-03-14T02:03:04+00:00",
            "backup_version_id": "20260314T020304999999Z",
        }

        response = client.post(
            "/api/v1/ops/versioned-json/calibration_models/rollback",
            json={"version_id": "20260314T010203000000Z"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True
        assert data["artifact"] == "calibration_models"
        assert data["restored_version_id"] == "20260314T010203000000Z"
        assert data["backup_version_id"] == "20260314T020304999999Z"


class TestMaterialParseWorkerLifecycle:
    def test_material_should_use_preview_stage_for_large_pdf_inputs(self):
        from app.main import _material_should_use_preview_stage

        assert (
            _material_should_use_preview_stage(
                b"x" * 400_000,
                "招标文件.pdf",
                material_type="tender_qa",
            )
            is True
        )
        assert (
            _material_should_use_preview_stage(
                b"x" * 120_000,
                "招标文件.pdf",
                material_type="tender_qa",
            )
            is False
        )
        assert (
            _material_should_use_preview_stage(
                b"x" * 400_000,
                "工程量清单.xlsx",
                material_type="boq",
            )
            is True
        )
        assert (
            _material_should_use_preview_stage(
                b"x" * 400_000,
                "工程量清单.pdf",
                material_type="boq",
            )
            is True
        )
        assert (
            _material_should_use_preview_stage(
                b"x" * 80_000,
                "工程量清单.xlsx",
                material_type="boq",
            )
            is False
        )
        assert (
            _material_should_use_preview_stage(
                b"x" * 80_000,
                "工程量清单.pdf",
                material_type="boq",
            )
            is False
        )

    @patch("app.main._bootstrap_material_parse_state")
    @patch("app.main.threading.Thread")
    def test_start_material_parse_worker_starts_worker_pool(
        self,
        mock_thread_cls,
        mock_bootstrap_state,
    ):
        from app import main as main_module

        original_worker = main_module._MATERIAL_PARSE_WORKER
        original_workers = list(main_module._MATERIAL_PARSE_WORKERS)
        original_event_state = main_module._MATERIAL_PARSE_STOP_EVENT.is_set()
        original_wake_state = main_module._MATERIAL_PARSE_WAKE_EVENT.is_set()
        worker_pool = [
            MagicMock(name=f"worker-{idx}")
            for idx in range(main_module._material_parse_total_worker_count())
        ]
        mock_thread_cls.side_effect = worker_pool
        main_module._MATERIAL_PARSE_WORKER = None
        main_module._MATERIAL_PARSE_WORKERS = []
        try:
            main_module._start_material_parse_worker()

            assert mock_thread_cls.call_count == main_module._material_parse_total_worker_count()
            for worker in worker_pool:
                worker.start.assert_called_once()
            mock_bootstrap_state.assert_called_once()
            assert main_module._MATERIAL_PARSE_WORKER is worker_pool[0]
            assert main_module._MATERIAL_PARSE_WORKERS == worker_pool
            first_kwargs = mock_thread_cls.call_args_list[0].kwargs
            assert first_kwargs["kwargs"]["preferred_parse_mode"] == "preview"
            assert first_kwargs["kwargs"]["allow_fallback"] is True
            assert (
                first_kwargs["kwargs"]["max_preview_speed_rank"]
                == main_module.DEFAULT_MATERIAL_PARSE_PREVIEW_EXPRESS_MAX_SPEED_RANK
            )
            second_kwargs = mock_thread_cls.call_args_list[1].kwargs
            assert second_kwargs["kwargs"]["preferred_parse_mode"] == "preview"
            assert second_kwargs["kwargs"]["allow_fallback"] is False
            assert second_kwargs["kwargs"]["max_preview_speed_rank"] is None
        finally:
            main_module._MATERIAL_PARSE_WORKER = original_worker
            main_module._MATERIAL_PARSE_WORKERS = original_workers
            if original_event_state:
                main_module._MATERIAL_PARSE_STOP_EVENT.set()
            else:
                main_module._MATERIAL_PARSE_STOP_EVENT.clear()
            if original_wake_state:
                main_module._MATERIAL_PARSE_WAKE_EVENT.set()
            else:
                main_module._MATERIAL_PARSE_WAKE_EVENT.clear()

    @patch("app.main._invalidate_material_index_cache")
    @patch("app.main.save_materials")
    @patch("app.main.save_material_parse_jobs")
    @patch("app.main.load_submissions")
    @patch("app.main.load_materials")
    @patch("app.main.load_material_parse_jobs")
    @patch("app.main.get_openai_api_key", return_value="")
    def test_claim_next_material_parse_job_prioritizes_scoring_blocker_required_materials(
        self,
        _mock_openai_key,
        mock_load_jobs,
        mock_load_materials,
        mock_load_submissions,
        mock_save_jobs,
        mock_save_materials,
        _mock_invalidate,
    ):
        from app.main import _claim_next_material_parse_job

        mock_load_materials.return_value = [
            {
                "id": "m-photo",
                "project_id": "p-materials",
                "material_type": "site_photo",
                "filename": "现场.jpg",
                "path": "/tmp/现场.jpg",
                "created_at": "2026-03-09T00:00:00+00:00",
                "updated_at": "2026-03-09T00:00:00+00:00",
                "parse_status": "queued",
                "parse_backend": "queued",
            },
            {
                "id": "m-drawing",
                "project_id": "p-score",
                "material_type": "drawing",
                "filename": "总图.pdf",
                "path": "/tmp/总图.pdf",
                "created_at": "2026-03-09T00:00:00+00:00",
                "updated_at": "2026-03-09T00:00:00+00:00",
                "parse_status": "queued",
                "parse_backend": "queued",
            },
            {
                "id": "m-boq",
                "project_id": "p-score",
                "material_type": "boq",
                "filename": "清单.xlsx",
                "path": "/tmp/清单.xlsx",
                "created_at": "2026-03-09T00:00:00+00:00",
                "updated_at": "2026-03-09T00:00:00+00:00",
                "parse_status": "queued",
                "parse_backend": "queued",
            },
        ]
        mock_load_jobs.return_value = [
            {
                "id": "j-photo",
                "material_id": "m-photo",
                "project_id": "p-materials",
                "material_type": "site_photo",
                "filename": "现场.jpg",
                "status": "queued",
                "created_at": "2026-03-09T00:00:00+00:00",
                "updated_at": "2026-03-09T00:00:00+00:00",
            },
            {
                "id": "j-drawing",
                "material_id": "m-drawing",
                "project_id": "p-score",
                "material_type": "drawing",
                "filename": "总图.pdf",
                "status": "queued",
                "created_at": "2026-03-09T00:00:00+00:00",
                "updated_at": "2026-03-09T00:00:00+00:00",
            },
            {
                "id": "j-boq",
                "material_id": "m-boq",
                "project_id": "p-score",
                "material_type": "boq",
                "filename": "清单.xlsx",
                "status": "queued",
                "created_at": "2026-03-09T00:00:00+00:00",
                "updated_at": "2026-03-09T00:00:00+00:00",
            },
        ]
        mock_load_submissions.return_value = [
            {
                "id": "s1",
                "project_id": "p-score",
                "filename": "施组1.docx",
                "text": "施工组织设计正文",
                "score": None,
                "report": None,
            }
        ]

        claimed = _claim_next_material_parse_job()

        assert claimed is not None
        assert claimed["id"] == "j-boq"
        saved_jobs = mock_save_jobs.call_args[0][0]
        prioritized = next(job for job in saved_jobs if job["id"] == "j-boq")
        assert prioritized["status"] == "processing"
        saved_rows = mock_save_materials.call_args[0][0]
        prioritized_row = next(row for row in saved_rows if row["id"] == "m-boq")
        assert prioritized_row["parse_status"] == "processing"
        assert prioritized_row["parse_backend"] == "local"

    @patch("app.main.load_materials")
    @patch("app.main.load_material_parse_jobs")
    @patch("app.main.save_material_parse_jobs")
    @patch("app.main.save_materials")
    @patch("app.main._invalidate_material_index_cache")
    @patch("app.main._get_material_parse_active_project_types")
    @patch("app.main._get_material_parse_active_projects")
    @patch("app.main._load_material_parse_job_priority_contexts")
    @patch("app.main._build_material_parse_project_stage_rank")
    def test_claim_next_material_parse_job_preview_reserved_worker_waits_for_preview_jobs(
        self,
        mock_build_stage_ranks,
        mock_load_priority_contexts,
        mock_get_active_projects,
        mock_get_active_project_types,
        _mock_invalidate,
        _mock_save_materials,
        _mock_save_jobs,
        mock_load_jobs,
        mock_load_materials,
    ):
        from app.main import _claim_next_material_parse_job

        mock_load_materials.return_value = [
            {
                "id": "m-boq",
                "project_id": "p1",
                "material_type": "boq",
                "filename": "清单.xlsx",
                "path": "/tmp/清单.xlsx",
                "created_at": "2026-03-09T00:00:00+00:00",
                "updated_at": "2026-03-09T00:00:00+00:00",
                "parse_status": "queued",
                "parse_backend": "queued",
            }
        ]
        mock_load_jobs.return_value = [
            {
                "id": "j-boq-full",
                "material_id": "m-boq",
                "project_id": "p1",
                "material_type": "boq",
                "filename": "清单.xlsx",
                "status": "queued",
                "parse_mode": "full",
                "created_at": "2026-03-09T00:00:00+00:00",
                "updated_at": "2026-03-09T00:00:00+00:00",
            }
        ]

        claimed = _claim_next_material_parse_job(
            preferred_parse_mode="preview",
            allow_fallback=False,
            prefer_active_projects=True,
        )

        assert claimed is None
        mock_build_stage_ranks.assert_not_called()
        mock_load_priority_contexts.assert_not_called()
        mock_get_active_projects.assert_not_called()
        mock_get_active_project_types.assert_not_called()

    @patch("app.main._invalidate_material_index_cache")
    @patch("app.main._invalidate_material_parse_claim_snapshot")
    @patch("app.main.save_materials")
    @patch("app.main.save_material_parse_jobs")
    @patch("app.main._get_material_parse_active_project_types")
    @patch("app.main._get_material_parse_active_projects")
    @patch("app.main._load_material_parse_job_priority_contexts")
    @patch("app.main._build_material_parse_project_stage_rank")
    @patch("app.main.load_materials")
    @patch("app.main.load_material_parse_jobs")
    def test_claim_next_material_parse_job_single_candidate_skips_priority_context(
        self,
        mock_load_jobs,
        mock_load_materials,
        mock_build_stage_ranks,
        mock_load_priority_contexts,
        mock_get_active_projects,
        mock_get_active_project_types,
        mock_save_jobs,
        mock_save_materials,
        mock_invalidate_claim_snapshot,
        _mock_invalidate,
    ):
        from app.main import _claim_next_material_parse_job

        mock_load_materials.return_value = [
            {
                "id": "m-boq",
                "project_id": "p1",
                "material_type": "boq",
                "filename": "清单.xlsx",
                "path": "/tmp/清单.xlsx",
                "created_at": "2026-03-09T00:00:00+00:00",
                "updated_at": "2026-03-09T00:00:00+00:00",
                "parse_status": "queued",
                "parse_backend": "queued",
            }
        ]
        mock_load_jobs.return_value = [
            {
                "id": "j-boq-preview",
                "material_id": "m-boq",
                "project_id": "p1",
                "material_type": "boq",
                "filename": "清单.xlsx",
                "status": "queued",
                "parse_mode": "preview",
                "created_at": "2026-03-09T00:00:00+00:00",
                "updated_at": "2026-03-09T00:00:00+00:00",
            }
        ]

        claimed = _claim_next_material_parse_job(prefer_active_projects=True)

        assert claimed is not None
        assert claimed["id"] == "j-boq-preview"
        mock_build_stage_ranks.assert_not_called()
        mock_load_priority_contexts.assert_not_called()
        mock_get_active_projects.assert_not_called()
        mock_get_active_project_types.assert_not_called()
        saved_jobs = mock_save_jobs.call_args[0][0]
        prioritized = next(job for job in saved_jobs if job["id"] == "j-boq-preview")
        assert prioritized["status"] == "processing"
        saved_rows = mock_save_materials.call_args[0][0]
        prioritized_row = next(row for row in saved_rows if row["id"] == "m-boq")
        assert prioritized_row["parse_status"] == "processing"
        mock_invalidate_claim_snapshot.assert_called_once()

    @patch("app.main._invalidate_material_index_cache")
    @patch("app.main.save_materials")
    @patch("app.main.save_material_parse_jobs")
    @patch(
        "app.main._material_parse_preview_speed_rank",
        side_effect=lambda *_args, **_kwargs: (2, 20_000, 2),
    )
    @patch("app.main.load_materials")
    @patch("app.main.load_material_parse_jobs")
    def test_claim_next_material_parse_job_preview_express_high_cost_miss_skips_speed_rerank(
        self,
        mock_load_jobs,
        mock_load_materials,
        mock_speed_rank,
        _mock_save_jobs,
        _mock_save_materials,
        _mock_invalidate,
    ):
        from app.main import _claim_next_material_parse_job

        mock_load_materials.return_value = [
            {
                "id": "m-tender",
                "project_id": "p1",
                "material_type": "tender_qa",
                "filename": "招标文件.pdf",
                "path": "/tmp/招标文件.pdf",
                "created_at": "2026-03-09T00:00:00+00:00",
                "updated_at": "2026-03-09T00:00:00+00:00",
                "parse_status": "queued",
                "parse_backend": "queued",
            }
        ]
        mock_load_jobs.return_value = [
            {
                "id": "j-tender-preview",
                "material_id": "m-tender",
                "project_id": "p1",
                "material_type": "tender_qa",
                "filename": "招标文件.pdf",
                "status": "queued",
                "parse_mode": "preview",
                "created_at": "2026-03-09T00:00:00+00:00",
                "updated_at": "2026-03-09T00:00:00+00:00",
            }
        ]

        claimed = _claim_next_material_parse_job(
            preferred_parse_mode="preview",
            allow_fallback=False,
            max_preview_speed_rank=1,
        )

        assert claimed is None
        assert mock_speed_rank.call_count == 1

    @patch("app.main._invalidate_material_index_cache")
    @patch("app.main.save_materials")
    @patch("app.main.save_material_parse_jobs")
    @patch("app.main._parse_iso_datetime_utc")
    @patch(
        "app.main._material_parse_preview_speed_rank",
        side_effect=[(0, 1_000, 0), (2, 20_000, 2)],
    )
    @patch("app.main.load_materials")
    @patch("app.main.load_material_parse_jobs")
    def test_claim_next_material_parse_job_preview_fallback_reuses_dynamic_eligibility_scan(
        self,
        mock_load_jobs,
        mock_load_materials,
        _mock_speed_rank,
        mock_parse_iso,
        mock_save_jobs,
        mock_save_materials,
        _mock_invalidate,
    ):
        from app.main import _claim_next_material_parse_job

        mock_parse_iso.side_effect = lambda value: datetime.fromisoformat(value) if value else None
        mock_load_materials.return_value = [
            {
                "id": "m-fast",
                "project_id": "p1",
                "material_type": "boq",
                "filename": "快速预解析.pdf",
                "path": "/tmp/快速预解析.pdf",
                "created_at": "2026-03-09T00:00:00+00:00",
                "updated_at": "2026-03-09T00:00:00+00:00",
                "parse_status": "queued",
                "parse_backend": "queued",
            },
            {
                "id": "m-slow",
                "project_id": "p1",
                "material_type": "tender_qa",
                "filename": "回退预解析.pdf",
                "path": "/tmp/回退预解析.pdf",
                "created_at": "2026-03-09T00:00:00+00:00",
                "updated_at": "2026-03-09T00:00:00+00:00",
                "parse_status": "queued",
                "parse_backend": "queued",
            },
        ]
        mock_load_jobs.return_value = [
            {
                "id": "j-fast-preview",
                "material_id": "m-fast",
                "project_id": "p1",
                "material_type": "boq",
                "filename": "快速预解析.pdf",
                "status": "failed",
                "attempt": 1,
                "next_retry_at": "2099-03-10T00:00:00+00:00",
                "parse_mode": "preview",
                "created_at": "2026-03-09T00:00:00+00:00",
                "updated_at": "2026-03-09T00:00:00+00:00",
            },
            {
                "id": "j-slow-preview",
                "material_id": "m-slow",
                "project_id": "p1",
                "material_type": "tender_qa",
                "filename": "回退预解析.pdf",
                "status": "queued",
                "parse_mode": "preview",
                "created_at": "2026-03-09T00:00:00+00:00",
                "updated_at": "2026-03-09T00:00:00+00:00",
            },
        ]

        claimed = _claim_next_material_parse_job(
            preferred_parse_mode="preview",
            allow_fallback=True,
            max_preview_speed_rank=1,
        )

        assert claimed is not None
        assert claimed["id"] == "j-slow-preview"
        assert mock_parse_iso.call_count == 1
        saved_jobs = mock_save_jobs.call_args[0][0]
        prioritized = next(job for job in saved_jobs if job["id"] == "j-slow-preview")
        assert prioritized["status"] == "processing"
        saved_rows = mock_save_materials.call_args[0][0]
        prioritized_row = next(row for row in saved_rows if row["id"] == "m-slow")
        assert prioritized_row["parse_status"] == "processing"

    @patch("app.main._invalidate_material_index_cache")
    @patch("app.main.save_materials")
    @patch("app.main.save_material_parse_jobs")
    @patch("app.main.load_materials")
    @patch("app.main.load_material_parse_jobs")
    @patch("app.main.get_openai_api_key", return_value="")
    def test_claim_next_material_parse_job_preview_express_worker_falls_back_to_general_preview(
        self,
        _mock_openai_key,
        mock_load_jobs,
        mock_load_materials,
        mock_save_jobs,
        mock_save_materials,
        _mock_invalidate,
    ):
        from app.main import _claim_next_material_parse_job

        mock_load_materials.return_value = [
            {
                "id": "m-tender",
                "project_id": "p1",
                "material_type": "tender_qa",
                "filename": "招标文件.pdf",
                "path": "/tmp/招标文件.pdf",
                "created_at": "2026-03-09T00:00:00+00:00",
                "updated_at": "2026-03-09T00:00:00+00:00",
                "parse_status": "queued",
                "parse_backend": "queued",
            }
        ]
        mock_load_jobs.return_value = [
            {
                "id": "j-tender-preview",
                "material_id": "m-tender",
                "project_id": "p1",
                "material_type": "tender_qa",
                "filename": "招标文件.pdf",
                "status": "queued",
                "parse_mode": "preview",
                "created_at": "2026-03-09T00:00:00+00:00",
                "updated_at": "2026-03-09T00:00:00+00:00",
            }
        ]

        claimed = _claim_next_material_parse_job(
            preferred_parse_mode="preview",
            allow_fallback=True,
            max_preview_speed_rank=1,
        )

        assert claimed is not None
        assert claimed["id"] == "j-tender-preview"
        saved_jobs = mock_save_jobs.call_args[0][0]
        prioritized = next(job for job in saved_jobs if job["id"] == "j-tender-preview")
        assert prioritized["status"] == "processing"
        saved_rows = mock_save_materials.call_args[0][0]
        prioritized_row = next(row for row in saved_rows if row["id"] == "m-tender")
        assert prioritized_row["parse_status"] == "processing"

    @patch("app.main._invalidate_material_index_cache")
    @patch("app.main.save_materials")
    @patch("app.main.save_material_parse_jobs")
    @patch("app.main.load_submissions")
    @patch("app.main.load_materials")
    @patch("app.main.load_material_parse_jobs")
    def test_claim_next_material_parse_job_preview_express_worker_claims_boq_pdf_preview(
        self,
        mock_load_jobs,
        mock_load_materials,
        mock_load_submissions,
        mock_save_jobs,
        mock_save_materials,
        _mock_invalidate,
    ):
        from app.main import _claim_next_material_parse_job

        mock_load_submissions.return_value = []
        mock_load_materials.return_value = [
            {
                "id": "m-boq-pdf",
                "project_id": "p1",
                "material_type": "boq",
                "filename": "工程量清单.pdf",
                "path": "/tmp/工程量清单.pdf",
                "created_at": "2026-03-09T00:00:00+00:00",
                "updated_at": "2026-03-09T00:00:00+00:00",
                "parse_status": "queued",
                "parse_backend": "queued",
            },
            {
                "id": "m-tender",
                "project_id": "p1",
                "material_type": "tender_qa",
                "filename": "招标文件.pdf",
                "path": "/tmp/招标文件.pdf",
                "created_at": "2026-03-09T00:00:00+00:00",
                "updated_at": "2026-03-09T00:00:00+00:00",
                "parse_status": "queued",
                "parse_backend": "queued",
            },
        ]
        mock_load_jobs.return_value = [
            {
                "id": "j-tender-preview",
                "material_id": "m-tender",
                "project_id": "p1",
                "material_type": "tender_qa",
                "filename": "招标文件.pdf",
                "status": "queued",
                "parse_mode": "preview",
                "created_at": "2026-03-09T00:00:00+00:00",
                "updated_at": "2026-03-09T00:00:00+00:00",
            },
            {
                "id": "j-boq-pdf-preview",
                "material_id": "m-boq-pdf",
                "project_id": "p1",
                "material_type": "boq",
                "filename": "工程量清单.pdf",
                "status": "queued",
                "parse_mode": "preview",
                "created_at": "2026-03-09T00:00:00+00:00",
                "updated_at": "2026-03-09T00:00:00+00:00",
            },
        ]

        claimed = _claim_next_material_parse_job(
            preferred_parse_mode="preview",
            allow_fallback=False,
            max_preview_speed_rank=1,
        )

        assert claimed is not None
        assert claimed["id"] == "j-boq-pdf-preview"
        saved_jobs = mock_save_jobs.call_args[0][0]
        prioritized = next(job for job in saved_jobs if job["id"] == "j-boq-pdf-preview")
        assert prioritized["status"] == "processing"
        saved_rows = mock_save_materials.call_args[0][0]
        prioritized_row = next(row for row in saved_rows if row["id"] == "m-boq-pdf")
        assert prioritized_row["parse_status"] == "processing"
        assert prioritized_row["parse_backend"] == "local_preview"

    @patch("app.main._invalidate_material_index_cache")
    @patch("app.main.save_materials")
    @patch("app.main.save_material_parse_jobs")
    @patch("app.main.load_submissions")
    @patch("app.main.load_materials")
    @patch("app.main.load_material_parse_jobs")
    def test_claim_next_material_parse_job_preview_express_prefers_tabular_boq_before_boq_pdf(
        self,
        mock_load_jobs,
        mock_load_materials,
        mock_load_submissions,
        mock_save_jobs,
        mock_save_materials,
        _mock_invalidate,
    ):
        from app.main import _claim_next_material_parse_job

        mock_load_submissions.return_value = []
        mock_load_materials.return_value = [
            {
                "id": "m-boq-xlsx",
                "project_id": "p1",
                "material_type": "boq",
                "filename": "工程量清单.xlsx",
                "path": "",
                "created_at": "2026-03-09T00:00:00+00:00",
                "updated_at": "2026-03-09T00:00:00+00:00",
                "parse_status": "queued",
                "parse_backend": "queued",
            },
            {
                "id": "m-boq-pdf",
                "project_id": "p1",
                "material_type": "boq",
                "filename": "工程量清单.pdf",
                "path": "",
                "created_at": "2026-03-09T00:00:00+00:00",
                "updated_at": "2026-03-09T00:00:00+00:00",
                "parse_status": "queued",
                "parse_backend": "queued",
            },
        ]
        mock_load_jobs.return_value = [
            {
                "id": "j-boq-pdf-preview",
                "material_id": "m-boq-pdf",
                "project_id": "p1",
                "material_type": "boq",
                "filename": "工程量清单.pdf",
                "status": "queued",
                "parse_mode": "preview",
                "created_at": "2026-03-09T00:00:00+00:00",
                "updated_at": "2026-03-09T00:00:00+00:00",
            },
            {
                "id": "j-boq-xlsx-preview",
                "material_id": "m-boq-xlsx",
                "project_id": "p1",
                "material_type": "boq",
                "filename": "工程量清单.xlsx",
                "status": "queued",
                "parse_mode": "preview",
                "created_at": "2026-03-09T00:00:01+00:00",
                "updated_at": "2026-03-09T00:00:01+00:00",
            },
        ]

        claimed = _claim_next_material_parse_job(
            preferred_parse_mode="preview",
            allow_fallback=False,
            max_preview_speed_rank=1,
        )

        assert claimed is not None
        assert claimed["id"] == "j-boq-xlsx-preview"
        saved_jobs = mock_save_jobs.call_args[0][0]
        prioritized = next(job for job in saved_jobs if job["id"] == "j-boq-xlsx-preview")
        assert prioritized["status"] == "processing"
        saved_rows = mock_save_materials.call_args[0][0]
        prioritized_row = next(row for row in saved_rows if row["id"] == "m-boq-xlsx")
        assert prioritized_row["parse_status"] == "processing"
        assert prioritized_row["parse_backend"] == "local_preview"

    @patch("app.main._invalidate_material_index_cache")
    @patch("app.main.save_materials")
    @patch("app.main.save_material_parse_jobs")
    @patch("app.main.load_submissions")
    @patch("app.main.load_materials")
    @patch("app.main.load_material_parse_jobs")
    @patch("app.main._build_material_parse_project_stage_rank")
    def test_claim_next_material_parse_job_same_project_multi_candidate_skips_stage_rank(
        self,
        mock_build_stage_rank,
        mock_load_jobs,
        mock_load_materials,
        mock_load_submissions,
        mock_save_jobs,
        mock_save_materials,
        _mock_invalidate,
    ):
        from app.main import _claim_next_material_parse_job

        mock_load_submissions.return_value = []
        mock_load_materials.return_value = [
            {
                "id": "m-boq",
                "project_id": "p1",
                "material_type": "boq",
                "filename": "工程量清单.xlsx",
                "path": "/tmp/工程量清单.xlsx",
                "created_at": "2026-03-09T00:00:00+00:00",
                "updated_at": "2026-03-09T00:00:00+00:00",
                "parse_status": "queued",
                "parse_backend": "queued",
            },
            {
                "id": "m-drawing",
                "project_id": "p1",
                "material_type": "drawing",
                "filename": "总图.pdf",
                "path": "/tmp/总图.pdf",
                "created_at": "2026-03-09T00:00:00+00:00",
                "updated_at": "2026-03-09T00:00:00+00:00",
                "parse_status": "queued",
                "parse_backend": "queued",
            },
        ]
        mock_load_jobs.return_value = [
            {
                "id": "j-drawing-preview",
                "material_id": "m-drawing",
                "project_id": "p1",
                "material_type": "drawing",
                "filename": "总图.pdf",
                "status": "queued",
                "parse_mode": "preview",
                "created_at": "2026-03-09T00:00:00+00:00",
                "updated_at": "2026-03-09T00:00:00+00:00",
            },
            {
                "id": "j-boq-preview",
                "material_id": "m-boq",
                "project_id": "p1",
                "material_type": "boq",
                "filename": "工程量清单.xlsx",
                "status": "queued",
                "parse_mode": "preview",
                "created_at": "2026-03-09T00:00:01+00:00",
                "updated_at": "2026-03-09T00:00:01+00:00",
            },
        ]

        claimed = _claim_next_material_parse_job(preferred_parse_mode="preview")

        assert claimed is not None
        assert claimed["id"] == "j-boq-preview"
        mock_build_stage_rank.assert_not_called()
        saved_jobs = mock_save_jobs.call_args[0][0]
        prioritized = next(job for job in saved_jobs if job["id"] == "j-boq-preview")
        assert prioritized["status"] == "processing"
        saved_rows = mock_save_materials.call_args[0][0]
        prioritized_row = next(row for row in saved_rows if row["id"] == "m-boq")
        assert prioritized_row["parse_status"] == "processing"

    @patch("app.main._invalidate_material_index_cache")
    @patch("app.main.save_materials")
    @patch("app.main.save_material_parse_jobs")
    @patch("app.main.load_submissions")
    @patch("app.main.load_materials")
    @patch("app.main.load_material_parse_jobs")
    def test_claim_next_material_parse_job_prefers_same_project_within_same_preview_tier(
        self,
        mock_load_jobs,
        mock_load_materials,
        mock_load_submissions,
        mock_save_jobs,
        mock_save_materials,
        _mock_invalidate,
    ):
        from app.main import _claim_next_material_parse_job

        mock_load_submissions.return_value = []
        mock_load_materials.return_value = [
            {
                "id": "m-p1",
                "project_id": "p1",
                "material_type": "boq",
                "filename": "工程量清单.pdf",
                "path": "/tmp/p1.pdf",
                "created_at": "2026-03-09T00:00:00+00:00",
                "updated_at": "2026-03-09T00:00:00+00:00",
                "parse_status": "queued",
                "parse_backend": "queued",
            },
            {
                "id": "m-p2",
                "project_id": "p2",
                "material_type": "boq",
                "filename": "工程量清单.pdf",
                "path": "/tmp/p2.pdf",
                "created_at": "2026-03-09T00:00:00+00:00",
                "updated_at": "2026-03-09T00:00:00+00:00",
                "parse_status": "queued",
                "parse_backend": "queued",
            },
        ]
        mock_load_jobs.return_value = [
            {
                "id": "j-p2-preview",
                "material_id": "m-p2",
                "project_id": "p2",
                "material_type": "boq",
                "filename": "工程量清单.pdf",
                "status": "queued",
                "parse_mode": "preview",
                "created_at": "2026-03-09T00:00:00+00:00",
                "updated_at": "2026-03-09T00:00:00+00:00",
            },
            {
                "id": "j-p1-preview",
                "material_id": "m-p1",
                "project_id": "p1",
                "material_type": "boq",
                "filename": "工程量清单.pdf",
                "status": "queued",
                "parse_mode": "preview",
                "created_at": "2026-03-09T00:00:01+00:00",
                "updated_at": "2026-03-09T00:00:01+00:00",
            },
        ]

        claimed = _claim_next_material_parse_job(
            preferred_parse_mode="preview",
            allow_fallback=False,
            max_preview_speed_rank=1,
            preferred_project_id="p1",
        )

        assert claimed is not None
        assert claimed["id"] == "j-p1-preview"
        saved_jobs = mock_save_jobs.call_args[0][0]
        prioritized = next(job for job in saved_jobs if job["id"] == "j-p1-preview")
        assert prioritized["status"] == "processing"
        saved_rows = mock_save_materials.call_args[0][0]
        prioritized_row = next(row for row in saved_rows if row["id"] == "m-p1")
        assert prioritized_row["parse_status"] == "processing"
        assert prioritized_row["parse_backend"] == "local_preview"

    @patch("app.main._invalidate_material_index_cache")
    @patch("app.main.save_materials")
    @patch("app.main.save_material_parse_jobs")
    @patch("app.main.load_submissions")
    @patch("app.main.load_materials")
    @patch("app.main.load_material_parse_jobs")
    def test_claim_next_material_parse_job_prefers_active_project_window_when_enabled(
        self,
        mock_load_jobs,
        mock_load_materials,
        mock_load_submissions,
        mock_save_jobs,
        mock_save_materials,
        _mock_invalidate,
    ):
        from app import main as main_module
        from app.main import _claim_next_material_parse_job

        original_active_projects = dict(main_module._MATERIAL_PARSE_ACTIVE_PROJECTS)
        original_active_project_claims = dict(main_module._MATERIAL_PARSE_ACTIVE_PROJECT_CLAIMS)
        mock_load_submissions.return_value = []
        mock_load_materials.return_value = [
            {
                "id": "m-p1",
                "project_id": "p1",
                "material_type": "boq",
                "filename": "工程量清单.pdf",
                "path": "/tmp/p1.pdf",
                "created_at": "2026-03-09T00:00:00+00:00",
                "updated_at": "2026-03-09T00:00:00+00:00",
                "parse_status": "queued",
                "parse_backend": "queued",
            },
            {
                "id": "m-p2",
                "project_id": "p2",
                "material_type": "boq",
                "filename": "工程量清单.pdf",
                "path": "/tmp/p2.pdf",
                "created_at": "2026-03-09T00:00:00+00:00",
                "updated_at": "2026-03-09T00:00:00+00:00",
                "parse_status": "queued",
                "parse_backend": "queued",
            },
        ]
        mock_load_jobs.return_value = [
            {
                "id": "j-p2-preview",
                "material_id": "m-p2",
                "project_id": "p2",
                "material_type": "boq",
                "filename": "工程量清单.pdf",
                "status": "queued",
                "parse_mode": "preview",
                "created_at": "2026-03-09T00:00:00+00:00",
                "updated_at": "2026-03-09T00:00:00+00:00",
            },
            {
                "id": "j-p1-preview",
                "material_id": "m-p1",
                "project_id": "p1",
                "material_type": "boq",
                "filename": "工程量清单.pdf",
                "status": "queued",
                "parse_mode": "preview",
                "created_at": "2026-03-09T00:00:01+00:00",
                "updated_at": "2026-03-09T00:00:01+00:00",
            },
        ]
        try:
            main_module._MATERIAL_PARSE_ACTIVE_PROJECTS.clear()
            main_module._MATERIAL_PARSE_ACTIVE_PROJECT_CLAIMS.clear()
            main_module._MATERIAL_PARSE_ACTIVE_PROJECTS["p1"] = 108.0
            main_module._MATERIAL_PARSE_ACTIVE_PROJECT_CLAIMS["p1"] = 1
            with patch("app.main.time.monotonic", return_value=100.0):
                claimed = _claim_next_material_parse_job(
                    preferred_parse_mode="preview",
                    allow_fallback=False,
                    max_preview_speed_rank=1,
                    prefer_active_projects=True,
                )
        finally:
            main_module._MATERIAL_PARSE_ACTIVE_PROJECTS.clear()
            main_module._MATERIAL_PARSE_ACTIVE_PROJECTS.update(original_active_projects)
            main_module._MATERIAL_PARSE_ACTIVE_PROJECT_CLAIMS.clear()
            main_module._MATERIAL_PARSE_ACTIVE_PROJECT_CLAIMS.update(original_active_project_claims)

        assert claimed is not None
        assert claimed["id"] == "j-p1-preview"
        saved_jobs = mock_save_jobs.call_args[0][0]
        prioritized = next(job for job in saved_jobs if job["id"] == "j-p1-preview")
        assert prioritized["status"] == "processing"
        saved_rows = mock_save_materials.call_args[0][0]
        prioritized_row = next(row for row in saved_rows if row["id"] == "m-p1")
        assert prioritized_row["parse_status"] == "processing"
        assert prioritized_row["parse_backend"] == "local_preview"

    @patch("app.main._invalidate_material_index_cache")
    @patch("app.main.save_materials")
    @patch("app.main.save_material_parse_jobs")
    @patch("app.main.load_submissions")
    @patch("app.main.load_materials")
    @patch("app.main.load_material_parse_jobs")
    def test_claim_next_material_parse_job_prefers_active_project_material_type_when_enabled(
        self,
        mock_load_jobs,
        mock_load_materials,
        mock_load_submissions,
        mock_save_jobs,
        mock_save_materials,
        _mock_invalidate,
    ):
        from app import main as main_module
        from app.main import _claim_next_material_parse_job

        original_active_projects = dict(main_module._MATERIAL_PARSE_ACTIVE_PROJECTS)
        original_active_project_types = dict(main_module._MATERIAL_PARSE_ACTIVE_PROJECT_TYPES)
        original_active_project_claims = dict(main_module._MATERIAL_PARSE_ACTIVE_PROJECT_CLAIMS)
        original_active_project_type_claims = dict(
            main_module._MATERIAL_PARSE_ACTIVE_PROJECT_TYPE_CLAIMS
        )
        mock_load_submissions.return_value = []
        mock_load_materials.return_value = [
            {
                "id": "m-p1-boq",
                "project_id": "p1",
                "material_type": "boq",
                "filename": "工程量清单.xlsx",
                "path": "/tmp/p1-boq.xlsx",
                "created_at": "2026-03-09T00:00:00+00:00",
                "updated_at": "2026-03-09T00:00:00+00:00",
                "parse_status": "queued",
                "parse_backend": "queued",
            },
            {
                "id": "m-p1-tender",
                "project_id": "p1",
                "material_type": "tender_qa",
                "filename": "招标文件.pdf",
                "path": "/tmp/p1-tender.pdf",
                "created_at": "2026-03-09T00:00:00+00:00",
                "updated_at": "2026-03-09T00:00:00+00:00",
                "parse_status": "queued",
                "parse_backend": "queued",
            },
        ]
        mock_load_jobs.return_value = [
            {
                "id": "j-p1-tender-full",
                "material_id": "m-p1-tender",
                "project_id": "p1",
                "material_type": "tender_qa",
                "filename": "招标文件.pdf",
                "status": "queued",
                "parse_mode": "full",
                "created_at": "2026-03-09T00:00:00+00:00",
                "updated_at": "2026-03-09T00:00:00+00:00",
            },
            {
                "id": "j-p1-boq-full",
                "material_id": "m-p1-boq",
                "project_id": "p1",
                "material_type": "boq",
                "filename": "工程量清单.xlsx",
                "status": "queued",
                "parse_mode": "full",
                "created_at": "2026-03-09T00:00:01+00:00",
                "updated_at": "2026-03-09T00:00:01+00:00",
            },
        ]
        try:
            main_module._MATERIAL_PARSE_ACTIVE_PROJECTS.clear()
            main_module._MATERIAL_PARSE_ACTIVE_PROJECT_TYPES.clear()
            main_module._MATERIAL_PARSE_ACTIVE_PROJECT_CLAIMS.clear()
            main_module._MATERIAL_PARSE_ACTIVE_PROJECT_TYPE_CLAIMS.clear()
            main_module._MATERIAL_PARSE_ACTIVE_PROJECTS["p1"] = 108.0
            main_module._MATERIAL_PARSE_ACTIVE_PROJECT_CLAIMS["p1"] = 1
            main_module._MATERIAL_PARSE_ACTIVE_PROJECT_TYPES[("p1", "boq")] = 108.0
            main_module._MATERIAL_PARSE_ACTIVE_PROJECT_TYPE_CLAIMS[("p1", "boq")] = 1
            with patch("app.main.time.monotonic", return_value=100.0):
                claimed = _claim_next_material_parse_job(prefer_active_projects=True)
        finally:
            main_module._MATERIAL_PARSE_ACTIVE_PROJECTS.clear()
            main_module._MATERIAL_PARSE_ACTIVE_PROJECTS.update(original_active_projects)
            main_module._MATERIAL_PARSE_ACTIVE_PROJECT_TYPES.clear()
            main_module._MATERIAL_PARSE_ACTIVE_PROJECT_TYPES.update(original_active_project_types)
            main_module._MATERIAL_PARSE_ACTIVE_PROJECT_CLAIMS.clear()
            main_module._MATERIAL_PARSE_ACTIVE_PROJECT_CLAIMS.update(original_active_project_claims)
            main_module._MATERIAL_PARSE_ACTIVE_PROJECT_TYPE_CLAIMS.clear()
            main_module._MATERIAL_PARSE_ACTIVE_PROJECT_TYPE_CLAIMS.update(
                original_active_project_type_claims
            )

        assert claimed is not None
        assert claimed["id"] == "j-p1-boq-full"
        saved_jobs = mock_save_jobs.call_args[0][0]
        prioritized = next(job for job in saved_jobs if job["id"] == "j-p1-boq-full")
        assert prioritized["status"] == "processing"
        saved_rows = mock_save_materials.call_args[0][0]
        prioritized_row = next(row for row in saved_rows if row["id"] == "m-p1-boq")
        assert prioritized_row["parse_status"] == "processing"

    @patch("app.main._invalidate_material_index_cache")
    @patch("app.main.save_materials")
    @patch("app.main.save_material_parse_jobs")
    @patch("app.main.load_submissions")
    @patch("app.main.load_materials")
    @patch("app.main.load_material_parse_jobs")
    def test_claim_next_material_parse_job_active_project_bonus_respects_quota(
        self,
        mock_load_jobs,
        mock_load_materials,
        mock_load_submissions,
        mock_save_jobs,
        mock_save_materials,
        _mock_invalidate,
    ):
        from app import main as main_module
        from app.main import _claim_next_material_parse_job

        original_active_projects = dict(main_module._MATERIAL_PARSE_ACTIVE_PROJECTS)
        original_active_project_claims = dict(main_module._MATERIAL_PARSE_ACTIVE_PROJECT_CLAIMS)
        original_active_project_types = dict(main_module._MATERIAL_PARSE_ACTIVE_PROJECT_TYPES)
        original_active_project_type_claims = dict(
            main_module._MATERIAL_PARSE_ACTIVE_PROJECT_TYPE_CLAIMS
        )
        mock_load_submissions.return_value = []
        mock_load_materials.return_value = [
            {
                "id": "m-p1",
                "project_id": "p1",
                "material_type": "boq",
                "filename": "工程量清单.pdf",
                "path": "/tmp/p1.pdf",
                "created_at": "2026-03-09T00:00:00+00:00",
                "updated_at": "2026-03-09T00:00:00+00:00",
                "parse_status": "queued",
                "parse_backend": "queued",
            },
            {
                "id": "m-p2",
                "project_id": "p2",
                "material_type": "boq",
                "filename": "工程量清单.pdf",
                "path": "/tmp/p2.pdf",
                "created_at": "2026-03-09T00:00:00+00:00",
                "updated_at": "2026-03-09T00:00:00+00:00",
                "parse_status": "queued",
                "parse_backend": "queued",
            },
        ]
        mock_load_jobs.return_value = [
            {
                "id": "j-p2-preview",
                "material_id": "m-p2",
                "project_id": "p2",
                "material_type": "boq",
                "filename": "工程量清单.pdf",
                "status": "queued",
                "parse_mode": "preview",
                "created_at": "2026-03-09T00:00:00+00:00",
                "updated_at": "2026-03-09T00:00:00+00:00",
            },
            {
                "id": "j-p1-preview",
                "material_id": "m-p1",
                "project_id": "p1",
                "material_type": "boq",
                "filename": "工程量清单.pdf",
                "status": "queued",
                "parse_mode": "preview",
                "created_at": "2026-03-09T00:00:01+00:00",
                "updated_at": "2026-03-09T00:00:01+00:00",
            },
        ]
        try:
            main_module._MATERIAL_PARSE_ACTIVE_PROJECTS.clear()
            main_module._MATERIAL_PARSE_ACTIVE_PROJECT_CLAIMS.clear()
            main_module._MATERIAL_PARSE_ACTIVE_PROJECT_TYPES.clear()
            main_module._MATERIAL_PARSE_ACTIVE_PROJECT_TYPE_CLAIMS.clear()
            main_module._MATERIAL_PARSE_ACTIVE_PROJECTS["p1"] = 108.0
            main_module._MATERIAL_PARSE_ACTIVE_PROJECT_CLAIMS["p1"] = (
                main_module.DEFAULT_MATERIAL_PARSE_ACTIVE_PROJECT_WINDOW_MAX_CLAIMS + 1
            )
            with patch("app.main.time.monotonic", return_value=100.0):
                claimed = _claim_next_material_parse_job(
                    preferred_parse_mode="preview",
                    allow_fallback=False,
                    max_preview_speed_rank=1,
                    prefer_active_projects=True,
                )
        finally:
            main_module._MATERIAL_PARSE_ACTIVE_PROJECTS.clear()
            main_module._MATERIAL_PARSE_ACTIVE_PROJECTS.update(original_active_projects)
            main_module._MATERIAL_PARSE_ACTIVE_PROJECT_CLAIMS.clear()
            main_module._MATERIAL_PARSE_ACTIVE_PROJECT_CLAIMS.update(original_active_project_claims)
            main_module._MATERIAL_PARSE_ACTIVE_PROJECT_TYPES.clear()
            main_module._MATERIAL_PARSE_ACTIVE_PROJECT_TYPES.update(original_active_project_types)
            main_module._MATERIAL_PARSE_ACTIVE_PROJECT_TYPE_CLAIMS.clear()
            main_module._MATERIAL_PARSE_ACTIVE_PROJECT_TYPE_CLAIMS.update(
                original_active_project_type_claims
            )

        assert claimed is not None
        assert claimed["id"] == "j-p2-preview"
        saved_jobs = mock_save_jobs.call_args[0][0]
        prioritized = next(job for job in saved_jobs if job["id"] == "j-p2-preview")
        assert prioritized["status"] == "processing"
        saved_rows = mock_save_materials.call_args[0][0]
        prioritized_row = next(row for row in saved_rows if row["id"] == "m-p2")
        assert prioritized_row["parse_status"] == "processing"

    @patch("app.main.load_materials")
    @patch("app.main.load_material_parse_jobs")
    def test_load_material_parse_state_snapshot_reuses_cache_until_signature_changes(
        self,
        mock_load_jobs,
        mock_load_materials,
    ):
        from app import main as main_module
        from app.main import (
            _invalidate_material_parse_claim_snapshot,
            _load_material_parse_state_snapshot,
        )

        original_signature = main_module._MATERIAL_PARSE_CLAIM_SNAPSHOT_SIGNATURE
        original_materials = list(main_module._MATERIAL_PARSE_CLAIM_SNAPSHOT_MATERIALS)
        original_jobs = list(main_module._MATERIAL_PARSE_CLAIM_SNAPSHOT_JOBS)
        mock_load_materials.side_effect = [
            [{"id": "m1", "project_id": "p1"}],
            [{"id": "m2", "project_id": "p2"}],
        ]
        mock_load_jobs.side_effect = [
            [{"id": "j1", "material_id": "m1"}],
            [{"id": "j2", "material_id": "m2"}],
        ]
        try:
            _invalidate_material_parse_claim_snapshot()
            with patch("app.main._material_parse_claim_cache_enabled", return_value=True), patch(
                "app.main._material_parse_state_files_signature",
                side_effect=[
                    ((1, 1), (1, 1)),
                    ((1, 1), (1, 1)),
                    ((2, 2), (2, 2)),
                ],
            ):
                materials1, jobs1 = _load_material_parse_state_snapshot()
                materials2, jobs2 = _load_material_parse_state_snapshot()
                materials3, jobs3 = _load_material_parse_state_snapshot()
        finally:
            main_module._MATERIAL_PARSE_CLAIM_SNAPSHOT_SIGNATURE = original_signature
            main_module._MATERIAL_PARSE_CLAIM_SNAPSHOT_MATERIALS[:] = original_materials
            main_module._MATERIAL_PARSE_CLAIM_SNAPSHOT_JOBS[:] = original_jobs

        assert mock_load_materials.call_count == 2
        assert mock_load_jobs.call_count == 2
        assert materials1[0]["id"] == "m1"
        assert jobs1[0]["id"] == "j1"
        assert materials2[0]["id"] == "m1"
        assert jobs2[0]["id"] == "j1"
        assert materials3[0]["id"] == "m2"
        assert jobs3[0]["id"] == "j2"
        materials2[0]["id"] = "mutated"
        jobs2[0]["id"] = "mutated"
        assert materials1[0]["id"] == "m1"
        assert jobs1[0]["id"] == "j1"

    def test_load_material_parse_claim_context_reuses_cache_until_signature_changes(self):
        from app import main as main_module
        from app.main import _load_material_parse_claim_context

        original_signature = main_module._MATERIAL_PARSE_CLAIM_CONTEXT_CACHE_SIGNATURE
        original_materials = list(main_module._MATERIAL_PARSE_CLAIM_CONTEXT_CACHE_MATERIALS)
        original_jobs = list(main_module._MATERIAL_PARSE_CLAIM_CONTEXT_CACHE_JOBS)
        original_material_by_id = {
            key: dict(value)
            for key, value in main_module._MATERIAL_PARSE_CLAIM_CONTEXT_CACHE_MATERIAL_BY_ID.items()
        }
        original_material_index_by_id = dict(
            main_module._MATERIAL_PARSE_CLAIM_CONTEXT_CACHE_MATERIAL_INDEX_BY_ID
        )
        original_size_hint_by_material_id = dict(
            main_module._MATERIAL_PARSE_CLAIM_CONTEXT_CACHE_SIZE_HINT_BY_MATERIAL_ID
        )
        original_retry_due_at_by_job_id = dict(
            main_module._MATERIAL_PARSE_CLAIM_CONTEXT_CACHE_RETRY_DUE_AT_BY_JOB_ID
        )
        original_sort_key_by_job_id = {
            key: tuple(value)
            for key, value in main_module._MATERIAL_PARSE_CLAIM_CONTEXT_CACHE_SORT_KEY_BY_JOB_ID.items()
        }
        original_preview_priority_by_job_id = {
            key: tuple(value)
            for key, value in main_module._MATERIAL_PARSE_CLAIM_CONTEXT_CACHE_PREVIEW_PRIORITY_BY_JOB_ID.items()
        }
        original_candidate_indices = list(
            main_module._MATERIAL_PARSE_CLAIM_CONTEXT_CACHE_CANDIDATE_INDICES
        )
        original_candidate_indices_by_mode = {
            key: list(value)
            for key, value in main_module._MATERIAL_PARSE_CLAIM_CONTEXT_CACHE_CANDIDATE_INDICES_BY_MODE.items()
        }
        original_preview_candidate_indices_by_speed = {
            int(key): list(value)
            for key, value in (
                main_module._MATERIAL_PARSE_CLAIM_CONTEXT_CACHE_PREVIEW_CANDIDATE_INDICES_BY_SPEED.items()
            )
        }
        original_preview_candidate_indices_up_to_speed = {
            int(key): list(value)
            for key, value in (
                main_module._MATERIAL_PARSE_CLAIM_CONTEXT_CACHE_PREVIEW_CANDIDATE_INDICES_UP_TO_SPEED.items()
            )
        }
        materials_v1 = [
            {"id": "m1", "project_id": "p1", "material_type": "boq", "filename": "清单1.xlsx"}
        ]
        materials_v2 = [
            {"id": "m2", "project_id": "p2", "material_type": "boq", "filename": "清单2.xlsx"}
        ]
        jobs_v1 = [{"id": "j1", "material_id": "m1", "project_id": "p1", "parse_mode": "preview"}]
        jobs_v2 = [{"id": "j2", "material_id": "m2", "project_id": "p2", "parse_mode": "preview"}]
        try:
            main_module._MATERIAL_PARSE_CLAIM_CONTEXT_CACHE_SIGNATURE = None
            main_module._MATERIAL_PARSE_CLAIM_CONTEXT_CACHE_MATERIALS.clear()
            main_module._MATERIAL_PARSE_CLAIM_CONTEXT_CACHE_JOBS.clear()
            main_module._MATERIAL_PARSE_CLAIM_CONTEXT_CACHE_MATERIAL_BY_ID.clear()
            main_module._MATERIAL_PARSE_CLAIM_CONTEXT_CACHE_MATERIAL_INDEX_BY_ID.clear()
            main_module._MATERIAL_PARSE_CLAIM_CONTEXT_CACHE_SIZE_HINT_BY_MATERIAL_ID.clear()
            main_module._MATERIAL_PARSE_CLAIM_CONTEXT_CACHE_RETRY_DUE_AT_BY_JOB_ID.clear()
            main_module._MATERIAL_PARSE_CLAIM_CONTEXT_CACHE_SORT_KEY_BY_JOB_ID.clear()
            main_module._MATERIAL_PARSE_CLAIM_CONTEXT_CACHE_PREVIEW_PRIORITY_BY_JOB_ID.clear()
            main_module._MATERIAL_PARSE_CLAIM_CONTEXT_CACHE_CANDIDATE_INDICES.clear()
            main_module._MATERIAL_PARSE_CLAIM_CONTEXT_CACHE_CANDIDATE_INDICES_BY_MODE.clear()
            (
                main_module._MATERIAL_PARSE_CLAIM_CONTEXT_CACHE_PREVIEW_CANDIDATE_INDICES_BY_SPEED.clear()
            )
            (
                main_module._MATERIAL_PARSE_CLAIM_CONTEXT_CACHE_PREVIEW_CANDIDATE_INDICES_UP_TO_SPEED.clear()
            )
            with patch(
                "app.main._material_parse_claim_context_cache_enabled",
                return_value=True,
            ), patch(
                "app.main._material_parse_state_files_signature",
                side_effect=[
                    ((1, 1), (1, 1)),
                    ((1, 1), (1, 1)),
                    ((1, 1), (1, 1)),
                    ((2, 2), (2, 2)),
                    ((2, 2), (2, 2)),
                ],
            ), patch(
                "app.main._load_material_parse_state_snapshot",
                side_effect=[
                    ([dict(row) for row in materials_v1], [dict(job) for job in jobs_v1]),
                    ([dict(row) for row in materials_v2], [dict(job) for job in jobs_v2]),
                ],
            ) as mock_load_snapshot:
                (
                    materials1,
                    jobs1,
                    material_by_id1,
                    material_index_by_id1,
                    size_hint_by_material_id1,
                    retry_due_at_by_job_id1,
                    sort_key_by_job_id1,
                    preview_priority_by_job_id1,
                    candidate_indices1,
                    candidate_indices_by_mode1,
                    preview_candidate_indices_by_speed1,
                    preview_candidate_indices_up_to_speed1,
                    materials_changed1,
                    jobs_changed1,
                ) = _load_material_parse_claim_context()
                materials1[0]["id"] = "mutated"
                jobs1[0]["id"] = "mutated"
                material_by_id1["m1"]["id"] = "mutated"
                material_index_by_id1["m1"] = 99
                size_hint_by_material_id1["m1"] = 1
                retry_due_at_by_job_id1["j1"] = None
                sort_key_by_job_id1["j1"] = ("x", "y", "z")
                preview_priority_by_job_id1["j1"] = (9, 9, 9)
                (
                    materials2,
                    jobs2,
                    material_by_id2,
                    material_index_by_id2,
                    size_hint_by_material_id2,
                    retry_due_at_by_job_id2,
                    sort_key_by_job_id2,
                    preview_priority_by_job_id2,
                    candidate_indices2,
                    candidate_indices_by_mode2,
                    preview_candidate_indices_by_speed2,
                    preview_candidate_indices_up_to_speed2,
                    materials_changed2,
                    jobs_changed2,
                ) = _load_material_parse_claim_context()
                (
                    materials3,
                    jobs3,
                    material_by_id3,
                    material_index_by_id3,
                    size_hint_by_material_id3,
                    retry_due_at_by_job_id3,
                    sort_key_by_job_id3,
                    preview_priority_by_job_id3,
                    candidate_indices3,
                    candidate_indices_by_mode3,
                    preview_candidate_indices_by_speed3,
                    preview_candidate_indices_up_to_speed3,
                    materials_changed3,
                    jobs_changed3,
                ) = _load_material_parse_claim_context()
        finally:
            main_module._MATERIAL_PARSE_CLAIM_CONTEXT_CACHE_SIGNATURE = original_signature
            main_module._MATERIAL_PARSE_CLAIM_CONTEXT_CACHE_MATERIALS[:] = original_materials
            main_module._MATERIAL_PARSE_CLAIM_CONTEXT_CACHE_JOBS[:] = original_jobs
            main_module._MATERIAL_PARSE_CLAIM_CONTEXT_CACHE_MATERIAL_BY_ID.clear()
            main_module._MATERIAL_PARSE_CLAIM_CONTEXT_CACHE_MATERIAL_BY_ID.update(
                original_material_by_id
            )
            main_module._MATERIAL_PARSE_CLAIM_CONTEXT_CACHE_MATERIAL_INDEX_BY_ID.clear()
            main_module._MATERIAL_PARSE_CLAIM_CONTEXT_CACHE_MATERIAL_INDEX_BY_ID.update(
                original_material_index_by_id
            )
            main_module._MATERIAL_PARSE_CLAIM_CONTEXT_CACHE_SIZE_HINT_BY_MATERIAL_ID.clear()
            main_module._MATERIAL_PARSE_CLAIM_CONTEXT_CACHE_SIZE_HINT_BY_MATERIAL_ID.update(
                original_size_hint_by_material_id
            )
            main_module._MATERIAL_PARSE_CLAIM_CONTEXT_CACHE_RETRY_DUE_AT_BY_JOB_ID.clear()
            main_module._MATERIAL_PARSE_CLAIM_CONTEXT_CACHE_RETRY_DUE_AT_BY_JOB_ID.update(
                original_retry_due_at_by_job_id
            )
            main_module._MATERIAL_PARSE_CLAIM_CONTEXT_CACHE_SORT_KEY_BY_JOB_ID.clear()
            main_module._MATERIAL_PARSE_CLAIM_CONTEXT_CACHE_SORT_KEY_BY_JOB_ID.update(
                original_sort_key_by_job_id
            )
            main_module._MATERIAL_PARSE_CLAIM_CONTEXT_CACHE_PREVIEW_PRIORITY_BY_JOB_ID.clear()
            main_module._MATERIAL_PARSE_CLAIM_CONTEXT_CACHE_PREVIEW_PRIORITY_BY_JOB_ID.update(
                original_preview_priority_by_job_id
            )
            main_module._MATERIAL_PARSE_CLAIM_CONTEXT_CACHE_CANDIDATE_INDICES[
                :
            ] = original_candidate_indices
            main_module._MATERIAL_PARSE_CLAIM_CONTEXT_CACHE_CANDIDATE_INDICES_BY_MODE.clear()
            main_module._MATERIAL_PARSE_CLAIM_CONTEXT_CACHE_CANDIDATE_INDICES_BY_MODE.update(
                original_candidate_indices_by_mode
            )
            (
                main_module._MATERIAL_PARSE_CLAIM_CONTEXT_CACHE_PREVIEW_CANDIDATE_INDICES_BY_SPEED.clear()
            )
            (
                main_module._MATERIAL_PARSE_CLAIM_CONTEXT_CACHE_PREVIEW_CANDIDATE_INDICES_BY_SPEED.update(
                    original_preview_candidate_indices_by_speed
                )
            )
            (
                main_module._MATERIAL_PARSE_CLAIM_CONTEXT_CACHE_PREVIEW_CANDIDATE_INDICES_UP_TO_SPEED.clear()
            )
            (
                main_module._MATERIAL_PARSE_CLAIM_CONTEXT_CACHE_PREVIEW_CANDIDATE_INDICES_UP_TO_SPEED.update(
                    original_preview_candidate_indices_up_to_speed
                )
            )

        assert mock_load_snapshot.call_count == 2
        assert materials_changed1 is True
        assert jobs_changed1 is True
        assert materials_changed2 is False
        assert jobs_changed2 is False
        assert candidate_indices1 == [0]
        assert candidate_indices2 == [0]
        assert candidate_indices3 == [0]
        assert candidate_indices_by_mode1 == {"preview": [0]}
        assert candidate_indices_by_mode2 == {"preview": [0]}
        assert candidate_indices_by_mode3 == {"preview": [0]}
        assert preview_candidate_indices_by_speed1 == {0: [0]}
        assert preview_candidate_indices_by_speed2 == {0: [0]}
        assert preview_candidate_indices_by_speed3 == {0: [0]}
        assert preview_candidate_indices_up_to_speed1 == {0: [0], 1: [0]}
        assert preview_candidate_indices_up_to_speed2 == {0: [0], 1: [0]}
        assert preview_candidate_indices_up_to_speed3 == {0: [0], 1: [0]}
        assert size_hint_by_material_id2 == {"m1": 10**12}
        assert size_hint_by_material_id3 == {"m2": 10**12}
        assert retry_due_at_by_job_id2 == {}
        assert retry_due_at_by_job_id3 == {}
        assert sort_key_by_job_id2["j1"][2] == "j1"
        assert sort_key_by_job_id2["j1"][0] == sort_key_by_job_id2["j1"][1]
        assert sort_key_by_job_id3["j2"][2] == "j2"
        assert sort_key_by_job_id3["j2"][0] == sort_key_by_job_id3["j2"][1]
        assert preview_priority_by_job_id2 == {"j1": (0, 360, 2)}
        assert preview_priority_by_job_id3 == {"j2": (0, 360, 2)}
        assert material_index_by_id2 == {"m1": 0}
        assert material_index_by_id3 == {"m2": 0}
        assert materials3[0]["id"] == "m2"
        assert jobs3[0]["id"] == "j2"
        assert material_by_id2["m1"]["id"] == "m1"
        assert material_by_id3["m2"]["id"] == "m2"

    def test_load_material_parse_claim_context_view_reuses_cache_without_copy(self):
        from app import main as main_module
        from app.main import _load_material_parse_claim_context_view

        original_signature = main_module._MATERIAL_PARSE_CLAIM_CONTEXT_CACHE_SIGNATURE
        original_materials = list(main_module._MATERIAL_PARSE_CLAIM_CONTEXT_CACHE_MATERIALS)
        original_jobs = list(main_module._MATERIAL_PARSE_CLAIM_CONTEXT_CACHE_JOBS)
        original_material_by_id = {
            key: dict(value)
            for key, value in main_module._MATERIAL_PARSE_CLAIM_CONTEXT_CACHE_MATERIAL_BY_ID.items()
        }
        original_material_index_by_id = dict(
            main_module._MATERIAL_PARSE_CLAIM_CONTEXT_CACHE_MATERIAL_INDEX_BY_ID
        )
        original_size_hint_by_material_id = dict(
            main_module._MATERIAL_PARSE_CLAIM_CONTEXT_CACHE_SIZE_HINT_BY_MATERIAL_ID
        )
        original_retry_due_at_by_job_id = dict(
            main_module._MATERIAL_PARSE_CLAIM_CONTEXT_CACHE_RETRY_DUE_AT_BY_JOB_ID
        )
        original_sort_key_by_job_id = {
            key: tuple(value)
            for key, value in main_module._MATERIAL_PARSE_CLAIM_CONTEXT_CACHE_SORT_KEY_BY_JOB_ID.items()
        }
        original_preview_priority_by_job_id = {
            key: tuple(value)
            for key, value in main_module._MATERIAL_PARSE_CLAIM_CONTEXT_CACHE_PREVIEW_PRIORITY_BY_JOB_ID.items()
        }
        original_candidate_indices = list(
            main_module._MATERIAL_PARSE_CLAIM_CONTEXT_CACHE_CANDIDATE_INDICES
        )
        original_candidate_indices_by_mode = {
            key: list(value)
            for key, value in main_module._MATERIAL_PARSE_CLAIM_CONTEXT_CACHE_CANDIDATE_INDICES_BY_MODE.items()
        }
        original_preview_candidate_indices_by_speed = {
            int(key): list(value)
            for key, value in (
                main_module._MATERIAL_PARSE_CLAIM_CONTEXT_CACHE_PREVIEW_CANDIDATE_INDICES_BY_SPEED.items()
            )
        }
        original_preview_candidate_indices_up_to_speed = {
            int(key): list(value)
            for key, value in (
                main_module._MATERIAL_PARSE_CLAIM_CONTEXT_CACHE_PREVIEW_CANDIDATE_INDICES_UP_TO_SPEED.items()
            )
        }
        try:
            main_module._MATERIAL_PARSE_CLAIM_CONTEXT_CACHE_SIGNATURE = None
            main_module._MATERIAL_PARSE_CLAIM_CONTEXT_CACHE_MATERIALS.clear()
            main_module._MATERIAL_PARSE_CLAIM_CONTEXT_CACHE_JOBS.clear()
            main_module._MATERIAL_PARSE_CLAIM_CONTEXT_CACHE_MATERIAL_BY_ID.clear()
            main_module._MATERIAL_PARSE_CLAIM_CONTEXT_CACHE_MATERIAL_INDEX_BY_ID.clear()
            main_module._MATERIAL_PARSE_CLAIM_CONTEXT_CACHE_SIZE_HINT_BY_MATERIAL_ID.clear()
            main_module._MATERIAL_PARSE_CLAIM_CONTEXT_CACHE_RETRY_DUE_AT_BY_JOB_ID.clear()
            main_module._MATERIAL_PARSE_CLAIM_CONTEXT_CACHE_SORT_KEY_BY_JOB_ID.clear()
            main_module._MATERIAL_PARSE_CLAIM_CONTEXT_CACHE_PREVIEW_PRIORITY_BY_JOB_ID.clear()
            main_module._MATERIAL_PARSE_CLAIM_CONTEXT_CACHE_CANDIDATE_INDICES.clear()
            main_module._MATERIAL_PARSE_CLAIM_CONTEXT_CACHE_CANDIDATE_INDICES_BY_MODE.clear()
            (
                main_module._MATERIAL_PARSE_CLAIM_CONTEXT_CACHE_PREVIEW_CANDIDATE_INDICES_BY_SPEED.clear()
            )
            (
                main_module._MATERIAL_PARSE_CLAIM_CONTEXT_CACHE_PREVIEW_CANDIDATE_INDICES_UP_TO_SPEED.clear()
            )
            with patch(
                "app.main._material_parse_claim_context_cache_enabled",
                return_value=True,
            ), patch(
                "app.main._material_parse_state_files_signature",
                return_value=((1, 1), (1, 1)),
            ), patch(
                "app.main._load_material_parse_state_snapshot",
                return_value=(
                    [
                        {
                            "id": "m1",
                            "project_id": "p1",
                            "material_type": "boq",
                            "filename": "清单1.xlsx",
                        }
                    ],
                    [
                        {
                            "id": "j1",
                            "material_id": "m1",
                            "project_id": "p1",
                            "parse_mode": "preview",
                        }
                    ],
                ),
            ) as mock_load_snapshot:
                (
                    materials1,
                    jobs1,
                    material_by_id1,
                    material_index_by_id1,
                    size_hint_by_material_id1,
                    retry_due_at_by_job_id1,
                    sort_key_by_job_id1,
                    preview_priority_by_job_id1,
                    _candidate_indices1,
                    _candidate_indices_by_mode1,
                    _preview_candidate_indices_by_speed1,
                    preview_candidate_indices_up_to_speed1,
                    *_,
                ) = _load_material_parse_claim_context_view()
                (
                    materials2,
                    jobs2,
                    material_by_id2,
                    material_index_by_id2,
                    size_hint_by_material_id2,
                    retry_due_at_by_job_id2,
                    sort_key_by_job_id2,
                    preview_priority_by_job_id2,
                    _candidate_indices2,
                    _candidate_indices_by_mode2,
                    _preview_candidate_indices_by_speed2,
                    preview_candidate_indices_up_to_speed2,
                    *_,
                ) = _load_material_parse_claim_context_view()
        finally:
            main_module._MATERIAL_PARSE_CLAIM_CONTEXT_CACHE_SIGNATURE = original_signature
            main_module._MATERIAL_PARSE_CLAIM_CONTEXT_CACHE_MATERIALS[:] = original_materials
            main_module._MATERIAL_PARSE_CLAIM_CONTEXT_CACHE_JOBS[:] = original_jobs
            main_module._MATERIAL_PARSE_CLAIM_CONTEXT_CACHE_MATERIAL_BY_ID.clear()
            main_module._MATERIAL_PARSE_CLAIM_CONTEXT_CACHE_MATERIAL_BY_ID.update(
                original_material_by_id
            )
            main_module._MATERIAL_PARSE_CLAIM_CONTEXT_CACHE_MATERIAL_INDEX_BY_ID.clear()
            main_module._MATERIAL_PARSE_CLAIM_CONTEXT_CACHE_MATERIAL_INDEX_BY_ID.update(
                original_material_index_by_id
            )
            main_module._MATERIAL_PARSE_CLAIM_CONTEXT_CACHE_SIZE_HINT_BY_MATERIAL_ID.clear()
            main_module._MATERIAL_PARSE_CLAIM_CONTEXT_CACHE_SIZE_HINT_BY_MATERIAL_ID.update(
                original_size_hint_by_material_id
            )
            main_module._MATERIAL_PARSE_CLAIM_CONTEXT_CACHE_RETRY_DUE_AT_BY_JOB_ID.clear()
            main_module._MATERIAL_PARSE_CLAIM_CONTEXT_CACHE_RETRY_DUE_AT_BY_JOB_ID.update(
                original_retry_due_at_by_job_id
            )
            main_module._MATERIAL_PARSE_CLAIM_CONTEXT_CACHE_SORT_KEY_BY_JOB_ID.clear()
            main_module._MATERIAL_PARSE_CLAIM_CONTEXT_CACHE_SORT_KEY_BY_JOB_ID.update(
                original_sort_key_by_job_id
            )
            main_module._MATERIAL_PARSE_CLAIM_CONTEXT_CACHE_PREVIEW_PRIORITY_BY_JOB_ID.clear()
            main_module._MATERIAL_PARSE_CLAIM_CONTEXT_CACHE_PREVIEW_PRIORITY_BY_JOB_ID.update(
                original_preview_priority_by_job_id
            )
            main_module._MATERIAL_PARSE_CLAIM_CONTEXT_CACHE_CANDIDATE_INDICES[
                :
            ] = original_candidate_indices
            main_module._MATERIAL_PARSE_CLAIM_CONTEXT_CACHE_CANDIDATE_INDICES_BY_MODE.clear()
            main_module._MATERIAL_PARSE_CLAIM_CONTEXT_CACHE_CANDIDATE_INDICES_BY_MODE.update(
                original_candidate_indices_by_mode
            )
            (
                main_module._MATERIAL_PARSE_CLAIM_CONTEXT_CACHE_PREVIEW_CANDIDATE_INDICES_BY_SPEED.clear()
            )
            (
                main_module._MATERIAL_PARSE_CLAIM_CONTEXT_CACHE_PREVIEW_CANDIDATE_INDICES_BY_SPEED.update(
                    original_preview_candidate_indices_by_speed
                )
            )
            (
                main_module._MATERIAL_PARSE_CLAIM_CONTEXT_CACHE_PREVIEW_CANDIDATE_INDICES_UP_TO_SPEED.clear()
            )
            (
                main_module._MATERIAL_PARSE_CLAIM_CONTEXT_CACHE_PREVIEW_CANDIDATE_INDICES_UP_TO_SPEED.update(
                    original_preview_candidate_indices_up_to_speed
                )
            )

        assert mock_load_snapshot.call_count == 1
        assert materials1 is materials2
        assert jobs1 is jobs2
        assert material_by_id1 is material_by_id2
        assert material_index_by_id1 is material_index_by_id2
        assert size_hint_by_material_id1 is size_hint_by_material_id2
        assert retry_due_at_by_job_id1 is retry_due_at_by_job_id2
        assert sort_key_by_job_id1 is sort_key_by_job_id2
        assert preview_priority_by_job_id1 is preview_priority_by_job_id2
        assert preview_candidate_indices_up_to_speed1 is preview_candidate_indices_up_to_speed2

    @patch("app.main.load_submissions")
    def test_load_material_parse_project_stage_ranks_reuses_cache_until_signature_changes(
        self,
        mock_load_submissions,
    ):
        from app import main as main_module
        from app.main import _load_material_parse_project_stage_ranks

        original_signature = main_module._MATERIAL_PARSE_PROJECT_STAGE_CACHE_SIGNATURE
        original_ranks = dict(main_module._MATERIAL_PARSE_PROJECT_STAGE_CACHE_RANKS)
        mock_load_submissions.side_effect = [
            [
                {
                    "id": "s1",
                    "project_id": "p1",
                    "text": "示例文本",
                    "report": {"scoring_status": "pending"},
                }
            ],
            [
                {
                    "id": "s2",
                    "project_id": "p1",
                    "text": "示例文本",
                    "report": {"scoring_status": "scored", "total_score": 88},
                }
            ],
        ]
        try:
            main_module._MATERIAL_PARSE_PROJECT_STAGE_CACHE_SIGNATURE = None
            main_module._MATERIAL_PARSE_PROJECT_STAGE_CACHE_RANKS.clear()
            with patch(
                "app.main._material_parse_project_stage_cache_enabled", return_value=True
            ), patch(
                "app.main._material_parse_state_file_signature",
                side_effect=[(1, 1), (1, 1), (1, 1), (2, 2), (2, 2)],
            ):
                ranks1 = _load_material_parse_project_stage_ranks({"p1"})
                ranks2 = _load_material_parse_project_stage_ranks({"p1"})
                ranks3 = _load_material_parse_project_stage_ranks({"p1"})
        finally:
            main_module._MATERIAL_PARSE_PROJECT_STAGE_CACHE_SIGNATURE = original_signature
            main_module._MATERIAL_PARSE_PROJECT_STAGE_CACHE_RANKS.clear()
            main_module._MATERIAL_PARSE_PROJECT_STAGE_CACHE_RANKS.update(original_ranks)

        assert mock_load_submissions.call_count == 2
        assert ranks1 == {"p1": 0}
        assert ranks2 == {"p1": 0}
        assert ranks3 == {"p1": 1}

    def test_load_material_parse_job_priority_contexts_reuses_cache_until_signature_changes(self):
        from app import main as main_module
        from app.main import _load_material_parse_job_priority_contexts

        original_signature = main_module._MATERIAL_PARSE_PRIORITY_CONTEXT_CACHE_SIGNATURE
        original_cache = dict(main_module._MATERIAL_PARSE_PRIORITY_CONTEXT_CACHE)
        jobs = [
            {
                "id": "j1",
                "material_id": "m1",
                "project_id": "p1",
                "material_type": "boq",
                "filename": "工程量清单.xlsx",
                "status": "queued",
                "parse_mode": "preview",
                "created_at": "2026-03-09T00:00:00+00:00",
                "updated_at": "2026-03-09T00:00:00+00:00",
            }
        ]
        material_by_id = {
            "m1": {
                "id": "m1",
                "project_id": "p1",
                "material_type": "boq",
                "filename": "工程量清单.xlsx",
                "path": "/tmp/工程量清单.xlsx",
            }
        }
        try:
            main_module._MATERIAL_PARSE_PRIORITY_CONTEXT_CACHE_SIGNATURE = None
            main_module._MATERIAL_PARSE_PRIORITY_CONTEXT_CACHE.clear()
            with patch("app.main._material_parse_priority_cache_enabled", return_value=True), patch(
                "app.main._material_parse_priority_snapshot_signature",
                side_effect=["sig-1", "sig-1", "sig-2"],
            ):
                contexts1 = _load_material_parse_job_priority_contexts(
                    jobs,
                    material_by_id,
                    {"p1": 0},
                )
                jobs[0]["parse_mode"] = "full"
                contexts2 = _load_material_parse_job_priority_contexts(
                    jobs,
                    material_by_id,
                    {"p1": 0},
                )
                contexts3 = _load_material_parse_job_priority_contexts(
                    jobs,
                    material_by_id,
                    {"p1": 0},
                )
        finally:
            main_module._MATERIAL_PARSE_PRIORITY_CONTEXT_CACHE_SIGNATURE = original_signature
            main_module._MATERIAL_PARSE_PRIORITY_CONTEXT_CACHE.clear()
            main_module._MATERIAL_PARSE_PRIORITY_CONTEXT_CACHE.update(original_cache)

        assert contexts1["j1"] == contexts2["j1"]
        assert contexts1["j1"] != contexts3["j1"]

    @patch("app.main._build_material_parse_job_priority_context")
    def test_load_material_parse_job_priority_contexts_scopes_to_requested_jobs(
        self,
        mock_build_context,
    ):
        from app.main import _load_material_parse_job_priority_contexts

        mock_build_context.side_effect = [
            ("p1", "m1", "boq", 0, 0, 0, 1, 0, 360, 0, 1, 1, "", "", ""),
        ]
        jobs = [
            {"id": "j1", "material_id": "m1", "project_id": "p1", "parse_mode": "preview"},
            {"id": "j2", "material_id": "m2", "project_id": "p1", "parse_mode": "full"},
        ]

        contexts = _load_material_parse_job_priority_contexts(
            [jobs[0]],
            {
                "m1": {
                    "id": "m1",
                    "project_id": "p1",
                    "material_type": "boq",
                    "filename": "a.xlsx",
                },
                "m2": {
                    "id": "m2",
                    "project_id": "p1",
                    "material_type": "tender_qa",
                    "filename": "b.pdf",
                },
            },
            {"p1": 0},
        )

        assert list(contexts) == ["j1"]
        mock_build_context.assert_called_once()

    def test_load_material_parse_job_priority_contexts_view_reuses_cache_without_copy(self):
        from app import main as main_module
        from app.main import _load_material_parse_job_priority_contexts_view

        original_signature = main_module._MATERIAL_PARSE_PRIORITY_CONTEXT_CACHE_SIGNATURE
        original_cache = dict(main_module._MATERIAL_PARSE_PRIORITY_CONTEXT_CACHE)
        try:
            main_module._MATERIAL_PARSE_PRIORITY_CONTEXT_CACHE_SIGNATURE = None
            main_module._MATERIAL_PARSE_PRIORITY_CONTEXT_CACHE.clear()
            jobs = [{"id": "j1", "material_id": "m1", "project_id": "p1", "parse_mode": "preview"}]
            with patch(
                "app.main._material_parse_priority_cache_enabled",
                return_value=True,
            ), patch(
                "app.main._material_parse_priority_snapshot_signature",
                return_value=(((1, 1), (1, 1)), (1, 1)),
            ), patch(
                "app.main._build_material_parse_job_priority_context",
                return_value=("p1", "m1", "boq", 0, 0, 0, 1, 0, 360, 0, 1, 1, "", "", ""),
            ) as mock_build_context:
                contexts1 = _load_material_parse_job_priority_contexts_view(
                    jobs,
                    {"m1": {"id": "m1", "project_id": "p1", "material_type": "boq"}},
                    {"p1": 0},
                )
                contexts2 = _load_material_parse_job_priority_contexts_view(
                    jobs,
                    {"m1": {"id": "m1", "project_id": "p1", "material_type": "boq"}},
                    {"p1": 0},
                )
        finally:
            main_module._MATERIAL_PARSE_PRIORITY_CONTEXT_CACHE_SIGNATURE = original_signature
            main_module._MATERIAL_PARSE_PRIORITY_CONTEXT_CACHE.clear()
            main_module._MATERIAL_PARSE_PRIORITY_CONTEXT_CACHE.update(original_cache)

        assert mock_build_context.call_count == 1
        assert contexts1 is contexts2
        assert list(contexts2) == ["j1"]

    @patch("app.main._material_parse_priority_cache_enabled", return_value=False)
    @patch("app.main._material_parse_job_sort_key")
    @patch("app.main._material_parse_preview_speed_rank")
    @patch("app.main._material_parse_row_size_hint")
    def test_load_material_parse_job_priority_contexts_view_reuses_precomputed_preview_priority(
        self,
        mock_row_size_hint,
        mock_preview_speed_rank,
        mock_sort_key,
        _mock_priority_cache_enabled,
    ):
        from app.main import _load_material_parse_job_priority_contexts_view

        contexts = _load_material_parse_job_priority_contexts_view(
            [
                {
                    "id": "j1",
                    "material_id": "m1",
                    "project_id": "p1",
                    "parse_mode": "preview",
                    "status": "queued",
                    "created_at": "2026-03-09T00:00:00+00:00",
                    "updated_at": "2026-03-09T00:00:00+00:00",
                }
            ],
            {
                "m1": {
                    "id": "m1",
                    "project_id": "p1",
                    "material_type": "boq",
                    "filename": "清单.xlsx",
                    "path": "",
                }
            },
            {"p1": 0},
            {"j1": (0, 360, 0)},
            {"m1": 2048},
            {"j1": ("u", "c", "j1")},
        )

        assert contexts["j1"][7:10] == (0, 360, 0)
        assert contexts["j1"][11] == 2048
        assert contexts["j1"][12:15] == ("u", "c", "j1")
        mock_preview_speed_rank.assert_not_called()
        mock_row_size_hint.assert_not_called()
        mock_sort_key.assert_not_called()

    @patch("app.main._load_material_parse_project_stage_ranks")
    def test_build_material_parse_project_stage_rank_scopes_to_candidate_jobs(
        self,
        mock_load_stage_ranks,
    ):
        from app.main import _build_material_parse_project_stage_rank

        mock_load_stage_ranks.return_value = {"p1": 0}
        ranks = _build_material_parse_project_stage_rank(
            {
                "m1": {"id": "m1", "project_id": "p1"},
                "m2": {"id": "m2", "project_id": "p2"},
            },
            [
                {"id": "j1", "material_id": "m1", "project_id": "p1"},
            ],
        )

        assert ranks == {"p1": 0}
        mock_load_stage_ranks.assert_called_once_with({"p1"})

    def test_collect_material_parse_active_window_state_cleans_expired_and_reports_quotas(self):
        from app import main as main_module
        from app.main import _collect_material_parse_active_window_state

        original_active_projects = dict(main_module._MATERIAL_PARSE_ACTIVE_PROJECTS)
        original_active_project_types = dict(main_module._MATERIAL_PARSE_ACTIVE_PROJECT_TYPES)
        original_active_project_claims = dict(main_module._MATERIAL_PARSE_ACTIVE_PROJECT_CLAIMS)
        original_active_project_type_claims = dict(
            main_module._MATERIAL_PARSE_ACTIVE_PROJECT_TYPE_CLAIMS
        )
        try:
            main_module._MATERIAL_PARSE_ACTIVE_PROJECTS.clear()
            main_module._MATERIAL_PARSE_ACTIVE_PROJECTS.update(
                {"p1": 108.0, "p2": 108.0, "expired": 99.0}
            )
            main_module._MATERIAL_PARSE_ACTIVE_PROJECT_TYPES.clear()
            main_module._MATERIAL_PARSE_ACTIVE_PROJECT_TYPES.update(
                {
                    ("p1", "boq"): 108.0,
                    ("p2", "tender_qa"): 108.0,
                    ("expired", "drawing"): 99.0,
                }
            )
            main_module._MATERIAL_PARSE_ACTIVE_PROJECT_CLAIMS.clear()
            main_module._MATERIAL_PARSE_ACTIVE_PROJECT_CLAIMS.update(
                {
                    "p1": 1,
                    "p2": main_module.DEFAULT_MATERIAL_PARSE_ACTIVE_PROJECT_WINDOW_MAX_CLAIMS + 1,
                    "expired": 1,
                }
            )
            main_module._MATERIAL_PARSE_ACTIVE_PROJECT_TYPE_CLAIMS.clear()
            main_module._MATERIAL_PARSE_ACTIVE_PROJECT_TYPE_CLAIMS.update(
                {
                    ("p1", "boq"): 1,
                    (
                        "p2",
                        "tender_qa",
                    ): main_module.DEFAULT_MATERIAL_PARSE_ACTIVE_PROJECT_TYPE_WINDOW_MAX_CLAIMS + 1,
                    ("expired", "drawing"): 1,
                }
            )
            with patch("app.main.time.monotonic", return_value=100.0):
                (
                    active_project_ids,
                    active_project_type_keys,
                    active_project_quota_exhausted,
                    active_project_type_quota_exhausted,
                ) = _collect_material_parse_active_window_state()
        finally:
            main_module._MATERIAL_PARSE_ACTIVE_PROJECTS.clear()
            main_module._MATERIAL_PARSE_ACTIVE_PROJECTS.update(original_active_projects)
            main_module._MATERIAL_PARSE_ACTIVE_PROJECT_TYPES.clear()
            main_module._MATERIAL_PARSE_ACTIVE_PROJECT_TYPES.update(original_active_project_types)
            main_module._MATERIAL_PARSE_ACTIVE_PROJECT_CLAIMS.clear()
            main_module._MATERIAL_PARSE_ACTIVE_PROJECT_CLAIMS.update(original_active_project_claims)
            main_module._MATERIAL_PARSE_ACTIVE_PROJECT_TYPE_CLAIMS.clear()
            main_module._MATERIAL_PARSE_ACTIVE_PROJECT_TYPE_CLAIMS.update(
                original_active_project_type_claims
            )

        assert active_project_ids == {"p1"}
        assert active_project_type_keys == {("p1", "boq")}
        assert active_project_quota_exhausted == 1
        assert active_project_type_quota_exhausted == 1

    def test_touch_material_parse_active_window_updates_project_and_type_in_one_pass(self):
        from app import main as main_module
        from app.main import _touch_material_parse_active_window

        original_active_projects = dict(main_module._MATERIAL_PARSE_ACTIVE_PROJECTS)
        original_active_project_types = dict(main_module._MATERIAL_PARSE_ACTIVE_PROJECT_TYPES)
        original_active_project_claims = dict(main_module._MATERIAL_PARSE_ACTIVE_PROJECT_CLAIMS)
        original_active_project_type_claims = dict(
            main_module._MATERIAL_PARSE_ACTIVE_PROJECT_TYPE_CLAIMS
        )
        try:
            main_module._MATERIAL_PARSE_ACTIVE_PROJECTS.clear()
            main_module._MATERIAL_PARSE_ACTIVE_PROJECT_TYPES.clear()
            main_module._MATERIAL_PARSE_ACTIVE_PROJECT_CLAIMS.clear()
            main_module._MATERIAL_PARSE_ACTIVE_PROJECT_TYPE_CLAIMS.clear()
            with patch("app.main.time.monotonic", return_value=100.0) as mock_monotonic:
                _touch_material_parse_active_window("p1", "boq")
            expected_expires_at = (
                100.0 + main_module.DEFAULT_MATERIAL_PARSE_ACTIVE_PROJECT_WINDOW_SECONDS
            )
            active_projects = dict(main_module._MATERIAL_PARSE_ACTIVE_PROJECTS)
            active_project_types = dict(main_module._MATERIAL_PARSE_ACTIVE_PROJECT_TYPES)
            active_project_claims = dict(main_module._MATERIAL_PARSE_ACTIVE_PROJECT_CLAIMS)
            active_project_type_claims = dict(
                main_module._MATERIAL_PARSE_ACTIVE_PROJECT_TYPE_CLAIMS
            )
        finally:
            main_module._MATERIAL_PARSE_ACTIVE_PROJECTS.clear()
            main_module._MATERIAL_PARSE_ACTIVE_PROJECTS.update(original_active_projects)
            main_module._MATERIAL_PARSE_ACTIVE_PROJECT_TYPES.clear()
            main_module._MATERIAL_PARSE_ACTIVE_PROJECT_TYPES.update(original_active_project_types)
            main_module._MATERIAL_PARSE_ACTIVE_PROJECT_CLAIMS.clear()
            main_module._MATERIAL_PARSE_ACTIVE_PROJECT_CLAIMS.update(original_active_project_claims)
            main_module._MATERIAL_PARSE_ACTIVE_PROJECT_TYPE_CLAIMS.clear()
            main_module._MATERIAL_PARSE_ACTIVE_PROJECT_TYPE_CLAIMS.update(
                original_active_project_type_claims
            )

        assert active_projects == {"p1": expected_expires_at}
        assert active_project_types == {("p1", "boq"): expected_expires_at}
        assert active_project_claims == {"p1": 1}
        assert active_project_type_claims == {("p1", "boq"): 1}
        assert mock_monotonic.call_count == 1
        assert "expired" not in main_module._MATERIAL_PARSE_ACTIVE_PROJECTS
        assert ("expired", "drawing") not in main_module._MATERIAL_PARSE_ACTIVE_PROJECT_TYPES

    def test_touch_material_parse_active_window_uses_precomputed_normalized_material_type(self):
        from app import main as main_module
        from app.main import _touch_material_parse_active_window

        original_active_projects = dict(main_module._MATERIAL_PARSE_ACTIVE_PROJECTS)
        original_active_project_types = dict(main_module._MATERIAL_PARSE_ACTIVE_PROJECT_TYPES)
        original_active_project_claims = dict(main_module._MATERIAL_PARSE_ACTIVE_PROJECT_CLAIMS)
        original_active_project_type_claims = dict(
            main_module._MATERIAL_PARSE_ACTIVE_PROJECT_TYPE_CLAIMS
        )
        try:
            main_module._MATERIAL_PARSE_ACTIVE_PROJECTS.clear()
            main_module._MATERIAL_PARSE_ACTIVE_PROJECT_TYPES.clear()
            main_module._MATERIAL_PARSE_ACTIVE_PROJECT_CLAIMS.clear()
            main_module._MATERIAL_PARSE_ACTIVE_PROJECT_TYPE_CLAIMS.clear()
            with patch("app.main._normalize_material_type") as mock_normalize:
                with patch("app.main.time.monotonic", return_value=100.0):
                    _touch_material_parse_active_window(
                        "p1",
                        "工程量清单.xlsx",
                        normalized_material_type="boq",
                    )
            active_projects = dict(main_module._MATERIAL_PARSE_ACTIVE_PROJECTS)
            active_project_types = dict(main_module._MATERIAL_PARSE_ACTIVE_PROJECT_TYPES)
        finally:
            main_module._MATERIAL_PARSE_ACTIVE_PROJECTS.clear()
            main_module._MATERIAL_PARSE_ACTIVE_PROJECTS.update(original_active_projects)
            main_module._MATERIAL_PARSE_ACTIVE_PROJECT_TYPES.clear()
            main_module._MATERIAL_PARSE_ACTIVE_PROJECT_TYPES.update(original_active_project_types)
            main_module._MATERIAL_PARSE_ACTIVE_PROJECT_CLAIMS.clear()
            main_module._MATERIAL_PARSE_ACTIVE_PROJECT_CLAIMS.update(original_active_project_claims)
            main_module._MATERIAL_PARSE_ACTIVE_PROJECT_TYPE_CLAIMS.clear()
            main_module._MATERIAL_PARSE_ACTIVE_PROJECT_TYPE_CLAIMS.update(
                original_active_project_type_claims
            )

        assert active_projects == {
            "p1": 100.0 + main_module.DEFAULT_MATERIAL_PARSE_ACTIVE_PROJECT_WINDOW_SECONDS
        }
        assert active_project_types == {
            ("p1", "boq"): 100.0 + main_module.DEFAULT_MATERIAL_PARSE_ACTIVE_PROJECT_WINDOW_SECONDS
        }
        mock_normalize.assert_not_called()

    def test_load_material_parse_jobs_summary_reuses_cache_until_signature_changes(self):
        from app import main as main_module
        from app.main import _load_material_parse_jobs_summary_cached

        original_signature = main_module._MATERIAL_PARSE_JOBS_SUMMARY_CACHE_SIGNATURE
        original_filtered = {
            key: [dict(job) for job in value]
            for key, value in main_module._MATERIAL_PARSE_JOBS_SUMMARY_CACHE_FILTERED.items()
        }
        original_summary = {
            key: dict(value)
            for key, value in main_module._MATERIAL_PARSE_JOBS_SUMMARY_CACHE_SUMMARY.items()
        }
        jobs_v1 = [
            {
                "id": "j1",
                "project_id": "p1",
                "material_id": "m1",
                "material_type": "boq",
                "filename": "工程量清单.xlsx",
                "status": "queued",
                "parse_backend": "local",
            },
            {
                "id": "j2",
                "project_id": "p1",
                "material_id": "m2",
                "material_type": "boq",
                "filename": "工程量清单.xlsx",
                "status": "parsed",
                "parse_backend": "gpt-5.4",
                "finished_at": "2026-03-09T00:02:03+00:00",
            },
        ]
        jobs_v2 = [
            {
                "id": "j3",
                "project_id": "p1",
                "material_id": "m3",
                "material_type": "boq",
                "filename": "工程量清单2.xlsx",
                "status": "failed",
                "parse_backend": "local",
            }
        ]
        try:
            main_module._MATERIAL_PARSE_JOBS_SUMMARY_CACHE_SIGNATURE = None
            main_module._MATERIAL_PARSE_JOBS_SUMMARY_CACHE_FILTERED.clear()
            main_module._MATERIAL_PARSE_JOBS_SUMMARY_CACHE_SUMMARY.clear()
            with patch(
                "app.main._material_parse_jobs_summary_cache_enabled",
                return_value=True,
            ), patch(
                "app.main._material_parse_state_file_signature",
                side_effect=[(1, 1), (1, 1), (1, 1), (2, 2), (2, 2)],
            ), patch(
                "app.main._load_material_parse_state_snapshot",
                side_effect=[
                    ([], [dict(job) for job in jobs_v1]),
                    ([], [dict(job) for job in jobs_v2]),
                ],
            ) as mock_load_snapshot:
                filtered1, summary1 = _load_material_parse_jobs_summary_cached("p1")
                filtered1[0]["status"] = "mutated"
                summary1["backlog"] = 99
                filtered2, summary2 = _load_material_parse_jobs_summary_cached("p1")
                filtered3, summary3 = _load_material_parse_jobs_summary_cached("p1")
        finally:
            main_module._MATERIAL_PARSE_JOBS_SUMMARY_CACHE_SIGNATURE = original_signature
            main_module._MATERIAL_PARSE_JOBS_SUMMARY_CACHE_FILTERED.clear()
            main_module._MATERIAL_PARSE_JOBS_SUMMARY_CACHE_FILTERED.update(original_filtered)
            main_module._MATERIAL_PARSE_JOBS_SUMMARY_CACHE_SUMMARY.clear()
            main_module._MATERIAL_PARSE_JOBS_SUMMARY_CACHE_SUMMARY.update(original_summary)

        assert mock_load_snapshot.call_count == 2
        assert [job["id"] for job in filtered1] == ["j1", "j2"]
        assert [job["id"] for job in filtered2] == ["j1", "j2"]
        assert [job["id"] for job in filtered3] == ["j3"]
        assert summary2["backlog"] == 1
        assert summary2["latest_finished_filename"] == "工程量清单.xlsx"
        assert summary3["failed_jobs"] == 1
        assert filtered2[0]["status"] == "queued"

    def test_load_material_parse_status_materials_payload_reuses_cache_until_signature_changes(
        self,
    ):
        from app import main as main_module
        from app.main import _load_material_parse_status_materials_payload

        original_signature = main_module._MATERIAL_PARSE_STATUS_MATERIALS_CACHE_SIGNATURE
        original_cache = {
            key: copy.deepcopy(value)
            for key, value in main_module._MATERIAL_PARSE_STATUS_MATERIALS_CACHE.items()
        }
        materials_v1 = [
            {
                "id": "m1",
                "project_id": "p1",
                "material_type": "boq",
                "filename": "工程量清单.xlsx",
                "path": "/tmp/工程量清单.xlsx",
                "parse_status": "queued",
                "parse_backend": "queued",
                "parse_phase": None,
                "created_at": "2026-03-09T00:00:00+00:00",
            }
        ]
        materials_v2 = [
            {
                "id": "m2",
                "project_id": "p1",
                "material_type": "boq",
                "filename": "工程量清单2.xlsx",
                "path": "/tmp/工程量清单2.xlsx",
                "parse_status": "parsed",
                "parse_backend": "local",
                "parse_phase": "full",
                "parse_ready_for_gate": True,
                "created_at": "2026-03-09T00:00:00+00:00",
            }
        ]
        jobs_v1 = [
            {
                "id": "j1",
                "material_id": "m1",
                "project_id": "p1",
                "material_type": "boq",
                "filename": "工程量清单.xlsx",
                "status": "queued",
                "parse_mode": "preview",
                "parse_backend": "local_preview",
            }
        ]
        jobs_v2 = [
            {
                "id": "j2",
                "material_id": "m2",
                "project_id": "p1",
                "material_type": "boq",
                "filename": "工程量清单2.xlsx",
                "status": "parsed",
                "parse_mode": "full",
                "parse_backend": "local",
            }
        ]
        jobs_summary_v1 = (
            [dict(job) for job in jobs_v1],
            {"backlog": 1},
        )
        jobs_summary_v2 = (
            [dict(job) for job in jobs_v2],
            {"backlog": 0},
        )
        try:
            main_module._MATERIAL_PARSE_STATUS_MATERIALS_CACHE_SIGNATURE = None
            main_module._MATERIAL_PARSE_STATUS_MATERIALS_CACHE.clear()
            with patch(
                "app.main._material_parse_status_materials_cache_enabled",
                return_value=True,
            ), patch(
                "app.main._material_parse_state_files_signature",
                side_effect=[
                    ((1, 1), (1, 1)),
                    ((1, 1), (1, 1)),
                    ((1, 1), (1, 1)),
                    ((2, 2), (2, 2)),
                    ((2, 2), (2, 2)),
                ],
            ), patch(
                "app.main._load_material_parse_state_snapshot",
                side_effect=[
                    ([dict(row) for row in materials_v1], []),
                    ([dict(row) for row in materials_v2], []),
                ],
            ) as mock_load_snapshot, patch(
                "app.main._build_material_parse_jobs_summary",
                side_effect=[jobs_summary_v1, jobs_summary_v2],
            ) as mock_build_jobs_summary:
                payload1 = _load_material_parse_status_materials_payload("p1")
                payload1["enriched_materials"][0]["parse_stage_label"] = "mutated"
                payload2 = _load_material_parse_status_materials_payload("p1")
                payload3 = _load_material_parse_status_materials_payload("p1")
        finally:
            main_module._MATERIAL_PARSE_STATUS_MATERIALS_CACHE_SIGNATURE = original_signature
            main_module._MATERIAL_PARSE_STATUS_MATERIALS_CACHE.clear()
            main_module._MATERIAL_PARSE_STATUS_MATERIALS_CACHE.update(original_cache)

        assert mock_load_snapshot.call_count == 2
        assert mock_build_jobs_summary.call_count == 2
        assert payload1["materials_total"] == 1
        assert payload2["materials_total"] == 1
        assert payload3["materials_total"] == 1
        assert payload2["enriched_materials"][0]["parse_stage_label"] != "mutated"
        assert payload2["enriched_materials"][0]["queue_position"] == 1
        assert payload3["enriched_materials"][0]["filename"] == "工程量清单2.xlsx"
        assert payload3["parsed_materials"] == 1

    def test_load_material_parse_status_core_payload_reuses_cache_until_signature_changes(self):
        from app import main as main_module
        from app.main import _load_material_parse_status_core_payload

        original_signature = main_module._MATERIAL_PARSE_STATUS_CORE_CACHE_SIGNATURE
        original_cache = {
            key: copy.deepcopy(value)
            for key, value in main_module._MATERIAL_PARSE_STATUS_CORE_CACHE.items()
        }
        jobs_summary_v1 = (
            [
                {
                    "id": "j1",
                    "project_id": "p1",
                    "material_id": "m1",
                    "status": "queued",
                }
            ],
            {
                "total_jobs": 1,
                "status_counts": {"queued": 1},
                "backlog": 1,
                "failed_jobs": 0,
                "gpt_jobs": 0,
                "gpt_failed_jobs": 0,
                "gpt_ratio": 0.0,
                "latest_finished_at": None,
                "latest_finished_filename": None,
            },
        )
        jobs_summary_v2 = (
            [
                {
                    "id": "j2",
                    "project_id": "p1",
                    "material_id": "m2",
                    "status": "parsed",
                }
            ],
            {
                "total_jobs": 1,
                "status_counts": {"parsed": 1},
                "backlog": 0,
                "failed_jobs": 0,
                "gpt_jobs": 0,
                "gpt_failed_jobs": 0,
                "gpt_ratio": 0.0,
                "latest_finished_at": "2026-03-09T00:02:03+00:00",
                "latest_finished_filename": "工程量清单2.xlsx",
            },
        )
        materials_payload_v1 = {
            "materials": [{"id": "m1", "project_id": "p1", "material_type": "boq"}],
            "enriched_materials": [
                {
                    "id": "m1",
                    "project_id": "p1",
                    "material_type": "boq",
                    "parse_stage_label": "排队中",
                }
            ],
            "materials_total": 1,
            "parsed_materials": 0,
            "failed_materials": 0,
            "processing_materials": 0,
            "queued_materials": 1,
            "previewed_materials": 0,
        }
        materials_payload_v2 = {
            "materials": [{"id": "m2", "project_id": "p1", "material_type": "boq"}],
            "enriched_materials": [
                {
                    "id": "m2",
                    "project_id": "p1",
                    "material_type": "boq",
                    "parse_stage_label": "已解析（local）",
                }
            ],
            "materials_total": 1,
            "parsed_materials": 1,
            "failed_materials": 0,
            "processing_materials": 0,
            "queued_materials": 0,
            "previewed_materials": 0,
        }
        boq_summary_v1 = {"boq_saved_row_count": 0}
        boq_summary_v2 = {"boq_saved_row_count": 12}
        try:
            main_module._MATERIAL_PARSE_STATUS_CORE_CACHE_SIGNATURE = None
            main_module._MATERIAL_PARSE_STATUS_CORE_CACHE.clear()
            with patch(
                "app.main._material_parse_status_core_cache_enabled",
                return_value=True,
            ), patch(
                "app.main._material_parse_state_files_signature",
                side_effect=[
                    ((1, 1), (1, 1)),
                    ((1, 1), (1, 1)),
                    ((1, 1), (1, 1)),
                    ((2, 2), (2, 2)),
                    ((2, 2), (2, 2)),
                ],
            ), patch(
                "app.main._build_material_parse_jobs_summary",
                side_effect=[jobs_summary_v1, jobs_summary_v2],
            ) as mock_jobs_summary, patch(
                "app.main._load_material_parse_status_materials_payload",
                side_effect=[materials_payload_v1, materials_payload_v2],
            ) as mock_materials_payload, patch(
                "app.main._build_boq_parse_status_summary",
                side_effect=[boq_summary_v1, boq_summary_v2],
            ) as mock_boq_summary:
                payload1 = _load_material_parse_status_core_payload("p1")
                payload1["summary"]["backlog"] = 99
                payload1["materials"][0]["parse_stage_label"] = "mutated"
                payload2 = _load_material_parse_status_core_payload("p1")
                payload3 = _load_material_parse_status_core_payload("p1")
        finally:
            main_module._MATERIAL_PARSE_STATUS_CORE_CACHE_SIGNATURE = original_signature
            main_module._MATERIAL_PARSE_STATUS_CORE_CACHE.clear()
            main_module._MATERIAL_PARSE_STATUS_CORE_CACHE.update(original_cache)

        assert mock_jobs_summary.call_count == 2
        assert mock_materials_payload.call_count == 2
        assert mock_boq_summary.call_count == 2
        assert payload2["summary"]["backlog"] == 1
        assert payload2["materials"][0]["parse_stage_label"] == "排队中"
        assert payload3["summary"]["boq_saved_row_count"] == 12
        assert payload3["summary"]["parsed_materials"] == 1
        assert payload3["jobs"][0]["id"] == "j2"

    @patch("app.main._invalidate_material_index_cache")
    @patch("app.main.save_materials")
    @patch("app.main.save_material_parse_jobs")
    @patch("app.main.load_submissions")
    @patch("app.main.load_materials")
    @patch("app.main.load_material_parse_jobs")
    def test_claim_next_material_parse_job_preview_express_worker_claims_low_ocr_drawing_preview(
        self,
        mock_load_jobs,
        mock_load_materials,
        mock_load_submissions,
        mock_save_jobs,
        mock_save_materials,
        _mock_invalidate,
    ):
        from app.main import _claim_next_material_parse_job

        mock_load_submissions.return_value = []
        mock_load_materials.return_value = [
            {
                "id": "m-drawing",
                "project_id": "p1",
                "material_type": "drawing",
                "filename": "总图.pdf",
                "path": "/tmp/总图.pdf",
                "created_at": "2026-03-09T00:00:00+00:00",
                "updated_at": "2026-03-09T00:00:00+00:00",
                "parse_status": "queued",
                "parse_backend": "queued",
            },
            {
                "id": "m-tender",
                "project_id": "p1",
                "material_type": "tender_qa",
                "filename": "招标文件.pdf",
                "path": "/tmp/招标文件.pdf",
                "created_at": "2026-03-09T00:00:00+00:00",
                "updated_at": "2026-03-09T00:00:00+00:00",
                "parse_status": "queued",
                "parse_backend": "queued",
            },
        ]
        mock_load_jobs.return_value = [
            {
                "id": "j-tender-preview",
                "material_id": "m-tender",
                "project_id": "p1",
                "material_type": "tender_qa",
                "filename": "招标文件.pdf",
                "status": "queued",
                "parse_mode": "preview",
                "created_at": "2026-03-09T00:00:00+00:00",
                "updated_at": "2026-03-09T00:00:00+00:00",
            },
            {
                "id": "j-drawing-preview",
                "material_id": "m-drawing",
                "project_id": "p1",
                "material_type": "drawing",
                "filename": "总图.pdf",
                "status": "queued",
                "parse_mode": "preview",
                "created_at": "2026-03-09T00:00:00+00:00",
                "updated_at": "2026-03-09T00:00:00+00:00",
            },
        ]

        claimed = _claim_next_material_parse_job(
            preferred_parse_mode="preview",
            allow_fallback=False,
            max_preview_speed_rank=1,
        )

        assert claimed is not None
        assert claimed["id"] == "j-drawing-preview"
        saved_jobs = mock_save_jobs.call_args[0][0]
        prioritized = next(job for job in saved_jobs if job["id"] == "j-drawing-preview")
        assert prioritized["status"] == "processing"
        saved_rows = mock_save_materials.call_args[0][0]
        prioritized_row = next(row for row in saved_rows if row["id"] == "m-drawing")
        assert prioritized_row["parse_status"] == "processing"
        assert prioritized_row["parse_backend"] == "local_preview"

    @patch("app.main._invalidate_material_index_cache")
    @patch("app.main.save_materials")
    @patch("app.main.save_material_parse_jobs")
    @patch("app.main.load_submissions")
    @patch("app.main.load_materials")
    @patch("app.main.load_material_parse_jobs")
    def test_claim_next_material_parse_job_preview_prefers_lighter_tabular_preview_tasks(
        self,
        mock_load_jobs,
        mock_load_materials,
        mock_load_submissions,
        mock_save_jobs,
        mock_save_materials,
        _mock_invalidate,
    ):
        from app.main import _claim_next_material_parse_job

        mock_load_submissions.return_value = []
        mock_load_materials.return_value = [
            {
                "id": "m-tender",
                "project_id": "p1",
                "material_type": "tender_qa",
                "filename": "招标文件.pdf",
                "path": "/tmp/招标文件.pdf",
                "created_at": "2026-03-09T00:00:00+00:00",
                "updated_at": "2026-03-09T00:00:00+00:00",
                "parse_status": "queued",
                "parse_backend": "queued",
            },
            {
                "id": "m-boq",
                "project_id": "p1",
                "material_type": "boq",
                "filename": "工程量清单.xlsx",
                "path": "/tmp/工程量清单.xlsx",
                "created_at": "2026-03-09T00:00:00+00:00",
                "updated_at": "2026-03-09T00:00:00+00:00",
                "parse_status": "queued",
                "parse_backend": "queued",
            },
        ]
        mock_load_jobs.return_value = [
            {
                "id": "j-tender-preview",
                "material_id": "m-tender",
                "project_id": "p1",
                "material_type": "tender_qa",
                "filename": "招标文件.pdf",
                "status": "queued",
                "parse_mode": "preview",
                "created_at": "2026-03-09T00:00:00+00:00",
                "updated_at": "2026-03-09T00:00:00+00:00",
            },
            {
                "id": "j-boq-preview",
                "material_id": "m-boq",
                "project_id": "p1",
                "material_type": "boq",
                "filename": "工程量清单.xlsx",
                "status": "queued",
                "parse_mode": "preview",
                "created_at": "2026-03-09T00:00:00+00:00",
                "updated_at": "2026-03-09T00:00:00+00:00",
            },
        ]

        claimed = _claim_next_material_parse_job(preferred_parse_mode="preview")

        assert claimed is not None
        assert claimed["id"] == "j-boq-preview"
        saved_jobs = mock_save_jobs.call_args[0][0]
        prioritized = next(job for job in saved_jobs if job["id"] == "j-boq-preview")
        assert prioritized["status"] == "processing"
        saved_rows = mock_save_materials.call_args[0][0]
        prioritized_row = next(row for row in saved_rows if row["id"] == "m-boq")
        assert prioritized_row["parse_status"] == "processing"
        assert prioritized_row["parse_backend"] == "local_preview"

    @patch("app.main._schedule_project_material_rebuild")
    @patch("app.main._notify_material_parse_workers")
    @patch("app.main._invalidate_material_index_cache")
    @patch("app.main.save_materials")
    @patch("app.main.save_material_parse_jobs")
    @patch("app.main.load_materials")
    @patch("app.main.load_material_parse_jobs")
    def test_complete_material_parse_job_enqueues_full_parse_after_preview(
        self,
        mock_load_jobs,
        mock_load_materials,
        mock_save_jobs,
        mock_save_materials,
        mock_invalidate,
        mock_notify_workers,
        mock_schedule_rebuild,
    ):
        from app.main import _complete_material_parse_job

        mock_load_jobs.return_value = [
            {
                "id": "j-preview",
                "material_id": "m1",
                "project_id": "p1",
                "material_type": "tender_qa",
                "filename": "招标文件.pdf",
                "status": "processing",
                "attempt": 0,
                "parse_mode": "preview",
                "created_at": "2026-03-30T00:00:00+00:00",
                "updated_at": "2026-03-30T00:00:00+00:00",
                "started_at": "2026-03-30T00:00:01+00:00",
            }
        ]
        mock_load_materials.return_value = [
            {
                "id": "m1",
                "project_id": "p1",
                "material_type": "tender_qa",
                "filename": "招标文件.pdf",
                "path": "/tmp/招标文件.pdf",
                "created_at": "2026-03-30T00:00:00+00:00",
                "updated_at": "2026-03-30T00:00:00+00:00",
                "parse_status": "processing",
                "parse_backend": "local_preview",
                "parse_phase": None,
                "parse_ready_for_gate": False,
                "job_id": "j-preview",
            }
        ]

        _complete_material_parse_job(
            "j-preview",
            {
                "project_id": "p1",
                "parse_backend": "local_preview",
                "parse_phase": "preview",
                "parse_ready_for_gate": False,
                "parse_confidence": 0.61,
                "parse_finished_at": "2026-03-30T00:00:02+00:00",
                "parse_version": "v3-gpt-async",
                "structured_summary": {"structured_quality_score": 0.61},
                "parsed_text": "预解析文本",
                "parsed_chars": 1200,
                "parsed_chunks": ["预解析文本"],
                "numeric_terms_norm": ["120"],
                "lexical_terms": ["工期"],
            },
            failed=False,
            followup_parse_mode="full",
        )

        saved_jobs = mock_save_jobs.call_args[0][0]
        preview_job = next(job for job in saved_jobs if job["id"] == "j-preview")
        followup_job = next(job for job in saved_jobs if job["id"] != "j-preview")
        assert preview_job["status"] == "parsed"
        assert preview_job["parse_mode"] == "preview"
        assert followup_job["status"] == "queued"
        assert followup_job["parse_mode"] == "full"
        assert followup_job["followup_from_preview"] is True

        saved_rows = mock_save_materials.call_args[0][0]
        saved_row = saved_rows[0]
        assert saved_row["parse_status"] == "parsed"
        assert saved_row["parse_phase"] == "preview"
        assert saved_row["parse_ready_for_gate"] is False
        assert saved_row["job_id"] == followup_job["id"]
        mock_invalidate.assert_called_once_with("p1")
        mock_notify_workers.assert_called_once()
        mock_schedule_rebuild.assert_not_called()

    @patch("app.main._invalidate_material_index_cache")
    @patch("app.main.save_materials")
    @patch("app.main.save_material_parse_jobs")
    @patch("app.main.load_submissions")
    @patch("app.main.load_materials")
    @patch("app.main.load_material_parse_jobs")
    def test_claim_next_material_parse_job_prefers_followup_full_after_preview(
        self,
        mock_load_jobs,
        mock_load_materials,
        mock_load_submissions,
        mock_save_jobs,
        mock_save_materials,
        _mock_invalidate,
    ):
        from app.main import _claim_next_material_parse_job

        mock_load_submissions.return_value = []
        mock_load_materials.return_value = [
            {
                "id": "m-followup",
                "project_id": "p1",
                "material_type": "boq",
                "filename": "工程量清单.pdf",
                "path": "/tmp/followup.pdf",
                "created_at": "2026-03-09T00:00:00+00:00",
                "updated_at": "2026-03-09T00:00:00+00:00",
                "parse_status": "queued",
                "parse_backend": "queued",
                "parse_phase": "preview",
                "parse_ready_for_gate": False,
            },
            {
                "id": "m-regular",
                "project_id": "p2",
                "material_type": "boq",
                "filename": "工程量清单.pdf",
                "path": "/tmp/regular.pdf",
                "created_at": "2026-03-09T00:00:00+00:00",
                "updated_at": "2026-03-09T00:00:00+00:00",
                "parse_status": "queued",
                "parse_backend": "queued",
            },
        ]
        mock_load_jobs.return_value = [
            {
                "id": "j-regular-full",
                "material_id": "m-regular",
                "project_id": "p2",
                "material_type": "boq",
                "filename": "工程量清单.pdf",
                "status": "queued",
                "parse_mode": "full",
                "followup_from_preview": False,
                "created_at": "2026-03-09T00:00:00+00:00",
                "updated_at": "2026-03-09T00:00:00+00:00",
            },
            {
                "id": "j-followup-full",
                "material_id": "m-followup",
                "project_id": "p1",
                "material_type": "boq",
                "filename": "工程量清单.pdf",
                "status": "queued",
                "parse_mode": "full",
                "followup_from_preview": True,
                "created_at": "2026-03-09T00:00:01+00:00",
                "updated_at": "2026-03-09T00:00:01+00:00",
            },
        ]

        claimed = _claim_next_material_parse_job()

        assert claimed is not None
        assert claimed["id"] == "j-followup-full"
        saved_jobs = mock_save_jobs.call_args[0][0]
        prioritized = next(job for job in saved_jobs if job["id"] == "j-followup-full")
        assert prioritized["status"] == "processing"
        saved_rows = mock_save_materials.call_args[0][0]
        prioritized_row = next(row for row in saved_rows if row["id"] == "m-followup")
        assert prioritized_row["job_id"] == "j-followup-full"

    @patch("app.main._invalidate_material_index_cache")
    @patch("app.main.save_materials")
    @patch("app.main.save_material_parse_jobs")
    @patch("app.main.load_submissions")
    @patch("app.main.load_materials")
    @patch("app.main.load_material_parse_jobs")
    def test_claim_next_material_parse_job_prefers_followup_full_for_same_material(
        self,
        mock_load_jobs,
        mock_load_materials,
        mock_load_submissions,
        mock_save_jobs,
        mock_save_materials,
        _mock_invalidate,
    ):
        from app.main import _claim_next_material_parse_job

        mock_load_submissions.return_value = []
        mock_load_materials.return_value = [
            {
                "id": "m-same",
                "project_id": "p1",
                "material_type": "boq",
                "filename": "工程量清单A.pdf",
                "path": "/tmp/same.pdf",
                "created_at": "2026-03-09T00:00:00+00:00",
                "updated_at": "2026-03-09T00:00:00+00:00",
                "parse_status": "queued",
                "parse_backend": "queued",
                "parse_phase": "preview",
                "parse_ready_for_gate": False,
            },
            {
                "id": "m-other",
                "project_id": "p1",
                "material_type": "boq",
                "filename": "工程量清单B.pdf",
                "path": "/tmp/other.pdf",
                "created_at": "2026-03-09T00:00:00+00:00",
                "updated_at": "2026-03-09T00:00:00+00:00",
                "parse_status": "queued",
                "parse_backend": "queued",
                "parse_phase": "preview",
                "parse_ready_for_gate": False,
            },
        ]
        mock_load_jobs.return_value = [
            {
                "id": "j-other-followup-full",
                "material_id": "m-other",
                "project_id": "p1",
                "material_type": "boq",
                "filename": "工程量清单B.pdf",
                "status": "queued",
                "parse_mode": "full",
                "followup_from_preview": True,
                "created_at": "2026-03-09T00:00:00+00:00",
                "updated_at": "2026-03-09T00:00:00+00:00",
            },
            {
                "id": "j-same-followup-full",
                "material_id": "m-same",
                "project_id": "p1",
                "material_type": "boq",
                "filename": "工程量清单A.pdf",
                "status": "queued",
                "parse_mode": "full",
                "followup_from_preview": True,
                "created_at": "2026-03-09T00:00:01+00:00",
                "updated_at": "2026-03-09T00:00:01+00:00",
            },
        ]

        claimed = _claim_next_material_parse_job(
            preferred_project_id="p1",
            preferred_material_id="m-same",
        )

        assert claimed is not None
        assert claimed["id"] == "j-same-followup-full"
        saved_jobs = mock_save_jobs.call_args[0][0]
        prioritized = next(job for job in saved_jobs if job["id"] == "j-same-followup-full")
        assert prioritized["status"] == "processing"
        saved_rows = mock_save_materials.call_args[0][0]
        prioritized_row = next(row for row in saved_rows if row["id"] == "m-same")
        assert prioritized_row["job_id"] == "j-same-followup-full"

    @patch("app.main._complete_material_parse_job")
    @patch("app.main._parse_material_record_payload")
    @patch("app.main.load_materials")
    def test_process_material_parse_job_reuses_cached_material_parse_result(
        self,
        mock_load_materials,
        mock_parse_payload,
        mock_complete_job,
    ):
        from app.main import _compute_material_content_hash, _process_material_parse_job

        content_hash = _compute_material_content_hash(b"same material content")
        mock_load_materials.return_value = [
            {
                "id": "m-target",
                "project_id": "p1",
                "material_type": "drawing",
                "filename": "总图-新.pdf",
                "path": "/tmp/总图-新.pdf",
                "content_hash": content_hash,
                "parse_status": "processing",
                "parse_backend": "queued",
                "job_id": "j-target",
            },
            {
                "id": "m-cache",
                "project_id": "p2",
                "material_type": "drawing",
                "filename": "总图-历史.pdf",
                "path": "/tmp/总图-历史.pdf",
                "content_hash": content_hash,
                "parse_status": "parsed",
                "parse_backend": "local",
                "parse_phase": "full",
                "parse_ready_for_gate": True,
                "parse_confidence": 0.88,
                "parse_version": "v3-gpt-async",
                "parsed_text": "历史全文解析结果",
                "parsed_chars": 9,
                "parsed_chunks": ["历史全文解析结果"],
                "numeric_terms_norm": ["120"],
                "lexical_terms": ["工期"],
                "structured_summary": {"structured_quality_score": 0.88},
                "drawing_structured_summary": {"structured_quality_score": 0.88},
                "updated_at": "2026-03-30T01:00:00+00:00",
            },
        ]

        _process_material_parse_job(
            {
                "id": "j-target",
                "material_id": "m-target",
                "project_id": "p1",
                "material_type": "drawing",
                "filename": "总图-新.pdf",
                "status": "processing",
                "parse_mode": "full",
            }
        )

        mock_parse_payload.assert_not_called()
        mock_complete_job.assert_called_once()
        args, kwargs = mock_complete_job.call_args
        assert args[0] == "j-target"
        assert args[1]["parsed_text"] == "历史全文解析结果"
        assert args[1]["parse_ready_for_gate"] is True
        assert args[1]["project_id"] == "p1"
        assert kwargs["failed"] is False
        assert kwargs["followup_parse_mode"] is None

    def test_build_material_parse_runtime_details_marks_previewed_backfill_state(self):
        from app.main import _build_material_parse_runtime_details

        details = _build_material_parse_runtime_details(
            {
                "id": "m1",
                "material_type": "tender_qa",
                "filename": "招标文件.pdf",
                "parse_status": "parsed",
                "parse_backend": "local_preview",
                "parse_phase": "preview",
                "parse_ready_for_gate": False,
            },
            active_job={
                "id": "j-full",
                "status": "queued",
                "parse_mode": "full",
            },
            queue_position=2,
        )

        assert details["parse_effective_status"] == "previewed"
        assert "预解析完成" in str(details["parse_stage_label"])
        assert "后台会继续补全全文" in str(details["parse_note"])
        assert details["parse_route_label"] == "本地极速预解析，后台补全全文"

    @patch("app.main.load_materials")
    def test_build_material_quality_snapshot_keeps_preview_parse_out_of_gate_ready_counts(
        self,
        mock_load_materials,
    ):
        from app.main import _build_material_quality_snapshot, _invalidate_material_index_cache

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as handle:
            handle.write(b"%PDF-1.4 preview data")
            temp_path = handle.name
        try:
            mock_load_materials.return_value = [
                {
                    "id": "m-preview",
                    "project_id": "p-preview",
                    "material_type": "tender_qa",
                    "filename": "招标文件.pdf",
                    "path": temp_path,
                    "created_at": "2026-03-30T00:00:00+00:00",
                    "updated_at": "2026-03-30T00:00:00+00:00",
                    "parse_status": "parsed",
                    "parse_backend": "local_preview",
                    "parse_phase": "preview",
                    "parse_ready_for_gate": False,
                    "parsed_text": "项目名称 工期 质量 安全文明 " * 60,
                    "parsed_chars": 1800,
                    "parsed_chunks": ["chunk-a", "chunk-b"],
                    "structured_summary": {"structured_quality_score": 0.55},
                }
            ]
            _invalidate_material_index_cache("p-preview")
            snapshot = _build_material_quality_snapshot("p-preview")
            assert snapshot["previewed_files"] == 1
            assert snapshot["parsed_ok_files"] == 0
            assert snapshot["total_parsed_chars"] == 0
            assert snapshot["parse_status_by_type"]["tender_qa"]["previewed"] == 1
        finally:
            os.unlink(temp_path)
            _invalidate_material_index_cache("p-preview")

    @patch("app.main.logger.warning")
    @patch("app.main.threading.Thread")
    def test_stop_timeout_keeps_worker_reference_to_avoid_duplicate_worker(
        self,
        mock_thread_cls,
        mock_logger_warning,
    ):
        from app import main as main_module

        worker = MagicMock()
        worker.is_alive.return_value = True
        original_worker = main_module._MATERIAL_PARSE_WORKER
        original_workers = list(main_module._MATERIAL_PARSE_WORKERS)
        original_event_state = main_module._MATERIAL_PARSE_STOP_EVENT.is_set()
        main_module._MATERIAL_PARSE_WORKER = worker
        main_module._MATERIAL_PARSE_WORKERS = [worker]
        try:
            main_module._stop_material_parse_worker()
            assert main_module._MATERIAL_PARSE_WORKER is worker

            main_module._start_material_parse_worker()

            worker.join.assert_called_once_with(timeout=2.0)
            mock_thread_cls.assert_not_called()
            mock_logger_warning.assert_called_once()
            assert main_module._MATERIAL_PARSE_WORKER is worker
        finally:
            main_module._MATERIAL_PARSE_WORKER = original_worker
            main_module._MATERIAL_PARSE_WORKERS = original_workers
            if original_event_state:
                main_module._MATERIAL_PARSE_STOP_EVENT.set()
            else:
                main_module._MATERIAL_PARSE_STOP_EVENT.clear()


class TestMaterialParsePerformanceGuards:
    @patch("app.main._call_gpt_material_parser")
    @patch("app.main._render_pdf_page_pngs_for_gpt")
    def test_augment_tender_summary_skips_pdf_page_vision_when_local_summary_is_mid_strong(
        self,
        mock_render_pages,
        mock_call_gpt,
    ):
        local_summary = {
            "structured_quality_score": 0.62,
            "section_titles": [
                "第一章 招标公告",
                "第二章 投标人须知",
                "第三章 评标办法",
            ],
            "scoring_point_terms": ["工期", "质量", "安全文明"],
            "mandatory_clause_terms": ["不得缺少专项方案", "必须响应关键节点"],
            "top_numeric_terms": ["90", "100", "120"],
        }
        parsed_text = "项目名称 工期 质量 安全文明 BIM 深化 关键节点 " * 120
        mock_call_gpt.return_value = (
            True,
            {
                "section_titles": ["第四章 合同"],
                "scoring_point_terms": ["加分项"],
                "mandatory_clause_terms": ["必须响应关键节点"],
                "numeric_constraints": ["120"],
                "focused_dimensions": ["09"],
                "parse_confidence": 0.71,
            },
            "",
        )

        with patch("app.main.get_openai_api_key", return_value="sk-test"):
            merged, backend, confidence, error = app_main._augment_tender_summary_with_gpt(
                b"%PDF-1.4",
                "招标文件正文.pdf",
                parsed_text,
                local_summary,
            )

        assert backend == "hybrid"
        assert error == ""
        assert confidence == 0.71
        assert merged["gpt_page_vision_skip_reason"] == "local_summary_text_only"
        mock_render_pages.assert_not_called()
        mock_call_gpt.assert_called_once()

    @patch("app.main._call_gpt_material_parser")
    @patch("app.main._render_pdf_page_pngs_for_gpt")
    def test_augment_tender_summary_uses_signal_excerpt_for_text_only_gpt(
        self,
        mock_render_pages,
        mock_call_gpt,
    ):
        local_summary = {
            "structured_quality_score": 0.62,
            "section_titles": [
                "第一章 招标公告",
                "第二章 投标人须知",
                "第三章 评标办法",
            ],
            "scoring_point_terms": ["工期", "质量", "安全文明"],
            "mandatory_clause_terms": ["不得缺少专项方案", "必须响应关键节点"],
            "top_numeric_terms": ["90", "100", "120"],
        }
        noisy_text = "\n".join(
            [
                "项目管理例会纪要 与评标无关",
                "第三章 评标办法",
                "评分细则 工期120天 质量90分 安全文明100分",
                "投标文件必须响应关键节点 不得缺少专项方案",
                "普通叙述 行政沟通 材料报审",
            ]
            * 24
        )
        mock_call_gpt.return_value = (
            True,
            {
                "section_titles": ["第四章 合同"],
                "scoring_point_terms": ["加分项"],
                "mandatory_clause_terms": ["必须响应关键节点"],
                "numeric_constraints": ["120"],
                "focused_dimensions": ["09"],
                "parse_confidence": 0.71,
            },
            "",
        )

        merged, backend, confidence, error = app_main._augment_tender_summary_with_gpt(
            b"%PDF-1.4",
            "招标文件正文.pdf",
            noisy_text,
            local_summary,
        )

        assert backend == "hybrid"
        assert error == ""
        assert confidence == 0.71
        assert merged["gpt_page_vision_skip_reason"] == "local_summary_text_only"
        mock_render_pages.assert_not_called()
        prompt = mock_call_gpt.call_args.args[0]
        assert "章节线索：第一章 招标公告、第二章 投标人须知、第三章 评标办法" in prompt
        assert "评分线索：工期、质量、安全文明" in prompt
        assert "强制条款：不得缺少专项方案、必须响应关键节点" in prompt
        assert "评分细则工期120天质量90分安全文明100分" in prompt
        assert "投标文件必须响应关键节点不得缺少专项方案" in prompt
        assert "项目管理例会纪要 与评标无关" not in prompt
        assert "普通叙述 行政沟通 材料报审" not in prompt

    @patch("app.main._call_gpt_material_parser")
    @patch("app.main._render_pdf_page_pngs_for_gpt")
    def test_augment_tender_summary_uses_adaptive_pdf_page_budget(
        self,
        mock_render_pages,
        mock_call_gpt,
    ):
        local_summary = {
            "structured_quality_score": 0.55,
            "section_titles": [
                "第一章 招标公告",
                "第二章 投标人须知",
                "第三章 评标办法",
            ],
            "scoring_point_terms": ["工期", "质量"],
            "mandatory_clause_terms": ["不得缺少专项方案", "必须响应关键节点"],
            "top_numeric_terms": ["90", "100", "120"],
        }
        parsed_text = "项目名称 工期 质量 安全文明 BIM 深化 关键节点 " * 220
        mock_render_pages.return_value = []
        mock_call_gpt.return_value = (False, {}, "gpt_parse_failed")

        with patch("app.main.get_openai_api_key", return_value="sk-test"):
            _merged, backend, confidence, error = app_main._augment_tender_summary_with_gpt(
                b"%PDF-1.4",
                "招标文件正文.pdf",
                parsed_text,
                local_summary,
            )

        assert backend == "local"
        assert confidence == 0.55
        assert error == "gpt_parse_failed"
        mock_render_pages.assert_called_once()
        assert mock_render_pages.call_args.kwargs["max_pages"] == 4
        assert mock_render_pages.call_args.kwargs["always_include_first_pages"] == 1
        assert "工期" in mock_render_pages.call_args.kwargs["preferred_tokens"]
        assert "必须响应关键节点" in mock_render_pages.call_args.kwargs["preferred_tokens"]

    @patch("app.main._call_gpt_material_parser")
    @patch("app.main._render_pdf_page_pngs_for_gpt")
    def test_augment_drawing_summary_skips_pdf_page_vision_when_local_summary_is_mid_strong(
        self,
        mock_render_pages,
        mock_call_gpt,
    ):
        local_summary = {
            "detected_format": "pdf",
            "structured_quality_score": 0.64,
            "structured_terms": [
                "总平面",
                "节点详图",
                "综合排布",
                "消防",
                "净高复核",
                "机电",
            ],
            "discipline_keywords": ["建筑", "机电"],
            "sheet_type_tags": ["节点详图/大样"],
            "risk_keywords": ["洞口", "高支模"],
            "top_numeric_terms": ["600", "1200", "3.5", "45"],
        }
        mock_call_gpt.return_value = (
            True,
            {
                "discipline": "建筑",
                "sheet_type": "节点详图/大样",
                "layout_tags": ["总平面"],
                "space_tags": ["机房"],
                "component_tags": ["梁"],
                "dimension_markers": ["1200"],
                "risk_tags": ["洞口"],
                "constraint_terms": ["净高复核"],
                "numeric_constraints": ["3.5"],
                "focused_dimensions": ["05"],
                "parse_confidence": 0.72,
            },
            "",
        )

        merged, backend, confidence, error = app_main._augment_drawing_summary_with_gpt(
            b"%PDF-1.4",
            "总图.pdf",
            "总平面 节点详图 机电 综合排布 标高 轴线 机房 梁 板 柱 " * 80,
            local_summary,
        )

        assert backend == "hybrid"
        assert error == ""
        assert confidence == 0.72
        assert merged["gpt_page_vision_skip_reason"] == "local_summary_text_only"
        mock_render_pages.assert_not_called()
        kwargs = mock_call_gpt.call_args.kwargs
        assert kwargs.get("image_bytes") is None

    @patch("app.main._call_gpt_material_parser")
    @patch("app.main._render_pdf_page_pngs_for_gpt")
    def test_augment_drawing_summary_skips_pdf_page_vision_when_local_summary_is_relaxed_mid_strong(
        self,
        mock_render_pages,
        mock_call_gpt,
    ):
        local_summary = {
            "detected_format": "pdf",
            "structured_quality_score": 0.54,
            "structured_terms": ["总平面", "节点详图", "综合排布", "消防", "净高复核"],
            "discipline_keywords": ["建筑"],
            "sheet_type_tags": ["节点详图/大样"],
            "risk_keywords": ["洞口"],
            "top_numeric_terms": ["600", "1200", "3.5"],
            "layout_tags": ["轴网"],
            "component_tags": ["梁", "板"],
        }
        mock_call_gpt.return_value = (
            True,
            {
                "discipline": "建筑",
                "sheet_type": "节点详图/大样",
                "layout_tags": ["总平面"],
                "space_tags": ["机房"],
                "component_tags": ["梁"],
                "dimension_markers": ["1200"],
                "risk_tags": ["洞口"],
                "constraint_terms": ["净高复核"],
                "numeric_constraints": ["3.5"],
                "focused_dimensions": ["05"],
                "parse_confidence": 0.69,
            },
            "",
        )

        merged, backend, confidence, error = app_main._augment_drawing_summary_with_gpt(
            b"%PDF-1.4",
            "总图.pdf",
            "总平面 节点详图 综合排布 消防 净高复核 轴网 梁 板 洞口 标高 1200 600 " * 24,
            local_summary,
        )

        assert backend == "hybrid"
        assert error == ""
        assert confidence == 0.69
        assert merged["gpt_page_vision_skip_reason"] == "local_summary_text_only"
        mock_render_pages.assert_not_called()
        kwargs = mock_call_gpt.call_args.kwargs
        assert kwargs.get("image_bytes") is None

    @patch("app.main._call_gpt_material_parser")
    @patch("app.main._render_pdf_page_pngs_for_gpt")
    def test_augment_drawing_summary_uses_signal_excerpt_for_text_only_gpt(
        self,
        mock_render_pages,
        mock_call_gpt,
    ):
        local_summary = {
            "detected_format": "pdf",
            "structured_quality_score": 0.64,
            "structured_terms": [
                "总平面",
                "节点详图",
                "综合排布",
                "消防",
                "净高复核",
                "机电",
            ],
            "discipline_keywords": ["建筑", "机电"],
            "sheet_type_tags": ["节点详图/大样"],
            "risk_keywords": ["洞口", "高支模"],
            "top_numeric_terms": ["600", "1200", "3.5", "45"],
        }
        noisy_text = "\n".join(
            [
                "会议纪要 与 图纸解析无关的说明文字",
                "节点详图 机电综合排布 轴网A-B 标高3.5 洞口预留 1200 600",
                "普通叙述 行政沟通 材料报审",
                "总平面 消防分区 净高复核 梁板关系 45 600 1200",
                "随手记录 与当前图纸无关",
            ]
            * 10
        )
        mock_call_gpt.return_value = (
            True,
            {
                "discipline": "建筑",
                "sheet_type": "节点详图/大样",
                "layout_tags": ["总平面"],
                "space_tags": ["机房"],
                "component_tags": ["梁"],
                "dimension_markers": ["1200"],
                "risk_tags": ["洞口"],
                "constraint_terms": ["净高复核"],
                "numeric_constraints": ["3.5"],
                "focused_dimensions": ["05"],
                "parse_confidence": 0.72,
            },
            "",
        )

        merged, backend, confidence, error = app_main._augment_drawing_summary_with_gpt(
            b"%PDF-1.4",
            "总图.pdf",
            noisy_text,
            local_summary,
        )

        assert backend == "hybrid"
        assert error == ""
        assert confidence == 0.72
        assert merged["gpt_page_vision_skip_reason"] == "local_summary_text_only"
        mock_render_pages.assert_not_called()
        prompt = mock_call_gpt.call_args.args[0]
        assert "识别专业：建筑、机电" in prompt
        assert "图纸类型：节点详图/大样" in prompt
        assert "节点详图机电综合排布轴网A-B 标高3.5 洞口预留 1200 600" in prompt
        assert "总平面消防分区净高复核梁板关系 45 600 1200" in prompt
        assert "会议纪要 与 图纸解析无关的说明文字" not in prompt
        assert "普通叙述 行政沟通 材料报审" not in prompt

    @patch("app.main._call_gpt_material_parser")
    @patch("app.main._collect_pdf_page_candidates_for_gpt")
    @patch("app.main._render_pdf_page_pngs_for_gpt")
    def test_augment_drawing_summary_prefers_high_signal_pdf_page_when_page_vision_is_needed(
        self,
        mock_render_pages,
        mock_collect_candidates,
        mock_call_gpt,
    ):
        local_summary = {
            "detected_format": "pdf",
            "structured_quality_score": 0.51,
            "structured_terms": ["总平面", "节点详图", "综合排布", "消防", "净高复核"],
            "discipline_keywords": ["建筑", "机电"],
            "sheet_type_tags": ["节点详图/大样"],
            "risk_keywords": ["洞口", "高支模"],
            "top_numeric_terms": ["600", "1200", "3.5", "45"],
            "layout_tags": ["轴网"],
            "component_tags": ["梁", "板"],
        }
        mock_collect_candidates.return_value = [
            {"page_no": 1, "text": "封面 图纸目录", "score": 3.0},
            {"page_no": 3, "text": "节点详图 机电综合排布", "score": 5.2},
        ]
        mock_render_pages.return_value = []
        mock_call_gpt.return_value = (False, {}, "gpt_parse_failed")

        _merged, backend, confidence, error = app_main._augment_drawing_summary_with_gpt(
            b"%PDF-1.4",
            "总图.pdf",
            "总平面 节点详图 机电 综合排布 标高 轴线 机房 梁 板 柱 " * 12,
            local_summary,
        )

        assert backend == "local"
        assert confidence == 0.51
        assert error == "gpt_parse_failed"
        mock_collect_candidates.assert_called_once()
        mock_render_pages.assert_called_once()
        kwargs = mock_render_pages.call_args.kwargs
        assert kwargs["max_pages"] == 1
        assert kwargs["always_include_first_pages"] == 0
        assert "总平面" in kwargs["preferred_tokens"]
        assert "节点详图/大样" in kwargs["preferred_tokens"]
        assert "机电" in kwargs["preferred_tokens"]
        assert "洞口" in kwargs["preferred_tokens"]
        assert "600" in kwargs["preferred_tokens"]
        assert kwargs["page_candidates"] == mock_collect_candidates.return_value

    def test_render_pdf_page_pngs_for_gpt_prefers_scoring_pages_when_focus_tokens_present(self):
        class _FakePixmap:
            def tobytes(self, fmt: str):
                return b"png"

        class _FakePage:
            def __init__(self, text: str):
                self._text = text

            def get_text(self):
                return self._text

            def get_pixmap(self, matrix=None, alpha=False):
                return _FakePixmap()

        class _FakeDoc:
            def __init__(self, pages):
                self._pages = pages

            def __iter__(self):
                return iter(self._pages)

            def __getitem__(self, index):
                return self._pages[index]

            def close(self):
                return None

        pages = [
            _FakePage("封面 项目名称 招标公告"),
            _FakePage("一般说明 施工部署 组织架构 资源配置 质量安全管理"),
            _FakePage("评标办法 评分细则 工期120天 质量90分 加分 扣分 关键节点"),
            _FakePage("附录 其他说明"),
        ]

        with patch("app.main.pymupdf") as mock_pymupdf:
            mock_pymupdf.open.return_value = _FakeDoc(pages)
            previews = app_main._render_pdf_page_pngs_for_gpt(
                b"%PDF-1.4",
                max_pages=2,
                preferred_tokens=["评分细则", "工期", "质量"],
                always_include_first_pages=1,
            )

        assert [item["page_no"] for item in previews] == [1, 3]

    def test_render_pdf_page_pngs_for_gpt_can_drop_first_page_bias_when_disabled(self):
        class _FakePixmap:
            def tobytes(self, fmt: str):
                return b"png"

        class _FakePage:
            def __init__(self, text: str):
                self._text = text

            def get_text(self):
                return self._text

            def get_pixmap(self, matrix=None, alpha=False):
                return _FakePixmap()

        class _FakeDoc:
            def __init__(self, pages):
                self._pages = pages

            def __iter__(self):
                return iter(self._pages)

            def __getitem__(self, index):
                return self._pages[index]

            def close(self):
                return None

        pages = [
            _FakePage("封面 项目名称 图纸目录"),
            _FakePage("一般说明 施工部署 资源配置"),
            _FakePage("节点详图 机电综合排布 轴网 标高600 1200 洞口 净高复核"),
        ]

        with patch("app.main.pymupdf") as mock_pymupdf:
            mock_pymupdf.open.return_value = _FakeDoc(pages)
            previews = app_main._render_pdf_page_pngs_for_gpt(
                b"%PDF-1.4",
                max_pages=1,
                preferred_tokens=["节点详图", "机电综合排布", "洞口"],
                always_include_first_pages=0,
            )

        assert [item["page_no"] for item in previews] == [3]

    @patch("app.main._call_gpt_material_parser")
    @patch("app.main._render_pdf_page_pngs_for_gpt")
    def test_augment_tender_summary_skips_gpt_when_local_summary_is_strong(
        self,
        mock_render_pages,
        mock_call_gpt,
    ):
        local_summary = {
            "structured_quality_score": 0.83,
            "section_titles": [
                "第一章 招标公告",
                "第二章 投标人须知",
                "第三章 评标办法",
                "第四章 合同",
            ],
            "scoring_point_terms": ["工期", "质量", "安全文明"],
            "mandatory_clause_terms": ["否决投标", "资格条件"],
            "top_numeric_terms": ["90", "100", "15", "48", "3"],
        }
        parsed_text = "项目名称 工期 质量 安全文明 " * 240

        with patch("app.main.get_openai_api_key", return_value="sk-test"):
            merged, backend, confidence, error = app_main._augment_tender_summary_with_gpt(
                b"%PDF-1.4",
                "招标文件正文.pdf",
                parsed_text,
                local_summary,
            )

        assert backend == "local"
        assert error == ""
        assert confidence == 0.83
        assert merged["gpt_skip_reason"] == "local_summary_strong"
        mock_render_pages.assert_not_called()
        mock_call_gpt.assert_not_called()

    @patch("app.main._call_gpt_material_parser")
    @patch("app.main._render_pdf_page_pngs_for_gpt")
    def test_augment_drawing_summary_skips_gpt_when_local_summary_is_strong(
        self,
        mock_render_pages,
        mock_call_gpt,
    ):
        local_summary = {
            "detected_format": "dwg",
            "structured_quality_score": 0.82,
            "structured_terms": [
                "总平面",
                "节点详图",
                "综合排布",
                "消防",
                "净高复核",
                "机电",
                "轴线",
                "标高",
            ],
            "discipline_keywords": ["建筑", "机电"],
            "sheet_type_tags": ["节点详图/大样", "系统图/原理图"],
            "risk_keywords": ["洞口", "高支模"],
            "top_numeric_terms": ["600", "1200", "3.5", "45", "90", "18"],
            "binary_marker_terms": ["WALL", "DOOR", "LEVEL", "GRID_A", "GRID_B", "BEAM"],
        }

        merged, backend, confidence, error = app_main._augment_drawing_summary_with_gpt(
            b"AC1027",
            "总图.dwg",
            "总平面 节点详图 机电 综合排布 标高 轴线 " * 80,
            local_summary,
        )

        assert backend == "local"
        assert error == ""
        assert confidence == 0.82
        assert merged["gpt_skip_reason"] == "local_summary_strong"
        mock_render_pages.assert_not_called()
        mock_call_gpt.assert_not_called()

    @patch("app.main._call_gpt_material_parser")
    @patch("app.main._collect_pdf_page_candidates_for_gpt")
    @patch("app.main._render_pdf_page_pngs_for_gpt")
    def test_augment_drawing_summary_skips_page_vision_when_focus_page_is_not_distinct(
        self,
        mock_render_pages,
        mock_collect_candidates,
        mock_call_gpt,
    ):
        local_summary = {
            "detected_format": "pdf",
            "structured_quality_score": 0.51,
            "structured_terms": ["总平面", "节点详图", "综合排布", "消防", "净高复核"],
            "discipline_keywords": ["建筑", "机电"],
            "sheet_type_tags": ["节点详图/大样"],
            "risk_keywords": ["洞口", "高支模"],
            "top_numeric_terms": ["600", "1200", "3.5", "45"],
            "layout_tags": ["轴网"],
            "component_tags": ["梁", "板"],
        }
        mock_collect_candidates.return_value = [
            {"page_no": 1, "text": "封面 图纸目录", "score": 4.2},
            {"page_no": 3, "text": "节点详图 机电综合排布", "score": 4.8},
        ]
        mock_call_gpt.return_value = (
            True,
            {
                "discipline": "建筑",
                "sheet_type": "节点详图/大样",
                "layout_tags": ["总平面"],
                "space_tags": ["机房"],
                "component_tags": ["梁"],
                "dimension_markers": ["1200"],
                "risk_tags": ["洞口"],
                "constraint_terms": ["净高复核"],
                "numeric_constraints": ["3.5"],
                "focused_dimensions": ["05"],
                "parse_confidence": 0.66,
            },
            "",
        )

        merged, backend, confidence, error = app_main._augment_drawing_summary_with_gpt(
            b"%PDF-1.4",
            "总图.pdf",
            "总平面 节点详图 机电 综合排布 标高 轴线 机房 梁 板 柱 " * 12,
            local_summary,
        )

        assert backend == "hybrid"
        assert error == ""
        assert confidence == 0.66
        assert merged["gpt_page_vision_skip_reason"] == "focus_page_not_distinct_enough"
        mock_collect_candidates.assert_called_once()
        mock_render_pages.assert_not_called()
        kwargs = mock_call_gpt.call_args.kwargs
        assert kwargs.get("image_bytes") is None

    @patch("app.main._call_gpt_material_parser")
    def test_augment_site_photo_summary_skips_gpt_when_local_summary_is_strong(
        self,
        mock_call_gpt,
    ):
        local_summary = {
            "structured_quality_score": 0.78,
            "ocr_quality_score": 0.66,
            "visual_capability": "ocr_multistage",
            "structured_terms": [
                "安全文明",
                "成品保护",
                "临边防护",
                "脚手架",
                "材料堆放",
                "进度形象",
                "质量样板",
                "封闭围挡",
            ],
            "safety_scene_tags": ["临边防护", "脚手架"],
            "civilization_scene_tags": ["材料堆放"],
            "quality_scene_tags": ["质量样板"],
            "progress_scene_tags": ["进度形象"],
            "top_numeric_terms": ["3", "5", "20", "120"],
        }

        merged, backend, confidence, error = app_main._augment_site_photo_summary_with_gpt(
            b"\x89PNG",
            "现场照片.png",
            "临边防护 脚手架 材料堆放 质量样板 进度形象 " * 20,
            local_summary,
        )

        assert backend == "local"
        assert error == ""
        assert confidence == 0.78
        assert merged["gpt_skip_reason"] == "local_summary_strong"
        assert merged["evidence_confidence"] == 0.78
        mock_call_gpt.assert_not_called()

    @patch("app.main._notify_material_parse_workers")
    @patch("app.main._rebuild_project_anchors_and_requirements")
    def test_material_parse_rebuilds_are_debounced_per_project(
        self,
        mock_rebuild,
        mock_notify_workers,
    ):
        from app import main as main_module

        original_pending = dict(main_module._MATERIAL_PARSE_PENDING_REBUILDS)
        try:
            main_module._MATERIAL_PARSE_PENDING_REBUILDS.clear()
            with patch("app.main.time.monotonic", return_value=100.0):
                main_module._schedule_project_material_rebuild("p1")
                main_module._schedule_project_material_rebuild("p1")
                main_module._schedule_project_material_rebuild("p2")
            with patch("app.main.time.monotonic", return_value=103.0):
                rebuilt = main_module._drain_due_project_material_rebuilds(limit=10)

            assert rebuilt == 2
            assert mock_rebuild.call_count == 2
            assert mock_notify_workers.call_count == 3
            mock_rebuild.assert_any_call("p1")
            mock_rebuild.assert_any_call("p2")
            assert "p1" not in main_module._MATERIAL_PARSE_PENDING_REBUILDS
            assert "p2" not in main_module._MATERIAL_PARSE_PENDING_REBUILDS
        finally:
            main_module._MATERIAL_PARSE_PENDING_REBUILDS.clear()
            main_module._MATERIAL_PARSE_PENDING_REBUILDS.update(original_pending)

    @patch("app.main.load_material_parse_jobs")
    @patch("app.main._rebuild_project_anchors_and_requirements")
    def test_material_parse_rebuilds_wait_for_parse_backlog_when_requested(
        self,
        mock_rebuild,
        mock_load_jobs,
    ):
        from app import main as main_module

        original_pending = dict(main_module._MATERIAL_PARSE_PENDING_REBUILDS)
        try:
            main_module._MATERIAL_PARSE_PENDING_REBUILDS.clear()
            mock_load_jobs.return_value = [
                {
                    "id": "j1",
                    "material_id": "m1",
                    "project_id": "p1",
                    "status": "queued",
                    "parse_mode": "full",
                    "created_at": "2026-03-30T00:00:00+00:00",
                    "updated_at": "2026-03-30T00:00:00+00:00",
                }
            ]
            with patch("app.main.time.monotonic", return_value=100.0):
                main_module._schedule_project_material_rebuild("p1")
            with patch("app.main.time.monotonic", return_value=103.0):
                rebuilt = main_module._drain_due_project_material_rebuilds(
                    limit=10,
                    skip_if_parse_backlog=True,
                )

            assert rebuilt == 0
            mock_rebuild.assert_not_called()
            assert "p1" in main_module._MATERIAL_PARSE_PENDING_REBUILDS
        finally:
            main_module._MATERIAL_PARSE_PENDING_REBUILDS.clear()
            main_module._MATERIAL_PARSE_PENDING_REBUILDS.update(original_pending)

    @patch("app.main._load_material_parse_claim_context")
    def test_has_pending_material_parse_jobs_uses_claim_context_cache_when_available(
        self,
        mock_load_claim_context,
    ):
        from app import main as main_module

        original_signature = main_module._MATERIAL_PARSE_CLAIM_CONTEXT_CACHE_SIGNATURE
        original_jobs = [dict(job) for job in main_module._MATERIAL_PARSE_CLAIM_CONTEXT_CACHE_JOBS]
        original_candidate_indices = list(
            main_module._MATERIAL_PARSE_CLAIM_CONTEXT_CACHE_CANDIDATE_INDICES
        )
        original_has_queued = main_module._MATERIAL_PARSE_CLAIM_CONTEXT_CACHE_HAS_QUEUED_CANDIDATES
        original_has_failed_without_due = (
            main_module._MATERIAL_PARSE_CLAIM_CONTEXT_CACHE_HAS_RETRYABLE_FAILED_WITHOUT_DUE
        )
        original_earliest_failed_at = (
            main_module._MATERIAL_PARSE_CLAIM_CONTEXT_CACHE_EARLIEST_RETRYABLE_FAILED_AT
        )
        try:
            signature = ((1, 1), (2, 2))
            main_module._MATERIAL_PARSE_CLAIM_CONTEXT_CACHE_SIGNATURE = signature
            main_module._MATERIAL_PARSE_CLAIM_CONTEXT_CACHE_JOBS[:] = [
                {
                    "id": "j1",
                    "material_id": "m1",
                    "project_id": "p1",
                    "status": "queued",
                    "parse_mode": "full",
                }
            ]
            main_module._MATERIAL_PARSE_CLAIM_CONTEXT_CACHE_CANDIDATE_INDICES[:] = [0]
            main_module._MATERIAL_PARSE_CLAIM_CONTEXT_CACHE_HAS_QUEUED_CANDIDATES = True
            main_module._MATERIAL_PARSE_CLAIM_CONTEXT_CACHE_HAS_RETRYABLE_FAILED_WITHOUT_DUE = False
            main_module._MATERIAL_PARSE_CLAIM_CONTEXT_CACHE_EARLIEST_RETRYABLE_FAILED_AT = None

            with patch("app.main._material_parse_state_files_signature", return_value=signature):
                assert main_module._has_pending_material_parse_jobs() is True
        finally:
            main_module._MATERIAL_PARSE_CLAIM_CONTEXT_CACHE_SIGNATURE = original_signature
            main_module._MATERIAL_PARSE_CLAIM_CONTEXT_CACHE_JOBS[:] = original_jobs
            main_module._MATERIAL_PARSE_CLAIM_CONTEXT_CACHE_CANDIDATE_INDICES[
                :
            ] = original_candidate_indices
            main_module._MATERIAL_PARSE_CLAIM_CONTEXT_CACHE_HAS_QUEUED_CANDIDATES = (
                original_has_queued
            )
            main_module._MATERIAL_PARSE_CLAIM_CONTEXT_CACHE_HAS_RETRYABLE_FAILED_WITHOUT_DUE = (
                original_has_failed_without_due
            )
            main_module._MATERIAL_PARSE_CLAIM_CONTEXT_CACHE_EARLIEST_RETRYABLE_FAILED_AT = (
                original_earliest_failed_at
            )

        mock_load_claim_context.assert_not_called()


class TestSecureDesktopExportBlock:
    def test_scoring_factors_markdown_returns_403_in_secure_mode(self, client):
        with patch("app.runtime_security.is_secure_desktop_mode_enabled", return_value=True):
            response = client.get("/api/v1/scoring/factors/markdown")

        assert response.status_code == 403
        assert "保密模式已启用" in response.json()["detail"]

    def test_project_analysis_bundle_returns_403_in_secure_mode(self, client):
        with patch("app.runtime_security.is_secure_desktop_mode_enabled", return_value=True):
            response = client.get("/api/v1/projects/p1/analysis_bundle")

        assert response.status_code == 403
        assert "保密模式已启用" in response.json()["detail"]

    def test_material_depth_report_markdown_returns_403_in_secure_mode(self, client):
        with patch("app.runtime_security.is_secure_desktop_mode_enabled", return_value=True):
            response = client.get("/api/v1/projects/p1/materials/depth_report/markdown")

        assert response.status_code == 403
        assert "保密模式已启用" in response.json()["detail"]

    def test_project_trial_preflight_markdown_download_returns_403_in_secure_mode(self, client):
        with patch("app.runtime_security.is_secure_desktop_mode_enabled", return_value=True):
            response = client.get("/api/v1/projects/p1/trial_preflight.md")

        assert response.status_code == 403
        assert "保密模式已启用" in response.json()["detail"]

    def test_project_trial_preflight_docx_download_returns_403_in_secure_mode(self, client):
        with patch("app.runtime_security.is_secure_desktop_mode_enabled", return_value=True):
            response = client.get("/api/v1/projects/p1/trial_preflight.docx")

        assert response.status_code == 403
        assert "保密模式已启用" in response.json()["detail"]

    def test_writing_guidance_markdown_download_returns_403_in_secure_mode(self, client):
        with patch("app.runtime_security.is_secure_desktop_mode_enabled", return_value=True):
            response = client.get("/api/v1/projects/p1/writing_guidance.md")

        assert response.status_code == 403
        assert "保密模式已启用" in response.json()["detail"]

    def test_writing_guidance_patch_bundle_download_returns_403_in_secure_mode(self, client):
        with patch("app.runtime_security.is_secure_desktop_mode_enabled", return_value=True):
            response = client.get("/api/v1/projects/p1/writing_guidance_patch_bundle.md")

        assert response.status_code == 403
        assert "保密模式已启用" in response.json()["detail"]

    def test_writing_guidance_patch_bundle_docx_download_returns_403_in_secure_mode(self, client):
        with patch("app.runtime_security.is_secure_desktop_mode_enabled", return_value=True):
            response = client.get("/api/v1/projects/p1/writing_guidance_patch_bundle.docx")

        assert response.status_code == 403
        assert "保密模式已启用" in response.json()["detail"]

    def test_submission_evidence_trace_download_returns_403_in_secure_mode(self, client):
        with patch("app.runtime_security.is_secure_desktop_mode_enabled", return_value=True):
            response = client.get("/api/v1/projects/p1/submissions/s1/evidence_trace.md")

        assert response.status_code == 403
        assert "保密模式已启用" in response.json()["detail"]


class TestScoreSelfAwareness:
    def test_build_score_self_awareness_high_when_evidence_and_dimension_support_are_strong(
        self,
    ):
        report = {
            "pred_confidence": {
                "fused_sigma": 1.2,
                "fused_ci95_lower": 84.0,
                "fused_ci95_upper": 88.0,
            },
            "meta": {
                "material_utilization": {
                    "retrieval_file_coverage_rate": 0.88,
                    "retrieval_hit_rate": 0.76,
                },
                "material_utilization_gate": {"blocked": False, "warned": False},
                "evidence_trace": {
                    "mandatory_hit_rate": 0.82,
                    "source_files_hit_count": 3,
                },
            },
        }
        material_knowledge_snapshot = {
            "summary": {
                "dimension_coverage_rate": 0.81,
                "structured_signal_total": 22,
                "structured_quality_avg": 0.78,
                "structured_quality_type_rate": 1.0,
                "numeric_category_summary": [
                    "工期/节点：90",
                    "规格/参数：1200",
                    "阈值/偏差：48",
                ],
            },
            "by_type": [
                {
                    "material_type": "tender_qa",
                    "files": 1,
                    "structured_signal_count": 8,
                    "structured_quality_score": 0.82,
                },
                {
                    "material_type": "drawing",
                    "files": 1,
                    "structured_signal_count": 7,
                    "structured_quality_score": 0.76,
                },
                {
                    "material_type": "boq",
                    "files": 1,
                    "structured_signal_count": 7,
                    "structured_quality_score": 0.75,
                },
            ],
        }

        out = _build_score_self_awareness(
            report,
            material_knowledge_snapshot=material_knowledge_snapshot,
        )

        assert out["level"] == "high"
        assert out["score_0_1"] >= 0.72
        assert out["dimension_coverage_rate"] == pytest.approx(0.81, abs=1e-6)
        assert out["source_files_hit_count"] == 3
        assert out["structured_signal_total"] == 22
        assert out["structured_quality_avg"] == pytest.approx(0.78, abs=1e-6)
        assert out["structured_quality_type_rate"] == pytest.approx(1.0, abs=1e-6)
        assert out["structured_type_coverage_rate"] == pytest.approx(1.0, abs=1e-6)

    def test_build_score_self_awareness_forces_low_when_material_gate_blocked(self):
        report = {
            "meta": {
                "material_utilization": {
                    "retrieval_file_coverage_rate": 0.92,
                    "retrieval_hit_rate": 0.8,
                },
                "material_utilization_gate": {"blocked": True, "warned": False},
                "evidence_trace": {
                    "mandatory_hit_rate": 0.9,
                    "source_files_hit_count": 4,
                },
            }
        }
        material_knowledge_snapshot = {
            "summary": {
                "dimension_coverage_rate": 0.9,
                "numeric_category_summary": ["工期/节点：90", "规格/参数：1200"],
            }
        }

        out = _build_score_self_awareness(
            report,
            material_knowledge_snapshot=material_knowledge_snapshot,
        )

        assert out["level"] == "low"
        assert out["score_0_1"] <= 0.18
        assert "资料利用门禁阻断" in out["reasons"]

    def test_build_score_self_awareness_backfills_material_usage_metadata(self):
        report = {
            "requirement_hits": [
                {
                    "source_pack_id": "runtime_material_dimension",
                    "dimension_id": "09",
                    "label": "资料维度约束：进度计划",
                    "hit": True,
                    "source_types": ["tender_qa"],
                }
            ],
            "meta": {
                "material_retrieval": {},
                "material_utilization_gate": {"blocked": False, "warned": False},
                "evidence_trace": {
                    "mandatory_hit_rate": 0.6,
                    "source_files_hit_count": 1,
                },
            },
        }

        out = _build_score_self_awareness(report, material_knowledge_snapshot={"summary": {}})

        assert out["material_dimension_hit_rate"] == pytest.approx(1.0, abs=1e-6)
        assert report["meta"]["material_utilization"]["material_dimension_total"] == 1

    def test_build_score_self_awareness_penalizes_weak_structured_material_signals(self):
        report = {
            "meta": {
                "material_utilization": {
                    "retrieval_file_coverage_rate": 0.78,
                    "retrieval_hit_rate": 0.64,
                    "material_dimension_hit_rate": 0.66,
                },
                "material_utilization_gate": {"blocked": False, "warned": False},
                "evidence_trace": {
                    "mandatory_hit_rate": 0.68,
                    "source_files_hit_count": 2,
                },
            }
        }
        material_knowledge_snapshot = {
            "summary": {
                "dimension_coverage_rate": 0.6,
                "structured_signal_total": 1,
                "structured_quality_avg": 0.12,
                "structured_quality_type_rate": 0.0,
                "numeric_category_summary": ["工期/节点：90"],
            },
            "by_type": [
                {
                    "material_type": "tender_qa",
                    "files": 1,
                    "structured_signal_count": 1,
                    "structured_quality_score": 0.12,
                },
                {
                    "material_type": "drawing",
                    "files": 1,
                    "structured_signal_count": 0,
                    "structured_quality_score": 0.06,
                },
                {
                    "material_type": "boq",
                    "files": 1,
                    "structured_signal_count": 0,
                    "structured_quality_score": 0.08,
                },
            ],
        }

        out = _build_score_self_awareness(
            report,
            material_knowledge_snapshot=material_knowledge_snapshot,
        )

        assert out["level"] in {"low", "medium"}
        assert out["structured_signal_total"] == 1
        assert out["structured_quality_avg"] == pytest.approx(0.12, abs=1e-6)
        assert out["structured_quality_type_rate"] == pytest.approx(0.0, abs=1e-6)
        assert out["structured_type_coverage_rate"] == pytest.approx(0.3333, abs=1e-6)
        assert any("结构化资料信号偏弱" in reason for reason in out["reasons"])
        assert any("结构化资料质量偏弱" in reason for reason in out["reasons"])


class TestIndexEndpoint:
    """Tests for GET / index endpoint."""

    def test_index_returns_html(self, client):
        """Index endpoint should return HTML page."""
        response = client.get("/")
        assert response.status_code == 200
        assert "<html>" in response.text
        assert "青天评标系统" in response.text

    def test_index_contains_forms(self, client):
        """Index page should contain all forms."""
        response = client.get("/")
        assert "createProject" in response.text
        assert "uploadMaterial" in response.text
        assert "uploadMaterialBoq" in response.text
        assert "uploadMaterialDrawing" in response.text
        assert "uploadMaterialPhoto" in response.text
        assert 'id="uploadZoneTenderQa"' in response.text
        assert 'id="uploadZoneBoq"' in response.text
        assert 'id="uploadZoneDrawing"' in response.text
        assert 'id="uploadZoneSitePhoto"' in response.text
        assert 'id="uploadZoneStateTenderQa"' in response.text
        assert 'id="uploadZoneStateBoq"' in response.text
        assert 'id="uploadZoneStateDrawing"' in response.text
        assert 'id="uploadZoneStateSitePhoto"' in response.text
        assert 'id="uploadMaterialFile"' in response.text
        assert 'id="uploadMaterialBoqFile"' in response.text
        assert 'id="uploadMaterialDrawingFile"' in response.text
        assert 'id="uploadMaterialPhotoFile"' in response.text
        assert 'id="uploadShigongFile"' in response.text
        assert 'id="feedFile"' in response.text
        assert 'id="uploadMaterialFileName"' in response.text
        assert 'id="uploadMaterialBoqFileName"' in response.text
        assert 'id="uploadMaterialDrawingFileName"' in response.text
        assert 'id="uploadMaterialPhotoFileName"' in response.text
        assert 'id="uploadShigongFileName"' in response.text
        assert 'id="feedFileName"' in response.text
        assert 'for="createProjectFromTenderFile"' in response.text
        assert 'for="uploadMaterialFile"' in response.text
        assert 'for="uploadShigongFile"' in response.text
        assert 'class="file-picker-btn" data-file-input-id="uploadMaterialFile"' in response.text
        assert 'class="file-picker-btn" data-file-input-id="feedFile"' in response.text
        assert "uploadShigong" in response.text
        assert 'id="projectDeleteSelect"' in response.text
        assert 'id="deleteSelectedProjects"' in response.text
        assert 'id="scoreScaleSelect"' in response.text
        assert 'name="score_scale_max"' in response.text
        assert 'id="btnMaterialKnowledgeProfile"' in response.text
        assert 'id="btnMaterialKnowledgeProfileDownload"' in response.text
        assert 'id="btnEvolutionHealth"' in response.text
        assert 'id="btnSelfCheck"' in response.text
        assert 'id="btnSystemImprovementOverview"' in response.text
        assert 'id="btnDataHygiene"' in response.text
        assert 'id="btnEvalSummaryV2"' in response.text
        assert 'id="btnTrialPreflight"' in response.text
        assert 'id="btnTrialPreflightDownload"' in response.text
        assert 'id="btnTrialPreflightDownloadDocx"' in response.text
        assert 'id="btnWritingGuidanceDownload"' in response.text
        assert 'id="btnWritingGuidancePatchBundleDownload"' in response.text
        assert 'id="btnWritingGuidancePatchBundleDownloadDocx"' in response.text
        assert 'id="btnEvidenceTrace"' in response.text
        assert 'id="btnScoringBasis"' in response.text
        assert 'id="btnScoringDiagnostic"' in response.text
        assert 'id="submissionDualTrackOverview"' in response.text
        assert 'id="shigongGateSummary"' in response.text
        assert 'id="scoringBasisResult"' in response.text
        assert 'id="scoringDiagnosticResult"' in response.text
        assert 'id="systemImprovementResult"' in response.text
        assert 'id="dataHygieneResult"' in response.text
        assert 'id="trialPreflightResult"' in response.text
        assert "解析状态" in response.text
        assert "双轨分数" in response.text
        assert "偏差诊断" in response.text
        assert 'id="groundTruthSubmissionSelect"' in response.text
        assert 'id="groundTruthFile"' not in response.text
        assert "/ground_truth/from_submission" in response.text
        assert 'id="section-adaptive" style="display:none"' in response.text
        assert "V2 反演校准闭环（核心能力，强烈建议执行）" in response.text
        assert ".dxf" in response.text

    def test_index_replaces_server_side_placeholders(self, client):
        """Index page should not leak template placeholders."""
        response = client.get("/")
        assert "__PROJECT_OPTIONS__" not in response.text
        assert "__CREATE_NOTICE_HTML__" not in response.text
        assert "__EXPERT_PROFILE_STATUS__" not in response.text
        assert "__EXPERT_WEIGHTS_ROWS__" not in response.text
        assert "__EXPERT_WEIGHTS_SUMMARY__" not in response.text
        assert "__GLOBAL_NOTICE_HTML__" not in response.text
        assert "__SELECTED_PROJECT_ID__" not in response.text
        assert "__SUBMISSION_DUAL_TRACK_OVERVIEW_HTML__" not in response.text
        assert "__SUBMISSION_DUAL_TRACK_OVERVIEW_DISPLAY__" not in response.text
        assert "__PROJECT_SCORE_SCALE_MAX__" not in response.text

    def test_index_renders_dual_track_submission_overview_for_selected_project(self, client):
        submission = {
            "id": "s1",
            "project_id": "p1",
            "filename": "施工组织设计A.docx",
            "total_score": 82.0,
            "report": {
                "scoring_status": "scored",
                "total_score": 82.0,
                "rule_total_score": 78.0,
                "pred_total_score": 82.0,
                "llm_total_score": 81.2,
                "meta": {},
            },
            "text": "t1",
            "created_at": "2026-03-16T08:00:00+00:00",
        }
        with (
            patch("app.main.load_projects", return_value=[{"id": "p1", "name": "测试项目"}]),
            patch("app.main.load_materials", return_value=[]),
            patch("app.main.load_submissions", return_value=[submission]),
            patch(
                "app.main.load_qingtian_results",
                return_value=[{"submission_id": "s1", "qt_total_score": 84.0}],
            ),
            patch("app.main.load_expert_profiles", return_value=[]),
            patch(
                "app.main._select_calibrator_model",
                return_value={"calibrator_version": "calib_v1"},
            ),
            patch("app.main._build_material_knowledge_profile", return_value={}),
            patch("app.main._ensure_report_score_self_awareness"),
        ):
            response = client.get("/?project_id=p1")

        assert response.status_code == 200
        page = response.text
        assert 'id="submissionDualTrackOverview"' in page
        assert "双轨总览" in page
        assert "双轨分数" in page
        assert "偏差诊断" in page
        assert "当前分层整体上更接近青天" in page
        assert "当前分: " in page
        assert "当前分偏差 " in page
        assert "查看满分优化清单（逐页）" in page
        assert "查看评分治理（异常样本/校准/回退）" in page

    def test_index_renders_blocked_submission_with_score_and_material_warning(self, client):
        submission = {
            "id": "s1",
            "project_id": "p1",
            "filename": "施工组织设计A.docx",
            "total_score": 74.65,
            "report": {
                "scoring_status": "blocked",
                "total_score": 74.65,
                "rule_total_score": 74.65,
                "meta": {
                    "material_utilization_gate": {
                        "blocked": True,
                        "reasons": ["关键资料未形成证据：图纸"],
                    }
                },
            },
            "text": "t1",
            "created_at": "2026-03-16T08:00:00+00:00",
        }
        with (
            patch(
                "app.main.load_projects",
                return_value=[{"id": "p1", "name": "测试项目", "meta": {"score_scale_max": 5}}],
            ),
            patch("app.main.load_materials", return_value=[]),
            patch("app.main.load_submissions", return_value=[submission]),
            patch("app.main.load_qingtian_results", return_value=[]),
            patch("app.main.load_expert_profiles", return_value=[]),
            patch("app.main._select_calibrator_model", return_value=None),
            patch("app.main._build_material_knowledge_profile", return_value={}),
            patch("app.main._ensure_report_score_self_awareness"),
        ):
            response = client.get("/?project_id=p1")

        assert response.status_code == 200
        page = response.text
        assert "已生成分数，但本施组触发资料利用预警。" in page
        assert "这是施组级资料利用预警，不是项目资料未上传。" in page
        assert "5分制" in page
        assert "折算100分口径" not in page
        assert "（100分口径）" not in page
        assert "查看满分优化清单（逐页）" in page
        assert "查看评分治理（异常样本/校准/回退）" in page
        assert '<div class="warn">已生成分数，但本施组触发资料利用预警。</div>' in page
        assert (
            '<div class="warn"><strong>已评分，但本施组对部分项目资料未形成足够证据关联。</strong></div>'
            in page
        )
        assert '<div class="warn">资料利用门禁未达标（建议补齐资料后重评分）</div>' in page
        assert '<div class="error">已生成分数，但本施组触发资料利用预警。</div>' not in page
        assert '<div class="error">资料利用门禁未达标（建议补齐资料后重评分）</div>' not in page

    def test_index_downgrades_optional_uploaded_material_issue_to_note(self, client):
        submission = {
            "id": "s1",
            "project_id": "p1",
            "filename": "施工组织设计A.docx",
            "total_score": 74.65,
            "report": {
                "scoring_status": "blocked",
                "total_score": 74.65,
                "rule_total_score": 74.65,
                "meta": {
                    "material_utilization_gate": {
                        "enabled": True,
                        "mode": "block",
                        "blocked": True,
                        "warned": False,
                        "passed": False,
                        "level": "blocked",
                        "reasons": [
                            "已上传资料类型覆盖率 75.0% 低于阈值 100.0%，未形成证据类型：现场照片"
                        ],
                        "thresholds": {
                            "min_retrieval_total": 2,
                            "min_retrieval_file_coverage_rate": 0.2,
                            "min_retrieval_hit_rate": 0.2,
                            "min_consistency_hit_rate": 0.2,
                            "max_uncovered_required_types": 0,
                            "min_required_type_presence_rate": 0.6,
                            "min_required_type_coverage_rate": 0.6,
                            "min_uploaded_type_coverage_rate": 1.0,
                        },
                        "required_types": ["tender_qa", "boq", "drawing"],
                        "uploaded_types": ["tender_qa", "boq", "drawing", "site_photo"],
                        "uncovered_uploaded_types": ["site_photo"],
                        "uploaded_type_coverage_rate": 0.75,
                        "metrics": {
                            "retrieval_total": 10,
                            "retrieval_hit_rate": 0.8,
                            "retrieval_file_total": 6,
                            "retrieval_file_coverage_rate": 0.8,
                            "consistency_total": 6,
                            "consistency_hit_rate": 0.8,
                        },
                    }
                },
            },
            "text": "t1",
            "created_at": "2026-03-16T08:00:00+00:00",
        }
        from app.submission_dual_track_views import build_selected_project_submission_render_context

        with (
            patch("app.main.load_qingtian_results", return_value=[]),
            patch("app.main._select_calibrator_model", return_value=None),
            patch("app.main._ensure_report_score_self_awareness"),
        ):
            rendered = build_selected_project_submission_render_context(
                "p1",
                [submission],
                allow_pred_score=False,
                score_scale_max=5,
                material_knowledge_snapshot={},
            )

        rows_html = rendered["rows_html"]
        assert '<tr class="submission-row">' in rows_html
        assert 'class="submission-file-cell"' in rows_html
        assert 'class="submission-score-cell"' in rows_html
        assert 'class="submission-diagnostic-cell"' in rows_html
        assert "资料利用存在补强提示（不阻断当前评分）。" in rows_html
        assert "已生成分数，但本施组触发资料利用预警。" not in rows_html
        assert "已评分，但本施组对部分项目资料未形成足够证据关联。" not in rows_html
        assert "资料利用门禁未达标（建议补齐资料后重评分）" not in rows_html

    def test_index_material_gate_warning_uses_warn_class_in_client_render_baseline(self, client):
        response = client.get("/")

        assert response.status_code == 200
        page = response.text
        assert ".warn { color: #9a3412; }" in page
        assert "html = '<div class=\"warn\">已生成分数，但本施组触发资料利用预警。</div>';" in page
        assert (
            "html += '<div class=\"warn\"><strong>已评分，但本施组对部分项目资料未形成足够证据关联。</strong></div>';"
            in page
        )
        assert (
            "scoreHtml += '<div class=\"warn\">资料利用门禁未达标（建议补齐资料后重评分）</div>';"
            in page
        )
        assert "折算100分口径" not in page
        assert "（100分口径）" not in page

    def test_index_renders_selected_project_material_rows(self, client):
        materials = [
            {
                "id": "m1",
                "project_id": "p1",
                "filename": "招标文件.docx",
                "material_type": "tender_qa",
                "parse_status": "parsed",
                "parse_backend": "gpt-5.4",
                "created_at": "2026-03-17T08:00:00+00:00",
            }
        ]
        with (
            patch("app.main.load_projects", return_value=[{"id": "p1", "name": "测试项目"}]),
            patch("app.main.load_materials", return_value=materials),
            patch("app.main.load_submissions", return_value=[]),
            patch("app.main.load_expert_profiles", return_value=[]),
        ):
            response = client.get("/?project_id=p1")

        assert response.status_code == 200
        page = response.text
        assert "招标文件.docx" in page
        assert "招标文件和答疑" in page
        assert "已解析（GPT-5.4）" in page
        assert 'data-material-id="m1"' in page

    def test_index_project_selector_shows_plain_names_and_search_controls(self, client):
        with (
            patch(
                "app.main.load_projects",
                return_value=[
                    {"id": "p1", "name": "恢复项目_p1"},
                    {"id": "p2", "name": "合肥轨道TOD甘棠路一期B地块公共区域精装修工程招标"},
                ],
            ),
            patch("app.main.load_materials", return_value=[]),
            patch("app.main.load_submissions", return_value=[]),
            patch("app.main.load_expert_profiles", return_value=[]),
        ):
            response = client.get("/?project_id=p2")

        assert response.status_code == 200
        page = response.text
        assert 'id="projectSearchInput"' in page
        assert 'class="project-search-input primary-input"' in page
        assert 'id="projectSelect" class="project-select-input wide-select"' in page
        assert 'id="currentProjectTag" class="current-project-text"' in page
        assert 'id="renameProjectNameInput"' not in page
        assert 'id="btnRenameProject"' not in page
        assert 'id="btnSelectProjectBySearch"' in page
        assert 'id="projectListMeta"' in page
        assert 'data-project-name="恢复项目_p1"' in page
        assert 'data-project-name="合肥轨道TOD甘棠路一期B地块公共区域精装修工程招标"' in page
        assert "恢复项目_p1 (p1" not in page
        assert "合肥轨道TOD甘棠路一期B地块公共区域精装修工程招标 (p2" not in page

    def test_index_compact_ui_keeps_core_workflow_buttons_visible(self, client):
        response = client.get("/")
        assert response.status_code == 200
        page = response.text
        for button_id in (
            "btnCreateProject",
            "btnCreateProjectFromTender",
            "deleteCurrentProject",
            "btnUploadMaterials",
            "btnUploadBoq",
            "btnUploadDrawing",
            "btnUploadSitePhotos",
            "btnUploadShigong",
            "btnScoreShigong",
            "btnUploadFeed",
            "btnAddGroundTruth",
            "btnEvolve",
            "btnEvolutionHealth",
            "btnSelfCheck",
            "btnSystemImprovementOverview",
            "btnDataHygiene",
            "btnEvalSummaryV2",
            "btnTrialPreflight",
            "btnTrialPreflightDownload",
            "btnTrialPreflightDownloadDocx",
            "btnWritingGuidance",
            "btnWritingGuidanceDownload",
            "btnWritingGuidancePatchBundleDownload",
            "btnWritingGuidancePatchBundleDownloadDocx",
        ):
            assert f'id="{button_id}"' in page
            assert f'id="{button_id}" class="secondary compact-hidden"' not in page
            assert f'id="{button_id}" class="compact-hidden"' not in page

    def test_index_exposes_evolution_review_audit_helpers(self, client):
        response = client.get("/")
        assert response.status_code == 200
        page = response.text
        assert "function renderEvolutionEnhancementAudit(data)" in page
        assert "function renderManualConfirmationAuditBlock(audit)" in page
        assert "const scaleMax = Number(info.score_scale_max || 100) === 5 ? 5 : 100;" in page
        assert "const rawDelta = Number.isFinite(Number(info.abs_delta_raw))" in page
        assert (
            "function renderGroundTruthPostAddFollowUpActionHint(projectName='', entrypointLabel='', actionLabel='')"
            in page
        )
        assert (
            "function buildPostAutoRunRefreshSummary(data, closureSummary=null, systemImprovementOverview=null, projectId='')"
            in page
        )
        assert "function renderPostAutoRunRefreshSummaryBlock(summary)" in page
        assert "if (!opts.silentOutput) {" in page
        assert "silentOutput: true" in page
        assert "真实评标录入后已自动刷新评分治理视图，可直接复验人工确认/校准状态。" in page
        assert "真实评标录入后已自动刷新评分治理视图，可直接处理当前人工确认阻塞。" in page
        assert "一键闭环执行后已自动刷新评分治理视图，可直接查看最新学习/校准状态。" in page
        assert "闭环后主因状态：" in page
        assert "闭环后的下一步" in page
        assert "双模型分歧，已自动回退到规则版建议" in page
        assert "已通过双模型复核" in page

    def test_index_compact_ui_hides_advanced_controls_by_default(self, client):
        response = client.get("/")
        assert response.status_code == 200
        page = response.text
        assert ".compact-hidden { display:none !important; }" in page
        assert "页面默认精简高级维护按钮，仅保留评分体系与分析包等关键入口，聚焦试车操作。" in page
        assert '<details style="margin-top:8px">' in page
        assert 'id="btnScoringFactors" class="secondary"' in page
        assert 'id="btnScoringFactorsMd" class="secondary"' in page
        assert 'id="btnAnalysisBundle" class="secondary"' in page
        assert 'id="btnAnalysisBundleDownload" class="secondary"' in page
        assert "2.5) 青天评标关注度（16维）" in page
        assert (
            '<div class="section card compact-hidden">\n        <h2>2.5) 青天评标关注度（16维）</h2>'
            not in page
        )
        for hidden_fragment in (
            'id="refreshProjects" class="compact-hidden"',
            'id="btnRefreshMaterials" class="secondary compact-hidden"',
            'id="btnMaterialDepthReport" class="secondary compact-hidden"',
            'id="btnMaterialKnowledgeProfile" class="secondary compact-hidden"',
            'id="btnRefreshSubmissions" class="secondary compact-hidden"',
            'id="btnScoringDiagnostic" class="secondary compact-hidden"',
            'id="btnRefreshGroundTruth" class="secondary compact-hidden"',
            'id="btnRefreshFeedMaterials" class="secondary compact-hidden"',
            'id="btnRefreshGroundTruthSubmissionOptions" class="secondary compact-hidden"',
            'id="btnFeedbackGovernance" class="secondary compact-hidden"',
            'id="btnCompilationInstructions" class="secondary compact-hidden"',
            '<div class="section card compact-hidden">',
            '<div class="section card compact-hidden" id="section-adaptive"',
        ):
            assert hidden_fragment in page

    def test_index_renders_auth_panel_and_api_key_hidden_inputs(self, client):
        with patch.dict(os.environ, {"API_KEYS": "admin:test-admin-key"}, clear=False):
            response = client.get("/")
        assert response.status_code == 200
        page = response.text
        for fragment in (
            'id="authPanel" style="display:block"',
            'id="apiKeyInput"',
            'id="btnSaveApiKey"',
            'id="btnClearApiKey"',
            'id="createProjectApiKey"',
            'id="createProjectFromTenderApiKey"',
            'id="deleteProjectApiKey"',
            'id="uploadMaterialApiKey"',
            'id="uploadMaterialBoqApiKey"',
            'id="uploadMaterialDrawingApiKey"',
            'id="uploadMaterialPhotoApiKey"',
            'id="uploadShigongApiKey"',
            "async function refreshAuthStatusUi()",
            "async function ensureVerifiedApiKeyForAction(",
            "创建成功，当前项目已自动切换：",
            "可直接下拉或输入名称快速定位",
            "window.applySecureDesktopUiGuards = applySecureDesktopUiGuards;",
            "if (typeof window.applySecureDesktopUiGuards === 'function') {",
            "refreshAuthStatusUi().finally(() => {",
            "if (INITIAL_CREATE_ERROR) {",
            "refreshProjects();",
            "Object.prototype.hasOwnProperty.call(authStatusState, 'ui_auth_required')",
            'let authStatusState = {"auth_enabled": true,',
        ):
            assert fragment in page

    def test_index_hides_auth_panel_for_trusted_localhost_request(self, client):
        with patch.dict(os.environ, {"API_KEYS": "admin:test-admin-key"}, clear=False):
            response = client.get("/", headers={"host": "127.0.0.1:8000"})
        assert response.status_code == 200
        page = response.text
        assert 'id="authPanel" style="display:none"' in page
        assert '"trusted_local_bypass_active": true' in page
        assert '"ui_auth_required": false' in page

    def test_index_frontend_refreshes_new_project_by_created_id(self, client):
        response = client.get("/")
        assert response.status_code == 200
        page = response.text
        assert 'id="btnStartNewProject"' in page
        assert page.index('id="btnStartNewProject"') < page.index("<h2>2) 选择项目</h2>")

    def test_index_frontend_wraps_refresh_click_handlers_without_pointerevent_leakage(self, client):
        response = client.get("/")
        assert response.status_code == 200
        page = response.text
        assert "safeClick0('refreshProjects', refreshProjects);" in page
        assert "safeClick0('btnRefreshFeedMaterials', refreshFeedMaterials);" in page
        assert "if (elRefresh) elRefresh.onclick = () => refreshProjects();" not in page
        assert "if (btnRefFeed) btnRefFeed.onclick = () => refreshFeedMaterials();" not in page
        assert "if (elRefresh) elRefresh.onclick = refreshProjects;" not in page
        assert "if (btnRefFeed) btnRefFeed.onclick = refreshFeedMaterials;" not in page

    def test_index_frontend_clears_stale_project_refresh_after_delete(self, client):
        response = client.get("/")
        assert response.status_code == 200
        page = response.text
        assert "clearProjectAutoRefresh();" in page
        assert "clearMaterialParsePolling();" in page
        assert "clearProjectRefreshInflight(id);" in page
        assert "projectSwitchSeq += 1;" in page
        assert "storageRemove('selected_project_id');" in page
        assert "syncProjectSelectionUrl('');" in page
        assert "resetProjectPanelsToStandby('');" in page
        assert 'id="projectMonthFilter"' in page
        assert "async function startNewProjectIntake()" in page
        assert "async function enterProjectIntakeMode(message, options=null)" in page
        assert "const intakeMode = allowEmptySelection || projectIntakeModeEnabled();" in page
        assert "function projectIsSystemGenerated(project)" in page
        assert "function buildProjectPickerView(projects)" in page
        assert (
            "function syncProjectMonthFilterToPreferredProject(projects, preferredProjectId='')"
            in page
        )
        assert (
            "safeChange0('projectMonthFilter', () => refreshProjects(selectedProjectIdStrict() || ''));"
            in page
        )
        assert "const INITIAL_CREATE_ERROR = __INITIAL_CREATE_ERROR__;" not in page
        assert "storageGet('project_intake_mode') === '1'" in page
        assert "await refreshProjects('', {" in page
        assert (
            "if (preferredProjectId) syncProjectMonthFilterToPreferredProject(projectListCache, preferredProjectId);"
            in page
        )
        assert "select.value = monthKey;" in page
        assert "allowEmptySelection: true," in page
        assert (
            "emptySelectionIsInfo: intakeMode || (pickerView.totalCount > 0 && list.length === 0),"
            in page
        )
        assert "自动创建未完成，当前保留在新项目录入界面；请先补齐项目名称或更换招标文件。" in page
        assert "已切换到新项目录入界面，历史项目已保留并隐藏。" in page
        assert "OPS/E2E 系统项目已默认隐藏" in page
        assert "删除资料不会直接删除已学习权重、校准器或真实评标记录" in page
        assert "这不会直接删除已学习的权重、校准器或真实评标记录" in page
        assert "纠正项目名" not in page
        assert "project_name_override" in page
        assert "normalizeTenderCreateErrorMessage(" in page
        assert "syncCreateProjectNameOverride()" in page
        assert "未识别到清晰项目名" in page
        assert (
            "const current = preferredProjectId || storageGet('selected_project_id') || pid() || '';"
            not in page
        )
        assert "await refreshProjects(String((created && created.id) || ''), {" in page
        assert "reason: 'create_project_success'" in page
        assert "autoFocusEntrypoint: true" not in page
        assert "nameInput.value = projectName;" in page

    def test_index_frontend_coalesces_project_refresh_requests(self, client):
        response = client.get("/")
        assert response.status_code == 200
        page = response.text
        assert "let projectRefreshInflight = new Map();" in page
        assert "function projectRefreshInflightKey(kind, projectId) {" in page
        assert "function clearProjectRefreshInflight(projectId='') {" in page
        assert "function runProjectRefreshTask(kind, projectId, runner) {" in page
        assert "clearProjectRefreshInflight();" in page
        assert "return runProjectRefreshTask(" in page
        assert "'project_picker'," in page
        assert "'feed_materials'," in page
        assert "'submissions'," in page
        assert "'materials_parse_status'," in page
        assert "skipCoalesce: true," in page
        assert "skipReadinessRefresh: skipReadinessRefresh," in page

    def test_index_frontend_adapts_project_auto_refresh_interval_for_stable_hot_projects(
        self, client
    ):
        response = client.get("/")
        assert response.status_code == 200
        page = response.text
        assert "const PROJECT_AUTO_REFRESH_INTERVAL_MS = 5000;" in page
        assert "const PROJECT_AUTO_REFRESH_WARM_INTERVAL_MS = 10000;" in page
        assert "const PROJECT_AUTO_REFRESH_STABLE_HOT_INTERVAL_MS = 15000;" in page
        assert "function currentProjectAutoRefreshInterval(projectId='') {" in page
        assert "if (document.visibilityState === 'hidden') return 0;" in page
        assert "return PROJECT_AUTO_REFRESH_STABLE_HOT_INTERVAL_MS;" in page
        assert "return PROJECT_AUTO_REFRESH_WARM_INTERVAL_MS;" in page
        assert "const overrideDelayMs = Number(opts.delayMs || 0);" in page
        assert (
            "const delayMs = overrideDelayMs > 0 ? overrideDelayMs : currentProjectAutoRefreshInterval(projectId);"
            in page
        )
        assert "}, delayMs);" in page
        assert (
            "return runProjectRefreshTask('scoring_readiness', id, () => refreshScoringReadiness(id, switchSeq, { skipCoalesce: true }));"
            in page
        )
        assert (
            "return runProjectRefreshTask('scoring_diagnostic_latest', id, () => refreshScoringDiagnostic(id, switchSeq, { skipCoalesce: true }));"
            in page
        )
        assert "const skipReadinessRefresh = !!opts.skipReadinessRefresh;" in page
        assert (
            "(typeof refreshSubmissions === 'function') ? refreshSubmissions(selectedId, switchSeq, { skipReadinessRefresh: true }) : Promise.resolve(),"
            in page
        )
        assert (
            "(typeof refreshMaterials === 'function') ? refreshMaterials(selectedId, switchSeq, { skipReadinessRefresh: true }) : Promise.resolve(),"
            in page
        )
        assert (
            "if (typeof refreshMaterials === 'function') await refreshMaterials(projectId, switchSeq, { skipReadinessRefresh: true });"
            in page
        )
        assert (
            "if (typeof refreshSubmissions === 'function') await refreshSubmissions(projectId, switchSeq, { skipReadinessRefresh: true });"
            in page
        )
        assert (
            "await refreshMaterials(projectId, switchSeq, { skipReadinessRefresh: true });" in page
        )

    def test_index_frontend_preserves_remaining_project_refresh_delay_across_brief_visibility_changes(
        self, client
    ):
        response = client.get("/")
        assert response.status_code == 200
        page = response.text
        assert "let projectAutoRefreshDueAtMs = 0;" in page
        assert "let projectVisibilityHiddenAtMs = 0;" in page
        assert "function clearProjectAutoRefresh(resetDueAt=true) {" in page
        assert "projectVisibilityHiddenAtMs = Date.now();" in page
        assert "clearProjectAutoRefresh(false);" in page
        assert (
            "const remainingMs = projectAutoRefreshDueAtMs > nowMs ? (projectAutoRefreshDueAtMs - nowMs) : 0;"
            in page
        )
        assert (
            "const hiddenDurationMs = hiddenAt > 0 ? Math.max(0, nowMs - hiddenAt) : Number.POSITIVE_INFINITY;"
            in page
        )
        assert "if (remainingMs > 0 && hiddenDurationMs < refreshIntervalMs) {" in page
        assert (
            "scheduleProjectAutoRefresh(currentId, projectSwitchSeq, { delayMs: remainingMs });"
            in page
        )

    def test_index_frontend_stabilizes_compare_report_opening_and_system_closure_binding(
        self, client
    ):
        response = client.get("/")
        assert response.status_code == 200
        page = response.text
        assert "let pendingOptimizationReportScroll = false;" in page
        assert "function scrollOptimizationReportPanelIntoView() {" in page
        assert "function flushOptimizationReportPanelScroll() {" in page
        assert "pendingOptimizationReportScroll = !!opts.scrollIntoView;" in page
        assert (
            "if (pendingOptimizationReportScroll) scrollOptimizationReportPanelIntoView();" in page
        )
        assert "flushOptimizationReportPanelScroll();" in page
        assert "replaceElementHtmlIfChanged(\n            'systemClosureSummaryResult'," in page
        assert "'system_closure_summary|' + buildTableRenderSignature([html])" in page
        assert "'scoring_diagnostic|' + String((latest && latest.filename) || '')" not in page

    def test_index_frontend_uses_structured_submission_table_layout(self, client):
        response = client.get("/")
        assert response.status_code == 200
        page = response.text
        assert '<div class="table-scroll submission-table-wrap">' in page
        assert '<table id="submissionsTable" class="submission-table">' in page
        assert '<col class="submission-col-file" />' in page
        assert '<col class="submission-col-score" />' in page
        assert '<col class="submission-col-diagnostic" />' in page
        assert '<col class="submission-col-created" />' in page
        assert '<col class="submission-col-actions" />' in page
        assert ".submission-table { table-layout:fixed; min-width:1120px; }" in page
        assert (
            ".submission-table .submission-stack { display:flex; flex-direction:column; gap:6px; min-width:0; }"
            in page
        )
        assert "function createSubmissionTableRowElement(submission, projectId, escapeFn) {" in page
        assert """+ '<tr class="submission-row">'""" in page
        assert """+ '<td class="submission-file-cell"><div class="submission-filename">'""" in page
        assert """+ '<td class="submission-score-cell"><div class="submission-stack">'""" in page
        assert (
            """+ '<td class="submission-diagnostic-cell"><div class="submission-stack">'""" in page
        )
        assert "function createMaterialTableRowElement(material, projectId) {" in page
        assert "function buildFeedMaterialTableRowHtml(material, projectId) {" in page
        assert "function buildGroundTruthTableRowHtml(record, index, isCurrent) {" in page
        assert """+ '<tr>'""" in page

    def test_index_frontend_skips_noop_table_rerenders_for_project_data(self, client):
        response = client.get("/")
        assert response.status_code == 200
        page = response.text
        assert "function clearTableRenderSignature(tableId) {" in page
        assert "function buildTableRenderSignature(rows, partsBuilder=null) {" in page
        assert "function replaceTableRowsIfChanged(tableId, rowsHtml, signature='') {" in page
        assert "clearTableRenderSignature(tableId);" in page
        assert (
            "replaceTableRowsIfChanged('submissionsTable', [], '__submissions__projectless__');"
            in page
        )
        assert (
            "replaceTableRowsIfChanged(\n            'submissionsTable',\n            rowHtml,"
            in page
        )
        assert (
            "replaceTableRowsIfChanged(\n            'materialsTable',\n            rowHtml,"
            in page
        )
        assert (
            "replaceTableRowsIfChanged(\n            'feedMaterialsTable',\n            rowHtml,"
            in page
        )
        assert (
            "replaceTableRowsIfChanged(\n            'groundTruthTable',\n            rowHtml,"
            in page
        )
        assert "function buildFeedMaterialTableRowHtml(material, projectId) {" in page
        assert "function buildGroundTruthTableRowHtml(record, index, isCurrent) {" in page
        assert "clearTableRenderSignature('submissionsTable');" in page
        assert "clearTableRenderSignature('materialsTable');" in page
        assert "clearTableRenderSignature('feedMaterialsTable');" in page
        assert "clearTableRenderSignature('groundTruthTable');" in page

    def test_index_frontend_skips_noop_panel_rerenders_for_project_diagnostics(self, client):
        response = client.get("/")
        assert response.status_code == 200
        page = response.text
        assert "function clearElementRenderSignature(elementId) {" in page
        assert "function replaceElementHtmlIfChanged(elementId, html, signature='') {" in page
        assert "clearElementRenderSignature('submissionDualTrackOverview');" in page
        assert "clearElementRenderSignature('scoringReadinessResult');" in page
        assert "clearElementRenderSignature('materialUtilizationResult');" in page
        assert "clearElementRenderSignature('scoringDiagnosticResult');" in page
        assert "replaceElementHtmlIfChanged(\n            'submissionDualTrackOverview'," in page
        assert "replaceElementHtmlIfChanged(\n            'scoringReadinessResult'," in page
        assert "replaceElementHtmlIfChanged(\n            'materialUtilizationResult'," in page
        assert "replaceElementHtmlIfChanged(\n            'scoringDiagnosticResult'," in page
        assert (
            "const generatedAtText = escapeHtmlText(String(data.generated_at || '').slice(0, 19) || '-');"
            in page
        )
        assert "const diagnosticSignatureHtml = html.replace(" in page
        assert "'；生成时间：__volatile__'" in page
        assert "function setResultLoading(resultId, label) {" in page
        assert "clearElementRenderSignature(resultId);" in page

    def test_index_frontend_renders_scoring_factors_panel_with_pending_feedback_templates(
        self, client
    ):
        response = client.get("/")
        assert response.status_code == 200
        page = response.text
        assert ".scoring-factors-grid { display:grid;" in page
        assert ".scoring-factors-card-grid { display:grid;" in page
        assert "function renderScoringFactorsPanel(payload) {" in page
        assert (
            "const pendingFeedbackPoints = Array.isArray(data.pending_feedback_scoring_points)"
            in page
        )
        assert "if (el.dataset) delete el.dataset.renderSignature;" in page
        assert "window.renderScoringFactorsPanel = renderScoringFactorsPanel;" in page
        assert "动态评分点（结合上传资料与真实评标）" in page
        assert "待确认真实评标反馈点（未正式生效）" in page
        assert "查看改写模板" in page
        assert "推荐章节：" in page
        assert "建议小节：" in page
        assert "当前施组锚点：" in page
        assert "插入建议" in page
        assert "查看当前施组锚点摘录" in page
        assert "查看小节草稿" in page
        assert "查看自动改写草案" in page
        assert "待确认反馈改写补丁包" in page
        assert "查看项目级补丁包" in page
        assert "查看可复制补丁" in page
        assert "阻断原因：" in page
        assert "句式模板" in page
        assert "可直接贴入正文" in page
        assert "写入方式：" in page
        assert "改写标题：" in page
        assert (
            "const pendingFeedbackPatchBundle = (data.pending_feedback_patch_bundle && typeof data.pending_feedback_patch_bundle === 'object')"
            in page
        )
        assert "const draftSectionParagraphs = Array.isArray(row.draft_section_paragraphs)" in page
        assert (
            "const autoRewriteParagraphs = Array.isArray(row.auto_rewrite_section_paragraphs)"
            in page
        )
        assert "const insertableParagraphs = Array.isArray(row.insertable_paragraphs)" in page
        assert "const rewriteSentences = Array.isArray(row.rewrite_sentences)" in page
        assert "；更新时间：__volatile__" in page
        assert "replaceElementHtmlIfChanged(\n            'scoringFactorsResult'," in page
        assert "renderScoringFactorsPanel(d);" in page
        assert "adaptive_point_count:" in page
        assert "pending_feedback_point_count:" in page
        assert "pending_feedback_patch_section_count:" in page

    def test_index_frontend_renders_writing_guidance_patch_bundle(self, client):
        response = client.get("/")
        assert response.status_code == 200
        page = response.text
        assert (
            "function renderPendingFeedbackPatchBundleSection(bundle, titleText='待确认反馈改写补丁包') {"
            in page
        )
        assert "下载改写补丁包(.md)" in page
        assert "下载改写补丁包(.docx)" in page
        assert "待确认反馈改写补丁包（来自待人工确认样本）" in page
        assert "这些反馈尚未正式进入评分主链，但已整理成项目级改写补丁供编制时参考。" in page
        assert (
            "const pendingFeedbackPatchBundle = (data.pending_feedback_patch_bundle && typeof data.pending_feedback_patch_bundle === 'object')"
            in page
        )
        assert (
            "const pendingFeedbackSummary = (data.pending_feedback_summary && typeof data.pending_feedback_summary === 'object')"
            in page
        )
        assert "html += renderPendingFeedbackPatchBundleSection(" in page
        assert "btnGuidancePatchBundleInlineDownload" in page
        assert "btnGuidancePatchBundleInlineDownloadDocx" in page
        assert "downloadWritingGuidancePatchBundle(projectId, 'guidanceResult');" in page
        assert "downloadWritingGuidancePatchBundleDocx(projectId, 'guidanceResult');" in page

    def test_index_frontend_web_button_contract_is_consistent(self, client):
        from scripts.check_web_button_contract import build_report_from_html

        response = client.get("/")
        assert response.status_code == 200
        report = build_report_from_html(response.text)

        assert report["ok"] is True
        assert report["missing_bindings"] == []
        assert report["button_count"] >= 60
        assert report["bound_button_count"] == report["button_count"]
        assert all(item["ok"] for item in report["export_contracts"])
        assert all(item["ok"] for item in report["inline_contracts"])
        assert all(item["ok"] for item in report["dynamic_button_contracts"])
        assert all(item["ok"] for item in report["critical_visible_button_contracts"])
        assert all(item["ok"] for item in report["action_result_contracts"])
        assert all(item["ok"] for item in report["guard_set_contracts"])
        assert all(item["ok"] for item in report["smoke_coverage_contracts"])
        assert report["smoke_gap_contracts"] == []
        assert report["smoke_allowlist_contract"]["ok"] is True
        assert report["smoke_allowlist_contract"]["stale_ids"] == []
        covered_ids = {item["button_id"] for item in report["smoke_coverage_contracts"]}
        assert "btnSaveApiKey" in covered_ids
        assert "btnClearApiKey" in covered_ids
        assert "btnStartNewProject" in covered_ids
        assert "btnCreateProjectFromTender" in covered_ids
        assert "deleteSelectedProjects" in covered_ids
        assert "refreshProjects" in covered_ids
        assert "btnSelectProjectBySearch" in covered_ids
        assert "btnUploadMaterials" in covered_ids
        assert "btnUploadBoq" in covered_ids
        assert "btnUploadDrawing" in covered_ids
        assert "btnUploadSitePhotos" in covered_ids
        assert "btnRefreshMaterials" in covered_ids
        assert "btnUploadShigong" in covered_ids
        assert "btnRefreshSubmissions" in covered_ids
        assert "btnRefreshFeedMaterials" in covered_ids
        assert "btnRefreshGroundTruth" in covered_ids
        assert "materialsTrialPreflightFollowUpAction" in covered_ids
        assert "btnCreateProject" in report["submit_bound_button_ids"]
        assert "btnCreateProjectFromTender" in report["submit_bound_button_ids"]
        assert "deleteCurrentProject" in report["submit_bound_button_ids"]

    def test_index_frontend_disallows_direct_safe_click_or_change_business_bindings(self, client):
        response = client.get("/")
        assert response.status_code == 200
        page = response.text
        direct_click_bindings = re.findall(
            r"safeClick\(\s*'[^']+'\s*,\s*([A-Za-z_][A-Za-z0-9_]*)\s*\)",
            page,
        )
        direct_change_bindings = re.findall(
            r"safeChange\(\s*'[^']+'\s*,\s*([A-Za-z_][A-Za-z0-9_]*)\s*\)",
            page,
        )
        assert direct_click_bindings == [], (
            "safeClick 业务绑定应通过 safeClick0 或显式 lambda 包装，避免把 click 事件对象误传进业务函数："
            f" {direct_click_bindings}"
        )
        assert direct_change_bindings == [], (
            "safeChange 业务绑定应通过 safeChange0 或显式 lambda 包装，避免把 change 事件对象误传进业务函数："
            f" {direct_change_bindings}"
        )

    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    def test_index_initial_picker_hides_ops_and_filters_to_default_month(
        self, mock_ensure, mock_load_projects, client
    ):
        mock_load_projects.return_value = [
            {
                "id": "ops1",
                "name": "OPS_SMOKE_1773985009729",
                "meta": {},
                "created_at": "2026-03-21T00:00:00+08:00",
                "updated_at": "2026-03-21T00:00:00+08:00",
            },
            {
                "id": "p-old",
                "name": "二月项目",
                "meta": {},
                "created_at": "2026-02-19T00:00:00+08:00",
                "updated_at": "2026-02-19T00:00:00+08:00",
            },
            {
                "id": "p-current",
                "name": "三月项目",
                "meta": {},
                "created_at": "2026-03-19T00:00:00+08:00",
                "updated_at": "2026-03-19T00:00:00+08:00",
            },
        ]

        response = client.get("/")

        assert response.status_code == 200
        page = response.text
        assert "OPS_SMOKE_1773985009729" not in page
        assert "二月项目" not in page
        assert "三月项目" in page
        assert 'id="projectMonthFilter"' in page
        assert "2026年02月" in page
        assert "2026年03月" in page

    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    def test_index_prefills_create_project_name_when_project_selected_by_query(
        self, mock_ensure, mock_load_projects, client
    ):
        mock_load_projects.return_value = [
            {
                "id": "p-dao",
                "name": "稻香村医疗救治服务综合楼EPC工程总承包",
                "meta": {},
                "created_at": "2026-03-27T00:00:00+08:00",
                "updated_at": "2026-03-27T00:00:00+08:00",
            }
        ]

        response = client.get("/?project_id=p-dao")

        assert response.status_code == 200
        page = response.text
        assert 'value="稻香村医疗救治服务综合楼EPC工程总承包"' in page
        assert 'title="稻香村医疗救治服务综合楼EPC工程总承包"' in page
        assert 'id="createProjectRecognizedName"' in page
        assert "系统已自动识别项目名称" in page

    def test_index_renders_16_dimension_weight_sliders(self, client):
        """Index page should render 16-dimension focus sliders on first paint."""
        response = client.get("/")
        assert response.status_code == 200
        for i in range(1, 17):
            dim_id = f"{i:02d}"
            assert f'id="w_{dim_id}"' in response.text

    def test_index_contains_web_fallback_forms(self, client):
        """Index page should keep non-JS fallback routes for upload/delete actions."""
        response = client.get("/")
        assert response.status_code == 200
        assert 'id="deleteProjectForm"' in response.text
        assert (
            'id="createProject" method="post" action="/web/create_project" class="create-project-form"'
            in response.text
        )
        assert 'id="apiKeyInput" class="auth-key-input primary-input"' in response.text
        assert (
            'class="field-label">项目名称：</span><input id="createProjectNameInput" class="project-name-input primary-input"'
            in response.text
        )
        assert 'id="createProjectRecognizedName"' in response.text
        assert 'id="createProjectRecognizedNameText"' in response.text
        assert 'action="/web/delete_project"' in response.text
        assert 'id="createProjectFromTender"' in response.text
        assert 'action="/web/create_project_from_tender"' in response.text
        assert 'id="btnStartNewProject"' in response.text
        assert response.text.index('id="btnStartNewProject"') < response.text.index(
            "<h2>2) 选择项目</h2>"
        )
        assert 'id="renameProjectNameInput"' not in response.text
        assert 'id="btnRenameProject"' not in response.text
        assert 'action="/web/upload_materials"' in response.text
        assert 'action="/web/upload_shigong"' in response.text
        assert 'id="scoreScaleSelect" class="compact-select"' in response.text
        assert 'id="groundTruthSubmissionSelect" class="wide-select"' in response.text
        assert 'id="gtJudgeCount" class="compact-select"' in response.text
        assert 'id="gtFinal" class="score-number-input"' in response.text

    def test_index_exposes_ground_truth_autocalc_and_evolve_autosave_bindings(self, client):
        response = client.get("/")
        assert response.status_code == 200
        page = response.text
        assert "施组文件" in page
        assert 'id="gtFinalRuleHint"' in page
        assert "function refreshGroundTruthScoringRule" in page
        assert "function applyGroundTruthFinalScoreAutoFill" in page
        assert "function submitGroundTruthDraft" in page
        assert "function clearGroundTruthDraftForm()" in page
        assert "clearGroundTruthDraftForm();" in page
        assert "function buildGroundTruthTableRowHtml(record, index, isCurrent) {" in page
        assert (
            "const sourceSubmissionFilename = String(row.source_submission_filename || '').trim();"
            in page
        )
        assert "/ground_truth/scoring_rule" in page
        assert "学习进化前需先完成真实评标录入" in page
        assert "本次已先自动录入当前表单中的真实评标，再执行学习进化。" in page
        assert "已评分（资料预警）" in page
        assert "let cachedProjectSubmissions = {};" in page
        assert "function buildGroundTruthSubmissionOptionsSignature" in page
        assert "await refreshGroundTruthSubmissionOptions(id, switchSeq, subs);" in page
        assert "isWarned: hasGate ? (!!utilGate.warned && !utilGate.blocked) : false," in page
        assert "const consistencyHitRate = toFiniteNumber(summary.consistency_hit_rate);" in page
        assert "当前缺口资料类型：" in page
        assert "阻断施组：" in page
        assert "建议动作：" in page
        assert "补充提示" in page
        assert "关键资料覆盖" in page
        assert "已上传类型覆盖" in page
        assert "提示：已有 " in page
        assert "const highlightColorForLevel = (level) =>" in page
        assert "return '#475569';" in page
        assert "小样本 bootstrap（已部署）" in page
        assert "小样本 bootstrap（候选未部署）" in page
        assert "当前校准形态" in page
        assert "最新项目级自动复核" in page
        assert "<b>自动复核</b>" in page
        assert "当前属于小样本 bootstrap 校准" in page
        assert (
            "await refreshGroundTruthSubmissionOptions(null, null, undefined, { forceFetch: true });"
            in page
        )
        assert (
            "(typeof refreshGroundTruthSubmissionOptions === 'function') ? refreshGroundTruthSubmissionOptions(selectedId, switchSeq) : Promise.resolve(),"
            not in page
        )

    def test_index_frontend_has_batch_project_delete_handler(self, client):
        """Section 2 should support selecting multiple projects and batch deleting them."""
        response = client.get("/")
        assert response.status_code == 200
        page = response.text
        assert (
            "const deleteSelectedProjectsBtn = document.getElementById('deleteSelectedProjects');"
            in page
        )
        assert "const delSel = document.getElementById('projectDeleteSelect');" in page
        assert "正在批量删除项目…" in page
        assert "batch_delete_projects" in page

    def test_index_head_returns_200(self, client):
        """HEAD / should be available to avoid browser connectivity false alarms."""
        response = client.head("/")
        assert response.status_code == 200

    def test_index_frontend_does_not_double_read_fetch_response_body(self, client):
        """UI handlers should not call res.text() after res.json() on the same response."""
        response = client.get("/")
        assert response.status_code == 200
        page = response.text
        assert "showJson('output', res.ok ? data : await res.text())" not in page
        assert "showJson('output', res.ok ? data : (data.detail || await res.text()))" not in page
        assert "function formatApiOutput" in page

    def test_index_frontend_compacts_output_and_applies_weights_without_confirm(self, client):
        response = client.get("/")
        assert response.status_code == 200
        page = response.text
        assert "执行结果摘要（最近一次操作）" in page
        assert "（这里只保留关键结果摘要，不展示冗长原始响应）" in page
        assert "function summarizeOutputScalar(value, maxLength=120)" in page
        assert "return [summarizeOutputScalar(data, 240)];" in page
        assert "function showOutputSummary(data)" in page
        assert "function showOutputTextSummary(text, message='')" in page
        assert "remainingKeys.slice(0, 8)" in page
        assert "…已省略 ' + (remainingKeys.length - 8) + ' 个字段" in page
        assert "当前项目已进入青天评标阶段。是否解锁并执行“保存+全项目重算”？" not in page
        assert "已接收请求：正在保存当前16维关注度并发起全项目重算..." in page
        assert "action: 'apply_weights_and_rescore'" in page
        assert "function buildExpertProfileSaveOutputSummary(data)" in page
        assert "function buildWeightsApplyOutputSummary(data, message='')" in page
        assert "function buildCalibratorActionOutputSummary(data, message='')" in page
        assert "function buildWeightsApplyRequestBody(forceUnlock=false)" in page
        assert "检测到项目已锁定，正在自动解锁并继续保存配置..." in page
        assert "检测到项目已锁定，正在自动解锁并继续重算..." in page
        assert "反馈闭环执行异常，请查看“执行结果摘要”。" in page
        assert "反馈闭环执行异常，请查看下方执行结果摘要。" in page
        assert "message: '系统自检已完成'" in page
        assert "message: '评分体系总览已加载'" in page
        assert "message: '评分体系 Markdown 已生成'" in page
        assert "message: '项目分析包已生成'" in page
        assert "message: '资料深读体检已生成'" in page
        assert "message: '资料知识画像已生成'" in page
        assert "message: '批量删除项目已完成'" in page
        assert "message: 'E2E 清理失败'" in page or "message: 'E2E 测试项目已清理'" in page
        assert "function renderEvaluationSummaryBlock(id, ok, title, payload)" in page
        assert "function renderEvaluationAggregateSummaryBlock(id, ok, title, payload)" in page
        assert "运行明细（已折叠）" in page
        assert "运行时校准治理" in page
        assert "已自动回退并回填评分" in page
        assert "当前项目级校准器" in page
        assert "近30天校准器/规则 MAE（内部校准指标）" in page
        assert "近30天校准器/规则 MAE</td>" not in page
        assert "历史回退候选" in page
        assert "最近运行时校准治理" in page
        assert "回退后已恢复稳定" in page
        assert "当前校准器状态" in page
        assert "校准器训练已完成" in page
        assert "校准分回填已完成" in page
        assert "一键闭环执行已完成" in page
        assert "闭环后状态" in page
        assert "当前分已与青天结果对齐" in page
        assert "当前分 MAE/RMSE 不劣于 V2" in page
        assert "当前分排序相关性不劣于 V2" in page
        assert "第一阶段封关 readiness" in page
        assert "通过门数：" in page
        assert "未通过门数：" in page
        assert "系统总封关 readiness" in page
        assert "系统封关推进摘要" in page
        assert "候选项目数：" in page
        assert "候选首选项目：" in page
        assert "候选推进阶段：" in page
        assert "候选推进建议：" in page
        assert "阻塞类型" in page
        assert "当前最该做的下一步" in page
        assert "下一步详情" in page
        assert "推荐入口" in page
        assert "入口动作" in page
        assert "前往：" not in page
        assert "最短闭环路径" in page
        assert "function ensureProjectSelectionClosureHintContainer()" in page
        assert "function clearProjectSelectionClosureHint()" in page
        assert "function renderProjectSelectionClosureHint(closure, projectId)" in page
        assert (
            """<button type="button" class="secondary" onclick="return focusSystemClosureEntrypoint("""
            in page
        )
        assert "actionEntrypointLabel: nextStepEntrypointLabel," in page
        assert "推荐入口：" in page
        assert "function clearActionScopedClosureZoneHint(actionStatusId)" in page
        assert "function buildProjectClosureStageSummary(closure, projectId)" in page
        assert "function buildProjectClosureStageInlineText(closure, projectId)" in page
        assert "function buildProjectClosureZoneHint(closure, projectId, zone)" in page
        assert "function ensureProjectClosureZoneHintContainer(anchorId, containerId)" in page
        assert "function clearProjectClosureZoneHint(containerId)" in page
        assert (
            "function renderProjectClosureZoneHint(zone, anchorId, containerId, closure, projectId)"
            in page
        )
        assert "function refreshProjectScopedClosureInlineHints()" in page
        assert (
            "renderProjectSelectionClosureHint(systemClosureSummaryState, String(matched.id || ''));"
            in page
        )
        assert "当前项目是系统总封关候选项目" in page
        assert "当前项目是当前优先收口项目" in page
        assert "当前项目已不是系统总封关的阻塞点" in page
        assert "zoneLabel + '是当前推荐入口'" in page
        assert "zoneLabel + '已完成当前阶段'" in page
        assert "系统封关阶段" in page
        assert "function buildSystemClosureNextStepText(closure, projectId, options=null)" in page
        assert (
            "function buildSystemClosureActionFollowUpMessage(baseMessage, closure, projectId, options=null)"
            in page
        )
        assert "function ensureSystemClosureActionHintContainer(anchorId)" in page
        assert "function clearSystemClosureActionHint(anchorId)" in page
        assert "function clearProjectScopedSystemClosureActionHints()" in page
        assert "function buildClosureHintCtaHtml(entrypointKey, buttonText='查看下一步')" in page
        assert "function renderClosureHintBlock(el, options=null)" in page
        assert "escapeHtmlText(JSON.stringify(normalizedKey))" in page
        assert "html += buildClosureHintCtaHtml(actionEntrypointKey, actionLabel);" in page
        assert "renderClosureHintBlock(el, {" in page
        assert "const gateSummary = String(card.material_gate_summary || '').trim();" in page
        assert (
            "function renderSystemClosureActionHint(anchorId, closure, projectId, options=null)"
            in page
        )
        assert (
            """<button type="button" class="secondary" onclick="return focusSystemClosureEntrypoint("""
            in page
        )
        assert "function buildCreateProjectFollowUpMessage(projectName, closure, projectId)" in page
        assert "该项目已成为系统总封关候选项目" in page
        assert "创建成功后的下一步" in page
        assert "资料上传后的下一步" in page
        assert "施组上传后的下一步" in page
        assert "评分完成后的下一步" in page
        assert "真实评标录入后的下一步" in page
        assert "next_step_action_label" in page
        assert "前往：" not in page
        assert "create_project_success" in page
        assert "create_from_tender_success" in page
        assert "reason: 'upload_materials'" in page
        assert "reason: 'upload_shigong'" in page
        assert "reason: 'score_shigong'" in page
        assert "reason: 'ground_truth_add'" in page
        assert "autoFocusEntrypoint: true" not in page
        assert "skipClosureSummaryRefresh: true" in page
        assert "reason: 'project_changed'" in page
        assert "function syncSystemClosureEntrypointHighlight(key, title)" in page
        assert "clearProjectScopedSystemClosureActionHints();" in page
        assert "clearSystemClosureActionHint(resultId);" in page
        assert "clearSystemClosureActionHint(id);" in page
        assert "clearSystemClosureActionHint('createProjectMessage');" in page
        assert "clearSystemClosureActionHint('selectProjectMessage');" in page
        assert "clearProjectSelectionClosureHint();" in page
        assert "clearActionScopedClosureZoneHint(id);" in page
        assert "clearProjectClosureZoneHint('materialsClosureHint');" in page
        assert "clearProjectClosureZoneHint('shigongClosureHint');" in page
        assert "已达第一阶段 ready 项目数：" in page
        assert "未 ready 项目数：" in page
        assert "下一优先项目：" in page
        assert "下一优先未通过门：" in page
        assert "项目级建议：" in page
        assert "async function refreshSystemClosureSummary(options=null)" in page
        assert "function systemClosureEntrypointMeta(key)" in page
        assert "function clearSystemClosureEntrypointHighlight()" in page
        assert "function highlightSystemClosureEntrypoint(key, title)" in page
        assert "function focusSystemClosureEntrypoint(key)" in page
        assert "clearSystemClosureEntrypointActionFocus();" in page
        assert "评估 规则/当前分/校准" in page
        assert "previewed_materials" in page
        assert "预解析完成" in page
        assert "后台仍在补全全文" in page

    def test_index_frontend_inline_scripts_are_valid_javascript(self, client):
        response = client.get("/")
        assert response.status_code == 200
        page = response.text
        scripts = re.findall(r"<script>(.*?)</script>", page, flags=re.S)
        assert scripts
        with tempfile.NamedTemporaryFile(
            "w", suffix=".js", delete=False, encoding="utf-8"
        ) as handle:
            handle.write("\n\n".join(scripts))
            script_path = handle.name
        try:
            result = subprocess.run(
                ["node", "--check", script_path],
                capture_output=True,
                text=True,
                check=False,
            )
        finally:
            os.unlink(script_path)
        assert result.returncode == 0, result.stderr

    def test_index_frontend_has_non_blocking_handlers_for_compare_and_adaptive(self, client):
        """Section 5/6 buttons should stay clickable and show explicit project-selection feedback."""
        response = client.get("/")
        assert response.status_code == 200
        page = response.text
        assert "const NON_BLOCKING_ACTION_BUTTON_IDS" in page
        assert "function ensureProjectForAction" in page
        assert "setResultLoading('compareResult'" in page
        assert "setResultLoading('adaptiveResult'" in page
        assert "setResultLoading('scoringDiagnosticResult'" in page
        assert "setResultLoading('systemImprovementResult'" in page
        assert "setResultLoading('selfCheckResult'" in page
        assert "setResultLoading('dataHygieneResult'" in page
        assert "setResultLoading('trialPreflightResult'" in page
        assert "function renderSystemImprovementOverviewPanel(payload)" in page
        assert (
            "const closureGateDetails = Array.isArray(data.closure_gate_details) ? data.closure_gate_details : [];"
            in page
        )
        assert (
            "const projectGapDetails = Array.isArray(data.project_gap_details) ? data.project_gap_details : [];"
            in page
        )
        assert (
            "const projectGateGapDetails = Array.isArray(data.project_gate_gap_details) ? data.project_gate_gap_details : [];"
            in page
        )
        assert (
            "const projectActionGapDetails = Array.isArray(data.project_action_gap_details) ? data.project_action_gap_details : [];"
            in page
        )
        assert (
            "const globalActionGapDetails = Array.isArray(data.global_action_gap_details) ? data.global_action_gap_details : [];"
            in page
        )
        assert (
            "const focusWorkstreamStatusSummaries = Array.isArray(data.focus_workstream_status_summaries)"
            in page
        )
        assert (
            "const opsAgentQualitySummary = (data.ops_agent_quality_summary && typeof data.ops_agent_quality_summary === 'object')"
            in page
        )
        assert (
            "const globalActionGroupSummaries = Array.isArray(data.global_action_group_summaries) ? data.global_action_group_summaries : [];"
            in page
        )
        assert (
            "const globalAutoActionGapDetails = Array.isArray(data.global_auto_action_gap_details) ? data.global_auto_action_gap_details : [];"
            in page
        )
        assert (
            "const globalReadonlyActionGapDetails = Array.isArray(data.global_readonly_action_gap_details) ? data.global_readonly_action_gap_details : [];"
            in page
        )
        assert (
            "const globalManualActionGapDetails = Array.isArray(data.global_manual_action_gap_details) ? data.global_manual_action_gap_details : [];"
            in page
        )
        assert "let systemImprovementOverviewLastPayload = null;" in page
        assert "let systemImprovementActionGroupFilter = 'all';" in page
        assert "let systemImprovementWorkstreamFilter = 'all';" in page
        assert "async function refreshSystemImprovementOverview(options=null)" in page
        assert "const normalizeSystemImprovementActionGroupFilter = (filter) => {" in page
        assert "const formatSystemImprovementActionGroupFilterLabel = (label, count) => {" in page
        assert (
            "const setSystemImprovementActionGroupFilter = (filter, rerender = true) => {" in page
        )
        assert "const normalizeSystemImprovementWorkstreamFilter = (filter) => {" in page
        assert "const formatSystemImprovementWorkstreamFilterLabel = (label, count) => {" in page
        assert "const setSystemImprovementWorkstreamFilter = (filter, rerender = true) => {" in page
        assert "const executionModeLabel = String(row.execution_mode_label || '').trim();" in page
        assert "const priorityReasonLabel = String(row.priority_reason_label || '').trim();" in page
        assert "const prioritySortLabel = String(row.priority_sort_label || '').trim();" in page
        assert "const groupReasonLabel = String(row.group_reason_label || '').trim();" in page
        assert "项目内未通过门清单" in page
        assert "按动作归并的项目收口清单" in page
        assert "系统级优先动作清单" in page
        assert "可自动收口动作" in page
        assert "只读诊断动作" in page
        assert "必须人工处理动作" in page
        assert "查看全部工作流" in page
        assert "待收口" in page
        assert "阻断" in page
        assert "const renderGlobalActionGroupSummaries = (rows, activeFilter) => {" in page
        assert (
            "const renderGlobalActionGroupEmptyState = (title, rows, actionGroup, activeFilter) => {"
            in page
        )
        assert (
            "const findFocusWorkstreamStatusSummary = (rows, statusValue) => rows.find((item) => {"
            in page
        )
        assert "const renderFocusWorkstreamFilters = (rows, summaries, activeFilter) => {" in page
        assert "当前没有命中该状态的工作流。" in page
        assert 'data-system-improvement-action-group-filter="all"' in page
        assert 'data-system-improvement-workstream-filter="all"' in page
        assert "formatSystemImprovementActionGroupFilterLabel('查看全部动作', totalCount)" in page
        assert "formatSystemImprovementActionGroupFilterLabel(label, count)" in page
        assert "formatSystemImprovementWorkstreamFilterLabel('查看全部工作流', totalCount)" in page
        assert "工作流状态" in page
        assert (
            "const priorityWorkstreamTitle = String(summaryRow.priority_workstream_title || '').trim();"
            in page
        )
        assert (
            "const priorityActionLabel = String(summaryRow.priority_action_label || '').trim();"
            in page
        )
        assert "建议优先工作流：" in page
        assert "当前仅显示：" in page
        assert "当前为空" in page
        assert "执行方式：" in page
        assert "分组依据：" in page
        assert "优先依据：" in page
        assert "排序优先依据：" in page
        assert "const renderGlobalActionGapTable = (title, rows) => {" in page
        assert "const renderOpsAgentQualitySummary = (summary) => {" in page
        assert "自动巡检智能体质量" in page
        assert (
            "const manualConfirmationRows = Array.isArray(row.manual_confirmation_rows) ? row.manual_confirmation_rows : [];"
            in page
        )
        assert "最近巡检轮次（通过/待收口/失败）" in page
        assert "最近连续未通过轮次/人工确认轮次" in page
        assert "当前主因标签/连续同主因轮次" in page
        assert "当前主因项目" in page
        assert "当前人工确认项目" in page
        assert "人工确认复验审计" in page
        assert "复验前：" in page
        assert "复验摘要：" in page
        assert "变化：" in page
        assert "执行一键闭环复验人工确认结果" in page
        assert "已自动高亮“一键闭环执行”按钮，可直接复验人工确认后的学习/校准状态。" in page
        assert "const entrypointHtml = renderSystemImprovementEntrypoint(detailRow);" in page
        assert "建议入口" in page
        assert "系统当前建议：" in page
        assert "confirm_extreme_sample=1" in page
        assert "请先在“施组文件”下拉中选择步骤4已上传施组，再录入真实评标。" in page
        assert "最近主因分布" in page
        assert "历史自动修复成功率/自动学习成功率" in page
        assert "最近巡检质量审计" in page
        assert "人工确认需求/复验失败" in page
        assert "Bootstrap 监控/低质量账号池" in page
        assert "function setDataHygieneResult(summary, details, isError)" in page
        assert "function renderDataHygienePanel(payload)" in page
        assert "window.renderDataHygienePanel = renderDataHygienePanel;" in page
        assert "async function runSystemDataHygiene()" in page
        assert "safeClick('btnSelfCheck'" in page
        assert "/system/self_check" in page
        assert "/system/improvement_overview" in page
        assert "/system/data_hygiene" in page
        assert "/evaluation/summary" in page
        assert "function syncProjectSelectionUrl(projectId='')" in page
        assert 'id="section-expert-profile"' in page
        assert 'id="section-learning-evolution"' in page
        assert (
            "const focusWorkstreams = Array.isArray(data.focus_workstreams) ? data.focus_workstreams : [];"
            in page
        )
        assert "const systemImprovementEntrypointMeta = (key) => {" in page
        assert "if (raw === 'system_self_check') {" in page
        assert (
            "return { sectionId: 'section-learning-evolution', focusId: 'btnSelfCheck' };" in page
        )
        assert (
            "if (raw === 'system_data_hygiene') return { sectionId: 'section-learning-evolution', focusId: 'btnDataHygiene' };"
            in page
        )
        assert "function ensureSystemImprovementProjectSelection(projectId, projectName='')" in page
        assert (
            "function renderSystemImprovementEntrypointActionHint(entrypointKey, projectName='', entrypointLabel='', actionLabel='')"
            in page
        )
        assert (
            "async function navigateSystemImprovementWorkstream(projectId, projectName, entrypointKey, entrypointLabel='', actionLabel='')"
            in page
        )
        assert "function clearSystemClosureEntrypointActionFocus()" in page
        assert "function focusSystemClosureEntrypointAction(key)" in page
        assert "window.__systemClosureEntrypointActionQueueTimer" in page
        assert "target.closest('button[data-system-improvement-workstream-filter]')" in page
        assert "target.closest('button[data-system-improvement-action-group-filter]')" in page
        assert "target.closest('a[data-system-improvement-workstream]')" in page
        assert 'data-system-improvement-workstream="1"' in page
        assert 'data-action-label="' in page
        assert 'data-project-id="' in page
        assert 'data-project-name="' in page
        assert "await refreshProjects(targetProjectId, {" in page
        assert "await onProjectChanged({ skipClosureSummaryRefresh: true });" in page
        assert (
            "renderProjectSelectionClosureHint(systemClosureSummaryState, targetProjectId);" in page
        )
        assert "focusSystemClosureEntrypoint(key);" in page
        assert "focusSystemClosureEntrypointAction(key);" in page
        assert "target.scrollIntoView({ behavior: 'auto', block: 'center' });" in page
        assert "focusSystemClosureEntrypoint('auto_run_reflection');" in page
        assert "await loadFeedbackGovernancePanel(projectId, {" in page
        assert "await refreshSystemImprovementOverview({" in page
        assert "silent: true," in page
        assert "renderSystemClosureActionHint('evolveResult', closureSummary, projectId, {" in page
        assert (
            "renderSystemClosureActionHint('calibTrainResult', closureSummary, projectId, {" in page
        )
        assert "focusSystemClosureEntrypoint(closureSummary.next_step_entrypoint_key);" in page
        assert "clearSystemClosureActionHint('evolveResult');" in page
        assert "clearSystemClosureActionHint('calibTrainResult');" in page
        assert "function safeClick0(id, fn) {" in page
        assert "function buttonBusy(target) {" in page
        assert "function setButtonBusyState(target, busy, options=null) {" in page
        assert "el.setAttribute('aria-busy', 'true');" in page
        assert "el.classList.add('button-busy');" in page
        assert "el.classList.remove('button-busy');" in page
        assert "function safeChange0(id, fn) {" in page
        assert "safeChange0('groundTruthOtherProject', refreshGroundTruth);" in page
        assert "safeClick0('btnRefreshGroundTruth', refreshGroundTruth);" in page
        assert "safeClick0('btnWeightsApply', applyExpertProfileAndRescore);" in page
        assert "safeClick0('btnStartNewProject', startNewProjectIntake);" in page
        assert "safeClick0('btnRenameProject', renameCurrentProject);" in page
        assert "safeChange0('projectSelect', onProjectChanged);" in page
        assert "safeClick0('btnSelectProjectBySearch', locateProjectBySearch);" in page
        assert "safeClick0('btnScoringFactors', loadScoringFactorsOverview);" in page
        assert "safeClick0('btnScoringFactorsMd', loadScoringFactorsMarkdown);" in page
        assert "safeClick0('btnAnalysisBundle', loadProjectAnalysisBundle);" in page
        assert "safeClick0('btnAnalysisBundleDownload', downloadProjectAnalysisBundle);" in page
        assert "section.scrollIntoView({ behavior: 'auto', block: 'start' });" in page
        assert "syncProjectSelectionUrl(selectedId);" in page
        assert "setSystemImprovementActionGroupFilter('all', false);" in page
        assert "setSystemImprovementWorkstreamFilter('all', false);" in page
        assert "继续完善工作流" in page
        assert "系统总封关未通过门清单" in page
        assert "逐项目收口清单" in page
        assert "系统继续完善总览" in page
        assert "function renderTrialPreflightPanel(payload)" in page
        assert "/trial_preflight" in page
        assert "/trial_preflight.md" in page
        assert "/trial_preflight.docx" in page
        assert (
            "const signoff = (data.signoff && typeof data.signoff === 'object') ? data.signoff : {};"
            in page
        )
        assert (
            "const warningDetails = (data.warning_details && typeof data.warning_details === 'object') ? data.warning_details : {};"
            in page
        )
        assert (
            "const recordDraft = (data.record_draft && typeof data.record_draft === 'object') ? data.record_draft : {};"
            in page
        )
        assert "const verificationChecklist = Array.isArray(signoff.verification_checklist)" in page
        assert (
            "const topHighSeverityConflicts = Array.isArray(warningDetails.high_severity_material_conflicts)"
            in page
        )
        assert "const warningAckItems = Array.isArray(recordDraft.warning_ack_items)" in page
        assert "签发决策：" in page
        assert "风险级别：" in page
        assert "签发摘要：" in page
        assert "核验清单" in page
        assert "试车记录草案（待确认）" in page
        assert "重点警告明细" in page
        assert "高严重度资料冲突清单" in page
        assert "冲突处理建议" in page
        assert "建议处理" in page
        assert "const entrypointLabel = String(row.entrypoint_label || '').trim();" in page
        assert (
            "const entrypointKey = String(row.entrypoint_key || 'upload_shigong').trim() || 'upload_shigong';"
            in page
        )
        assert (
            "const entrypointReasonLabel = String(row.entrypoint_reason_label || '').trim();"
            in page
        )
        assert "const materialType = String(row.material_type || '').trim();" in page
        assert (
            "const materialReviewEntryLabel = String(row.material_review_entrypoint_label || '').trim();"
            in page
        )
        assert (
            "const materialReviewReasonLabel = String(row.material_review_reason_label || '').trim();"
            in page
        )
        assert (
            "const materialReviewEntryAnchor = String(row.material_review_entrypoint_anchor || '').trim()"
            in page
        )
        assert "const actionLabel = String(row.action_label || '').trim();" in page
        assert "推荐入口依据：" in page
        assert "资料核对依据：" in page
        assert 'data-trial-preflight-entry="shigong_update"' in page
        assert 'data-trial-preflight-entry="material_review"' in page
        assert 'data-material-entry-label="' in page
        assert 'data-material-entry-reason-label="' in page
        assert "function materialTypeUploadButtonId(materialType)" in page
        assert "function materialTrialPreflightHintButtonId(materialType)" in page
        assert "function materialTrialPreflightBadgeId(materialType)" in page
        assert (
            "function materialTrialPreflightPriorityLabel(materialType, entrypointLabel='')" in page
        )
        assert "function materialTrialPreflightHintLabel(materialType)" in page
        assert "function setMaterialTrialPreflightButtonHint(materialType='')" in page
        assert (
            "function setMaterialTrialPreflightActionBadge(materialType='', entrypointLabel='')"
            in page
        )
        assert "let materialTrialPreflightFollowUpState = null;" in page
        assert (
            "function setMaterialTrialPreflightFollowUp(entrypointLabel='', entrypointReasonLabel='')"
            in page
        )
        assert "function materialTrialPreflightFollowUpSummary()" in page
        assert "function materialTrialPreflightFollowUpEntryKey(entrypointLabel='')" in page
        assert (
            "function materialTrialPreflightFollowUpActionLabel(entrypointLabel='', entrypointReasonLabel='', promoted=false)"
            in page
        )
        assert (
            "function setMaterialTrialPreflightHint(entrypointLabel='', entrypointReasonLabel='')"
            in page
        )
        assert (
            "function setMaterialTrialPreflightFollowUpHint(entrypointLabel='', entrypointReasonLabel='')"
            in page
        )
        assert (
            "function setMaterialTrialPreflightFollowUpAction(entrypointLabel='', entrypointReasonLabel='', promoted=false)"
            in page
        )
        assert "function focusMaterialTrialPreflightFollowUpAction()" in page
        assert "function clearTrialPreflightEntrypointFocus()" in page
        assert "function markTrialPreflightEntrypointFocus(el, styleConfig)" in page
        assert (
            "function focusMaterialUploadEntrypoint(materialType, entrypointLabel='', entrypointReasonLabel='')"
            in page
        )
        assert (
            "function focusShigongWorkspaceEntrypoint(entrypointKey, entrypointLabel, entrypointReasonLabel)"
            in page
        )
        assert (
            "function navigateTrialPreflightEntrypoint(kind, anchor, materialType, entrypointKey, entrypointLabel, entrypointReasonLabel, materialReviewEntryLabel, materialReviewReasonLabel)"
            in page
        )
        assert "setMaterialTrialPreflightButtonHint(materialType);" in page
        assert "setMaterialTrialPreflightButtonHint('');" in page
        assert "setMaterialTrialPreflightActionBadge(materialType, entrypointLabel);" in page
        assert "setMaterialTrialPreflightActionBadge('', '');" in page
        assert "setMaterialTrialPreflightFollowUp(entrypointLabel, entrypointReasonLabel);" in page
        assert "setMaterialTrialPreflightFollowUp('', '');" in page
        assert "setMaterialTrialPreflightHint(entrypointLabel, entrypointReasonLabel);" in page
        assert "setMaterialTrialPreflightHint('', '');" in page
        assert "setMaterialTrialPreflightFollowUpHint('', '');" in page
        assert (
            "setMaterialTrialPreflightFollowUpAction(entrypointLabel, entrypointReasonLabel, false);"
            in page
        )
        assert "setMaterialTrialPreflightFollowUpAction('', '');" in page
        assert "actionEl.classList.toggle('secondary', !promoted);" in page
        assert "actionEl.classList.toggle('trial-follow-up-promoted', promoted);" in page
        assert "focusMaterialTrialPreflightFollowUpAction();" in page
        assert "const gateSummary = document.getElementById('shigongGateSummary');" in page
        assert "const actionStatus = document.getElementById('shigongActionStatus');" in page
        assert "setShigongTrialPreflightButtonHints(key, entrypointLabel);" in page
        assert "setShigongTrialPreflightButtonHints('', '');" in page
        assert "setShigongTrialPreflightActionBadge(key, entrypointLabel);" in page
        assert "setShigongTrialPreflightActionBadge('', '');" in page
        assert "setShigongTrialPreflightHint(entrypointLabel, entrypointReasonLabel);" in page
        assert "setShigongTrialPreflightHint('', '');" in page
        assert "setShigongTrialPreflightState('', '', '');" in page
        assert "setShigongTrialPreflightState(key, entrypointLabel, entrypointReasonLabel);" in page
        assert (
            "if (okCount > 0 && visibleConfirmed) promoteShigongTrialPreflightAfterUpload();"
            in page
        )
        assert "markTrialPreflightEntrypointFocus(section, {" in page
        assert "markTrialPreflightEntrypointFocus(primaryButton, {" in page
        assert "markTrialPreflightEntrypointFocus(gateSummary, {" in page
        assert "markTrialPreflightEntrypointFocus(actionStatus, {" in page
        assert (
            "return focusShigongWorkspaceEntrypoint(entrypointKey, entrypointLabel, entrypointReasonLabel);"
            in page
        )
        assert 'data-entrypoint-label="' in page
        assert 'data-entrypoint-reason-label="' in page
        assert "target.closest('a[data-trial-preflight-entry]')" in page
        assert "trialPreflightResultEl.addEventListener('click', (event) => {" in page
        assert "记录状态" in page
        assert "建议试车时间" in page
        assert "需确认警告项" in page
        assert "建议优先动作：" in page
        assert "试车报告下载准备中..." in page
        assert "试车报告 DOCX 下载准备中..." in page
        assert "试车前综合体检报告下载已触发。" in page
        assert "试车前综合体检 DOCX 报告下载已触发。" in page
        assert "function updateShigongGateSummary" in page
        assert "function materialTypeUploadAnchor" in page
        assert "function applyMaterialUploadZoneHighlights" in page
        assert "function clearMaterialUploadZoneHighlights" in page
        assert "function clearMaterialParsePolling" in page
        assert "function applyMaterialParseZoneState" in page
        assert "function scheduleMaterialParsePolling" in page
        assert "function scheduleProjectAutoRefresh" in page
        assert "materialsParseSummary" in page
        assert "materialsDebugPanel" in page
        assert "materialsDebugInfo" in page
        assert "materialViewResult" in page
        assert "function setMaterialParseSummary" in page
        assert "function renderMaterialParseSummary" in page
        assert "function renderMaterialParseDebugInfo" in page
        assert "function viewMaterialRow" in page
        assert "底层运行监控（Debug Info）" in page
        assert "<th>资料名称</th>" in page
        assert "<th>解析完成时间</th>" in page
        assert "latest_finished_at" in page
        assert "parse_stage_label" in page
        assert "parse_route_label" in page
        assert "queue_position" in page
        assert "boq_saved_row_count" in page
        assert "boq_resume_hit_rate" in page
        assert "scheduler_project_continuity_bonus_hits" in page
        assert "scheduler_active_project_bonus_hits" in page
        assert "scheduler_claim_context_cache_hits" in page
        assert "scheduler_status_core_cache_rebuilds" in page
        assert "scheduler_cache_hit_total" in page
        assert "scheduler_cache_hit_ratio" in page
        assert "scheduler_project_cache_hit_total" in page
        assert "scheduler_project_status_core_cache_hits" in page
        assert "scheduler_project_cache_net_savings" in page
        assert "scheduler_project_cache_state" in page
        assert "scheduler_project_jobs_summary_cache_state" in page
        assert "scheduler_project_cache_hot_layer_count" in page
        assert "scheduler_project_recent_avoided_rebuild_layers" in page
        assert "scheduler_project_recent_rebuilt_layers" in page
        assert "scheduler_project_recent_request_window_size" in page
        assert "scheduler_project_recent_cold_start_round_count" in page
        assert "scheduler_project_recent_warming_round_count" in page
        assert "scheduler_project_recent_steady_round_count" in page
        assert "scheduler_project_recent_consecutive_steady_round_count" in page
        assert "scheduler_project_recent_stable_hot_threshold" in page
        assert "scheduler_project_recent_stable_hot" in page
        assert "scheduler_project_recent_stable_hot_remaining_rounds" in page
        assert "scheduler_project_recent_stable_hot_progress_completed_rounds" in page
        assert "scheduler_project_recent_stable_hot_progress_label" in page
        assert "scheduler_project_recent_stable_hot_progress_ratio" in page
        assert "scheduler_project_recent_stable_hot_progress_percent" in page
        assert "scheduler_project_recent_stable_hot_progress_percent_label" in page
        assert "scheduler_project_recent_stable_hot_eta_hint" in page
        assert "scheduler_project_recent_stable_hot_eta_short_label" in page
        assert "scheduler_project_recent_stable_hot_progress_summary_label" in page
        assert "scheduler_project_recent_stable_hot_badge_label" in page
        assert "scheduler_project_recent_stable_hot_rule_label" in page
        assert "scheduler_project_recent_window_state" in page
        assert "scheduler_project_recent_avoided_rebuild_work_units" in page
        assert "scheduler_project_recent_rebuilt_work_units" in page
        assert "scheduler_project_recent_avoided_rebuild_work_units_avg" in page
        assert "scheduler_project_recent_rebuilt_work_units_avg" in page
        assert "detail.indexOf('force_unlock=true') >= 0" in page
        assert "if (v == null) return null;" in page
        assert "if (typeof v === 'string' && !v.trim()) return null;" in page
        assert "检测到项目已锁定，正在解锁并继续重算..." in page
        assert "预解析完成" in page
        assert "后台仍在补全全文" in page
        assert "BOQ 提速：" in page
        assert "差量命中率" in page
        assert "调度命中：" in page
        assert "缓存命中：" in page
        assert "缓存重建：" in page
        assert "缓存总览：" in page
        assert "缓存命中率" in page
        assert "当前项目缓存：" in page
        assert "净节省冷路径" in page
        assert "状态 已转热" in page
        assert "轮阶段：冷启动首轮" in page
        assert "窗口状态 已稳定转热" in page
        assert "连续稳定轮询 " in page
        assert "已达到稳定转热阈值" in page
        assert "还差 " in page
        assert "稳定转热进度 " in page
        assert "热态标签 " in page
        assert "规则：" in page
        assert "最近一轮避开重建：" in page
        assert "最近一轮估算链路成本：" in page
        assert "轮平均链路成本：避开 " in page
        assert "缓存分层：" in page
        assert "schedulerProjectJobsSummaryCacheState" in page
        assert "活跃项目命中" in page
        assert "同项目接力" in page
        assert "follow-up full 接力" in page
        assert "配额耗尽 项目" in page
        assert "确认删除该资料文件？\\n\\n" in page
        assert "PROJECT_AUTO_REFRESH_INTERVAL_MS" in page
        assert "visibilitychange" in page
        assert "/materials/parse_status" in page
        assert "parsed_ready" in page
        assert "资料提取锚点：" in page
        assert "资料锚点类别：" in page
        assert "命中证据：" in page
        assert "缺口证据：" in page
        assert "命中类别：" in page
        assert "待补类别：" in page
        assert "资料支撑维度总览" in page
        assert "支撑较强维度" in page
        assert "证据薄弱维度" in page
        assert "维度支撑明细（16维）" in page
        assert "评分置信等级（内部指数）" in page
        assert "评分置信度" not in page
        assert "评分进化约束总览" in page
        assert "进化反馈约束" in page
        assert "高置信逻辑骨架约束" in page
        assert "当前有效权重（Top）" in page
        assert "已关联评分记录" in page

    def test_index_frontend_has_no_broken_multiline_regex_literal(self, client):
        """Rendered JS should not contain regex literals split by line breaks (would break entire script)."""
        response = client.get("/")
        assert response.status_code == 200
        page = response.text
        assert re.search(r"replace\(/\s*\n\s*/g", page) is None
        assert "replace(/\\n/g" in page

    def test_index_frontend_does_not_embed_raw_multiline_confirm_string(self, client):
        response = client.get("/")
        assert response.status_code == 200
        page = response.text
        assert (
            "window.confirm(warning + '\\n\\n是否确认将该样本纳入本次「' + actionLabel + '」？');"
            in page
        )
        assert (
            "window.confirm(warning + '\n\n是否确认将该样本纳入本次「' + actionLabel + '」？');"
            not in page
        )
        assert (
            "window.confirm(detail + '\\n\\n是否确认将该极端样本纳入本次「' + actionLabel + '」？');"
            in page
        )
        assert (
            "window.confirm(detail + '\n\n是否确认将该极端样本纳入本次「' + actionLabel + '」？');"
            not in page
        )
        assert "window.confirm(detail + '\\n\\n是否确认解锁当前项目并继续重算？');" in page
        assert "window.confirm(detail + '\n\n是否确认解锁当前项目并继续重算？');" not in page

    def test_index_frontend_binds_core_buttons_for_sections_5_6_7(self, client):
        """Core buttons in sections 5/6/7 should have safeClick bindings in generated page."""
        response = client.get("/")
        assert response.status_code == 200
        page = response.text
        for button_id in (
            "btnOptimizationReport",
            "btnCompare",
            "btnCompareReport",
            "btnInsights",
            "btnLearning",
            "btnScoringDiagnostic",
            "btnEvidenceTrace",
            "btnScoringBasis",
            "btnAdaptive",
            "btnAdaptivePatch",
            "btnAdaptiveValidate",
            "btnAdaptiveApply",
            "btnEvolve",
            "btnSelfCheck",
            "btnSystemImprovementOverview",
            "btnDataHygiene",
            "btnEvalSummaryV2",
            "btnTrialPreflight",
            "btnWritingGuidance",
            "btnCompilationInstructions",
            "btnRefreshGroundTruthSubmissionOptions",
        ):
            assert f"safeClick('{button_id}'" in page

    def test_index_frontend_section_5_6_7_actions_have_explicit_project_guard(self, client):
        """Section 5/6/7 action handlers should proactively show project-selection errors instead of silent failure."""
        response = client.get("/")
        assert response.status_code == 200
        page = response.text
        for guard_result_id in (
            "compareResult",
            "compareReportResult",
            "insightsResult",
            "learningResult",
            "scoringDiagnosticResult",
            "trialPreflightResult",
            "evidenceTraceResult",
            "scoringBasisResult",
            "adaptiveResult",
            "adaptivePatchResult",
            "adaptiveValidateResult",
            "adaptiveApplyResult",
            "evolveResult",
            "guidanceResult",
            "compilationInstructionsResult",
        ):
            assert f"ensureProjectForAction('{guard_result_id}')" in page

    def test_index_upload_buttons_use_inline_fallback_click_and_form_submit_compat(self, client):
        """Upload/score forms should rely on submit handlers and native file labels, not old fallback click interception."""
        response = client.get("/")
        assert response.status_code == 200
        page = response.text
        assert (
            '<button type="submit" id="btnUploadMaterials" data-loading-label="上传资料中...">上传资料</button>'
            in page
        )
        assert (
            'id="btnUploadMaterialsBadge" class="status-badge-warning" style="display:none"></span>'
            in page
        )
        assert 'id="btnUploadMaterialsHint" class="note" style="display:none"></span>' in page
        assert (
            'id="btnUploadBoqBadge" class="status-badge-warning" style="display:none"></span>'
            in page
        )
        assert 'id="btnUploadBoqHint" class="note" style="display:none"></span>' in page
        assert (
            'id="btnUploadDrawingBadge" class="status-badge-warning" style="display:none"></span>'
            in page
        )
        assert 'id="btnUploadDrawingHint" class="note" style="display:none"></span>' in page
        assert (
            'id="btnUploadSitePhotosBadge" class="status-badge-warning" style="display:none"></span>'
            in page
        )
        assert 'id="btnUploadSitePhotosHint" class="note" style="display:none"></span>' in page
        assert 'id="materialsTrialPreflightHint" style="display:none' in page
        assert (
            'id="materialsTrialPreflightFollowUpAction" class="secondary" style="display:none'
            in page
        )
        assert (
            'id="btnUploadShigong" name="submit_action" value="upload" data-loading-label="上传施组中...">上传施组</button>'
            in page
        )
        assert 'id="btnUploadShigongHint" class="note" style="display:none"></span>' in page
        assert (
            'id="btnScoreShigong" class="secondary" formaction="/web/score_shigong" name="submit_action" value="score" data-default-label="评分施组" data-locked-label="评分施组（确认后重算）" data-loading-label="评分施组中...">评分施组</button>'
            in page
        )
        assert 'id="btnScoreShigongHint" class="note" style="display:none"></span>' in page
        assert "button.secondary.button-lock-emphasis" in page
        assert 'id="shigongTrialPreflightBadge"' in page
        assert (
            'id="shigongTrialPreflightBadge" class="status-badge-warning" style="display:none"></span>'
            in page
        )
        assert 'id="shigongScoreLockBadge"' in page
        assert 'class="status-badge-warning"' in page
        assert (
            'id="shigongScoreLockBadge" class="status-badge-warning" style="display:none"' in page
        )
        assert "锁定项目可确认后重算" in page
        assert 'id="shigongScoreLockHint"' in page
        assert 'class="status-callout"' in page
        assert 'id="shigongScoreLockHint" class="status-callout" style="display:none"' in page
        assert 'id="shigongTrialPreflightHint" style="display:none' in page
        assert "锁定说明：" in page
        assert (
            "若项目已进入青天评标阶段，点击“评分施组”后会先提示确认；确认后系统会自动解锁并继续重算，无需删除项目。"
            in page
        )
        assert (
            "function shigongTrialPreflightPriorityLabel(entrypointKey, entrypointLabel='')" in page
        )
        assert (
            "function setShigongTrialPreflightActionBadge(entrypointKey='', entrypointLabel='')"
            in page
        )
        assert (
            "function setShigongTrialPreflightButtonHints(entrypointKey='', entrypointLabel='')"
            in page
        )
        assert "let shigongTrialPreflightState = null;" in page
        assert (
            "function setShigongTrialPreflightState(entrypointKey='', entrypointLabel='', entrypointReasonLabel='')"
            in page
        )
        assert "function promoteShigongTrialPreflightAfterUpload()" in page
        assert "const badgeEl = document.getElementById('shigongTrialPreflightBadge');" in page
        assert "const uploadHintEl = document.getElementById('btnUploadShigongHint');" in page
        assert "const scoreHintEl = document.getElementById('btnScoreShigongHint');" in page
        assert "建议先核对招标文件和答疑" in page
        assert "建议先核对清单" in page
        assert "建议先核对图纸" in page
        assert "建议先核对现场照片" in page
        assert "优先：核对招标文件和答疑" in page
        assert "优先：核对清单" in page
        assert "优先：核对图纸" in page
        assert "优先：核对现场照片" in page
        assert "试车下一步：" in page
        assert "完成本步后：" in page
        assert "继续处理施组：" in page
        assert "const hintEl = document.getElementById('materialsTrialPreflightHint');" in page
        assert (
            "const actionEl = document.getElementById('materialsTrialPreflightFollowUpAction');"
            in page
        )
        assert "actionEl.scrollIntoView({ behavior: 'smooth', block: 'nearest' });" in page
        assert "button.trial-follow-up-promoted" in page
        assert (
            "const materialsTrialPreflightFollowUpActionEl = document.getElementById('materialsTrialPreflightFollowUpAction');"
            in page
        )
        assert (
            "materialsTrialPreflightFollowUpActionEl.addEventListener('click', (event) => {" in page
        )
        assert "优先：上传新版施组" in page
        assert "优先：评分施组" in page
        assert "建议先上传新版" in page
        assert "建议直接评分" in page
        assert "新版施组已显示到列表，可直接继续评分。" in page
        assert (
            "function setShigongTrialPreflightHint(entrypointLabel='', entrypointReasonLabel='')"
            in page
        )
        assert "const hintEl = document.getElementById('shigongTrialPreflightHint');" in page
        assert "当前建议：" in page
        assert "原因：" in page
        assert "function setShigongScoreLockNotice(locked=false)" in page
        assert "scoreBtn.classList.toggle('button-lock-emphasis', visible);" in page
        assert (
            "const defaultLabel = String(scoreBtn.dataset.defaultLabel || '评分施组').trim() || '评分施组';"
            in page
        )
        assert "activeLabel = visible ? lockedLabel : defaultLabel;" in page
        assert "scoreBtn.textContent = activeLabel;" in page
        assert "function currentScoreShigongActionLabel()" in page
        assert "const currentLabel = String(scoreBtn.textContent || '').trim();" in page
        assert "const statusEl = document.getElementById('shigongActionStatus');" in page
        assert "currentStatus.indexOf('待机：可上传施组或点击“') === 0" in page
        assert "statusEl.textContent = '待机：可上传施组或点击“' + activeLabel + '”。';" in page
        assert "setShigongScoreLockNotice(false);" in page
        assert "setShigongScoreLockNotice(expertProfileLocked);" in page
        assert "待机：可上传施组或点击“' + currentScoreShigongActionLabel() + '”。" in page
        assert (
            "评分前置已满足，可直接点击“' + escapeHtmlText(currentScoreShigongActionLabel()) + '”。"
            in page
        )
        assert (
            "已满足评分条件，可点击“' + escapeHtmlText(currentScoreShigongActionLabel()) + '”。"
            in page
        )
        assert "btnUploadMaterials: { resultId: 'materialsActionStatus'" in page
        assert "btnUploadBoq: { resultId: 'materialsActionStatusBoq'" in page
        assert "btnUploadDrawing: { resultId: 'materialsActionStatusDrawing'" in page
        assert "btnUploadSitePhotos: { resultId: 'materialsActionStatusPhoto'" in page
        assert "btnUploadShigong: { resultId: 'shigongActionStatus'" in page
        assert "btnScoreShigong: { resultId: 'shigongActionStatus'" in page
        assert (
            'id="btnSelfCheck" class="secondary" '
            'onclick="return window.__zhifeiFallbackClick(event, '
            "'btnSelfCheck')\"" in page
        )
        assert (
            'id="btnSystemImprovementOverview" class="secondary" '
            'onclick="return window.__zhifeiFallbackClick(event, '
            "'btnSystemImprovementOverview')\"" in page
        )
        assert (
            'id="btnDataHygiene" class="secondary" '
            'onclick="return window.__zhifeiFallbackClick(event, '
            "'btnDataHygiene')\"" in page
        )
        assert (
            'id="btnEvalSummaryV2" class="secondary" '
            'onclick="return window.__zhifeiFallbackClick(event, '
            "'btnEvalSummaryV2')\"" in page
        )
        assert (
            'id="btnTrialPreflightDownload" class="secondary" '
            'onclick="return window.__zhifeiFallbackClick(event, '
            "'btnTrialPreflightDownload')\"" in page
        )
        assert (
            'id="btnTrialPreflightDownloadDocx" class="secondary" '
            'onclick="return window.__zhifeiFallbackClick(event, '
            "'btnTrialPreflightDownloadDocx')\"" in page
        )
        assert (
            'id="btnWritingGuidanceDownload" class="secondary" '
            'onclick="return window.__zhifeiFallbackClick(event, '
            "'btnWritingGuidanceDownload')\"" in page
        )
        assert (
            'id="btnWritingGuidancePatchBundleDownload" class="secondary" '
            'onclick="return window.__zhifeiFallbackClick(event, '
            "'btnWritingGuidancePatchBundleDownload')\"" in page
        )
        assert (
            'id="btnWritingGuidancePatchBundleDownloadDocx" class="secondary" '
            'onclick="return window.__zhifeiFallbackClick(event, '
            "'btnWritingGuidancePatchBundleDownloadDocx')\"" in page
        )
        assert "btnSelfCheck: { resultId: 'selfCheckResult'" in page
        assert "btnSystemImprovementOverview: { resultId: 'systemImprovementResult'" in page
        assert "btnDataHygiene: { resultId: 'dataHygieneResult'" in page
        assert "btnEvalSummaryV2: { resultId: 'evalResult'" in page
        assert "btnTrialPreflightDownload: { resultId: 'trialPreflightResult'" in page
        assert "btnTrialPreflightDownloadDocx: { resultId: 'trialPreflightResult'" in page
        assert "btnMaterialDepthReportDownload: { resultId: 'materialDepthReportResult'" in page
        assert (
            "btnMaterialKnowledgeProfileDownload: { resultId: 'materialKnowledgeProfileResult'"
            in page
        )
        assert "btnWritingGuidanceDownload: { resultId: 'guidanceResult'" in page
        assert "btnWritingGuidancePatchBundleDownload: { resultId: 'guidanceResult'" in page
        assert "btnWritingGuidancePatchBundleDownloadDocx: { resultId: 'guidanceResult'" in page
        assert "function secureDesktopEnabled() {" in page
        assert "if (secureDesktopEnabled()) {" in page
        assert "if (!secureDesktopEnabled()) {" in page
        assert "safeClick('btnMaterialDepthReportDownload'" in page
        assert "safeClick('btnMaterialKnowledgeProfileDownload'" in page
        assert (
            "safeClick('btnMaterialDepthReportDownload', async () => {\n"
            "          if (secureDesktopEnabled()) {" in page
        )
        assert (
            "safeClick('btnMaterialKnowledgeProfileDownload', async () => {\n"
            "          if (secureDesktopEnabled()) {" in page
        )
        assert "loading: '系统自检执行中...'" in page
        assert "loading: '系统继续完善总览生成中...'" in page
        assert "loading: '数据卫生巡检中...'" in page
        assert "loading: '跨项目汇总评估中...'" in page
        assert "loading: '体检报告下载准备中...'" in page
        assert "loading: '知识画像下载准备中...'" in page
        assert "loading: '试车报告下载准备中...'" in page
        assert "loading: '试车报告 DOCX 下载准备中...'" in page
        assert "loading: '编制指导下载准备中...'" in page
        assert "loading: '改写补丁包下载准备中...'" in page
        assert "loading: '改写补丁包 DOCX 下载准备中...'" in page
        assert "safeClick('btnSelfCheck'" in page
        assert "safeClick('btnSystemImprovementOverview'" in page
        assert "safeClick('btnDataHygiene'" in page
        assert "safeClick('btnEvalSummaryV2'" in page
        assert "const FALLBACK_PROJECTLESS_ACTION_IDS = new Set([" in page
        assert "function fallbackActionRequiresProject(actionId) {" in page
        assert "if (fallbackActionRequiresProject(actionId) && !projectId) {" in page
        assert "if (actionId === 'btnSelfCheck')" in page
        assert "if (actionId === 'btnDataHygiene')" in page
        assert "if (actionId === 'btnEvalSummaryV2')" in page
        assert "if (actionId === 'btnTrialPreflightDownload')" in page
        assert "if (actionId === 'btnTrialPreflightDownloadDocx')" in page
        assert "if (actionId === 'btnWritingGuidancePatchBundleDownload')" in page
        assert "if (actionId === 'btnWritingGuidancePatchBundleDownloadDocx')" in page
        assert "a.download = 'trial_preflight_' + projectId + '.md';" in page
        assert "a.download = 'trial_preflight_' + projectId + '.docx';" in page
        assert "a.download = 'writing_guidance_patch_bundle_' + projectId + '.md';" in page
        assert "a.download = 'writing_guidance_patch_bundle_' + projectId + '.docx';" in page
        assert "setResult(cfg.resultId, '试车前综合体检报告下载已触发。', false);" in page
        assert "setResult(cfg.resultId, '试车前综合体检 DOCX 报告下载已触发。', false);" in page
        assert "setResult(cfg.resultId, '改写补丁包 Markdown 下载已触发。', false);" in page
        assert "setResult(cfg.resultId, '改写补丁包 DOCX 下载已触发。', false);" in page
        assert "fallbackSetResult(cfg.resultId, '试车前综合体检报告下载已触发。', false);" in page
        assert "fallbackSetResult(cfg.resultId, '改写补丁包 DOCX 下载已触发。', false);" in page
        assert (
            "fallbackSetResult(cfg.resultId, '试车前综合体检 DOCX 报告下载已触发。', false);"
            in page
        )
        assert "fallbackSetResult(cfg.resultId, '改写补丁包 Markdown 下载已触发。', false);" in page
        assert 'id="btnOptimizationReport" class="secondary">满分优化清单（逐页）</button>' in page
        assert "safeClick('btnUploadMaterials', uploadMaterialsAction);" not in page
        assert "safeClick('btnUploadShigong', uploadShigongAction);" not in page
        assert "safeClick('btnScoreShigong', scoreShigongAction);" not in page
        assert "safeClick('btnOptimizationReport'" in page
        assert "function captureViewportY()" not in page
        assert "function restoreViewportY(y)" not in page
        assert "let uploadShigongInFlight = false;" in page
        assert "let scoreShigongInFlight = false;" in page
        assert "let shigongSubmitIntent = 'upload';" in page
        assert "const uploadedSubmissions = [];" in page
        assert "upsertSubmissionRowsImmediately(projectId, uploadedSubmissions);" in page
        assert (
            "const uploadSummary = '施组上传完成：成功 ' + okCount + '，失败 ' + failCount + '。';"
            in page
        )
        assert "let visibleConfirmed = false;" in page
        assert "await ensureUploadedSubmissionsVisible(" in page
        assert "hasUploadedSubmissionsVisible(refreshedSubs, uploadedSubmissions);" in page
        assert "已显示到施组列表，可直接继续评分。" in page
        assert "系统会继续自动刷新。" in page
        assert "await waitForNextPaint();" in page
        assert "function waitForNextPaint()" in page
        assert "function buildSubmissionVisibilityKey(submission)" in page
        assert (
            "function hasUploadedSubmissionsVisible(currentSubmissions, expectedSubmissions)"
            in page
        )
        assert (
            "async function ensureUploadedSubmissionsVisible(projectId, expectedSubmissions, switchSeq, options=null)"
            in page
        )
        assert "function upsertSubmissionRowsImmediately(projectId, submissions)" in page
        assert "function triggerOptimizationReportAction(projectId='', options=null)" in page
        assert "function describeSelectedFiles(files, emptyText = '未选择任何文件')" in page
        assert (
            "function updateFilePickerText(inputId, textId, emptyText = '未选择任何文件')" in page
        )
        assert "function refreshAllFilePickerTexts()" in page
        assert "document.querySelectorAll('[data-file-input-id]').forEach((button) => {" not in page
        assert "window.materialTypeLabel = materialTypeLabel;" in page
        assert "typeof window.materialTypeLabel === 'function'" in page
        assert "async function inferProjectNameFromTenderSelection" in page
        assert "/api/v1/projects/infer_name_from_tender" in page
        assert (
            "bindFilePicker('createProjectFromTenderFile', 'createProjectFromTenderFileName');"
            in page
        )
        assert "bindFilePicker('uploadMaterialFile', 'uploadMaterialFileName');" in page
        assert "bindFilePicker('uploadShigongFile', 'uploadShigongFileName');" in page
        assert (
            "const formCreateFromTender = document.getElementById('createProjectFromTender');"
            in page
        )
        assert "/api/v1/projects/create_from_tender" in page
        assert "['feedFile', 'feedFileName']," in page
        assert "refreshAllFilePickerTexts();" in page
        assert (
            "const isScoreSubmit = sid === 'btnScoreShigong' || shigongSubmitIntent === 'score';"
            in page
        )
        assert "/submissions?t=' + Date.now()" in page

    def test_index_frontend_binds_tender_infer_to_file_change(self, client):
        response = client.get("/")
        assert response.status_code == 200
        page = response.text
        assert "function setRecognizedProjectName(name)" in page
        assert (
            "const createProjectFromTenderFileInput = document.getElementById('createProjectFromTenderFile');"
            in page
        )
        assert "function currentSelectedProjectDisplayName()" in page
        assert "async function ensureTenderCreateUsesRecognizedProject(inferredName)" in page
        assert "setRecognizedProjectName(inferredName);" in page
        assert "await ensureTenderCreateUsesRecognizedProject(inferredName);" in page
        assert "createProjectFromTenderFileInput.addEventListener('change', async () => {" in page
        assert (
            "await inferProjectNameFromTenderSelection(createProjectFromTenderFileInput);" in page
        )
        assert "旧项目已暂时隐藏，避免混淆。" in page


class TestWebCreateProjectFallback:
    """Tests for non-JS project creation fallback endpoint."""

    def test_web_create_project_empty_name_redirects_error(self, client):
        response = client.post("/web/create_project", data={"name": "   "}, follow_redirects=False)
        assert response.status_code == 303
        assert "create_error=" in response.headers.get("location", "")

    @patch("app.main.create_project")
    def test_web_create_project_success_redirects_ok(self, mock_create_project, client):
        response = client.post(
            "/web/create_project", data={"name": "测试项目"}, follow_redirects=False
        )
        assert response.status_code == 303
        assert "create_ok=" in response.headers.get("location", "")
        assert mock_create_project.called

    def test_web_create_project_redirects_readable_error_when_auth_enabled_without_key(
        self, client
    ):
        with patch.dict(os.environ, {"API_KEYS": "admin:test-admin-key"}, clear=False):
            response = client.post(
                "/web/create_project",
                data={"name": "测试项目"},
                follow_redirects=False,
            )
        assert response.status_code == 303
        location = response.headers.get("location", "")
        assert "create_error=" in location
        assert "%E8%AF%B7%E5%85%88%E5%A1%AB%E5%86%99%E5%B9%B6%E4%BF%9D%E5%AD%98+API+Key" in location

    @patch("app.main.create_project")
    def test_web_create_project_accepts_form_api_key_when_auth_enabled(
        self, mock_create_project, client
    ):
        with patch.dict(os.environ, {"API_KEYS": "admin:test-admin-key"}, clear=False):
            response = client.post(
                "/web/create_project",
                data={"name": "测试项目", "api_key": "test-admin-key"},
                follow_redirects=False,
            )
        assert response.status_code == 303
        assert "create_ok=" in response.headers.get("location", "")
        assert mock_create_project.called
        assert mock_create_project.call_args.kwargs["api_key"] == "test-admin-key"

    @patch("app.main.create_project")
    def test_web_create_project_localhost_bypasses_api_key_when_auth_enabled(
        self, mock_create_project, client
    ):
        with patch.dict(os.environ, {"API_KEYS": "admin:test-admin-key"}, clear=False):
            response = client.post(
                "/web/create_project",
                data={"name": "测试项目"},
                headers={"host": "127.0.0.1:8000"},
                follow_redirects=False,
            )
        assert response.status_code == 303
        assert "create_ok=" in response.headers.get("location", "")
        assert mock_create_project.called
        assert mock_create_project.call_args.kwargs["api_key"] is None

    @patch("app.main.create_project_from_tender")
    def test_web_create_project_from_tender_success_redirects_ok(
        self, mock_create_from_tender, client
    ):
        from app.schemas import MaterialRecord, ProjectCreateFromTenderResponse, ProjectRecord

        mock_create_from_tender.return_value = ProjectCreateFromTenderResponse(
            project=ProjectRecord(
                id="p1",
                name="合肥轨道TOD甘棠路一期B地块公共区域精装修工程招标",
                meta={},
                created_at="2026-03-19T00:00:00+00:00",
            ),
            material=MaterialRecord(
                id="m1",
                project_id="p1",
                material_type="tender_qa",
                filename="招标文件.txt",
                path="/tmp/materials/p1/tender_qa/招标文件.txt",
                created_at="2026-03-19T00:00:00+00:00",
            ),
            inferred_name="合肥轨道TOD甘棠路一期B地块公共区域精装修工程招标",
            created=True,
            reused_existing=False,
        )

        response = client.post(
            "/web/create_project_from_tender",
            files={
                "file": (
                    "招标文件.txt",
                    "项目名称：合肥轨道TOD甘棠路一期B地块公共区域精装修工程招标".encode("utf-8"),
                    "text/plain",
                )
            },
            follow_redirects=False,
        )

        assert response.status_code == 303
        location = response.headers.get("location", "")
        assert "create_ok=" in location
        assert "project_id=p1" in location
        assert mock_create_from_tender.called


class TestWebFallbackOps:
    """Tests for non-JS fallback operation endpoints."""

    def test_web_delete_project_missing_id(self, client):
        response = client.post(
            "/web/delete_project", data={"project_id": ""}, follow_redirects=False
        )
        assert response.status_code == 303
        assert "msg_type=error" in response.headers.get("location", "")

    @patch("app.main._delete_project_cascade")
    def test_web_delete_project_success(self, mock_delete, client):
        mock_delete.return_value = {"project_name": "项目A"}
        response = client.post(
            "/web/delete_project", data={"project_id": "p1"}, follow_redirects=False
        )
        assert response.status_code == 303
        location = response.headers.get("location", "")
        assert "msg_type=success" in location
        assert "project_id" not in location
        mock_delete.assert_called_once()

    def test_delete_project_cascade_recovers_missing_project_record(self):
        target = {
            "id": "p-recovered",
            "name": "OPS招标项目1工程",
            "meta": {},
            "region": "合肥",
            "expert_profile_id": "",
            "qingtian_model_version": "qingtian-2026.02",
            "scoring_engine_version_locked": "v2.0.0",
            "calibrator_version_locked": None,
            "status": "scoring_preparation",
            "created_at": "2026-03-29T00:00:00+00:00",
            "updated_at": "2026-03-29T00:00:00+00:00",
        }

        with ExitStack() as stack:
            stack.enter_context(patch("app.main.ensure_data_dirs"))
            stack.enter_context(patch("app.main.load_projects", return_value=[]))
            mock_find_project = stack.enter_context(
                patch("app.main._find_project", return_value=target)
            )
            stack.enter_context(patch("app.main.save_projects"))
            stack.enter_context(
                patch(
                    "app.main.load_materials",
                    return_value=[
                        {
                            "id": "m1",
                            "project_id": "p-recovered",
                            "path": "/tmp/missing-material.txt",
                        }
                    ],
                )
            )
            stack.enter_context(patch("app.main.save_materials"))
            stack.enter_context(patch("app.main.load_material_parse_jobs", return_value=[]))
            stack.enter_context(patch("app.main.save_material_parse_jobs"))
            stack.enter_context(patch("app.main._invalidate_material_index_cache"))
            stack.enter_context(patch("app.main.load_submissions", return_value=[]))
            stack.enter_context(patch("app.main.save_submissions"))
            stack.enter_context(patch("app.main.load_score_reports", return_value=[]))
            stack.enter_context(patch("app.main.save_score_reports"))
            stack.enter_context(patch("app.main._load_evidence_units_safe", return_value=[]))
            stack.enter_context(patch("app.main.save_evidence_units"))
            stack.enter_context(patch("app.main.load_qingtian_results", return_value=[]))
            stack.enter_context(patch("app.main.save_qingtian_results"))
            stack.enter_context(patch("app.main.load_delta_cases", return_value=[]))
            stack.enter_context(patch("app.main.save_delta_cases"))
            stack.enter_context(patch("app.main.load_calibration_samples", return_value=[]))
            stack.enter_context(patch("app.main.save_calibration_samples"))
            stack.enter_context(patch("app.main.load_patch_packages", return_value=[]))
            stack.enter_context(patch("app.main.save_patch_packages"))
            stack.enter_context(patch("app.main.load_patch_deployments", return_value=[]))
            stack.enter_context(patch("app.main.save_patch_deployments"))
            stack.enter_context(patch("app.main.load_project_anchors", return_value=[]))
            stack.enter_context(patch("app.main.save_project_anchors"))
            stack.enter_context(patch("app.main.load_project_requirements", return_value=[]))
            stack.enter_context(patch("app.main.save_project_requirements"))
            stack.enter_context(patch("app.main.load_learning_profiles", return_value=[]))
            stack.enter_context(patch("app.main.save_learning_profiles"))
            stack.enter_context(patch("app.main.load_score_history", return_value=[]))
            stack.enter_context(patch("app.main.save_score_history"))
            stack.enter_context(patch("app.main.load_project_context", return_value={}))
            stack.enter_context(patch("app.main.save_project_context"))
            stack.enter_context(patch("app.main.load_ground_truth", return_value=[]))
            stack.enter_context(patch("app.main.save_ground_truth"))
            stack.enter_context(patch("app.main.load_evolution_reports", return_value={}))
            stack.enter_context(patch("app.main.save_evolution_reports"))
            result = app_main._delete_project_cascade("p-recovered")

        assert result["project_id"] == "p-recovered"
        assert result["project_name"] == "OPS招标项目1工程"
        mock_find_project.assert_called_once()

    def test_web_upload_materials_requires_file(self, client):
        response = client.post(
            "/web/upload_materials", data={"project_id": "p1"}, follow_redirects=False
        )
        assert response.status_code == 303
        assert "msg_type=error" in response.headers.get("location", "")

    def test_web_upload_materials_redirects_readable_error_when_auth_enabled_without_key(
        self, client
    ):
        with patch.dict(os.environ, {"API_KEYS": "admin:test-admin-key"}, clear=False):
            response = client.post(
                "/web/upload_materials",
                data={"project_id": "p1"},
                files=[("file", ("a.txt", BytesIO(b"demo"), "text/plain"))],
                follow_redirects=False,
            )
        assert response.status_code == 303
        location = response.headers.get("location", "")
        assert "msg_type=error" in location
        assert "%E8%AF%B7%E5%85%88%E5%A1%AB%E5%86%99%E5%B9%B6%E4%BF%9D%E5%AD%98+API+Key" in location

    def test_web_upload_materials_get_fallback_redirects(self, client):
        response = client.get("/web/upload_materials", follow_redirects=False)
        assert response.status_code == 303
        location = response.headers.get("location", "")
        assert "msg_type=error" in location
        assert "%E4%B8%8A%E4%BC%A0%E8%B5%84%E6%96%99" in location

    def test_web_upload_materials_put_fallback_redirects(self, client):
        response = client.put("/web/upload_materials", follow_redirects=False)
        assert response.status_code == 303
        location = response.headers.get("location", "")
        assert "msg_type=error" in location
        assert "%E4%B8%8A%E4%BC%A0%E8%B5%84%E6%96%99" in location

    @patch("app.main.upload_material")
    def test_web_upload_materials_partial_success(self, mock_upload_material, client):
        def _side_effect(*args, **kwargs):
            file_obj = kwargs.get("file")
            if file_obj and file_obj.filename == "bad.txt":
                raise ValueError("bad file")
            return {"status": "ok"}

        mock_upload_material.side_effect = _side_effect
        response = client.post(
            "/web/upload_materials",
            data={"project_id": "p1"},
            files=[
                ("file", ("good.txt", BytesIO(b"ok"), "text/plain")),
                ("file", ("bad.txt", BytesIO(b"bad"), "text/plain")),
            ],
            follow_redirects=False,
        )
        assert response.status_code == 303
        location = response.headers.get("location", "")
        assert "project_id=p1" in location
        assert "msg_type=error" in location

    @patch("app.main.upload_shigong")
    def test_web_upload_shigong_success(self, mock_upload_shigong, client):
        mock_upload_shigong.return_value = {"id": "s1"}
        response = client.post(
            "/web/upload_shigong",
            data={"project_id": "p1"},
            files=[("file", ("a.txt", BytesIO(b"demo"), "text/plain"))],
            follow_redirects=False,
        )
        assert response.status_code == 303
        location = response.headers.get("location", "")
        assert "project_id=p1" in location
        assert "msg_type=success" in location

    def test_web_upload_shigong_get_fallback_redirects(self, client):
        response = client.get("/web/upload_shigong", follow_redirects=False)
        assert response.status_code == 303
        location = response.headers.get("location", "")
        assert "msg_type=error" in location
        assert "%E4%B8%8A%E4%BC%A0%E6%96%BD%E7%BB%84" in location

    def test_web_upload_shigong_put_fallback_redirects(self, client):
        response = client.put("/web/upload_shigong", follow_redirects=False)
        assert response.status_code == 303
        location = response.headers.get("location", "")
        assert "msg_type=error" in location
        assert "%E4%B8%8A%E4%BC%A0%E6%96%BD%E7%BB%84" in location

    @patch("app.main.rescore_project_submissions")
    def test_web_score_shigong_success_uses_reports_generated(self, mock_rescore, client):
        from types import SimpleNamespace

        mock_rescore.return_value = SimpleNamespace(reports_generated=3)
        response = client.post(
            "/web/score_shigong",
            data={"project_id": "p1"},
            follow_redirects=False,
        )
        assert response.status_code == 303
        location = response.headers.get("location", "")
        assert "msg_type=success" in location
        assert "%E5%B7%B2%E9%87%8D%E7%AE%97+3+%E4%BB%BD" in location
        assert "#section-shigong" in location

    def test_web_score_shigong_redirects_readable_error_when_auth_enabled_without_key(self, client):
        with patch.dict(os.environ, {"API_KEYS": "admin:test-admin-key"}, clear=False):
            response = client.post(
                "/web/score_shigong",
                data={"project_id": "p1"},
                follow_redirects=False,
            )
        assert response.status_code == 303
        location = response.headers.get("location", "")
        assert "msg_type=error" in location
        assert "%E8%AF%B7%E5%85%88%E5%A1%AB%E5%86%99%E5%B9%B6%E4%BF%9D%E5%AD%98+API+Key" in location


class TestCleanupE2EEndpoint:
    """Tests for /projects/cleanup_e2e endpoint."""

    @patch("app.main._delete_project_cascade")
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    def test_cleanup_e2e_projects_only_matches_prefix(
        self,
        mock_ensure,
        mock_load_projects,
        mock_delete,
        client,
    ):
        mock_load_projects.return_value = [
            {"id": "1", "name": "E2E_20260210_100000"},
            {"id": "2", "name": "正式项目A"},
            {"id": "3", "name": "E2E_20260210_110000"},
        ]
        mock_delete.return_value = {"project_id": "1"}
        response = client.post("/api/v1/projects/cleanup_e2e?prefix=E2E_")
        assert response.status_code == 200
        data = response.json()
        assert data["matched"] == 2
        assert data["removed_count"] == 2
        assert data["failed_count"] == 0
        assert mock_delete.call_count == 2


class TestMetricsEndpoint:
    """Tests for Prometheus metrics endpoint."""

    def test_metrics_returns_200(self, client):
        """GET /metrics should return 200."""
        response = client.get("/metrics")
        assert response.status_code == 200

    def test_metrics_returns_text_plain(self, client):
        """GET /metrics should return text/plain content type."""
        response = client.get("/metrics")
        assert "text/plain" in response.headers["content-type"]

    def test_metrics_contains_prometheus_format(self, client):
        """Metrics response should contain Prometheus format."""
        response = client.get("/metrics")
        content = response.text
        assert "# HELP" in content
        assert "# TYPE" in content

    def test_metrics_contains_qingtian_metrics(self, client):
        """Metrics should contain qingtian custom metrics."""
        response = client.get("/metrics")
        content = response.text
        assert "qingtian_" in content

    def test_metrics_contains_project_stats(self, client):
        """Metrics should contain project statistics."""
        response = client.get("/metrics")
        content = response.text
        assert "qingtian_projects_total" in content
        assert "qingtian_submissions_total" in content

    def test_metrics_reflect_previous_http_requests(self, client):
        client.get("/health")
        response = client.get("/metrics")

        assert response.status_code == 200
        assert (
            'qingtian_http_requests_total{endpoint="/health",method="GET",status_code="200"}'
            in response.text
        )

    @patch("app.main.load_projects")
    @patch("app.main.load_submissions")
    @patch("app.main.ensure_data_dirs")
    def test_metrics_updates_project_stats(
        self, mock_ensure_dirs, mock_submissions, mock_projects, client
    ):
        """Metrics endpoint should update project stats."""
        mock_projects.return_value = [{"id": "1"}, {"id": "2"}]
        mock_submissions.return_value = [{"id": "s1"}, {"id": "s2"}, {"id": "s3"}]

        response = client.get("/metrics")
        assert response.status_code == 200
        # 指标应该被更新（验证通过无异常即可）

    @patch("app.main.ensure_data_dirs")
    def test_metrics_handles_stats_error_gracefully(self, mock_ensure_dirs, client):
        """Metrics should not fail if stats collection errors."""
        mock_ensure_dirs.side_effect = Exception("Test error")

        response = client.get("/metrics")
        # 应该仍然返回 200，即使统计收集失败
        assert response.status_code == 200


class TestHealthEndpoints:
    """Tests for health check endpoints."""

    def test_health_returns_healthy(self, client):
        """GET /health should return healthy status."""
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert data["version"] == "1.0.0"

    def test_health_response_structure(self, client):
        """Health response should have correct structure."""
        response = client.get("/health")
        data = response.json()
        assert "status" in data
        assert "version" in data

    @patch("app.main.load_config")
    @patch("app.main.ensure_data_dirs")
    def test_ready_returns_ready_when_all_checks_pass(
        self, mock_ensure_dirs, mock_load_config, client
    ):
        """GET /ready should return ready when all checks pass."""
        mock_load_config.return_value = MagicMock()
        mock_ensure_dirs.return_value = None

        response = client.get("/ready")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ready"
        assert data["checks"]["config"] is True
        assert data["checks"]["data_dirs"] is True

    @patch("app.main.load_config")
    @patch("app.main.ensure_data_dirs")
    def test_ready_returns_not_ready_when_config_fails(
        self, mock_ensure_dirs, mock_load_config, client
    ):
        """GET /ready should return not_ready when config check fails."""
        mock_load_config.side_effect = Exception("Config error")
        mock_ensure_dirs.return_value = None

        response = client.get("/ready")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "not_ready"
        assert data["checks"]["config"] is False
        assert data["checks"]["data_dirs"] is True

    @patch("app.main.load_config")
    @patch("app.main.ensure_data_dirs")
    def test_ready_returns_not_ready_when_data_dirs_fails(
        self, mock_ensure_dirs, mock_load_config, client
    ):
        """GET /ready should return not_ready when data_dirs check fails."""
        mock_load_config.return_value = MagicMock()
        mock_ensure_dirs.side_effect = Exception("Cannot create dirs")

        response = client.get("/ready")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "not_ready"
        assert data["checks"]["config"] is True
        assert data["checks"]["data_dirs"] is False

    def test_ready_response_structure(self, client):
        """Ready response should have correct structure."""
        response = client.get("/ready")
        data = response.json()
        assert "status" in data
        assert "checks" in data
        assert isinstance(data["checks"], dict)


class TestScoreEndpoint:
    """Tests for POST /score endpoint."""

    @patch("app.main.load_config")
    @patch("app.main.score_text")
    @patch("app.main.get_cached_score")
    def test_score_endpoint_success(self, mock_cached, mock_score, mock_config, client):
        """Score endpoint should return score report."""
        from app.schemas import LogicLockResult, ScoreReport

        mock_cached.return_value = None
        mock_config.return_value = MagicMock(rubric={}, lexicon={})
        mock_score.return_value = ScoreReport(
            total_score=85.0,
            dimension_scores={},
            logic_lock=LogicLockResult(
                definition_score=1.0,
                analysis_score=1.0,
                solution_score=1.0,
                breaks=[],
                evidence=[],
            ),
            penalties=[],
            suggestions=[],
            meta={},
            judge_mode="local",
            judge_source="scorer",
            fallback_reason="",
        )
        response = client.post("/api/v1/score", json={"text": "测试文本"})
        assert response.status_code == 200
        mock_score.assert_called_once()


class TestProjectsEndpoints:
    """Tests for /projects endpoints."""

    @patch("app.main.save_projects")
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    def test_create_project_success(self, mock_ensure, mock_load, mock_save, client):
        """Create project should return project record."""
        mock_load.return_value = []
        response = client.post("/api/v1/projects", json={"name": "测试项目"})
        assert response.status_code == 200
        data = response.json()
        assert "id" in data
        assert data["name"] == "测试项目"
        mock_save.assert_called_once()

    @patch("app.main.save_projects")
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    def test_create_project_with_meta(self, mock_ensure, mock_load, mock_save, client):
        """Create project with meta should include meta."""
        mock_load.return_value = []
        response = client.post(
            "/api/v1/projects", json={"name": "测试项目", "meta": {"key": "value"}}
        )
        assert response.status_code == 200
        data = response.json()
        assert data["meta"]["key"] == "value"
        assert data["meta"]["enforce_material_gate"] is True
        assert "required_material_types" in data["meta"]

    @patch("app.main.save_projects")
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    def test_create_project_tolerates_legacy_project_without_name(
        self, mock_ensure, mock_load, mock_save, client
    ):
        """Legacy project rows missing name should be auto-healed instead of causing 500."""
        mock_load.return_value = [{"id": "legacy-p1"}]
        response = client.post("/api/v1/projects", json={"name": "新项目"})
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "新项目"
        assert mock_save.call_count == 2
        repaired_projects = mock_save.call_args_list[0].args[0]
        assert repaired_projects[0]["name"] == "恢复项目_legacy-p"

    @patch("app.main.save_projects")
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    def test_rename_project_success(self, mock_ensure, mock_load, mock_save, client):
        mock_load.return_value = [
            {
                "id": "p1",
                "name": "房建市政工程总承包招标示范文本〈2023年版〉",
                "meta": {},
                "created_at": "2026-03-26T00:00:00+00:00",
                "updated_at": "2026-03-26T00:00:00+00:00",
            }
        ]

        response = client.put(
            "/api/v1/projects/p1", json={"name": "包河区档案馆提升改造项目施工总承包"}
        )

        assert response.status_code == 200
        data = response.json()
        assert data["id"] == "p1"
        assert data["name"] == "包河区档案馆提升改造项目施工总承包"
        mock_save.assert_called_once()

    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    def test_rename_project_rejects_duplicate_name(self, mock_ensure, mock_load, client):
        mock_load.return_value = [
            {
                "id": "p1",
                "name": "项目一",
                "meta": {},
                "created_at": "2026-03-26T00:00:00+00:00",
                "updated_at": "2026-03-26T00:00:00+00:00",
            },
            {
                "id": "p2",
                "name": "项目二",
                "meta": {},
                "created_at": "2026-03-26T00:00:00+00:00",
                "updated_at": "2026-03-26T00:00:00+00:00",
            },
        ]

        response = client.put("/api/v1/projects/p1", json={"name": "项目二"})

        assert response.status_code == 422
        assert response.json()["detail"] == "项目名称已存在，请更换名称"

    @patch("app.main.save_expert_profiles")
    @patch("app.main.load_expert_profiles")
    @patch("app.main.save_projects")
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    def test_rename_project_syncs_default_profile_name(
        self,
        mock_ensure,
        mock_load_projects,
        mock_save_projects,
        mock_load_profiles,
        mock_save_profiles,
        client,
    ):
        mock_load_projects.return_value = [
            {
                "id": "p1",
                "name": "塘册中心老旧小区改造工程招标",
                "meta": {},
                "expert_profile_id": "ep1",
                "created_at": "2026-04-02T00:00:00+00:00",
                "updated_at": "2026-04-02T00:00:00+00:00",
            }
        ]
        mock_load_profiles.return_value = [
            {
                "id": "ep1",
                "name": "塘册中心老旧小区改造工程招标 默认配置",
                "weights_raw": {f"{i:02d}": 5 for i in range(1, 17)},
                "created_at": "2026-04-02T00:00:00+00:00",
                "updated_at": "2026-04-02T00:00:00+00:00",
            }
        ]

        response = client.put(
            "/api/v1/projects/p1",
            json={"name": "塘岗中心老旧小区改造工程招标"},
        )

        assert response.status_code == 200
        assert response.json()["name"] == "塘岗中心老旧小区改造工程招标"
        assert mock_load_profiles.return_value[0]["name"] == "塘岗中心老旧小区改造工程招标 默认配置"
        mock_save_projects.assert_called_once()
        mock_save_profiles.assert_called_once()

    @patch("app.main.upload_material")
    @patch("app.main.save_projects")
    @patch("app.main.load_projects")
    @patch("app.main._read_uploaded_file_content")
    @patch("app.main._read_uploaded_file_preview_for_project_name")
    @patch("app.main.ensure_data_dirs")
    def test_create_project_from_tender_creates_new_project(
        self,
        mock_ensure,
        mock_preview_reader,
        mock_full_reader,
        mock_load,
        mock_save,
        mock_upload_material,
        client,
    ):
        mock_load.return_value = []
        mock_preview_reader.return_value = (
            "项目名称：合肥轨道TOD甘棠路一期B地块公共区域精装修工程招标"
        )
        mock_full_reader.side_effect = AssertionError(
            "full parser should not run during auto-create"
        )

        def _fake_upload_material(project_id, file, material_type, api_key, locale):
            return {
                "material": {
                    "id": "m1",
                    "project_id": project_id,
                    "material_type": material_type,
                    "filename": "招标文件.txt",
                    "path": f"/tmp/materials/{project_id}/tender_qa/招标文件.txt",
                    "created_at": "2026-03-19T00:00:00+00:00",
                }
            }

        mock_upload_material.side_effect = _fake_upload_material

        response = client.post(
            "/api/v1/projects/create_from_tender",
            files={
                "file": (
                    "招标文件.txt",
                    "项目名称：合肥轨道TOD甘棠路一期B地块公共区域精装修工程招标".encode("utf-8"),
                    "text/plain",
                )
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["created"] is True
        assert data["reused_existing"] is False
        assert data["inferred_name"] == "合肥轨道TOD甘棠路一期B地块公共区域精装修工程招标"
        assert data["project"]["name"] == "合肥轨道TOD甘棠路一期B地块公共区域精装修工程招标"
        assert data["material"]["material_type"] == "tender_qa"
        mock_save.assert_called_once()
        mock_preview_reader.assert_called_once()
        mock_full_reader.assert_not_called()

    @patch("app.main.upload_material")
    @patch("app.main.save_projects")
    @patch("app.main.load_projects")
    @patch("app.main._read_uploaded_file_content")
    @patch("app.main._read_uploaded_file_preview_for_project_name")
    @patch("app.main.ensure_data_dirs")
    def test_create_project_from_tender_reuses_existing_project(
        self,
        mock_ensure,
        mock_preview_reader,
        mock_full_reader,
        mock_load,
        mock_save,
        mock_upload_material,
        client,
    ):
        mock_load.return_value = [
            {
                "id": "p1",
                "name": "合肥轨道TOD甘棠路一期B地块公共区域精装修工程招标",
                "meta": {},
                "region": "安徽",
                "expert_profile_id": None,
                "qingtian_model_version": "gpt-5.4",
                "scoring_engine_version_locked": "v2",
                "calibrator_version_locked": "",
                "status": "scoring_preparation",
                "created_at": "2026-03-19T00:00:00+00:00",
                "updated_at": "2026-03-19T00:00:00+00:00",
            }
        ]
        mock_preview_reader.return_value = (
            "项目名称：合肥轨道TOD甘棠路一期B地块公共区域精装修工程招标"
        )
        mock_full_reader.side_effect = AssertionError(
            "full parser should not run during auto-create"
        )

        def _fake_upload_material(project_id, file, material_type, api_key, locale):
            return {
                "material": {
                    "id": "m1",
                    "project_id": project_id,
                    "material_type": material_type,
                    "filename": "招标文件.txt",
                    "path": f"/tmp/materials/{project_id}/tender_qa/招标文件.txt",
                    "created_at": "2026-03-19T00:00:00+00:00",
                }
            }

        mock_upload_material.side_effect = _fake_upload_material

        response = client.post(
            "/api/v1/projects/create_from_tender",
            files={
                "file": (
                    "招标文件.txt",
                    "项目名称：合肥轨道TOD甘棠路一期B地块公共区域精装修工程招标".encode("utf-8"),
                    "text/plain",
                )
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["created"] is False
        assert data["reused_existing"] is True
        assert data["project"]["id"] == "p1"
        assert data["project"]["name"] == "合肥轨道TOD甘棠路一期B地块公共区域精装修工程招标"
        mock_save.assert_called_once()
        mock_preview_reader.assert_called_once()
        mock_full_reader.assert_not_called()

    @patch("app.main._read_uploaded_file_preview_for_project_name")
    @patch("app.main.ensure_data_dirs")
    def test_infer_project_name_from_tender(
        self,
        mock_ensure,
        mock_preview_reader,
        client,
    ):
        mock_preview_reader.return_value = (
            "项目名称：合肥轨道TOD甘棠路一期B地块公共区域精装修工程招标"
        )

        response = client.post(
            "/api/v1/projects/infer_name_from_tender",
            files={
                "file": (
                    "招标文件.txt",
                    "项目名称：合肥轨道TOD甘棠路一期B地块公共区域精装修工程招标".encode("utf-8"),
                    "text/plain",
                )
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["filename"] == "招标文件.txt"
        assert data["inferred_name"] == "合肥轨道TOD甘棠路一期B地块公共区域精装修工程招标"
        mock_preview_reader.assert_called_once()

    @patch("app.main._read_uploaded_file_preview_for_project_name")
    @patch("app.main.ensure_data_dirs")
    def test_infer_project_name_from_tender_title_line_without_explicit_field(
        self,
        mock_ensure,
        mock_preview_reader,
        client,
    ):
        mock_preview_reader.return_value = "包河区档案馆提升改造项目施工总承包招标文件"

        response = client.post(
            "/api/v1/projects/infer_name_from_tender",
            files={
                "file": (
                    "招标文件正文.pdf",
                    b"fake-pdf",
                    "application/pdf",
                )
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["inferred_name"] == "包河区档案馆提升改造项目施工总承包"

    @patch("app.main._read_uploaded_file_preview_for_project_name")
    @patch("app.main.ensure_data_dirs")
    def test_infer_project_name_from_tender_filename_strips_copy_suffix(
        self,
        mock_ensure,
        mock_preview_reader,
        client,
    ):
        mock_preview_reader.return_value = ""

        response = client.post(
            "/api/v1/projects/infer_name_from_tender",
            files={
                "file": (
                    "包河区档案馆提升改造项目施工总承包招标文件正文(2).pdf",
                    b"fake-pdf",
                    "application/pdf",
                )
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["inferred_name"] == "包河区档案馆提升改造项目施工总承包"

    @patch("app.main._read_uploaded_file_preview_for_project_name")
    @patch("app.main.ensure_data_dirs")
    def test_infer_project_name_from_tender_rejects_generic_template_title(
        self,
        mock_ensure,
        mock_preview_reader,
        client,
    ):
        mock_preview_reader.return_value = "房建市政工程总承包招标示范文本〈2023年版〉"

        response = client.post(
            "/api/v1/projects/infer_name_from_tender",
            files={
                "file": (
                    "房建市政工程总承包招标示范文本〈2023年版〉.pdf",
                    b"fake-pdf",
                    "application/pdf",
                )
            },
        )

        assert response.status_code == 422
        data = response.json()
        assert "招标示范文本/模板" in data["detail"]

    def test_read_uploaded_file_preview_for_project_name_scans_pdf_until_valid_project_name(self):
        with patch("app.main._extract_pdf_text_preview", return_value="preview") as mock_preview:
            result = app_main._read_uploaded_file_preview_for_project_name(
                b"%PDF-1.4\n", "招标文件.pdf"
            )

        assert result == "preview"
        mock_preview.assert_called_once_with(
            b"%PDF-1.4\n",
            "招标文件.pdf",
            material_type="tender_qa",
            max_pages=10,
            max_chars=32000,
            ocr_pages=3,
            stop_when_project_name_found=True,
        )

    def test_extract_pdf_text_preview_stops_early_when_tender_signals_are_sufficient(self):
        class _FakePage:
            def __init__(self, text: str):
                self._text = text

            def get_text(self):
                return self._text

        class _FakeDoc:
            def __init__(self, pages):
                self._pages = pages

            def __iter__(self):
                return iter(self._pages)

            def close(self):
                return None

        fake_doc = _FakeDoc(
            [
                _FakePage(
                    "第一章 施工组织设计总体部署\n"
                    "第二章 质量管理与验收标准\n"
                    "第三章 进度计划与节点控制\n"
                    + (
                        "本章说明项目总体部署、施工顺序、资源投入、质量控制、安全文明与节点安排。\n"
                        * 18
                    )
                ),
                _FakePage(
                    "第四章 评分办法\n"
                    "工期120日历天，质量90分，安全文明80分。\n"
                    "投标文件必须响应关键节点，不得缺少专项方案。\n"
                    "评分、评审、加分、扣分均按条款执行。"
                    + (
                        "\n评分细则要求体现BIM深化、危大工程专项方案、质量验收、进度里程碑与安全文明措施。"
                        * 14
                    )
                ),
                _FakePage("不应再继续扫描到这一页"),
            ]
        )

        with patch("app.main.pymupdf") as mock_pymupdf, patch(
            "app.main._score_ocr_text_candidate", return_value=5.0
        ):
            mock_pymupdf.open.return_value = fake_doc
            preview = app_main._extract_pdf_text_preview(
                b"%PDF-1.4\n",
                "招标文件.pdf",
                material_type="tender_qa",
                max_pages=6,
                max_chars=32000,
                ocr_pages=0,
                stop_when_project_name_found=False,
            )

        assert "[PAGE:1]" in preview
        assert "[PAGE:2]" in preview
        assert "[PAGE:3]" not in preview
        assert "不应再继续扫描到这一页" not in preview

    def test_extract_pdf_text_preview_stops_early_when_boq_signals_are_sufficient(self):
        class _FakePage:
            def __init__(self, text: str):
                self._text = text

            def get_text(self):
                return self._text

        class _FakeDoc:
            def __init__(self, pages):
                self._pages = pages

            def __iter__(self):
                return iter(self._pages)

            def close(self):
                return None

        fake_doc = _FakeDoc(
            [
                _FakePage(
                    "项目编码 项目名称 单位 工程量 综合单价 合价\n"
                    + "\n".join(
                        f"0101{i:03d} 土方开挖{i} m3 {100+i} {30+i} {(100+i)*(30+i)}"
                        for i in range(1, 18)
                    )
                ),
                _FakePage(
                    "项目编码 项目名称 单位 工程量 综合单价 合价\n"
                    + "\n".join(
                        f"0201{i:03d} 混凝土{i} m3 {80+i} {40+i} {(80+i)*(40+i)}"
                        for i in range(1, 18)
                    )
                ),
                _FakePage("不应再继续扫描到这一页"),
            ]
        )

        with patch("app.main.pymupdf") as mock_pymupdf, patch(
            "app.main._score_ocr_text_candidate", return_value=4.8
        ):
            mock_pymupdf.open.return_value = fake_doc
            preview = app_main._extract_pdf_text_preview(
                b"%PDF-1.4\n",
                "工程量清单.pdf",
                material_type="boq",
                max_pages=6,
                max_chars=32000,
                ocr_pages=0,
                stop_when_project_name_found=False,
            )

        assert "[PAGE:1]" in preview
        assert "[PAGE:2]" in preview
        assert "[PAGE:3]" not in preview
        assert "不应再继续扫描到这一页" not in preview

    def test_extract_pdf_text_stops_early_when_tender_full_parse_signals_are_sufficient(self):
        class _FakePage:
            def __init__(self, text: str):
                self._text = text

            def get_text(self):
                return self._text

        class _FakeDoc:
            def __init__(self, pages):
                self._pages = pages

            def __iter__(self):
                return iter(self._pages)

            def close(self):
                return None

        repeated_intro = (
            "施工组织设计总体部署 质量管理与验收标准 进度计划与节点控制 安全文明施工 "
            "资源配置 危大工程专项方案 BIM 深化 "
        ) * 14
        scoring_page = (
            "第四章 评分办法\n"
            "评分细则：工期120日历天，质量90分，安全文明80分，专项方案加分。\n"
            "投标文件必须响应关键节点，不得缺少危大工程专项方案。\n"
        ) * 10
        pages = [
            _FakePage("第一章 招标公告\n" + repeated_intro),
            _FakePage("第二章 投标人须知\n" + repeated_intro),
            _FakePage("第三章 合同条款\n" + repeated_intro),
            _FakePage(scoring_page),
            _FakePage("不应继续扫描到这一页"),
        ]

        with patch("app.main.pymupdf") as mock_pymupdf, patch(
            "app.main._score_ocr_text_candidate", return_value=4.8
        ):
            mock_pymupdf.open.return_value = _FakeDoc(pages)
            text = app_main._extract_pdf_text(
                b"%PDF-1.4\n",
                "招标文件.pdf",
                material_type="tender_qa",
            )

        assert "[PAGE:1]" in text
        assert "[PAGE:4]" in text
        assert "[PDF_EARLY_STOP_AFTER_PAGE:4] tender_qa_enough_signals" in text
        assert "[PAGE:5]" not in text
        assert "不应继续扫描到这一页" not in text

    def test_extract_pdf_text_stops_early_when_boq_full_parse_signals_are_sufficient(self):
        class _FakePage:
            def __init__(self, text: str):
                self._text = text

            def get_text(self):
                return self._text

        class _FakeDoc:
            def __init__(self, pages):
                self._pages = pages

            def __iter__(self):
                return iter(self._pages)

            def close(self):
                return None

        pages = [
            _FakePage(
                "项目编码 项目名称 单位 工程量 综合单价 合价\n"
                + "\n".join(
                    f"0101{i:03d} 土方开挖{i} m3 {100+i} {30+i} {(100+i)*(30+i)}"
                    for i in range(1, 22)
                )
            ),
            _FakePage(
                "项目编码 项目名称 单位 工程量 综合单价 合价\n"
                + "\n".join(
                    f"0201{i:03d} 混凝土{i} m3 {80+i} {40+i} {(80+i)*(40+i)}" for i in range(1, 22)
                )
            ),
            _FakePage(
                "项目编码 项目名称 单位 工程量 综合单价 合价\n"
                + "\n".join(
                    f"0301{i:03d} 模板工程{i} ㎡ {60+i} {25+i} {(60+i)*(25+i)}"
                    for i in range(1, 22)
                )
            ),
            _FakePage("不应继续扫描到这一页"),
        ]

        with patch("app.main.pymupdf") as mock_pymupdf, patch(
            "app.main._score_ocr_text_candidate", return_value=4.7
        ):
            mock_pymupdf.open.return_value = _FakeDoc(pages)
            text = app_main._extract_pdf_text(
                b"%PDF-1.4\n",
                "工程量清单.pdf",
                material_type="boq",
            )

        assert "[PAGE:1]" in text
        assert "[PAGE:3]" in text
        assert "[PDF_EARLY_STOP_AFTER_PAGE:3] boq_enough_signals" in text
        assert "[PAGE:4]" not in text
        assert "不应继续扫描到这一页" not in text

    def test_build_boq_full_parse_text_resumes_pdf_from_preview_page(self):
        from app.main import _build_boq_full_parse_text

        class _FakePage:
            def __init__(self, text: str):
                self._text = text

            def get_text(self):
                return self._text

        class _FakeDoc:
            def __init__(self, pages):
                self._pages = pages

            def __iter__(self):
                return iter(self._pages)

            def close(self):
                return None

        pages = [
            _FakePage(
                "项目编码 项目名称 单位 工程量 综合单价 合价\n"
                + "\n".join(
                    f"0101{i:03d} 土方开挖{i} m3 {100+i} {30+i} {(100+i)*(30+i)}"
                    for i in range(1, 22)
                )
            ),
            _FakePage(
                "项目编码 项目名称 单位 工程量 综合单价 合价\n"
                + "\n".join(
                    f"0201{i:03d} 混凝土{i} m3 {80+i} {40+i} {(80+i)*(40+i)}" for i in range(1, 22)
                )
            ),
            _FakePage(
                "项目编码 项目名称 单位 工程量 综合单价 合价\n"
                + "\n".join(
                    f"0301{i:03d} 模板工程{i} ㎡ {60+i} {25+i} {(60+i)*(25+i)}"
                    for i in range(1, 22)
                )
            ),
            _FakePage("不应继续扫描到这一页"),
        ]
        prior_text = "[PDF_BACKEND:pymupdf]\n" "[PAGE:1]\n第一页预解析\n\n" "[PAGE:2]\n第二页预解析"

        with patch("app.main.pymupdf") as mock_pymupdf, patch(
            "app.main._score_ocr_text_candidate", return_value=4.7
        ):
            mock_pymupdf.open.return_value = _FakeDoc(pages)
            text = _build_boq_full_parse_text(
                b"%PDF-1.4\n",
                "工程量清单.pdf",
                prior_text=prior_text,
                prior_summary={"parse_stage": "preview", "preview_last_page": 2},
            )

        assert text.count("[PAGE:1]") == 1
        assert text.count("[PAGE:2]") == 1
        assert "[PAGE:3]" in text
        assert "[PAGE:4]" not in text
        assert "不应继续扫描到这一页" not in text

    def test_extract_pdf_text_stops_early_when_drawing_full_parse_signals_are_sufficient(self):
        class _FakePage:
            def __init__(self, text: str):
                self._text = text

            def get_text(self):
                return self._text

        class _FakeDoc:
            def __init__(self, pages):
                self._pages = pages

            def __iter__(self):
                return iter(self._pages)

            def close(self):
                return None

        pages = [
            _FakePage(
                "建筑总平面图 轴线1-8 标高3.500 净高2.900 机电综合 碰撞 净高复核 "
                "节点详图 梁 板 柱 消防 风管 桥架"
            ),
            _FakePage(
                "平面布置图 轴线A-F 标高4.200 管径DN65 综合管线 BIM 深化 节点 大样 "
                "给排水 电气 暖通 设备机房"
            ),
            _FakePage(
                "剖面图 立面图 标高6.000 节点详图 洞口 预留预埋 套管 " "消防喷淋 管径 DN100 梁板柱"
            ),
            _FakePage("不应继续扫描到这一页"),
        ]

        with patch("app.main.pymupdf") as mock_pymupdf, patch(
            "app.main._score_ocr_text_candidate", return_value=4.8
        ):
            mock_pymupdf.open.return_value = _FakeDoc(pages)
            text = app_main._extract_pdf_text(
                b"%PDF-1.4\n",
                "总图.pdf",
                material_type="drawing",
            )

        assert "[PAGE:1]" in text
        assert "[PAGE:3]" in text
        assert "[PDF_EARLY_STOP_AFTER_PAGE:3] drawing_enough_signals" in text
        assert "[PAGE:4]" not in text
        assert "不应继续扫描到这一页" not in text

    @patch("app.main._extract_pdf_text", return_value="[PDF_BACKEND:pymupdf]\nfull text")
    def test_read_uploaded_file_content_passes_material_type_to_pdf_parser(self, mock_extract_pdf):
        text = app_main._read_uploaded_file_content(
            b"%PDF-1.4\n",
            "招标文件.pdf",
            material_type="tender_qa",
        )

        assert text == "[PDF_BACKEND:pymupdf]\nfull text"
        mock_extract_pdf.assert_called_once_with(
            b"%PDF-1.4\n",
            "招标文件.pdf",
            material_type="tender_qa",
        )

    def test_should_run_pdf_page_ocr_skips_non_empty_page_after_text_layer_confirmed(self):
        analysis = {
            "text": "附表A 进度计划 工期120天 质量90分 安全文明80分 " * 3,
            "char_count": 72,
            "score": 1.6,
        }

        should_ocr = app_main._should_run_pdf_page_ocr(
            page_index=3,
            page_analysis=analysis,
            max_ocr_pages=3,
            text_layer_confirmed=True,
        )

        assert should_ocr is False

    def test_should_run_pdf_page_ocr_keeps_empty_page_eligible_after_text_layer_confirmed(self):
        analysis = {
            "text": "",
            "char_count": 0,
            "score": 0.0,
        }

        should_ocr = app_main._should_run_pdf_page_ocr(
            page_index=3,
            page_analysis=analysis,
            max_ocr_pages=3,
            text_layer_confirmed=True,
        )

        assert should_ocr is True

    def test_extract_pdf_text_preview_skips_followup_ocr_after_text_layer_is_confirmed(self):
        class _FakePixmap:
            def tobytes(self, fmt: str):
                return b"png"

        class _FakePage:
            def __init__(self, text: str):
                self._text = text
                self.get_pixmap = MagicMock(return_value=_FakePixmap())

            def get_text(self):
                return self._text

        class _FakeDoc:
            def __init__(self, pages):
                self._pages = pages

            def __iter__(self):
                return iter(self._pages)

            def close(self):
                return None

        strong_text = (
            "施工组织设计总体部署 质量管理 进度计划 安全文明 资源配置 "
            "危大工程 BIM 深化 关键节点 施工顺序 专项方案 "
        ) * 24
        weak_text = (
            "附表A 进度计划 工期120天 质量90分 安全文明80分，"
            "关键节点详见后附说明及分项安排，材料设备进场计划、劳动力投入计划、"
            "质量验收节点与安全文明措施要求见附录。"
        )
        pages = [
            _FakePage(strong_text),
            _FakePage(strong_text),
            _FakePage(weak_text),
        ]
        fake_doc = _FakeDoc(pages)

        with patch("app.main.pymupdf") as mock_pymupdf, patch(
            "app.main.pytesseract", MagicMock()
        ), patch("app.main.Image", MagicMock()), patch(
            "app.main._score_ocr_text_candidate",
            side_effect=[4.8, 4.8, 1.4],
        ):
            mock_pymupdf.open.return_value = fake_doc
            preview = app_main._extract_pdf_text_preview(
                b"%PDF-1.4\n",
                "综合说明.pdf",
                material_type="site_photo",
                max_pages=6,
                max_chars=32000,
                ocr_pages=3,
                stop_when_project_name_found=False,
            )

        assert "[PAGE:3]" in preview
        for page in pages:
            page.get_pixmap.assert_not_called()

    def test_read_uploaded_file_preview_for_project_name_keeps_scanning_until_explicit_field(self):
        class _FakePage:
            def __init__(self, text: str):
                self._text = text

            def get_text(self):
                return self._text

        class _FakeDoc:
            def __init__(self, pages):
                self._pages = pages

            def __iter__(self):
                return iter(self._pages)

            def close(self):
                return None

        fake_doc = _FakeDoc(
            [
                _FakePage("合肥市房屋建筑和市政基础设施工程施工\n招标文件示范文本"),
                _FakePage(
                    "包河经开区延边路（繁华大道-沈阳路）、\n"
                    "月谭路（饮马井路-南淝河路）、饮马井路\n"
                    "（月谭路-长春路）等3条道路工程招标"
                ),
                _FakePage(""),
                _FakePage(""),
                _FakePage(""),
                _FakePage(""),
                _FakePage(""),
                _FakePage(""),
                _FakePage(
                    "第一章 招标公告\n"
                    "1.1 项目名称：包河经开区延边路（繁华大道-沈阳路）、月谭路（饮马井路-南淝河路）、饮马井路\n"
                    "（月谭路-长春路）等3条道路工程"
                ),
            ]
        )

        with patch("app.main.pymupdf") as mock_pymupdf, patch(
            "app.main._score_ocr_text_candidate", return_value=5.0
        ):
            mock_pymupdf.open.return_value = fake_doc
            preview = app_main._read_uploaded_file_preview_for_project_name(
                b"%PDF-1.4\n",
                "招标文件正文 (2).pdf",
            )

        assert "1.1 项目名称" in preview
        assert (
            app_main._infer_project_name_from_tender_text(preview, "招标文件正文 (2).pdf")
            == "包河经开区延边路(繁华大道-沈阳路)、月谭路(饮马井路-南淝河路)、"
            "饮马井路(月谭路-长春路)等3条道路工程"
        )

    def test_infer_project_name_from_tender_normalizes_ocr_spacing_in_project_name(self):
        preview = """
[PAGE:9]
第一章、招标公告
1.1 项目名称:包河经开区延边路(繁华大道-沈阳路)、月谭路(饮马井
路-南淝河路)、饮马井路(月谭路-长春路)等3 条道路工程
"""

        assert (
            app_main._infer_project_name_from_tender_text(preview, "招标文件正文 (2).pdf")
            == "包河经开区延边路(繁华大道-沈阳路)、月谭路(饮马井路-南淝河路)、"
            "饮马井路(月谭路-长春路)等3条道路工程"
        )

    def test_infer_project_name_from_tender_prefers_explicit_project_name_without_project_suffix(
        self,
    ):
        preview = """
[PAGE:5]
第一章 招标公告
1.1 项目名称：蜀山区城区危房改造
2.1 招标项目名称：蜀山区危险围墙、外墙维修工程
"""

        assert (
            app_main._infer_project_name_from_tender_text(preview, "招标文件.pdf")
            == "蜀山区城区危房改造"
        )

    def test_infer_project_name_from_tender_applies_contextual_proper_noun_correction(self):
        preview = """
[PAGE:1]
房建市政施工招标示范文本202501版
塘册中心老旧小区改造工程招标
[PAGE:2]
第一章 招标公告
塘岗中心老旧小区改造工程招标
"""

        assert (
            app_main._infer_project_name_from_tender_text(preview, "招标文件.pdf")
            == "塘岗中心老旧小区改造工程招标"
        )

    def test_infer_project_name_from_tender_prefers_more_specific_cover_title_when_semantic_base_matches(
        self,
    ):
        preview = """
[PAGE:1]
塘岗中心老旧小区改造工程招标
[PAGE:3]
第一章 招标公告
1.1 项目名称：塘岗中心老旧小区改造项目
2.1 招标项目名称：塘岗中心老旧小区改造工程
"""

        assert (
            app_main._infer_project_name_from_tender_text(preview, "招标文件.pdf")
            == "塘岗中心老旧小区改造工程招标"
        )

    def test_read_uploaded_file_preview_for_project_name_does_not_early_stop_before_field_name(
        self,
    ):
        class _FakePage:
            def __init__(self, text: str):
                self._text = text

            def get_text(self):
                return self._text

            def get_pixmap(self, *args, **kwargs):
                raise AssertionError("should not request OCR pixmap in this test")

        class _FakeDoc:
            def __init__(self, pages):
                self._pages = pages

            def __iter__(self):
                return iter(self._pages)

            def close(self):
                return None

        fake_doc = _FakeDoc(
            [
                _FakePage("房建市政施工招标示范文本202501版\n塘册中心老旧小区改造工程招标"),
                _FakePage("目录\n第一章 招标公告\n第二章 投标人须知"),
                _FakePage("第一章 招标公告\n1.1 项目名称：塘岗中心老旧小区改造项目"),
            ]
        )

        with patch("app.main.pymupdf") as mock_pymupdf, patch(
            "app.main._should_early_stop_pdf_preview", return_value=True
        ), patch("app.main._score_ocr_text_candidate", return_value=5.0):
            mock_pymupdf.open.return_value = fake_doc
            preview = app_main._read_uploaded_file_preview_for_project_name(
                b"%PDF-1.4\n",
                "招标文件.pdf",
            )

        assert "1.1 项目名称：塘岗中心老旧小区改造项目" in preview
        assert (
            app_main._infer_project_name_from_tender_text(preview, "招标文件.pdf")
            == "塘岗中心老旧小区改造项目"
        )

    @patch("app.main._read_uploaded_file_preview_for_project_name")
    @patch("app.main.ensure_data_dirs")
    def test_infer_project_name_from_tender_skips_generic_cover_and_uses_real_project_title(
        self,
        mock_ensure,
        mock_preview_reader,
        client,
    ):
        mock_preview_reader.return_value = """
[PAGE:1]
房建市政工程总承包招标示范文本（2023年版）
合肥市房屋建筑和市政基础设施工程总承包
招标文件示范文本
[PAGE:2]
稻香村医疗救治服务综合楼EPC工程总承包
招标文件
[PAGE:5]
1.1 项目名称：稻香村医疗救治服务综合楼EPC工程总承包
"""

        response = client.post(
            "/api/v1/projects/infer_name_from_tender",
            files={
                "file": (
                    "招标文件.pdf",
                    b"fake-pdf",
                    "application/pdf",
                )
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["inferred_name"] == "稻香村医疗救治服务综合楼EPC工程总承包"

    @patch("app.main._read_uploaded_file_preview_for_project_name")
    @patch("app.main.ensure_data_dirs")
    def test_infer_project_name_from_tender_reads_project_name_field_from_page7(
        self,
        mock_ensure,
        mock_preview_reader,
        client,
    ):
        mock_preview_reader.return_value = """
[PAGE:1]
房建市政工程总承包招标示范文本（2023年版）
合肥市房屋建筑和市政基础设施工程总承包
[PAGE:7]
第一章、招标公告
1.1 项目名称：包河经开区延边路（繁华大道-沈阳路）、月潭路（炊马井路-南淝河路）、炊马井路（月潭路-长春路）等3条道路工程
"""

        response = client.post(
            "/api/v1/projects/infer_name_from_tender",
            files={
                "file": (
                    "招标文件.pdf",
                    b"fake-pdf",
                    "application/pdf",
                )
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert (
            data["inferred_name"]
            == "包河经开区延边路(繁华大道-沈阳路)、月潭路(炊马井路-南淝河路)、炊马井路(月潭路-长春路)等3条道路工程"
        )

    @patch("app.main._read_uploaded_file_preview_for_project_name")
    @patch("app.main.ensure_data_dirs")
    def test_infer_project_name_from_tender_skips_generic_construction_cover_and_reads_page9_name(
        self,
        mock_ensure,
        mock_preview_reader,
        client,
    ):
        mock_preview_reader.return_value = """
[PAGE:1]
合肥市房屋建筑和市政基础设施工程施工
招标文件示范文本
[PAGE:9]
第一章、招标公告
1.1 项目名称：包河经开区延边路（繁华大道-沈阳路）、月谭路（饮马井路-南淝河路）、饮马井路（月谭路-长春路）等3条道路工程
"""

        response = client.post(
            "/api/v1/projects/infer_name_from_tender",
            files={
                "file": (
                    "招标文件正文 (2).pdf",
                    b"fake-pdf",
                    "application/pdf",
                )
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert (
            data["inferred_name"]
            == "包河经开区延边路(繁华大道-沈阳路)、月谭路(饮马井路-南淝河路)、饮马井路(月谭路-长春路)等3条道路工程"
        )

    @patch("app.main.upload_material")
    @patch("app.main.save_projects")
    @patch("app.main.load_projects")
    @patch("app.main._read_uploaded_file_preview_for_project_name")
    @patch("app.main.ensure_data_dirs")
    def test_create_project_from_tender_accepts_manual_project_name_override(
        self,
        mock_ensure,
        mock_preview_reader,
        mock_load_projects,
        mock_save_projects,
        mock_upload_material,
        client,
    ):
        mock_load_projects.return_value = []
        mock_preview_reader.return_value = "招标文件正文"

        def _fake_upload_material(project_id, file, material_type, api_key, locale):
            return {
                "material": {
                    "id": "m1",
                    "project_id": project_id,
                    "material_type": material_type,
                    "filename": "招标文件正文.pdf",
                    "path": f"/tmp/materials/{project_id}/tender_qa/招标文件正文.pdf",
                    "created_at": "2026-03-26T00:00:00+00:00",
                }
            }

        mock_upload_material.side_effect = _fake_upload_material

        response = client.post(
            "/api/v1/projects/create_from_tender",
            data={"project_name_override": "包河区档案馆提升改造项目施工总承包"},
            files={
                "file": (
                    "招标文件正文.pdf",
                    b"fake-pdf",
                    "application/pdf",
                )
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["created"] is True
        assert data["inferred_name"] == "包河区档案馆提升改造项目施工总承包"
        assert data["project"]["name"] == "包河区档案馆提升改造项目施工总承包"
        mock_save_projects.assert_called_once()

    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    def test_list_projects_empty(self, mock_ensure, mock_load, client):
        """List projects should return empty list when no projects."""
        mock_load.return_value = []
        response = client.get("/api/v1/projects")
        assert response.status_code == 200
        assert response.json() == []

    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    def test_list_projects_with_data(self, mock_ensure, mock_load, client):
        """List projects should return all projects."""
        mock_load.return_value = [
            {
                "id": "p1",
                "name": "项目1",
                "meta": {},
                "created_at": "2026-01-01T00:00:00Z",
            }
        ]
        response = client.get("/api/v1/projects")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["name"] == "项目1"

    @patch("app.main.save_projects")
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    def test_list_projects_backfills_missing_created_at(
        self, mock_ensure, mock_load, mock_save, client
    ):
        """List projects should tolerate legacy records without created_at."""
        mock_load.return_value = [{"id": "p1", "name": "项目1", "meta": {}}]
        response = client.get("/api/v1/projects")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["id"] == "p1"

    @patch("app.main.save_projects")
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    def test_list_projects_backfills_missing_name(self, mock_ensure, mock_load, mock_save, client):
        """List projects should tolerate legacy records without name."""
        mock_load.return_value = [{"id": "p1", "meta": {}, "created_at": "2026-01-01T00:00:00Z"}]

        response = client.get("/api/v1/projects")

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["name"] == "恢复项目_p1"
        mock_save.assert_called_once()
        assert data[0]["created_at"]
        mock_save.assert_called_once()


class TestExpertProfileEndpoints:
    """Tests for /projects/{project_id}/expert-profile and rescore endpoints."""

    @patch("app.main.save_expert_profiles")
    @patch("app.main.save_projects")
    @patch("app.main.load_expert_profiles")
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    def test_get_project_expert_profile_auto_create_default(
        self,
        mock_ensure,
        mock_load_projects,
        mock_load_profiles,
        mock_save_projects,
        mock_save_profiles,
        client,
    ):
        mock_load_projects.return_value = [
            {"id": "p1", "name": "项目1", "meta": {}, "created_at": "2026-01-01T00:00:00Z"}
        ]
        mock_load_profiles.return_value = []

        response = client.get("/api/v1/projects/p1/expert-profile")
        assert response.status_code == 200
        data = response.json()
        assert data["project"]["id"] == "p1"
        assert data["project"]["region"] == "合肥"
        assert data["expert_profile"]["weights_raw"]["01"] == 5
        assert data["expert_profile"]["weights_raw"]["16"] == 5
        assert data["expert_profile"]["norm_rule_version"] == "v1_m=0.5+a/10_norm=sum"
        mock_save_profiles.assert_called_once()
        mock_save_projects.assert_called_once()

    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    def test_update_project_expert_profile_locked_without_force_unlock(
        self,
        mock_ensure,
        mock_load_projects,
        client,
    ):
        mock_load_projects.return_value = [
            {
                "id": "p1",
                "name": "项目1",
                "meta": {},
                "status": "submitted_to_qingtian",
                "created_at": "2026-01-01T00:00:00Z",
            }
        ]
        weights = {f"{i:02d}": 5 for i in range(1, 17)}
        response = client.put(
            "/api/v1/projects/p1/expert-profile",
            json={"name": "锁定测试", "weights_raw": weights},
        )
        assert response.status_code == 409
        assert "force_unlock=true" in response.json()["detail"]

    @patch("app.main.save_expert_profiles")
    @patch("app.main.save_projects")
    @patch("app.main.load_expert_profiles")
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    def test_update_project_expert_profile_locked_with_force_unlock(
        self,
        mock_ensure,
        mock_load_projects,
        mock_load_profiles,
        mock_save_projects,
        mock_save_profiles,
        client,
    ):
        mock_load_projects.return_value = [
            {
                "id": "p1",
                "name": "项目1",
                "meta": {},
                "status": "submitted_to_qingtian",
                "created_at": "2026-01-01T00:00:00Z",
            }
        ]
        mock_load_profiles.return_value = []
        weights = {f"{i:02d}": 5 for i in range(1, 17)}
        response = client.put(
            "/api/v1/projects/p1/expert-profile",
            json={"name": "锁定解锁测试", "weights_raw": weights, "force_unlock": True},
        )
        assert response.status_code == 200
        mock_save_profiles.assert_called_once()
        mock_save_projects.assert_called_once()

    @patch("app.main.save_expert_profiles")
    @patch("app.main.save_projects")
    @patch("app.main.load_expert_profiles")
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    def test_update_project_expert_profile_success(
        self,
        mock_ensure,
        mock_load_projects,
        mock_load_profiles,
        mock_save_projects,
        mock_save_profiles,
        client,
    ):
        mock_load_projects.return_value = [
            {"id": "p1", "name": "项目1", "meta": {}, "created_at": "2026-01-01T00:00:00Z"}
        ]
        mock_load_profiles.return_value = []
        weights = {f"{i:02d}": 5 for i in range(1, 17)}
        weights["02"] = 10
        response = client.put(
            "/api/v1/projects/p1/expert-profile",
            json={"name": "安全偏重", "weights_raw": weights},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["expert_profile"]["name"] == "安全偏重"
        assert data["project"]["expert_profile_id"] == data["expert_profile"]["id"]
        assert data["project"]["meta"]["expert_profile_read_only"] is True
        assert data["project"]["meta"]["expert_profile_lock_source"] == "manual"
        norm = data["expert_profile"]["weights_norm"]
        assert abs(sum(norm.values()) - 1.0) < 1e-6
        assert norm["02"] > norm["01"]
        mock_save_profiles.assert_called_once()
        mock_save_projects.assert_called_once()

    @patch("app.main.save_projects")
    @patch("app.main.save_expert_profiles")
    @patch("app.main.calibrate_weights")
    @patch("app.main._build_feedback_records_for_project")
    @patch("app.main.load_expert_profiles")
    @patch("app.main.load_projects")
    def test_auto_update_project_weights_keeps_project_profile_read_only(
        self,
        mock_load_projects,
        mock_load_profiles,
        mock_build_feedback_records,
        mock_calibrate_weights,
        mock_save_profiles,
        mock_save_projects,
    ):
        from app.main import _auto_update_project_weights_from_delta_cases

        project = {
            "id": "p1",
            "name": "项目1",
            "expert_profile_id": "ep1",
            "meta": {"expert_profile_read_only": True},
        }
        profile = {
            "id": "ep1",
            "name": "默认",
            "weights_raw": {f"{i:02d}": 5 for i in range(1, 17)},
            "weights_norm": {f"{i:02d}": 1 / 16 for i in range(1, 17)},
            "norm_rule_version": "v1_m=0.5+a/10_norm=sum",
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
        }
        mock_load_projects.return_value = [project]
        mock_load_profiles.return_value = [profile]
        mock_build_feedback_records.return_value = [{"final_score": 90.0, "delta": 2.0}]
        mock_calibrate_weights.return_value = {
            "weights_norm": {
                **{f"{i:02d}": 1 / 16 for i in range(1, 17)},
                "02": 0.1,
                "01": 0.025,
            },
            "stats": {"sample_count": 1},
        }

        out = _auto_update_project_weights_from_delta_cases("p1")

        assert out["updated"] is True
        assert out["project_profile_mutated"] is False
        assert project["expert_profile_id"] == "ep1"
        mock_save_profiles.assert_called_once()
        mock_save_projects.assert_not_called()

    @patch("app.main._run_feedback_closed_loop")
    @patch("app.main._validate_material_gate_for_scoring")
    @patch("app.main.record_history_score")
    @patch("app.main.save_score_reports")
    @patch("app.main.load_score_reports")
    @patch("app.main.save_submissions")
    @patch("app.main.load_submissions")
    @patch("app.main.score_text")
    @patch("app.main.load_config")
    @patch("app.main.save_projects")
    @patch("app.main.load_expert_profiles")
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    def test_rescore_project_success(
        self,
        mock_ensure,
        mock_load_projects,
        mock_load_profiles,
        mock_save_projects,
        mock_load_config,
        mock_score_text,
        mock_load_submissions,
        mock_save_submissions,
        mock_load_score_reports,
        mock_save_score_reports,
        mock_record_history,
        mock_material_gate,
        mock_feedback_loop,
        client,
    ):
        mock_load_projects.return_value = [
            {
                "id": "p1",
                "name": "项目1",
                "meta": {},
                "created_at": "2026-01-01T00:00:00Z",
                "expert_profile_id": "ep1",
            }
        ]
        mock_load_profiles.return_value = [
            {
                "id": "ep1",
                "name": "默认",
                "weights_raw": {f"{i:02d}": 5 for i in range(1, 17)},
                "weights_norm": {f"{i:02d}": 1 / 16 for i in range(1, 17)},
                "norm_rule_version": "v1_m=0.5+a/10_norm=sum",
                "created_at": "2026-01-01T00:00:00Z",
                "updated_at": "2026-01-01T00:00:00Z",
            }
        ]
        mock_load_submissions.return_value = [
            {
                "id": "s1",
                "project_id": "p1",
                "filename": "f1.txt",
                "text": "test content",
                "total_score": 60.0,
                "report": {},
                "created_at": "2026-01-01T00:00:00Z",
            }
        ]
        mock_load_score_reports.return_value = []
        mock_load_config.return_value = MagicMock(rubric={}, lexicon={})
        mock_score_text.return_value = MagicMock(
            model_dump=lambda: {
                "total_score": 88.8,
                "dimension_scores": {},
                "penalties": [],
                "suggestions": [],
            }
        )
        mock_material_gate.return_value = (
            {
                "project_id": "p1",
                "total_files": 3,
                "counts_by_type": {"tender_qa": 1, "boq": 1, "drawing": 1},
                "total_parsed_chars": 26000,
                "parse_fail_ratio": 0.0,
                "gate": {"passed": True, "issues": []},
            },
            [],
        )

        response = client.post(
            "/api/v1/projects/p1/rescore",
            json={"scoring_engine_version": "v2", "scope": "project", "score_scale_max": 5},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True
        assert data["submission_count"] == 1
        assert data["reports_generated"] == 1
        assert data["score_scale_max"] == 5
        assert data["score_scale_label"] == "5分制"
        assert "material_utilization" in data
        assert "material_utilization_alerts" in data
        assert "material_utilization_gate" in data
        mock_save_submissions.assert_called_once()
        mock_save_score_reports.assert_called_once()
        mock_record_history.assert_called_once()
        mock_feedback_loop.assert_called_once_with("p1", locale="zh", trigger="rescore")

    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    def test_rescore_project_locked_without_force_unlock(
        self,
        mock_ensure,
        mock_load_projects,
        client,
    ):
        mock_load_projects.return_value = [
            {
                "id": "p1",
                "name": "项目1",
                "meta": {},
                "status": "submitted_to_qingtian",
                "created_at": "2026-01-01T00:00:00Z",
            }
        ]
        response = client.post(
            "/api/v1/projects/p1/rescore",
            json={"scoring_engine_version": "v2", "scope": "project"},
        )
        assert response.status_code == 409
        assert "force_unlock=true" in response.json()["detail"]

    @patch("app.main._validate_material_gate_for_scoring")
    @patch("app.main.load_submissions")
    @patch("app.main.load_expert_profiles")
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    def test_rescore_project_rejects_when_material_gate_failed(
        self,
        mock_ensure,
        mock_load_projects,
        mock_load_profiles,
        mock_load_submissions,
        mock_material_gate,
        client,
    ):
        mock_load_projects.return_value = [
            {
                "id": "p1",
                "name": "项目1",
                "meta": {},
                "created_at": "2026-01-01T00:00:00Z",
                "expert_profile_id": "ep1",
            }
        ]
        mock_load_profiles.return_value = [
            {
                "id": "ep1",
                "name": "默认",
                "weights_raw": {f"{i:02d}": 5 for i in range(1, 17)},
                "weights_norm": {f"{i:02d}": 1 / 16 for i in range(1, 17)},
                "norm_rule_version": "v1_m=0.5+a/10_norm=sum",
                "created_at": "2026-01-01T00:00:00Z",
                "updated_at": "2026-01-01T00:00:00Z",
            }
        ]
        mock_load_submissions.return_value = [
            {
                "id": "s1",
                "project_id": "p1",
                "filename": "f1.txt",
                "text": "test content",
                "total_score": 60.0,
                "report": {},
                "created_at": "2026-01-01T00:00:00Z",
            }
        ]
        mock_material_gate.side_effect = HTTPException(
            status_code=422,
            detail="资料门禁未通过：缺少必需资料类型：清单",
        )

        response = client.post(
            "/api/v1/projects/p1/rescore",
            json={"scoring_engine_version": "v2", "scope": "project"},
        )
        assert response.status_code == 422
        assert "资料门禁未通过" in response.json()["detail"]


class TestFeedbackLoopHooks:
    @patch("app.main._run_feedback_closed_loop")
    @patch("app.main.save_calibration_samples")
    @patch("app.main.load_calibration_samples")
    @patch("app.main.save_delta_cases")
    @patch("app.main.load_delta_cases")
    @patch("app.main.save_qingtian_results")
    @patch("app.main.load_qingtian_results")
    @patch("app.main.save_evidence_units")
    @patch("app.main.load_evidence_units")
    @patch("app.main.save_score_reports")
    @patch("app.main.load_score_reports")
    @patch("app.main.save_submissions")
    @patch("app.main.load_submissions")
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    def test_delete_submission_triggers_feedback_loop(
        self,
        mock_ensure,
        mock_load_projects,
        mock_load_submissions,
        mock_save_submissions,
        mock_load_score_reports,
        mock_save_score_reports,
        mock_load_evidence_units,
        mock_save_evidence_units,
        mock_load_qingtian_results,
        mock_save_qingtian_results,
        mock_load_delta_cases,
        mock_save_delta_cases,
        mock_load_calibration_samples,
        mock_save_calibration_samples,
        mock_feedback_loop,
        client,
    ):
        mock_load_projects.return_value = [{"id": "p1"}]
        mock_load_submissions.return_value = [
            {"id": "s1", "project_id": "p1", "filename": "f1.txt", "path": ""}
        ]
        mock_load_score_reports.return_value = [
            {"id": "r1", "project_id": "p1", "submission_id": "s1"}
        ]
        mock_load_evidence_units.return_value = [{"submission_id": "s1"}]
        mock_load_qingtian_results.return_value = [{"submission_id": "s1"}]
        mock_load_delta_cases.return_value = [{"submission_id": "s1"}]
        mock_load_calibration_samples.return_value = [{"submission_id": "s1"}]

        response = client.delete("/api/v1/projects/p1/submissions/s1")
        assert response.status_code == 200
        assert response.json()["ok"] is True
        mock_feedback_loop.assert_called_once_with("p1", locale="zh", trigger="delete_submission")


class TestMaterialsEndpoint:
    """Tests for /projects/{project_id}/materials endpoint."""

    @patch("app.main._run_feedback_closed_loop_safe")
    @patch("app.main._invalidate_material_index_cache")
    @patch("app.main.save_material_parse_jobs")
    @patch("app.main.load_material_parse_jobs")
    @patch("app.main.save_materials")
    @patch("app.main.load_materials")
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    def test_delete_material_keeps_learning_artifacts_intact(
        self,
        mock_ensure,
        mock_load_projects,
        mock_load_materials,
        mock_save_materials,
        mock_load_parse_jobs,
        mock_save_parse_jobs,
        mock_invalidate_cache,
        mock_feedback_loop,
        client,
    ):
        mock_load_projects.return_value = [{"id": "p1", "name": "项目1", "meta": {}}]
        mock_load_materials.return_value = [
            {
                "id": "m1",
                "project_id": "p1",
                "filename": "招标文件正文.pdf",
                "path": "/tmp/nonexistent-material.pdf",
            }
        ]
        mock_load_parse_jobs.return_value = [{"material_id": "m1"}]

        response = client.delete("/api/v1/projects/p1/materials/m1")

        assert response.status_code == 200
        assert response.json()["ok"] is True
        mock_save_materials.assert_called_once()
        mock_save_parse_jobs.assert_called_once()
        mock_invalidate_cache.assert_called_once_with("p1")
        mock_feedback_loop.assert_not_called()

    @patch("app.main._notify_material_parse_workers")
    @patch("app.main.save_material_parse_jobs")
    @patch("app.main.save_materials")
    @patch("app.main.load_materials")
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    @patch("app.main.MATERIALS_DIR")
    def test_upload_material_success(
        self,
        mock_dir,
        mock_ensure,
        mock_load_proj,
        mock_load_mat,
        mock_save,
        mock_save_jobs,
        mock_notify_workers,
        client,
        tmp_path,
    ):
        """Upload material should save file and return record."""
        mock_dir.__truediv__ = lambda self, x: tmp_path / x
        mock_load_proj.return_value = [{"id": "p1", "scoring_engine_version_locked": "v1"}]
        mock_load_mat.return_value = []

        file_content = b"test content"
        response = client.post(
            "/api/v1/projects/p1/materials",
            files={"file": ("test.txt", BytesIO(file_content), "text/plain")},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert "material" in data
        assert data["material"]["parse_status"] == "queued"
        assert data["parse_job"]["status"] == "queued"
        assert data["constraint_sync"]["mode"] == "async_parse_pending"
        saved_materials = mock_save.call_args[0][0]
        assert len(saved_materials) == 1
        assert str(saved_materials[0].get("content_hash") or "").strip()
        mock_save_jobs.assert_called_once()
        mock_notify_workers.assert_called_once()

    @patch("app.main._notify_material_parse_workers")
    @patch("app.main.save_material_parse_jobs")
    @patch("app.main.load_material_parse_jobs")
    @patch("app.main.save_materials")
    @patch("app.main.load_materials")
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    @patch("app.main.MATERIALS_DIR")
    def test_upload_material_enters_material_parse_state_lock(
        self,
        mock_dir,
        mock_ensure,
        mock_load_proj,
        mock_load_mat,
        mock_save,
        mock_load_jobs,
        mock_save_jobs,
        mock_notify_workers,
        client,
        tmp_path,
    ):
        mock_dir.__truediv__ = lambda self, x: tmp_path / x
        mock_load_proj.return_value = [{"id": "p1"}]
        mock_load_mat.return_value = []
        mock_load_jobs.return_value = []

        events = []

        class _SpyLock:
            def __enter__(self):
                events.append("enter")
                return self

            def __exit__(self, exc_type, exc, tb):
                events.append("exit")
                return False

        with patch("app.main._MATERIAL_PARSE_STATE_LOCK", _SpyLock()):
            response = client.post(
                "/api/v1/projects/p1/materials",
                files={"file": ("test.txt", BytesIO(b"test content"), "text/plain")},
            )

        assert response.status_code == 200
        assert events == ["enter", "exit"]
        mock_save.assert_called_once()
        mock_save_jobs.assert_called_once()
        mock_notify_workers.assert_called_once()

    @patch("app.main.save_materials")
    @patch("app.main.load_materials")
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    @patch("app.main.MATERIALS_DIR")
    def test_upload_material_persists_material_type(
        self, mock_dir, mock_ensure, mock_load_proj, mock_load_mat, mock_save, client, tmp_path
    ):
        mock_dir.__truediv__ = lambda self, x: tmp_path / x
        mock_load_proj.return_value = [{"id": "p1"}]
        mock_load_mat.return_value = []

        response = client.post(
            "/api/v1/projects/p1/materials",
            data={"material_type": "boq"},
            files={"file": ("工程量清单.xlsx", BytesIO(b"excel"), "application/octet-stream")},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["material"]["material_type"] == "boq"
        saved_materials = mock_save.call_args[0][0]
        assert any(m.get("material_type") == "boq" for m in saved_materials)

    @patch("app.main.save_materials")
    @patch("app.main.load_materials")
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    @patch("app.main.MATERIALS_DIR")
    def test_upload_material_same_filename_different_type_kept_separately(
        self, mock_dir, mock_ensure, mock_load_proj, mock_load_mat, mock_save, client, tmp_path
    ):
        mock_dir.__truediv__ = lambda self, x: tmp_path / x
        mock_load_proj.return_value = [{"id": "p1"}]
        mock_load_mat.return_value = [
            {
                "id": "m-old",
                "project_id": "p1",
                "material_type": "tender_qa",
                "filename": "test.txt",
                "path": str(tmp_path / "old.txt"),
                "created_at": "2026-02-17T00:00:00+00:00",
            }
        ]

        response = client.post(
            "/api/v1/projects/p1/materials",
            data={"material_type": "boq"},
            files={"file": ("test.txt", BytesIO(b"new content"), "text/plain")},
        )
        assert response.status_code == 200
        saved_materials = mock_save.call_args[0][0]
        same_name = [
            m
            for m in saved_materials
            if m.get("project_id") == "p1" and m.get("filename") == "test.txt"
        ]
        assert len(same_name) == 2
        assert {m.get("material_type") for m in same_name} == {"tender_qa", "boq"}

    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    def test_upload_material_invalid_material_type(self, mock_ensure, mock_load, client):
        mock_load.return_value = [{"id": "p1"}]
        response = client.post(
            "/api/v1/projects/p1/materials",
            data={"material_type": "bad_type"},
            files={"file": ("test.txt", BytesIO(b"test"), "text/plain")},
        )
        assert response.status_code == 422
        assert "material_type" in response.json()["detail"]

    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    def test_upload_material_project_not_found(self, mock_ensure, mock_load, client):
        """Upload material should return 404 if project not found."""
        mock_load.return_value = []
        response = client.post(
            "/api/v1/projects/nonexistent/materials",
            files={"file": ("test.txt", BytesIO(b"test"), "text/plain")},
        )
        assert response.status_code == 404
        assert "项目不存在" in response.json()["detail"]

    @patch("app.main._validate_material_gate_for_scoring")
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    def test_get_materials_health_success(
        self, mock_ensure, mock_load_projects, mock_material_gate, client
    ):
        mock_load_projects.return_value = [{"id": "p1", "name": "项目1", "meta": {}}]
        mock_material_gate.return_value = (
            {
                "project_id": "p1",
                "total_files": 4,
                "counts_by_type": {"tender_qa": 1, "boq": 1, "drawing": 1, "site_photo": 1},
                "total_parsed_chars": 32000,
                "parse_fail_ratio": 0.0,
                "gate": {"passed": True, "issues": []},
            },
            [],
        )
        response = client.get("/api/v1/projects/p1/materials/health")
        assert response.status_code == 200
        data = response.json()
        assert data["project_id"] == "p1"
        assert data["gate"]["passed"] is True
        mock_material_gate.assert_called_once()

    @patch("app.main.load_material_parse_jobs")
    @patch("app.main.load_materials")
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    def test_get_material_parse_status_success(
        self,
        mock_ensure,
        mock_load_projects,
        mock_load_materials,
        mock_load_jobs,
        client,
    ):
        from app import main as main_module

        original_active_projects = dict(main_module._MATERIAL_PARSE_ACTIVE_PROJECTS)
        original_active_project_types = dict(main_module._MATERIAL_PARSE_ACTIVE_PROJECT_TYPES)
        original_active_project_claims = dict(main_module._MATERIAL_PARSE_ACTIVE_PROJECT_CLAIMS)
        original_active_project_type_claims = dict(
            main_module._MATERIAL_PARSE_ACTIVE_PROJECT_TYPE_CLAIMS
        )
        original_scheduler_stats = dict(main_module._MATERIAL_PARSE_SCHEDULER_STATS)
        original_project_cache_stats = {
            key: dict(value)
            for key, value in main_module._MATERIAL_PARSE_PROJECT_CACHE_STATS.items()
        }
        original_project_cache_request_history = {
            key: [dict(item) for item in value]
            for key, value in main_module._MATERIAL_PARSE_PROJECT_CACHE_REQUEST_HISTORY.items()
        }
        mock_load_projects.return_value = [{"id": "p1", "name": "项目1"}]
        mock_load_materials.return_value = [
            {
                "id": "m1",
                "project_id": "p1",
                "material_type": "tender_qa",
                "filename": "招标文件.pdf",
                "path": "/tmp/招标文件.pdf",
                "created_at": "2026-03-09T00:00:00+00:00",
                "parse_status": "processing",
                "parse_backend": "gpt-5.4-vision",
                "parse_confidence": 0.81,
                "job_id": "j1",
            }
        ]
        mock_load_jobs.return_value = [
            {
                "id": "j1",
                "material_id": "m1",
                "project_id": "p1",
                "material_type": "tender_qa",
                "filename": "招标文件.pdf",
                "status": "processing",
                "parse_backend": "gpt-5.4-vision",
                "attempt": 1,
            },
            {
                "id": "j0",
                "material_id": "m0",
                "project_id": "p1",
                "material_type": "boq",
                "filename": "工程量清单.xlsx",
                "status": "parsed",
                "parse_backend": "local",
                "attempt": 1,
                "finished_at": "2026-03-09T00:02:03+00:00",
            },
        ]
        try:
            main_module._MATERIAL_PARSE_ACTIVE_PROJECTS.clear()
            main_module._MATERIAL_PARSE_ACTIVE_PROJECT_TYPES.clear()
            main_module._MATERIAL_PARSE_ACTIVE_PROJECT_CLAIMS.clear()
            main_module._MATERIAL_PARSE_ACTIVE_PROJECT_TYPE_CLAIMS.clear()
            main_module._MATERIAL_PARSE_SCHEDULER_STATS.clear()
            main_module._MATERIAL_PARSE_PROJECT_CACHE_STATS.clear()
            main_module._MATERIAL_PARSE_PROJECT_CACHE_REQUEST_HISTORY.clear()
            response = client.get("/api/v1/projects/p1/materials/parse_status")
        finally:
            main_module._MATERIAL_PARSE_ACTIVE_PROJECTS.clear()
            main_module._MATERIAL_PARSE_ACTIVE_PROJECTS.update(original_active_projects)
            main_module._MATERIAL_PARSE_ACTIVE_PROJECT_TYPES.clear()
            main_module._MATERIAL_PARSE_ACTIVE_PROJECT_TYPES.update(original_active_project_types)
            main_module._MATERIAL_PARSE_ACTIVE_PROJECT_CLAIMS.clear()
            main_module._MATERIAL_PARSE_ACTIVE_PROJECT_CLAIMS.update(original_active_project_claims)
            main_module._MATERIAL_PARSE_ACTIVE_PROJECT_TYPE_CLAIMS.clear()
            main_module._MATERIAL_PARSE_ACTIVE_PROJECT_TYPE_CLAIMS.update(
                original_active_project_type_claims
            )
            main_module._MATERIAL_PARSE_SCHEDULER_STATS.clear()
            main_module._MATERIAL_PARSE_SCHEDULER_STATS.update(original_scheduler_stats)
            main_module._MATERIAL_PARSE_PROJECT_CACHE_STATS.clear()
            main_module._MATERIAL_PARSE_PROJECT_CACHE_STATS.update(
                {key: dict(value) for key, value in original_project_cache_stats.items()}
            )
            main_module._MATERIAL_PARSE_PROJECT_CACHE_REQUEST_HISTORY.clear()
            main_module._MATERIAL_PARSE_PROJECT_CACHE_REQUEST_HISTORY.update(
                {
                    key: deque(
                        [dict(item) for item in value],
                        maxlen=main_module._MATERIAL_PARSE_PROJECT_CACHE_REQUEST_HISTORY_WINDOW_SIZE,
                    )
                    for key, value in original_project_cache_request_history.items()
                }
            )

        assert response.status_code == 200
        data = response.json()
        assert data["project_id"] == "p1"
        assert data["overview"]["materials_total"] == 1
        assert data["overview"]["queued_materials"] == 1
        assert data["overview"]["latest_finished_filename"] == "工程量清单.xlsx"
        assert data["summary"]["materials_total"] == 1
        assert data["summary"]["queued_materials"] == 1
        assert data["summary"]["worker_count"] == app_main._material_parse_total_worker_count()
        assert (
            data["summary"]["preview_express_reserved_worker_count"]
            == app_main.DEFAULT_MATERIAL_PARSE_PREVIEW_EXPRESS_RESERVED_WORKER_COUNT
        )
        assert (
            data["summary"]["preview_reserved_worker_count"]
            == app_main.DEFAULT_MATERIAL_PARSE_PREVIEW_RESERVED_WORKER_COUNT
        )
        assert data["summary"]["backlog"] == 1
        assert data["summary"]["latest_finished_at"] == "2026-03-09T00:02:03+00:00"
        assert data["summary"]["latest_finished_filename"] == "工程量清单.xlsx"
        assert (
            data["debug_info"]["pipeline"]["worker_count"]
            == app_main._material_parse_total_worker_count()
        )
        assert data["debug_info"]["pipeline"]["backlog"] == 1
        assert data["debug_info"]["cache"]["scheduler_cache_hit_total"] == 0
        assert data["debug_info"]["project_cache"]["scheduler_project_cache_state"] == "cold"
        assert data["summary"]["scheduler_project_continuity_bonus_hits"] == 0
        assert data["summary"]["scheduler_followup_full_bonus_hits"] == 0
        assert data["summary"]["scheduler_same_material_followup_bonus_hits"] == 0
        assert data["summary"]["scheduler_active_project_bonus_hits"] == 0
        assert data["summary"]["scheduler_active_project_type_bonus_hits"] == 0
        assert data["summary"]["scheduler_claim_snapshot_cache_hits"] == 0
        assert data["summary"]["scheduler_claim_snapshot_cache_rebuilds"] == 0
        assert data["summary"]["scheduler_claim_context_cache_hits"] == 0
        assert data["summary"]["scheduler_claim_context_cache_rebuilds"] == 0
        assert data["summary"]["scheduler_project_stage_cache_hits"] == 0
        assert data["summary"]["scheduler_project_stage_cache_rebuilds"] == 0
        assert data["summary"]["scheduler_priority_context_cache_hits"] == 0
        assert data["summary"]["scheduler_priority_context_cache_rebuilds"] == 0
        assert data["summary"]["scheduler_jobs_summary_cache_hits"] == 0
        assert data["summary"]["scheduler_jobs_summary_cache_rebuilds"] == 0
        assert data["summary"]["scheduler_status_materials_cache_hits"] == 0
        assert data["summary"]["scheduler_status_materials_cache_rebuilds"] == 0
        assert data["summary"]["scheduler_status_core_cache_hits"] == 0
        assert data["summary"]["scheduler_status_core_cache_rebuilds"] == 0
        assert data["summary"]["scheduler_cache_hit_total"] == 0
        assert data["summary"]["scheduler_cache_rebuild_total"] == 0
        assert data["summary"]["scheduler_cache_hit_ratio"] == 0.0
        assert data["summary"]["scheduler_project_jobs_summary_cache_hits"] == 0
        assert data["summary"]["scheduler_project_jobs_summary_cache_rebuilds"] == 0
        assert data["summary"]["scheduler_project_jobs_summary_cache_state"] == "cold"
        assert data["summary"]["scheduler_project_status_materials_cache_hits"] == 0
        assert data["summary"]["scheduler_project_status_materials_cache_rebuilds"] == 0
        assert data["summary"]["scheduler_project_status_materials_cache_state"] == "cold"
        assert data["summary"]["scheduler_project_status_core_cache_hits"] == 0
        assert data["summary"]["scheduler_project_status_core_cache_rebuilds"] == 0
        assert data["summary"]["scheduler_project_status_core_cache_state"] == "cold"
        assert data["summary"]["scheduler_project_cache_hit_total"] == 0
        assert data["summary"]["scheduler_project_cache_rebuild_total"] == 0
        assert data["summary"]["scheduler_project_cache_hit_ratio"] == 0.0
        assert data["summary"]["scheduler_project_cache_net_savings"] == 0
        assert data["summary"]["scheduler_project_cache_state"] == "cold"
        assert data["summary"]["scheduler_project_cache_hot_layer_count"] == 0
        assert data["summary"]["scheduler_project_cache_warming_layer_count"] == 0
        assert data["summary"]["scheduler_project_cache_cold_layer_count"] == 3
        assert data["summary"]["scheduler_project_recent_avoided_rebuild_layers"] == []
        assert data["summary"]["scheduler_project_recent_rebuilt_layers"] == []
        assert data["summary"]["scheduler_project_recent_avoided_rebuild_layer_count"] == 0
        assert data["summary"]["scheduler_project_recent_rebuilt_layer_count"] == 0
        assert data["summary"]["scheduler_project_recent_avoided_rebuild_work_units"] == 0
        assert data["summary"]["scheduler_project_recent_rebuilt_work_units"] == 0
        assert data["summary"]["scheduler_project_recent_request_window_size"] == 0
        assert data["summary"]["scheduler_project_recent_cold_start_round_count"] == 0
        assert data["summary"]["scheduler_project_recent_warming_round_count"] == 0
        assert data["summary"]["scheduler_project_recent_steady_round_count"] == 0
        assert data["summary"]["scheduler_project_recent_consecutive_steady_round_count"] == 0
        assert (
            data["summary"]["scheduler_project_recent_stable_hot_threshold"]
            == main_module._MATERIAL_PARSE_PROJECT_CACHE_STEADY_THRESHOLD
        )
        assert data["summary"]["scheduler_project_recent_stable_hot"] is False
        assert data["summary"]["scheduler_project_recent_stable_hot_remaining_rounds"] == 3
        assert data["summary"]["scheduler_project_recent_stable_hot_progress_completed_rounds"] == 0
        assert data["summary"]["scheduler_project_recent_stable_hot_progress_label"] == "0/3"
        assert data["summary"]["scheduler_project_recent_stable_hot_progress_ratio"] == 0.0
        assert data["summary"]["scheduler_project_recent_stable_hot_progress_percent"] == 0
        assert data["summary"]["scheduler_project_recent_stable_hot_progress_percent_label"] == "0%"
        assert (
            data["summary"]["scheduler_project_recent_stable_hot_eta_hint"]
            == "还需连续 3 轮 steady"
        )
        assert data["summary"]["scheduler_project_recent_stable_hot_eta_short_label"] == "还需3轮"
        assert (
            data["summary"]["scheduler_project_recent_stable_hot_progress_summary_label"]
            == "0/3（0%），还需3轮"
        )
        assert data["summary"]["scheduler_project_recent_stable_hot_badge_label"] == "冷启动"
        assert (
            data["summary"]["scheduler_project_recent_stable_hot_rule_label"]
            == main_module._material_parse_project_cache_stable_hot_rule_label()
        )
        assert data["summary"]["scheduler_project_recent_window_state"] == "cold"
        assert data["summary"]["scheduler_project_recent_avoided_rebuild_work_units_avg"] == 0.0
        assert data["summary"]["scheduler_project_recent_rebuilt_work_units_avg"] == 0.0
        assert data["summary"]["scheduler_project_recent_net_saved_work_units_avg"] == 0.0
        assert data["summary"]["scheduler_active_project_window_count"] == 0
        assert data["summary"]["scheduler_active_project_type_window_count"] == 0
        assert data["summary"]["scheduler_active_project_quota_exhausted_count"] == 0
        assert data["summary"]["scheduler_active_project_type_quota_exhausted_count"] == 0
        assert data["jobs"][0]["status"] == "processing"
        assert data["materials"][0]["parse_backend"] == "queued"
        assert data["materials"][0]["parse_effective_status"] == "processing"
        assert "解析中" in str(data["materials"][0]["parse_stage_label"])
        assert data["materials"][0]["parse_route_label"] == "本地预解析，必要时 GPT 深解析"

    @patch("app.main.load_material_parse_jobs")
    @patch("app.main.load_materials")
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    def test_get_material_detail_success(
        self,
        mock_ensure,
        mock_load_projects,
        mock_load_materials,
        mock_load_jobs,
        client,
    ):
        mock_load_projects.return_value = [{"id": "p1", "name": "项目1"}]
        mock_load_materials.return_value = [
            {
                "id": "m1",
                "project_id": "p1",
                "material_type": "tender_qa",
                "filename": "招标文件正文.pdf",
                "path": "/tmp/招标文件正文.pdf",
                "created_at": "2026-03-09T00:00:00+00:00",
                "updated_at": "2026-03-09T00:05:00+00:00",
                "parse_status": "parsed",
                "parse_backend": "local",
                "parse_finished_at": "2026-03-09T00:05:00+00:00",
                "parsed_chars": 1280,
                "parsed_text": "第一页文本\\n第二页文本\\n第三页文本",
            }
        ]
        mock_load_jobs.return_value = []

        response = client.get("/api/v1/projects/p1/materials/m1/detail")

        assert response.status_code == 200
        data = response.json()
        assert data["id"] == "m1"
        assert data["filename"] == "招标文件正文.pdf"
        assert data["material_type"] == "tender_qa"
        assert data["parse_status"] == "parsed"
        assert data["parse_finished_at"] == "2026-03-09T00:05:00+00:00"
        assert data["parsed_chars"] == 1280
        assert "第一页文本" in data["parsed_text_preview"]

    @patch("app.main.load_material_parse_jobs")
    @patch("app.main.load_materials")
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    def test_get_material_parse_status_includes_scheduler_metrics(
        self,
        mock_ensure,
        mock_load_projects,
        mock_load_materials,
        mock_load_jobs,
        client,
    ):
        from app import main as main_module

        original_active_projects = dict(main_module._MATERIAL_PARSE_ACTIVE_PROJECTS)
        original_active_project_types = dict(main_module._MATERIAL_PARSE_ACTIVE_PROJECT_TYPES)
        original_active_project_claims = dict(main_module._MATERIAL_PARSE_ACTIVE_PROJECT_CLAIMS)
        original_active_project_type_claims = dict(
            main_module._MATERIAL_PARSE_ACTIVE_PROJECT_TYPE_CLAIMS
        )
        original_scheduler_stats = dict(main_module._MATERIAL_PARSE_SCHEDULER_STATS)
        original_project_cache_stats = {
            key: dict(value)
            for key, value in main_module._MATERIAL_PARSE_PROJECT_CACHE_STATS.items()
        }
        original_project_cache_request_history = {
            key: [dict(item) for item in value]
            for key, value in main_module._MATERIAL_PARSE_PROJECT_CACHE_REQUEST_HISTORY.items()
        }
        mock_load_projects.return_value = [{"id": "p1", "name": "项目1"}]
        mock_load_materials.return_value = [
            {
                "id": "m1",
                "project_id": "p1",
                "material_type": "boq",
                "filename": "工程量清单.xlsx",
                "path": "/tmp/工程量清单.xlsx",
                "created_at": "2026-03-10T00:00:00+00:00",
                "parse_status": "parsed",
                "parse_backend": "local",
                "parse_phase": "full",
                "parse_ready_for_gate": True,
            }
        ]
        mock_load_jobs.return_value = []
        try:
            main_module._MATERIAL_PARSE_ACTIVE_PROJECTS.clear()
            main_module._MATERIAL_PARSE_ACTIVE_PROJECT_TYPES.clear()
            main_module._MATERIAL_PARSE_ACTIVE_PROJECT_CLAIMS.clear()
            main_module._MATERIAL_PARSE_ACTIVE_PROJECT_TYPE_CLAIMS.clear()
            main_module._MATERIAL_PARSE_SCHEDULER_STATS.clear()
            main_module._MATERIAL_PARSE_PROJECT_CACHE_REQUEST_HISTORY.clear()
            main_module._MATERIAL_PARSE_ACTIVE_PROJECTS.update({"p1": 108.0, "p2": 108.0})
            main_module._MATERIAL_PARSE_ACTIVE_PROJECT_TYPES.update(
                {("p1", "boq"): 108.0, ("p2", "tender_qa"): 108.0}
            )
            main_module._MATERIAL_PARSE_ACTIVE_PROJECT_CLAIMS.update(
                {
                    "p1": 2,
                    "p2": main_module.DEFAULT_MATERIAL_PARSE_ACTIVE_PROJECT_WINDOW_MAX_CLAIMS + 1,
                }
            )
            main_module._MATERIAL_PARSE_ACTIVE_PROJECT_TYPE_CLAIMS.update(
                {
                    ("p1", "boq"): 1,
                    (
                        "p2",
                        "tender_qa",
                    ): main_module.DEFAULT_MATERIAL_PARSE_ACTIVE_PROJECT_TYPE_WINDOW_MAX_CLAIMS + 1,
                }
            )
            main_module._MATERIAL_PARSE_SCHEDULER_STATS.update(
                {
                    "project_continuity_bonus_hits": 4,
                    "followup_full_bonus_hits": 3,
                    "same_material_followup_bonus_hits": 2,
                    "active_project_bonus_hits": 5,
                    "active_project_type_bonus_hits": 1,
                    "claim_snapshot_cache_hits": 8,
                    "claim_snapshot_cache_rebuilds": 2,
                    "claim_context_cache_hits": 7,
                    "claim_context_cache_rebuilds": 3,
                    "project_stage_cache_hits": 6,
                    "project_stage_cache_rebuilds": 2,
                    "priority_context_cache_hits": 5,
                    "priority_context_cache_rebuilds": 1,
                    "jobs_summary_cache_hits": 4,
                    "jobs_summary_cache_rebuilds": 2,
                    "status_materials_cache_hits": 3,
                    "status_materials_cache_rebuilds": 1,
                    "status_core_cache_hits": 2,
                    "status_core_cache_rebuilds": 1,
                }
            )
            main_module._MATERIAL_PARSE_PROJECT_CACHE_STATS.clear()
            main_module._MATERIAL_PARSE_PROJECT_CACHE_STATS.update(
                {
                    "p1": {
                        "jobs_summary_cache_hits": 4,
                        "jobs_summary_cache_rebuilds": 2,
                        "status_materials_cache_hits": 3,
                        "status_materials_cache_rebuilds": 1,
                        "status_core_cache_hits": 2,
                        "status_core_cache_rebuilds": 1,
                    },
                    "p2": {
                        "jobs_summary_cache_hits": 9,
                        "jobs_summary_cache_rebuilds": 4,
                    },
                }
            )
            main_module._MATERIAL_PARSE_PROJECT_CACHE_REQUEST_HISTORY.clear()
            main_module._MATERIAL_PARSE_PROJECT_CACHE_REQUEST_HISTORY.update(
                {
                    "p1": deque(
                        [
                            {
                                "avoided_rebuild_work_units": 6,
                                "rebuilt_work_units": 0,
                                "avoided_rebuild_layer_count": 3,
                                "rebuilt_layer_count": 0,
                            },
                            {
                                "avoided_rebuild_work_units": 2,
                                "rebuilt_work_units": 4,
                                "avoided_rebuild_layer_count": 1,
                                "rebuilt_layer_count": 2,
                            },
                        ],
                        maxlen=main_module._MATERIAL_PARSE_PROJECT_CACHE_REQUEST_HISTORY_WINDOW_SIZE,
                    )
                }
            )
            with patch("app.main.time.monotonic", return_value=100.0):
                response = client.get("/api/v1/projects/p1/materials/parse_status")
        finally:
            main_module._MATERIAL_PARSE_ACTIVE_PROJECTS.clear()
            main_module._MATERIAL_PARSE_ACTIVE_PROJECTS.update(original_active_projects)
            main_module._MATERIAL_PARSE_ACTIVE_PROJECT_TYPES.clear()
            main_module._MATERIAL_PARSE_ACTIVE_PROJECT_TYPES.update(original_active_project_types)
            main_module._MATERIAL_PARSE_ACTIVE_PROJECT_CLAIMS.clear()
            main_module._MATERIAL_PARSE_ACTIVE_PROJECT_CLAIMS.update(original_active_project_claims)
            main_module._MATERIAL_PARSE_ACTIVE_PROJECT_TYPE_CLAIMS.clear()
            main_module._MATERIAL_PARSE_ACTIVE_PROJECT_TYPE_CLAIMS.update(
                original_active_project_type_claims
            )
            main_module._MATERIAL_PARSE_SCHEDULER_STATS.clear()
            main_module._MATERIAL_PARSE_SCHEDULER_STATS.update(original_scheduler_stats)
            main_module._MATERIAL_PARSE_PROJECT_CACHE_STATS.clear()
            main_module._MATERIAL_PARSE_PROJECT_CACHE_STATS.update(
                {key: dict(value) for key, value in original_project_cache_stats.items()}
            )
            main_module._MATERIAL_PARSE_PROJECT_CACHE_REQUEST_HISTORY.clear()
            main_module._MATERIAL_PARSE_PROJECT_CACHE_REQUEST_HISTORY.update(
                {
                    key: deque(
                        [dict(item) for item in value],
                        maxlen=main_module._MATERIAL_PARSE_PROJECT_CACHE_REQUEST_HISTORY_WINDOW_SIZE,
                    )
                    for key, value in original_project_cache_request_history.items()
                }
            )

        assert response.status_code == 200
        data = response.json()
        assert data["summary"]["scheduler_project_continuity_bonus_hits"] == 4
        assert data["debug_info"]["scheduler_hits"]["scheduler_project_continuity_bonus_hits"] == 4
        assert data["debug_info"]["cache"]["scheduler_cache_hit_total"] == 35
        assert data["debug_info"]["project_cache"]["scheduler_project_cache_state"] == "warm"
        assert data["summary"]["scheduler_followup_full_bonus_hits"] == 3
        assert data["summary"]["scheduler_same_material_followup_bonus_hits"] == 2
        assert data["summary"]["scheduler_active_project_bonus_hits"] == 5
        assert data["summary"]["scheduler_active_project_type_bonus_hits"] == 1
        assert data["summary"]["scheduler_claim_snapshot_cache_hits"] == 8
        assert data["summary"]["scheduler_claim_snapshot_cache_rebuilds"] == 2
        assert data["summary"]["scheduler_claim_context_cache_hits"] == 7
        assert data["summary"]["scheduler_claim_context_cache_rebuilds"] == 3
        assert data["summary"]["scheduler_project_stage_cache_hits"] == 6
        assert data["summary"]["scheduler_project_stage_cache_rebuilds"] == 2
        assert data["summary"]["scheduler_priority_context_cache_hits"] == 5
        assert data["summary"]["scheduler_priority_context_cache_rebuilds"] == 1
        assert data["summary"]["scheduler_jobs_summary_cache_hits"] == 4
        assert data["summary"]["scheduler_jobs_summary_cache_rebuilds"] == 2
        assert data["summary"]["scheduler_status_materials_cache_hits"] == 3
        assert data["summary"]["scheduler_status_materials_cache_rebuilds"] == 1
        assert data["summary"]["scheduler_status_core_cache_hits"] == 2
        assert data["summary"]["scheduler_status_core_cache_rebuilds"] == 1
        assert data["summary"]["scheduler_cache_hit_total"] == 35
        assert data["summary"]["scheduler_cache_rebuild_total"] == 12
        assert data["summary"]["scheduler_cache_hit_ratio"] == 0.7447
        assert data["summary"]["scheduler_project_jobs_summary_cache_hits"] == 4
        assert data["summary"]["scheduler_project_jobs_summary_cache_rebuilds"] == 2
        assert data["summary"]["scheduler_project_jobs_summary_cache_state"] == "warm"
        assert data["summary"]["scheduler_project_status_materials_cache_hits"] == 3
        assert data["summary"]["scheduler_project_status_materials_cache_rebuilds"] == 1
        assert data["summary"]["scheduler_project_status_materials_cache_state"] == "warm"
        assert data["summary"]["scheduler_project_status_core_cache_hits"] == 2
        assert data["summary"]["scheduler_project_status_core_cache_rebuilds"] == 1
        assert data["summary"]["scheduler_project_status_core_cache_state"] == "warm"
        assert data["summary"]["scheduler_project_cache_hit_total"] == 9
        assert data["summary"]["scheduler_project_cache_rebuild_total"] == 4
        assert data["summary"]["scheduler_project_cache_hit_ratio"] == 0.6923
        assert data["summary"]["scheduler_project_cache_net_savings"] == 5
        assert data["summary"]["scheduler_project_cache_state"] == "warm"
        assert data["summary"]["scheduler_project_cache_hot_layer_count"] == 3
        assert data["summary"]["scheduler_project_cache_warming_layer_count"] == 0
        assert data["summary"]["scheduler_project_cache_cold_layer_count"] == 0
        assert data["summary"]["scheduler_project_recent_avoided_rebuild_layers"] == []
        assert data["summary"]["scheduler_project_recent_rebuilt_layers"] == []
        assert data["summary"]["scheduler_project_recent_avoided_rebuild_layer_count"] == 0
        assert data["summary"]["scheduler_project_recent_rebuilt_layer_count"] == 0
        assert data["summary"]["scheduler_project_recent_avoided_rebuild_work_units"] == 0
        assert data["summary"]["scheduler_project_recent_rebuilt_work_units"] == 0
        assert data["summary"]["scheduler_project_recent_request_window_size"] == 2
        assert data["summary"]["scheduler_project_recent_cold_start_round_count"] == 0
        assert data["summary"]["scheduler_project_recent_warming_round_count"] == 1
        assert data["summary"]["scheduler_project_recent_steady_round_count"] == 1
        assert data["summary"]["scheduler_project_recent_consecutive_steady_round_count"] == 0
        assert (
            data["summary"]["scheduler_project_recent_stable_hot_threshold"]
            == main_module._MATERIAL_PARSE_PROJECT_CACHE_STEADY_THRESHOLD
        )
        assert data["summary"]["scheduler_project_recent_stable_hot"] is False
        assert data["summary"]["scheduler_project_recent_stable_hot_remaining_rounds"] == 3
        assert data["summary"]["scheduler_project_recent_stable_hot_progress_completed_rounds"] == 0
        assert data["summary"]["scheduler_project_recent_stable_hot_progress_label"] == "0/3"
        assert data["summary"]["scheduler_project_recent_stable_hot_progress_ratio"] == 0.0
        assert data["summary"]["scheduler_project_recent_stable_hot_progress_percent"] == 0
        assert data["summary"]["scheduler_project_recent_stable_hot_progress_percent_label"] == "0%"
        assert (
            data["summary"]["scheduler_project_recent_stable_hot_eta_hint"]
            == "还需连续 3 轮 steady"
        )
        assert data["summary"]["scheduler_project_recent_stable_hot_eta_short_label"] == "还需3轮"
        assert (
            data["summary"]["scheduler_project_recent_stable_hot_progress_summary_label"]
            == "0/3（0%），还需3轮"
        )
        assert (
            data["summary"]["scheduler_project_recent_stable_hot_badge_label"]
            == "预热中 0/3（0%），还需3轮"
        )
        assert (
            data["summary"]["scheduler_project_recent_stable_hot_rule_label"]
            == main_module._material_parse_project_cache_stable_hot_rule_label()
        )
        assert data["summary"]["scheduler_project_recent_window_state"] == "warming"
        assert data["summary"]["scheduler_project_recent_avoided_rebuild_work_units_avg"] == 4.0
        assert data["summary"]["scheduler_project_recent_rebuilt_work_units_avg"] == 2.0
        assert data["summary"]["scheduler_project_recent_net_saved_work_units_avg"] == 2.0
        assert data["summary"]["scheduler_active_project_window_count"] == 1
        assert data["summary"]["scheduler_active_project_type_window_count"] == 1
        assert data["summary"]["scheduler_active_project_quota_exhausted_count"] == 1
        assert data["summary"]["scheduler_active_project_type_quota_exhausted_count"] == 1

    def test_build_material_parse_project_cache_request_delta_core_hit_implies_full_chain_saved(
        self,
    ):
        from app import main as main_module

        delta = main_module._build_material_parse_project_cache_request_delta(
            {},
            {
                "status_core_cache_hits": 1,
            },
        )

        assert delta["scheduler_project_recent_avoided_rebuild_layers"] == [
            "status_core",
            "status_materials",
            "jobs_summary",
        ]
        assert delta["scheduler_project_recent_rebuilt_layers"] == []
        assert delta["scheduler_project_recent_avoided_rebuild_layer_count"] == 3
        assert delta["scheduler_project_recent_rebuilt_layer_count"] == 0
        assert delta["scheduler_project_recent_avoided_rebuild_work_units"] == 6
        assert delta["scheduler_project_recent_rebuilt_work_units"] == 0

    def test_build_material_parse_project_cache_request_delta_tracks_mixed_hit_and_rebuild_layers(
        self,
    ):
        from app import main as main_module

        delta = main_module._build_material_parse_project_cache_request_delta(
            {},
            {
                "jobs_summary_cache_rebuilds": 1,
                "status_materials_cache_hits": 1,
                "status_core_cache_rebuilds": 1,
            },
        )

        assert delta["scheduler_project_recent_avoided_rebuild_layers"] == ["status_materials"]
        assert delta["scheduler_project_recent_rebuilt_layers"] == ["status_core", "jobs_summary"]
        assert delta["scheduler_project_recent_avoided_rebuild_layer_count"] == 1
        assert delta["scheduler_project_recent_rebuilt_layer_count"] == 2
        assert delta["scheduler_project_recent_avoided_rebuild_work_units"] == 2
        assert delta["scheduler_project_recent_rebuilt_work_units"] == 4

    def test_build_material_parse_project_cache_request_delta_excludes_same_request_rebuild_from_saved_layers(
        self,
    ):
        from app import main as main_module

        delta = main_module._build_material_parse_project_cache_request_delta(
            {},
            {
                "jobs_summary_cache_hits": 1,
                "jobs_summary_cache_rebuilds": 1,
            },
        )

        assert delta["scheduler_project_recent_avoided_rebuild_layers"] == []
        assert delta["scheduler_project_recent_rebuilt_layers"] == ["jobs_summary"]
        assert delta["scheduler_project_recent_avoided_rebuild_layer_count"] == 0
        assert delta["scheduler_project_recent_rebuilt_layer_count"] == 1
        assert delta["scheduler_project_recent_avoided_rebuild_work_units"] == 0
        assert delta["scheduler_project_recent_rebuilt_work_units"] == 1

    def test_record_material_parse_project_cache_request_delta_ignores_zero_delta(self):
        from app import main as main_module

        original_history = {
            key: [dict(item) for item in value]
            for key, value in main_module._MATERIAL_PARSE_PROJECT_CACHE_REQUEST_HISTORY.items()
        }
        try:
            main_module._MATERIAL_PARSE_PROJECT_CACHE_REQUEST_HISTORY.clear()
            main_module._record_material_parse_project_cache_request_delta(
                "p1",
                {
                    "scheduler_project_recent_avoided_rebuild_work_units": 0,
                    "scheduler_project_recent_rebuilt_work_units": 0,
                    "scheduler_project_recent_avoided_rebuild_layer_count": 0,
                    "scheduler_project_recent_rebuilt_layer_count": 0,
                },
            )
            assert main_module._material_parse_project_cache_request_history_snapshot("p1") == []
        finally:
            main_module._MATERIAL_PARSE_PROJECT_CACHE_REQUEST_HISTORY.clear()
            main_module._MATERIAL_PARSE_PROJECT_CACHE_REQUEST_HISTORY.update(
                {
                    key: deque(
                        [dict(item) for item in value],
                        maxlen=main_module._MATERIAL_PARSE_PROJECT_CACHE_REQUEST_HISTORY_WINDOW_SIZE,
                    )
                    for key, value in original_history.items()
                }
            )

    def test_record_material_parse_project_cache_request_delta_respects_window_size(self):
        from app import main as main_module

        original_history = {
            key: [dict(item) for item in value]
            for key, value in main_module._MATERIAL_PARSE_PROJECT_CACHE_REQUEST_HISTORY.items()
        }
        try:
            main_module._MATERIAL_PARSE_PROJECT_CACHE_REQUEST_HISTORY.clear()
            for value in range(
                main_module._MATERIAL_PARSE_PROJECT_CACHE_REQUEST_HISTORY_WINDOW_SIZE + 2
            ):
                main_module._record_material_parse_project_cache_request_delta(
                    "p1",
                    {
                        "scheduler_project_recent_avoided_rebuild_work_units": value + 1,
                        "scheduler_project_recent_rebuilt_work_units": 0,
                        "scheduler_project_recent_avoided_rebuild_layer_count": 1,
                        "scheduler_project_recent_rebuilt_layer_count": 0,
                    },
                )
            history = main_module._material_parse_project_cache_request_history_snapshot("p1")
            assert (
                len(history)
                == main_module._MATERIAL_PARSE_PROJECT_CACHE_REQUEST_HISTORY_WINDOW_SIZE
            )
            assert history[0]["avoided_rebuild_work_units"] == 3
            assert history[-1]["avoided_rebuild_work_units"] == (
                main_module._MATERIAL_PARSE_PROJECT_CACHE_REQUEST_HISTORY_WINDOW_SIZE + 2
            )
        finally:
            main_module._MATERIAL_PARSE_PROJECT_CACHE_REQUEST_HISTORY.clear()
            main_module._MATERIAL_PARSE_PROJECT_CACHE_REQUEST_HISTORY.update(
                {
                    key: deque(
                        [dict(item) for item in value],
                        maxlen=main_module._MATERIAL_PARSE_PROJECT_CACHE_REQUEST_HISTORY_WINDOW_SIZE,
                    )
                    for key, value in original_history.items()
                }
            )

    def test_material_parse_project_cache_request_stage_classifies_rounds(self):
        from app import main as main_module

        assert (
            main_module._material_parse_project_cache_request_stage(
                {
                    "avoided_rebuild_work_units": 0,
                    "rebuilt_work_units": 6,
                }
            )
            == "cold_start"
        )
        assert (
            main_module._material_parse_project_cache_request_stage(
                {
                    "avoided_rebuild_work_units": 6,
                    "rebuilt_work_units": 0,
                }
            )
            == "steady"
        )
        assert (
            main_module._material_parse_project_cache_request_stage(
                {
                    "avoided_rebuild_work_units": 2,
                    "rebuilt_work_units": 4,
                }
            )
            == "warming"
        )

    def test_material_parse_project_cache_recent_consecutive_stage_count_tracks_tail_only(self):
        from app import main as main_module

        history = [
            {"avoided_rebuild_work_units": 6, "rebuilt_work_units": 0},
            {"avoided_rebuild_work_units": 0, "rebuilt_work_units": 6},
            {"avoided_rebuild_work_units": 6, "rebuilt_work_units": 0},
            {"avoided_rebuild_work_units": 6, "rebuilt_work_units": 0},
        ]

        assert (
            main_module._material_parse_project_cache_recent_consecutive_stage_count(
                history,
                "steady",
            )
            == 2
        )

    def test_build_material_parse_scheduler_summary_marks_stable_hot_after_threshold(self):
        from app import main as main_module

        original_history = {
            key: [dict(item) for item in value]
            for key, value in main_module._MATERIAL_PARSE_PROJECT_CACHE_REQUEST_HISTORY.items()
        }
        original_scheduler_stats = dict(main_module._MATERIAL_PARSE_SCHEDULER_STATS)
        original_project_cache_stats = {
            key: dict(value)
            for key, value in main_module._MATERIAL_PARSE_PROJECT_CACHE_STATS.items()
        }
        original_active_projects = dict(main_module._MATERIAL_PARSE_ACTIVE_PROJECTS)
        original_active_project_types = dict(main_module._MATERIAL_PARSE_ACTIVE_PROJECT_TYPES)
        original_active_project_claims = dict(main_module._MATERIAL_PARSE_ACTIVE_PROJECT_CLAIMS)
        original_active_project_type_claims = dict(
            main_module._MATERIAL_PARSE_ACTIVE_PROJECT_TYPE_CLAIMS
        )
        try:
            main_module._MATERIAL_PARSE_PROJECT_CACHE_REQUEST_HISTORY.clear()
            main_module._MATERIAL_PARSE_PROJECT_CACHE_REQUEST_HISTORY.update(
                {
                    "p1": deque(
                        [
                            {"avoided_rebuild_work_units": 6, "rebuilt_work_units": 0},
                            {"avoided_rebuild_work_units": 6, "rebuilt_work_units": 0},
                            {"avoided_rebuild_work_units": 6, "rebuilt_work_units": 0},
                        ],
                        maxlen=main_module._MATERIAL_PARSE_PROJECT_CACHE_REQUEST_HISTORY_WINDOW_SIZE,
                    )
                }
            )
            main_module._MATERIAL_PARSE_SCHEDULER_STATS.clear()
            main_module._MATERIAL_PARSE_PROJECT_CACHE_STATS.clear()
            main_module._MATERIAL_PARSE_ACTIVE_PROJECTS.clear()
            main_module._MATERIAL_PARSE_ACTIVE_PROJECT_TYPES.clear()
            main_module._MATERIAL_PARSE_ACTIVE_PROJECT_CLAIMS.clear()
            main_module._MATERIAL_PARSE_ACTIVE_PROJECT_TYPE_CLAIMS.clear()

            summary = main_module._build_material_parse_scheduler_summary("p1")
        finally:
            main_module._MATERIAL_PARSE_PROJECT_CACHE_REQUEST_HISTORY.clear()
            main_module._MATERIAL_PARSE_PROJECT_CACHE_REQUEST_HISTORY.update(
                {
                    key: deque(
                        [dict(item) for item in value],
                        maxlen=main_module._MATERIAL_PARSE_PROJECT_CACHE_REQUEST_HISTORY_WINDOW_SIZE,
                    )
                    for key, value in original_history.items()
                }
            )
            main_module._MATERIAL_PARSE_SCHEDULER_STATS.clear()
            main_module._MATERIAL_PARSE_SCHEDULER_STATS.update(original_scheduler_stats)
            main_module._MATERIAL_PARSE_PROJECT_CACHE_STATS.clear()
            main_module._MATERIAL_PARSE_PROJECT_CACHE_STATS.update(
                {key: dict(value) for key, value in original_project_cache_stats.items()}
            )
            main_module._MATERIAL_PARSE_ACTIVE_PROJECTS.clear()
            main_module._MATERIAL_PARSE_ACTIVE_PROJECTS.update(original_active_projects)
            main_module._MATERIAL_PARSE_ACTIVE_PROJECT_TYPES.clear()
            main_module._MATERIAL_PARSE_ACTIVE_PROJECT_TYPES.update(original_active_project_types)
            main_module._MATERIAL_PARSE_ACTIVE_PROJECT_CLAIMS.clear()
            main_module._MATERIAL_PARSE_ACTIVE_PROJECT_CLAIMS.update(original_active_project_claims)
            main_module._MATERIAL_PARSE_ACTIVE_PROJECT_TYPE_CLAIMS.clear()
            main_module._MATERIAL_PARSE_ACTIVE_PROJECT_TYPE_CLAIMS.update(
                original_active_project_type_claims
            )

        assert summary["scheduler_project_recent_steady_round_count"] == 3
        assert summary["scheduler_project_recent_consecutive_steady_round_count"] == 3
        assert (
            summary["scheduler_project_recent_stable_hot_threshold"]
            == main_module._MATERIAL_PARSE_PROJECT_CACHE_STEADY_THRESHOLD
        )
        assert summary["scheduler_project_recent_stable_hot"] is True
        assert summary["scheduler_project_recent_stable_hot_remaining_rounds"] == 0
        assert summary["scheduler_project_recent_stable_hot_progress_completed_rounds"] == 3
        assert summary["scheduler_project_recent_stable_hot_progress_label"] == "3/3"
        assert summary["scheduler_project_recent_stable_hot_progress_ratio"] == 1.0
        assert summary["scheduler_project_recent_stable_hot_progress_percent"] == 100
        assert summary["scheduler_project_recent_stable_hot_progress_percent_label"] == "100%"
        assert summary["scheduler_project_recent_stable_hot_eta_hint"] == "已达到稳定转热阈值"
        assert summary["scheduler_project_recent_stable_hot_eta_short_label"] == "已达阈值"
        assert (
            summary["scheduler_project_recent_stable_hot_progress_summary_label"]
            == "3/3（100%），已达阈值"
        )
        assert summary["scheduler_project_recent_stable_hot_badge_label"] == "稳定热态"
        assert (
            summary["scheduler_project_recent_stable_hot_rule_label"]
            == main_module._material_parse_project_cache_stable_hot_rule_label()
        )
        assert summary["scheduler_project_recent_window_state"] == "stable_hot"

    @patch("app.main.load_material_parse_jobs")
    @patch("app.main.load_materials")
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    def test_get_material_parse_status_includes_boq_resume_metrics(
        self,
        mock_ensure,
        mock_load_projects,
        mock_load_materials,
        mock_load_jobs,
        client,
    ):
        mock_load_projects.return_value = [{"id": "p1", "name": "项目1"}]
        mock_load_materials.return_value = [
            {
                "id": "m2",
                "project_id": "p1",
                "material_type": "boq",
                "filename": "工程量清单.xlsx",
                "path": "/tmp/工程量清单.xlsx",
                "created_at": "2026-03-10T00:00:00+00:00",
                "parse_status": "parsed",
                "parse_backend": "local",
                "parse_phase": "full",
                "parse_ready_for_gate": True,
                "boq_structured_summary": {
                    "scan_strategy": "preview_guided_full",
                    "scan_guidance_strength": "strong",
                    "skipped_tail_sheets": 1,
                    "sheets": [
                        {
                            "sheet": "分部分项工程量清单",
                            "resumed_from_prior_summary": True,
                            "resume_from_row": 181,
                        },
                        {
                            "sheet": "措施项目清单",
                            "resumed_from_prior_summary": True,
                            "resume_from_row": 121,
                        },
                        {
                            "sheet": "封面说明",
                            "resumed_from_prior_summary": False,
                            "resume_from_row": 1,
                        },
                    ],
                },
            }
        ]
        mock_load_jobs.return_value = []

        response = client.get("/api/v1/projects/p1/materials/parse_status")
        assert response.status_code == 200
        data = response.json()
        assert data["summary"]["boq_guided_full_materials"] == 1
        assert data["summary"]["boq_guided_full_strong_materials"] == 1
        assert data["summary"]["boq_resumed_full_materials"] == 1
        assert data["summary"]["boq_resumed_sheet_count"] == 2
        assert data["summary"]["boq_saved_row_count"] == 300
        assert data["summary"]["boq_skipped_tail_sheets"] == 1
        assert data["summary"]["boq_resume_hit_rate"] == 1.0
        assert "已复用预解析前段" in data["materials"][0]["parse_note"]
        assert "续跑 2 张 sheet" in data["materials"][0]["parse_note"]
        assert "少扫 300 行" in data["materials"][0]["parse_note"]
        assert "尾部略过 1 张辅助 sheet" in data["materials"][0]["parse_note"]

    @patch("app.main.load_material_parse_jobs")
    @patch("app.main.load_materials")
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    def test_get_material_parse_status_includes_boq_preview_note(
        self,
        mock_ensure,
        mock_load_projects,
        mock_load_materials,
        mock_load_jobs,
        client,
    ):
        mock_load_projects.return_value = [{"id": "p1", "name": "项目1"}]
        mock_load_materials.return_value = [
            {
                "id": "m3",
                "project_id": "p1",
                "material_type": "boq",
                "filename": "工程量清单.csv",
                "path": "/tmp/工程量清单.csv",
                "created_at": "2026-03-10T00:00:00+00:00",
                "parse_status": "queued",
                "parse_backend": "queued",
                "parse_phase": "preview",
                "parse_ready_for_gate": False,
                "boq_structured_summary": {
                    "parse_stage": "preview",
                    "sheets": [
                        {"sheet": "csv", "scanned_rows": 180},
                    ],
                },
                "job_id": "j3",
            }
        ]
        mock_load_jobs.return_value = [
            {
                "id": "j3",
                "material_id": "m3",
                "project_id": "p1",
                "material_type": "boq",
                "filename": "工程量清单.csv",
                "status": "queued",
                "parse_backend": "local_preview",
                "parse_mode": "full",
                "attempt": 1,
            }
        ]

        response = client.get("/api/v1/projects/p1/materials/parse_status")
        assert response.status_code == 200
        data = response.json()
        assert data["materials"][0]["parse_effective_status"] == "previewed"
        assert "BOQ 已完成轻量预解析" in data["materials"][0]["parse_note"]
        assert "已先扫 1 张 sheet" in data["materials"][0]["parse_note"]
        assert "约 180 行" in data["materials"][0]["parse_note"]
        assert "后台会继续补全 full 解析" in data["materials"][0]["parse_note"]

    @patch("app.main.load_material_parse_jobs")
    @patch("app.main.load_materials")
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    def test_get_material_parse_status_includes_boq_pdf_note(
        self,
        mock_ensure,
        mock_load_projects,
        mock_load_materials,
        mock_load_jobs,
        client,
    ):
        mock_load_projects.return_value = [{"id": "p1", "name": "项目1"}]
        mock_load_materials.return_value = [
            {
                "id": "m4",
                "project_id": "p1",
                "material_type": "boq",
                "filename": "工程量清单.pdf",
                "path": "/tmp/工程量清单.pdf",
                "created_at": "2026-03-10T00:00:00+00:00",
                "parse_status": "parsed",
                "parse_backend": "local",
                "parse_phase": "full",
                "parse_ready_for_gate": True,
                "boq_structured_summary": {
                    "detected_format": "pdf",
                    "text_chars": 7134,
                },
            }
        ]
        mock_load_jobs.return_value = []

        response = client.get("/api/v1/projects/p1/materials/parse_status")
        assert response.status_code == 200
        data = response.json()
        assert data["materials"][0]["parse_effective_status"] == "parsed"
        assert "已完成本地解析。" in data["materials"][0]["parse_note"]
        assert "当前为 PDF 清单" in data["materials"][0]["parse_note"]
        assert "未进入表格型 BOQ 的 preview/full 差量补全路径" in data["materials"][0]["parse_note"]

    @patch("app.main.load_material_parse_jobs")
    @patch("app.main.load_materials")
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    def test_get_material_parse_status_includes_boq_pdf_resume_note(
        self,
        mock_ensure,
        mock_load_projects,
        mock_load_materials,
        mock_load_jobs,
        client,
    ):
        mock_load_projects.return_value = [{"id": "p1", "name": "项目1"}]
        mock_load_materials.return_value = [
            {
                "id": "m5",
                "project_id": "p1",
                "material_type": "boq",
                "filename": "工程量清单.pdf",
                "path": "/tmp/工程量清单.pdf",
                "created_at": "2026-03-10T00:00:00+00:00",
                "parse_status": "parsed",
                "parse_backend": "local",
                "parse_phase": "full",
                "parse_ready_for_gate": True,
                "boq_structured_summary": {
                    "detected_format": "pdf",
                    "scan_strategy": "preview_guided_full_pdf",
                    "resume_from_page": 3,
                    "saved_page_count": 2,
                    "parsed_page_count": 3,
                    "text_chars": 7134,
                },
            }
        ]
        mock_load_jobs.return_value = []

        response = client.get("/api/v1/projects/p1/materials/parse_status")
        assert response.status_code == 200
        data = response.json()
        assert data["materials"][0]["parse_effective_status"] == "parsed"
        assert "已复用预解析前页" in data["materials"][0]["parse_note"]
        assert "从第 3 页继续" in data["materials"][0]["parse_note"]
        assert "少扫前 2 页" in data["materials"][0]["parse_note"]

    @patch("app.main._notify_material_parse_workers")
    @patch("app.main.save_material_parse_jobs")
    @patch("app.main.save_materials")
    @patch("app.main.load_material_parse_jobs")
    @patch("app.main.load_materials")
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    def test_reparse_project_materials_requeues_and_clears_errors(
        self,
        mock_ensure,
        mock_load_projects,
        mock_load_materials,
        mock_load_jobs,
        mock_save_materials,
        mock_save_jobs,
        mock_notify_workers,
        client,
    ):
        mock_load_projects.return_value = [{"id": "p1", "name": "项目1"}]
        mock_load_materials.return_value = [
            {
                "id": "m1",
                "project_id": "p1",
                "material_type": "drawing",
                "filename": "总图.dxf",
                "path": "/tmp/总图.dxf",
                "created_at": "2026-03-09T00:00:00+00:00",
                "updated_at": "2026-03-09T00:00:00+00:00",
                "parse_status": "failed",
                "parse_backend": "local",
                "parse_error_class": "ocr_failed",
                "parse_error_message": "empty_result",
                "job_id": "j-old",
            }
        ]
        mock_load_jobs.return_value = [
            {
                "id": "j-old",
                "material_id": "m1",
                "project_id": "p1",
                "material_type": "drawing",
                "filename": "总图.dxf",
                "status": "failed",
                "attempt": 2,
            }
        ]

        response = client.post("/api/v1/projects/p1/materials/reparse")
        assert response.status_code == 200
        data = response.json()
        assert data["summary"]["queued_materials"] == 1
        saved_rows = mock_save_materials.call_args[0][0]
        assert saved_rows[0]["parse_status"] == "queued"
        mock_notify_workers.assert_called_once()
        assert saved_rows[0]["parse_error_message"] is None
        assert mock_save_jobs.called is True

    @patch("app.main.save_material_parse_jobs")
    @patch("app.main.save_materials")
    @patch("app.main.load_material_parse_jobs")
    @patch("app.main.load_materials")
    def test_bootstrap_material_parse_state_compacts_duplicate_active_jobs(
        self,
        mock_load_materials,
        mock_load_jobs,
        mock_save_materials,
        mock_save_jobs,
    ):
        from app.main import _bootstrap_material_parse_state

        mock_load_materials.return_value = [
            {
                "id": "m1",
                "project_id": "p1",
                "material_type": "drawing",
                "filename": "总图.dxf",
                "path": "/tmp/总图.dxf",
                "created_at": "2026-03-09T00:00:00+00:00",
                "updated_at": "2026-03-09T00:00:00+00:00",
                "parse_status": "processing",
                "parse_backend": "local",
                "job_id": "j-queued",
            }
        ]
        mock_load_jobs.return_value = [
            {
                "id": "j-processing",
                "material_id": "m1",
                "project_id": "p1",
                "material_type": "drawing",
                "filename": "总图.dxf",
                "status": "processing",
                "created_at": "2026-03-09T00:00:00+00:00",
                "updated_at": "2026-03-09T00:10:00+00:00",
            },
            {
                "id": "j-queued",
                "material_id": "m1",
                "project_id": "p1",
                "material_type": "drawing",
                "filename": "总图.dxf",
                "status": "queued",
                "created_at": "2026-03-09T00:11:00+00:00",
                "updated_at": "2026-03-09T00:11:00+00:00",
            },
        ]

        summary = _bootstrap_material_parse_state()

        assert summary["deduplicated_jobs"] == 1
        assert summary["recovered_jobs"] == 1
        saved_jobs = mock_save_jobs.call_args[0][0]
        assert len(saved_jobs) == 1
        assert saved_jobs[0]["id"] == "j-processing"
        assert saved_jobs[0]["status"] == "queued"
        saved_rows = mock_save_materials.call_args[0][0]
        assert saved_rows[0]["job_id"] == "j-processing"
        assert saved_rows[0]["parse_status"] == "queued"

    @patch("app.main.save_material_parse_jobs")
    @patch("app.main.save_materials")
    @patch("app.main.load_material_parse_jobs")
    @patch("app.main.load_materials")
    def test_bootstrap_material_parse_state_syncs_stale_processing_job_to_parsed(
        self,
        mock_load_materials,
        mock_load_jobs,
        mock_save_materials,
        mock_save_jobs,
    ):
        from app.main import _bootstrap_material_parse_state

        mock_load_materials.return_value = [
            {
                "id": "m1",
                "project_id": "p1",
                "material_type": "drawing",
                "filename": "总图.dxf",
                "path": "/tmp/总图.dxf",
                "created_at": "2026-03-09T00:00:00+00:00",
                "updated_at": "2026-03-09T00:05:00+00:00",
                "parse_status": "parsed",
                "parse_backend": "local",
                "parse_confidence": 0.91,
                "parse_finished_at": "2026-03-09T00:05:00+00:00",
                "job_id": "j-processing",
            }
        ]
        mock_load_jobs.return_value = [
            {
                "id": "j-processing",
                "material_id": "m1",
                "project_id": "p1",
                "material_type": "drawing",
                "filename": "总图.dxf",
                "status": "processing",
                "created_at": "2026-03-09T00:00:00+00:00",
                "updated_at": "2026-03-09T00:01:00+00:00",
            }
        ]

        summary = _bootstrap_material_parse_state()

        assert summary["terminal_synced_jobs"] == 1
        saved_jobs = mock_save_jobs.call_args[0][0]
        assert saved_jobs[0]["status"] == "parsed"
        assert saved_jobs[0]["finished_at"] == "2026-03-09T00:05:00+00:00"
        assert saved_jobs[0]["parse_backend"] == "local"
        assert saved_jobs[0]["parse_confidence"] == 0.91
        saved_rows = mock_save_materials.call_args[0][0]
        assert saved_rows[0]["parse_status"] == "parsed"
        assert saved_rows[0]["job_id"] == "j-processing"

    @patch("app.main.save_material_parse_jobs")
    @patch("app.main.save_materials")
    @patch("app.main.load_material_parse_jobs")
    @patch("app.main.load_materials")
    def test_bootstrap_material_parse_state_requeues_stale_terminal_job_without_material_payload(
        self,
        mock_load_materials,
        mock_load_jobs,
        mock_save_materials,
        mock_save_jobs,
    ):
        from app.main import _bootstrap_material_parse_state

        mock_load_materials.return_value = [
            {
                "id": "m1",
                "project_id": "p1",
                "material_type": "boq",
                "filename": "工程量清单.pdf",
                "path": "/tmp/工程量清单.pdf",
                "created_at": "2026-03-09T00:00:00+00:00",
                "updated_at": "2026-03-09T00:05:00+00:00",
                "parse_status": "queued",
                "parse_backend": "queued",
                "parse_phase": None,
                "parse_ready_for_gate": False,
                "parse_confidence": 0.0,
                "parse_error_class": "worker_recovered",
                "parse_error_message": "worker_recovered",
                "job_id": "j-parsed",
                "parsed_text": "",
                "structured_summary": None,
                "parsed_chars": 0,
                "parsed_chunks": [],
                "numeric_terms_norm": [],
                "lexical_terms": [],
            }
        ]
        mock_load_jobs.return_value = [
            {
                "id": "j-parsed",
                "material_id": "m1",
                "project_id": "p1",
                "material_type": "boq",
                "filename": "工程量清单.pdf",
                "status": "parsed",
                "parse_mode": "full",
                "attempt": 1,
                "created_at": "2026-03-09T00:00:00+00:00",
                "updated_at": "2026-03-09T00:05:00+00:00",
                "finished_at": "2026-03-09T00:05:00+00:00",
                "parse_backend": "local",
                "parse_confidence": 0.48,
            }
        ]

        summary = _bootstrap_material_parse_state()

        assert summary["requeued_terminal_jobs"] == 1
        saved_jobs = mock_save_jobs.call_args[0][0]
        assert len(saved_jobs) == 2
        assert any(job["id"] == "j-parsed" and job["status"] == "parsed" for job in saved_jobs)
        repaired_jobs = [job for job in saved_jobs if job["id"] != "j-parsed"]
        assert len(repaired_jobs) == 1
        assert repaired_jobs[0]["material_id"] == "m1"
        assert repaired_jobs[0]["status"] == "queued"
        assert repaired_jobs[0]["parse_mode"] == "full"
        saved_rows = mock_save_materials.call_args[0][0]
        assert saved_rows[0]["parse_status"] == "queued"
        assert saved_rows[0]["parse_backend"] == "queued"
        assert saved_rows[0]["job_id"] != "j-parsed"
        assert saved_rows[0]["parse_error_message"] == "stale_terminal_job_without_material_payload"

    @patch("app.main.save_material_parse_jobs")
    @patch("app.main.save_materials")
    @patch("app.main.load_material_parse_jobs")
    @patch("app.main.load_materials")
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    def test_reparse_project_materials_reuses_active_job_without_appending_duplicates(
        self,
        mock_ensure,
        mock_load_projects,
        mock_load_materials,
        mock_load_jobs,
        mock_save_materials,
        mock_save_jobs,
        client,
    ):
        mock_load_projects.return_value = [{"id": "p1", "name": "项目1"}]
        mock_load_materials.return_value = [
            {
                "id": "m1",
                "project_id": "p1",
                "material_type": "drawing",
                "filename": "总图.dxf",
                "path": "/tmp/总图.dxf",
                "created_at": "2026-03-09T00:00:00+00:00",
                "updated_at": "2026-03-09T00:00:00+00:00",
                "parse_status": "processing",
                "parse_backend": "local",
                "job_id": "j-active",
            }
        ]
        mock_load_jobs.return_value = [
            {
                "id": "j-active",
                "material_id": "m1",
                "project_id": "p1",
                "material_type": "drawing",
                "filename": "总图.dxf",
                "status": "processing",
                "attempt": 1,
                "created_at": "2026-03-09T00:00:00+00:00",
                "updated_at": "2026-03-09T00:01:00+00:00",
            }
        ]

        response = client.post("/api/v1/projects/p1/materials/reparse")
        assert response.status_code == 200
        saved_jobs = mock_save_jobs.call_args[0][0]
        assert len(saved_jobs) == 1
        assert saved_jobs[0]["id"] == "j-active"
        assert saved_jobs[0]["status"] == "queued"
        saved_rows = mock_save_materials.call_args[0][0]
        assert saved_rows[0]["job_id"] == "j-active"
        assert saved_rows[0]["parse_status"] == "queued"

    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    def test_get_scoring_readiness_project_not_found(self, mock_ensure, mock_load_projects, client):
        mock_load_projects.return_value = []
        response = client.get("/api/v1/projects/nonexistent/scoring_readiness")
        assert response.status_code == 404
        assert "项目不存在" in response.json()["detail"]

    @patch("app.main._now_iso", return_value="2026-02-26T00:00:00+00:00")
    @patch("app.main._submission_is_scored")
    @patch("app.main.load_submissions")
    @patch("app.main._resolve_material_utilization_policy")
    @patch("app.main._validate_material_gate_for_scoring")
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    def test_get_scoring_readiness_blocked_when_gate_failed(
        self,
        mock_ensure,
        mock_load_projects,
        mock_material_gate,
        mock_resolve_policy,
        mock_load_submissions,
        mock_submission_is_scored,
        mock_now_iso,
        client,
    ):
        mock_load_projects.return_value = [{"id": "p1", "name": "项目1", "meta": {}}]
        mock_material_gate.return_value = (
            {
                "project_id": "p1",
                "total_files": 2,
                "parsed_ok_files": 1,
                "parse_fail_ratio": 0.5,
                "gate": {"passed": False, "issues": ["缺少必需资料类型：图纸"]},
            },
            ["缺少必需资料类型：图纸"],
        )
        mock_resolve_policy.return_value = {"required_types": ["tender_qa", "drawing"]}
        mock_load_submissions.return_value = [
            {"id": "s1", "project_id": "p1", "text": "施组内容"},
            {"id": "s2", "project_id": "p1", "text": ""},
        ]
        mock_submission_is_scored.side_effect = [False, False]

        response = client.get("/api/v1/projects/p1/scoring_readiness")
        assert response.status_code == 200
        data = response.json()
        assert data["project_id"] == "p1"
        assert data["ready"] is False
        assert data["score_button_enabled"] is False
        assert data["gate_passed"] is False
        assert data["issues"] == ["缺少必需资料类型：图纸"]
        assert data["submissions"]["total"] == 2
        assert data["submissions"]["non_empty"] == 1
        assert data["submissions"]["scored"] == 0
        assert data["generated_at"] == "2026-02-26T00:00:00+00:00"

    @patch("app.main._now_iso", return_value="2026-02-26T00:00:00+00:00")
    @patch("app.main._submission_is_scored")
    @patch("app.main.load_submissions")
    @patch("app.main._resolve_material_utilization_policy")
    @patch("app.main._validate_material_gate_for_scoring")
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    def test_get_scoring_readiness_ready_when_gate_passed_and_submission_exists(
        self,
        mock_ensure,
        mock_load_projects,
        mock_material_gate,
        mock_resolve_policy,
        mock_load_submissions,
        mock_submission_is_scored,
        mock_now_iso,
        client,
    ):
        mock_load_projects.return_value = [{"id": "p1", "name": "项目1", "meta": {}}]
        mock_material_gate.return_value = (
            {
                "project_id": "p1",
                "total_files": 4,
                "parsed_ok_files": 4,
                "parse_fail_ratio": 0.0,
                "gate": {"passed": True, "issues": []},
            },
            [],
        )
        mock_resolve_policy.return_value = {"required_types": ["tender_qa", "boq", "drawing"]}
        mock_load_submissions.return_value = [{"id": "s1", "project_id": "p1", "text": "施组内容"}]
        mock_submission_is_scored.return_value = True

        response = client.get("/api/v1/projects/p1/scoring_readiness")
        assert response.status_code == 200
        data = response.json()
        assert data["project_id"] == "p1"
        assert data["ready"] is True
        assert data["score_button_enabled"] is True
        assert data["gate_passed"] is True
        assert data["issues"] == []
        assert data["submissions"]["total"] == 1
        assert data["submissions"]["non_empty"] == 1
        assert data["submissions"]["scored"] == 1
        assert data["generated_at"] == "2026-02-26T00:00:00+00:00"

    @patch("app.main._now_iso", return_value="2026-02-26T00:00:00+00:00")
    @patch("app.main._submission_is_scored")
    @patch("app.main.load_submissions")
    @patch("app.main._resolve_material_utilization_policy")
    @patch("app.main._validate_material_gate_for_scoring")
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    def test_get_scoring_readiness_blocked_when_depth_gate_enforced_and_failed(
        self,
        mock_ensure,
        mock_load_projects,
        mock_material_gate,
        mock_resolve_policy,
        mock_load_submissions,
        mock_submission_is_scored,
        mock_now_iso,
        client,
    ):
        mock_load_projects.return_value = [{"id": "p1", "name": "项目1", "meta": {}}]
        mock_material_gate.return_value = (
            {
                "project_id": "p1",
                "total_files": 3,
                "parsed_ok_files": 3,
                "total_parsed_chunks": 9,
                "total_numeric_terms": 4,
                "gate": {"passed": True, "issues": []},
                "depth_gate": {
                    "enforce": True,
                    "passed": False,
                    "issues": ["图纸解析分块不足：1 段（建议至少 3 段）"],
                },
            },
            [],
        )
        mock_resolve_policy.return_value = {"required_types": ["tender_qa", "boq", "drawing"]}
        mock_load_submissions.return_value = [{"id": "s1", "project_id": "p1", "text": "施组内容"}]
        mock_submission_is_scored.return_value = False

        response = client.get("/api/v1/projects/p1/scoring_readiness")
        assert response.status_code == 200
        data = response.json()
        assert data["ready"] is False
        assert data["gate_passed"] is False
        assert data["issues"][0].startswith("资料深读门禁：")
        assert data["material_depth_gate"]["enforce"] is True
        assert data["material_depth_gate"]["passed"] is False

    @patch("app.main._now_iso", return_value="2026-02-26T00:00:00+00:00")
    @patch("app.main._submission_is_scored")
    @patch("app.main.load_submissions")
    @patch("app.main._resolve_material_utilization_policy")
    @patch("app.main._validate_material_gate_for_scoring")
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    def test_get_scoring_readiness_depth_gate_warn_only_when_not_enforced(
        self,
        mock_ensure,
        mock_load_projects,
        mock_material_gate,
        mock_resolve_policy,
        mock_load_submissions,
        mock_submission_is_scored,
        mock_now_iso,
        client,
    ):
        mock_load_projects.return_value = [{"id": "p1", "name": "项目1", "meta": {}}]
        mock_material_gate.return_value = (
            {
                "project_id": "p1",
                "total_files": 3,
                "parsed_ok_files": 3,
                "total_parsed_chunks": 9,
                "total_numeric_terms": 4,
                "gate": {"passed": True, "issues": []},
                "depth_gate": {
                    "enforce": False,
                    "passed": False,
                    "issues": ["清单数字约束提取不足：2 项（建议至少 8 项）"],
                },
            },
            [],
        )
        mock_resolve_policy.return_value = {"required_types": ["tender_qa", "boq", "drawing"]}
        mock_load_submissions.return_value = [{"id": "s1", "project_id": "p1", "text": "施组内容"}]
        mock_submission_is_scored.return_value = False

        response = client.get("/api/v1/projects/p1/scoring_readiness")
        assert response.status_code == 200
        data = response.json()
        assert data["ready"] is True
        assert data["gate_passed"] is True
        assert data["issues"] == []
        assert any("资料深读预警" in w for w in data["warnings"])
        assert data["material_depth_gate"]["enforce"] is False
        assert data["material_depth_gate"]["passed"] is False

    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    def test_get_project_mece_audit_project_not_found(
        self, mock_ensure, mock_load_projects, client
    ):
        mock_load_projects.return_value = []
        response = client.get("/api/v1/projects/nonexistent/mece_audit")
        assert response.status_code == 404
        assert "项目不存在" in response.json()["detail"]

    @patch("app.main._build_submission_scoring_basis_report")
    @patch("app.main._resolve_submission_score_fields")
    @patch("app.main._submission_is_scored")
    @patch("app.main.load_submissions")
    @patch("app.main._run_system_self_check")
    @patch("app.main._build_evolution_health_report")
    @patch("app.main._build_material_depth_report")
    @patch("app.main._build_scoring_readiness")
    @patch("app.main._now_iso", return_value="2026-02-28T00:00:00+00:00")
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    def test_get_project_mece_audit_success(
        self,
        mock_ensure,
        mock_load_projects,
        mock_now_iso,
        mock_build_readiness,
        mock_build_depth,
        mock_build_evo_health,
        mock_self_check,
        mock_load_submissions,
        mock_submission_is_scored,
        mock_resolve_score_fields,
        mock_scoring_basis,
        client,
    ):
        mock_load_projects.return_value = [{"id": "p1", "name": "项目1", "meta": {}}]
        mock_build_readiness.return_value = {
            "project_id": "p1",
            "ready": True,
            "gate_passed": True,
            "issues": [],
            "warnings": [],
            "material_quality": {"total_parsed_chars": 8600},
            "material_gate": {"required_types": ["tender_qa", "boq", "drawing"]},
        }
        mock_build_depth.return_value = {
            "quality_summary": {"total_files": 4},
        }
        mock_build_evo_health.return_value = {
            "summary": {
                "ground_truth_count": 4,
                "matched_prediction_count": 3,
                "has_evolved_multipliers": True,
                "current_weights_source": "evolution",
            },
            "drift": {"level": "low"},
        }
        mock_self_check.return_value = {
            "ok": True,
            "items": [{"name": "health", "ok": True}],
        }
        mock_load_submissions.return_value = [
            {
                "id": "s1",
                "project_id": "p1",
                "created_at": "2026-02-27T00:00:00+00:00",
                "updated_at": "2026-02-27T00:00:00+00:00",
                "report": {"scoring_status": "done"},
            }
        ]
        mock_submission_is_scored.return_value = True
        mock_resolve_score_fields.return_value = {"total_score": 78.6}
        mock_scoring_basis.return_value = {
            "mece_inputs": {
                "project_materials_extracted": True,
                "shigong_parsed": True,
                "bid_requirements_loaded": True,
                "attention_16d_weights_injected": True,
                "custom_instructions_injected": True,
            },
            "evidence_trace": {"total_requirements": 10, "total_hits": 8},
        }

        response = client.get("/api/v1/projects/p1/mece_audit")
        assert response.status_code == 200
        data = response.json()
        assert data["project_id"] == "p1"
        assert data["overall"]["level"] == "good"
        assert data["overall"]["health_score"] >= 90
        assert data["summary"]["submission_scored"] == 1
        assert data["summary"]["ground_truth_count"] == 4
        assert isinstance(data["dimensions"], list) and len(data["dimensions"]) >= 4
        keys = {row["key"] for row in data["dimensions"]}
        assert "input_chain" in keys
        assert "scoring_validity" in keys
        assert "self_evolution_loop" in keys
        assert "runtime_stability" in keys

    @patch("app.main._build_submission_scoring_basis_report")
    @patch("app.main._resolve_submission_score_fields")
    @patch("app.main._submission_is_scored")
    @patch("app.main.load_submissions")
    @patch("app.main._run_system_self_check")
    @patch("app.main._build_evolution_health_report")
    @patch("app.main._build_material_depth_report")
    @patch("app.main._build_scoring_readiness")
    @patch("app.main._now_iso", return_value="2026-02-28T00:00:00+00:00")
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    def test_get_project_mece_audit_marks_runtime_warn_for_optional_failures(
        self,
        mock_ensure,
        mock_load_projects,
        mock_now_iso,
        mock_build_readiness,
        mock_build_depth,
        mock_build_evo_health,
        mock_self_check,
        mock_load_submissions,
        mock_submission_is_scored,
        mock_resolve_score_fields,
        mock_scoring_basis,
        client,
    ):
        mock_load_projects.return_value = [{"id": "p1", "name": "项目1", "meta": {}}]
        mock_build_readiness.return_value = {
            "project_id": "p1",
            "ready": True,
            "gate_passed": True,
            "issues": [],
            "warnings": [],
            "material_quality": {"total_parsed_chars": 8600},
            "material_gate": {"required_types": ["tender_qa", "boq", "drawing"]},
        }
        mock_build_depth.return_value = {"quality_summary": {"total_files": 4}}
        mock_build_evo_health.return_value = {
            "summary": {
                "ground_truth_count": 4,
                "matched_prediction_count": 3,
                "has_evolved_multipliers": True,
                "stored_evolved_multipliers": True,
                "current_weights_source": "evolution",
            },
            "drift": {"level": "low"},
        }
        mock_self_check.return_value = {
            "ok": True,
            "items": [
                {"name": "health", "ok": True},
                {"name": "data_hygiene", "ok": False, "detail": "orphan_records=2"},
            ],
        }
        mock_load_submissions.return_value = [
            {
                "id": "s1",
                "project_id": "p1",
                "created_at": "2026-02-27T00:00:00+00:00",
                "updated_at": "2026-02-27T00:00:00+00:00",
                "report": {"scoring_status": "done"},
            }
        ]
        mock_submission_is_scored.return_value = True
        mock_resolve_score_fields.return_value = {"total_score": 78.6}
        mock_scoring_basis.return_value = {
            "mece_inputs": {
                "project_materials_extracted": True,
                "shigong_parsed": True,
                "bid_requirements_loaded": True,
                "attention_16d_weights_injected": True,
                "custom_instructions_injected": True,
            },
            "evidence_trace": {"total_requirements": 10, "total_hits": 8},
        }

        response = client.get("/api/v1/projects/p1/mece_audit")
        assert response.status_code == 200
        rows = {row["key"]: row for row in response.json()["dimensions"]}
        assert rows["runtime_stability"]["status"] == "warn"
        assert any("data_hygiene" in str(x) for x in rows["runtime_stability"]["warnings"])

    @patch("app.main._build_evolution_health_report")
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    def test_get_project_evolution_health_success(
        self,
        mock_ensure,
        mock_load_projects,
        mock_build_health,
        client,
    ):
        mock_load_projects.return_value = [{"id": "p1", "name": "项目1", "meta": {}}]
        mock_build_health.return_value = {
            "project_id": "p1",
            "generated_at": "2026-02-27T00:00:00+00:00",
            "summary": {
                "ground_truth_count": 4,
                "matched_prediction_count": 4,
                "unmatched_ground_truth_count": 0,
                "current_weights_source": "evolution",
                "has_evolved_multipliers": True,
            },
            "windows": {
                "all": {"count": 4, "mae": 2.1, "rmse": 2.8},
                "recent_30d": {"count": 3, "mae": 1.9, "rmse": 2.4},
                "recent_90d": {"count": 4, "mae": 2.1, "rmse": 2.8},
                "prev_30_90d": {"count": 1, "mae": 2.7, "rmse": 2.7},
            },
            "drift": {"level": "low", "half_life_days": 30.0},
            "recommendations": ["继续录入最新真实评分以保持时效。"],
        }

        response = client.get("/api/v1/projects/p1/evolution/health")
        assert response.status_code == 200
        data = response.json()
        assert data["project_id"] == "p1"
        assert data["summary"]["ground_truth_count"] == 4
        assert data["drift"]["level"] == "low"

    @patch("app.main._build_project_trial_preflight")
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    def test_get_project_trial_preflight_success(
        self,
        mock_ensure,
        mock_load_projects,
        mock_build_preflight,
        client,
    ):
        mock_load_projects.return_value = [{"id": "p1", "name": "项目1", "meta": {}}]
        mock_build_preflight.return_value = {
            "generated_at": "2026-03-31T10:00:00+08:00",
            "base_url": "/api/v1",
            "project_id": "p1",
            "project_name": "项目1",
            "trial_run_ready": True,
            "status": "watch",
            "status_label": "可试车，但建议先关注警告项",
            "metrics": {
                "self_check_ok": True,
                "project_ready_to_score": True,
                "project_gate_passed": True,
                "project_mece_level": "good",
                "project_mece_health_score": 100.0,
                "ground_truth_count": 2,
                "matched_prediction_count": 2,
                "matched_score_record_count": 2,
                "current_calibrator_version": "calib_auto_offset_20260331_075649",
                "current_calibrator_degraded": False,
                "evolution_weights_usable": True,
                "drift_level": "insufficient_data",
                "latest_score_confidence_level": "high",
                "material_conflict_high_severity_count": 3,
                "system_closure_ready": False,
                "system_closure_failed_gates": ["minimum_ready_projects"],
                "orphan_records_total": 0,
            },
            "signoff": {
                "decision": "approve_with_watch",
                "decision_label": "建议试车（带警告）",
                "risk_level": "medium",
                "risk_label": "中风险",
                "summary_label": "建议试车（带警告） / 中风险 / 阻断 0 / 警告 1 / 状态 可试车，但建议先关注警告项",
                "verification_checklist": [
                    {"name": "系统自检", "passed": True, "detail": "运行时必需项全部正常"},
                ],
            },
            "warning_details": {
                "high_severity_material_conflict_count": 1,
                "high_severity_material_conflicts": [
                    {
                        "dimension_id": "13",
                        "material_type": "boq",
                        "material_type_label": "清单",
                        "conflict_kind_label": "数值不一致",
                        "label": "跨资料一致性：施组需体现清单关键约束",
                        "summary_label": "维度13 / 清单 / 数值不一致 / 跨资料一致性：施组需体现清单关键约束",
                        "detail_label": "强制项；术语 0/2；数值 0/1；来源 retrieval_chunks",
                        "entrypoint_key": "upload_shigong",
                        "entrypoint_label": "前往「4) 项目施组」上传新版施组",
                        "entrypoint_anchor": "#section-shigong",
                        "entrypoint_reason_label": "当前项目已有已评分施组；若已按冲突项修改内容，应先上传新版施组，再重新评分。",
                        "material_review_entrypoint_label": "前往「3) 项目资料」核对清单",
                        "material_review_entrypoint_anchor": "#uploadMaterialBoq",
                        "material_review_reason_label": "当前该高严重度冲突来自清单数值不一致，建议先核对对应资料来源文件和量化约束。",
                        "action_label": "优先回到施组，补齐与清单一致的量化约束、工程量或参数。",
                        "secondary_hint": "必要时再回看「3) 项目资料」中的清单来源文件是否齐全。",
                    }
                ],
                "material_conflict_recommendations": [
                    "清单一致性命中率偏低（0.0%），建议补充明确的量化约束与章节引用。"
                ],
            },
            "record_draft": {
                "status": "pending_manual_confirmation",
                "status_label": "待人工确认",
                "summary_label": "待人工确认 / 建议试车（带警告） / 中风险",
                "suggested_executed_at": "2026-03-31T10:00:00+08:00",
                "executor_name": "待填写",
                "recommended_conclusion": "建议试车（带警告）",
                "recommended_risk_label": "中风险",
                "warning_ack_required": True,
                "warning_ack_items": ["系统总封关前置条件尚未全部满足，但这不阻断当前项目试车。"],
                "confirmation_hint": "当前存在警告项，试车后请人工补记确认意见并逐条复核警告项。",
                "next_recommended_action": "继续累计真实评标样本，提升漂移判断与自学习稳定性。",
            },
            "strengths": ["系统自检通过，运行时必需项全部正常。"],
            "blockers": [],
            "warnings": ["系统总封关前置条件尚未全部满足，但这不阻断当前项目试车。"],
            "recommendations": ["继续累计真实评标样本，提升漂移判断与自学习稳定性。"],
        }

        response = client.get("/api/v1/projects/p1/trial_preflight")
        assert response.status_code == 200
        data = response.json()
        assert data["project_id"] == "p1"
        assert data["trial_run_ready"] is True
        assert data["status"] == "watch"
        assert data["metrics"]["ground_truth_count"] == 2
        assert data["metrics"]["system_closure_failed_gates"] == ["minimum_ready_projects"]
        assert data["signoff"]["decision"] == "approve_with_watch"
        assert data["signoff"]["risk_level"] == "medium"
        assert data["warning_details"]["high_severity_material_conflict_count"] == 1
        assert (
            data["warning_details"]["high_severity_material_conflicts"][0]["dimension_id"] == "13"
        )
        assert (
            data["warning_details"]["high_severity_material_conflicts"][0][
                "material_review_entrypoint_label"
            ]
            == "前往「3) 项目资料」核对清单"
        )
        assert (
            data["warning_details"]["high_severity_material_conflicts"][0][
                "entrypoint_reason_label"
            ]
            == "当前项目已有已评分施组；若已按冲突项修改内容，应先上传新版施组，再重新评分。"
        )
        assert (
            data["warning_details"]["high_severity_material_conflicts"][0][
                "material_review_reason_label"
            ]
            == "当前该高严重度冲突来自清单数值不一致，建议先核对对应资料来源文件和量化约束。"
        )
        assert data["record_draft"]["status"] == "pending_manual_confirmation"
        assert data["record_draft"]["recommended_conclusion"] == "建议试车（带警告）"

    @patch("app.main._build_system_improvement_overview")
    @patch("app.main.ensure_data_dirs")
    def test_get_system_improvement_overview_success(
        self,
        mock_ensure,
        mock_build_overview,
        client,
    ):
        mock_build_overview.return_value = {
            "generated_at": "2026-03-31T10:00:00+08:00",
            "base_url": "/api/v1",
            "overall_ready": False,
            "status": "watch",
            "status_label": "系统可继续试车，但仍需按清单持续收口",
            "summary_label": "系统可继续试车，但仍需按清单持续收口 / 阻断 0 / 警告 3 / 项目 3",
            "metrics": {
                "self_check_ok": True,
                "failed_required_count": 0,
                "failed_optional_count": 1,
                "orphan_records_total": 0,
                "affected_dataset_count": 0,
                "project_count": 3,
                "evaluated_project_count": 2,
                "ready_project_count": 1,
                "not_ready_project_count": 1,
                "candidate_project_count": 1,
                "minimum_ready_projects": 2,
                "system_closure_ready": False,
                "system_closure_failed_gates": ["minimum_ready_projects"],
                "current_display_matches_qt_pass_count": 1,
                "current_mae_rmse_not_worse_pass_count": 1,
                "current_rank_corr_not_worse_pass_count": 2,
                "next_priority_project_name": "项目A",
                "next_candidate_project_name": "项目B",
            },
            "ops_agent_quality_summary": {
                "snapshot_available": True,
                "snapshot_path": "build/ops_agents_status.json",
                "generated_at": "2026-03-31T10:00:00+08:00",
                "overall_status": "warn",
                "overall_status_label": "巡检待关注",
                "quality_status": "watch",
                "quality_status_label": "自动巡检可继续运行，但质量仍需关注",
                "summary_label": "自动巡检可继续运行，但质量仍需关注 / 通过 8 / 待收口 1 / 失败 0",
                "agent_count": 9,
                "pass_count": 8,
                "warn_count": 1,
                "fail_count": 0,
                "duration_ms": 6122,
                "recent_cycle_count": 2,
                "recent_pass_cycle_count": 1,
                "recent_warn_cycle_count": 1,
                "recent_fail_cycle_count": 0,
                "recent_non_pass_streak_count": 1,
                "recent_manual_gate_cycle_count": 1,
                "recent_post_verify_failed_cycle_count": 0,
                "latest_quality_reason_code": "manual_confirmation_required",
                "latest_quality_reason_label": "自动学习需人工确认",
                "latest_quality_reason_project_id": "p-manual",
                "latest_quality_reason_project_name": "项目手工确认A",
                "latest_quality_reason_project_detail": "待人工确认极端样本 4 条；当前暂无可关联预测样本",
                "recent_same_reason_streak_count": 1,
                "recent_quality_reason_summary_rows": [
                    {
                        "quality_reason_code": "auto_actions_executed",
                        "quality_reason_label": "已执行自动动作",
                        "count": 1,
                    },
                    {
                        "quality_reason_code": "manual_confirmation_required",
                        "quality_reason_label": "自动学习需人工确认",
                        "count": 1,
                    },
                ],
                "auto_repair_enabled": True,
                "auto_evolve_enabled": True,
                "auto_repair_attempted_count": 0,
                "auto_repair_success_count": 0,
                "auto_fixed_count": 0,
                "total_auto_repair_attempted_count": 1,
                "total_auto_repair_success_count": 1,
                "repair_success_rate": 1.0,
                "auto_evolve_attempted_count": 0,
                "auto_evolve_success_count": 0,
                "total_auto_evolve_attempted_count": 1,
                "total_auto_evolve_success_count": 1,
                "evolve_success_rate": 1.0,
                "manual_confirmation_required_count": 1,
                "manual_confirmation_rows": [
                    {
                        "project_id": "p-manual",
                        "project_name": "项目手工确认A",
                        "pending_extreme_ground_truth_count": 4,
                        "matched_submission_count": 0,
                        "entrypoint_key": "ground_truth",
                        "entrypoint_label": "前往「5) 自我学习与进化」录入真实评标",
                        "action_label": "录入真实评标并人工确认极端样本",
                        "manual_override_hint": "confirm_extreme_sample=1",
                        "current_calibrator_deployment_mode": "prior_fallback",
                        "detail": "待人工确认极端样本 4 条；当前暂无可关联预测样本",
                        "recommendation": "存在 4 条极端偏差样本，自动调权/自动校准已被暂停；人工确认后再执行学习进化或一键闭环。",
                    }
                ],
                "post_verify_failed_count": 0,
                "bootstrap_monitoring_count": 1,
                "llm_account_low_quality_pool_count": 2,
                "recent_audit_rows": [
                    {
                        "generated_at": "2026-03-30T10:00:00+08:00",
                        "overall_status": "pass",
                        "auto_repair_attempted_count": 1,
                        "auto_repair_success_count": 1,
                        "auto_evolve_attempted_count": 1,
                        "auto_evolve_success_count": 1,
                        "manual_confirmation_required_count": 0,
                        "post_verify_failed_count": 0,
                        "quality_reason_code": "auto_actions_executed",
                        "quality_reason_label": "已执行自动动作",
                        "quality_reason_detail": "自动修复 1/1；自动学习 1/1",
                        "quality_audit_label": "已执行自动动作",
                        "top_recommendation": "",
                    },
                    {
                        "generated_at": "2026-03-31T10:00:00+08:00",
                        "overall_status": "warn",
                        "auto_repair_attempted_count": 0,
                        "auto_repair_success_count": 0,
                        "auto_evolve_attempted_count": 0,
                        "auto_evolve_success_count": 0,
                        "manual_confirmation_required_count": 1,
                        "post_verify_failed_count": 0,
                        "quality_reason_code": "manual_confirmation_required",
                        "quality_reason_label": "自动学习需人工确认",
                        "quality_reason_detail": "人工确认需求 1 项",
                        "quality_reason_project_id": "p-manual",
                        "quality_reason_project_name": "项目手工确认A",
                        "quality_reason_project_detail": "待人工确认极端样本 4 条；当前暂无可关联预测样本",
                        "quality_audit_label": "自动学习需人工确认",
                        "top_recommendation": "有 1 个项目存在极端偏差样本，需人工确认后才能继续自动学习。",
                    },
                ],
                "agent_rows": [
                    {
                        "name": "learning_calibration",
                        "label": "学习校准智能体",
                        "status": "warn",
                        "recommendation": "有 1 个项目存在极端偏差样本，需人工确认后才能继续自动学习。",
                    }
                ],
                "strengths": ["自动修复开关已开启。"],
                "blockers": [],
                "warnings": ["当前仍有 1 个项目需人工确认后，自动学习链路才能继续推进。"],
                "recommendations": ["有 1 个项目存在极端偏差样本，需人工确认后才能继续自动学习。"],
            },
            "focus_workstreams": [
                {
                    "id": "runtime_stability",
                    "title": "运行稳定性",
                    "status": "warn",
                    "summary": "系统自检已通过，但仍有 1 项降级告警。",
                    "detail": "核心失败 0 项；降级告警 1 项。",
                    "entrypoint_key": "system_self_check",
                    "entrypoint_label": "前往「5) 自我学习与进化」执行“系统自检”",
                    "action_label": "执行系统自检",
                },
                {
                    "id": "system_closure",
                    "title": "系统总封关",
                    "status": "warn",
                    "summary": "暂不可封系统总关",
                    "detail": "先处理当前分与青天未完全对齐的问题。",
                    "project_id": "p1",
                    "project_name": "项目A",
                    "entrypoint_key": "evaluation_summary",
                    "entrypoint_label": "前往「5) 自我学习与进化」执行“跨项目汇总评估”",
                    "action_label": "查看跨项目汇总",
                },
            ],
            "focus_workstream_status_summaries": [
                {
                    "workstream_status": "ok",
                    "workstream_status_label": "正常",
                    "count": 0,
                    "status": "empty",
                    "summary": "当前没有处于正常状态的工作流，说明系统级主工作流仍在持续收口。",
                    "empty_reason_label": "当前没有处于正常状态的工作流，说明系统级主工作流仍在持续收口。",
                    "priority_workstream_id": "",
                    "priority_workstream_title": "",
                    "priority_project_id": "",
                    "priority_project_name": "",
                    "priority_entrypoint_key": "",
                    "priority_entrypoint_label": "",
                    "priority_action_label": "",
                },
                {
                    "workstream_status": "warn",
                    "workstream_status_label": "待收口",
                    "count": 2,
                    "status": "active",
                    "summary": "当前已有 2 条工作流仍待收口，建议继续按建议入口推进。",
                    "empty_reason_label": "",
                    "priority_workstream_id": "system_closure",
                    "priority_workstream_title": "系统总封关",
                    "priority_project_id": "p1",
                    "priority_project_name": "项目A",
                    "priority_entrypoint_key": "evaluation_summary",
                    "priority_entrypoint_label": "前往「5) 自我学习与进化」执行“跨项目汇总评估”",
                    "priority_action_label": "查看跨项目汇总",
                },
                {
                    "workstream_status": "blocked",
                    "workstream_status_label": "阻断",
                    "count": 0,
                    "status": "empty",
                    "summary": "当前没有阻断工作流，说明现有系统级缺口暂不阻断继续试车。",
                    "empty_reason_label": "当前没有阻断工作流，说明现有系统级缺口暂不阻断继续试车。",
                    "priority_workstream_id": "",
                    "priority_workstream_title": "",
                    "priority_project_id": "",
                    "priority_project_name": "",
                    "priority_entrypoint_key": "",
                    "priority_entrypoint_label": "",
                    "priority_action_label": "",
                },
            ],
            "closure_gate_details": [
                {
                    "id": "minimum_ready_projects",
                    "label": "达到第一阶段 ready 的项目数达到 2 个",
                    "summary": "当前仅有 1 个项目达到第一阶段 ready，至少需要 2 个。",
                    "detail": "先推进未 ready 项目进入第一阶段 ready。",
                    "project_id": "p1",
                    "project_name": "项目A",
                    "entrypoint_key": "ground_truth",
                    "entrypoint_label": "前往「5) 自我学习与进化」录入真实评标",
                    "action_label": "录入真实评标",
                }
            ],
            "project_gap_details": [
                {
                    "id": "phase1_ready_gap:p1",
                    "kind": "phase1_ready_gap",
                    "kind_label": "第一阶段 ready 缺口",
                    "project_id": "p1",
                    "project_name": "项目A",
                    "summary": "当前项目仍未达到第一阶段 ready（未通过门 2 个）。",
                    "detail": "未通过门：current_display_matches_qt、drift_low；当前项目尚未满足第一阶段封关条件，优先收口未通过门。",
                    "entrypoint_key": "auto_run_reflection",
                    "entrypoint_label": "前往「5) 自我学习与进化」执行“一键闭环执行”",
                    "action_label": "执行一键闭环",
                }
            ],
            "project_gate_gap_details": [
                {
                    "id": "p1:current_display_matches_qt",
                    "kind": "project_failed_gate",
                    "kind_label": "项目内未通过门",
                    "project_id": "p1",
                    "project_name": "项目A",
                    "gate_id": "current_display_matches_qt",
                    "gate_label": "当前分已与青天结果对齐",
                    "summary": "当前项目仍未通过：当前分已与青天结果对齐。",
                    "detail": "门内详情：false 当前项目尚未满足第一阶段封关条件，优先收口未通过门。 先重跑 V2 一键闭环，收口当前分、校准器和漂移状态。",
                    "entrypoint_key": "auto_run_reflection",
                    "entrypoint_label": "前往「5) 自我学习与进化」执行“一键闭环执行”",
                    "action_label": "执行一键闭环",
                }
            ],
            "project_action_gap_details": [
                {
                    "id": "p1:auto_run_reflection",
                    "kind": "project_action_gap",
                    "kind_label": "按动作归并的项目收口",
                    "project_id": "p1",
                    "project_name": "项目A",
                    "entrypoint_key": "auto_run_reflection",
                    "entrypoint_label": "前往「5) 自我学习与进化」执行“一键闭环执行”",
                    "action_label": "执行一键闭环",
                    "gate_count": 2,
                    "summary": "当前项目有 2 个未通过门建议通过该动作收口。",
                    "detail": "关联未通过门：当前分已与青天结果对齐、近30天漂移等级为 low 先重跑 V2 一键闭环，收口当前分、校准器和漂移状态。",
                }
            ],
            "global_action_gap_details": [
                {
                    "id": "global_action:auto_run_reflection",
                    "kind": "global_action_gap",
                    "kind_label": "系统级优先动作",
                    "project_id": "p1",
                    "project_name": "项目A",
                    "entrypoint_key": "auto_run_reflection",
                    "entrypoint_label": "前往「5) 自我学习与进化」执行“一键闭环执行”",
                    "action_label": "执行一键闭环",
                    "priority_reason_label": "该项目是当前系统总封关优先收口项目。",
                    "priority_sort_label": "系统总封关优先项目。",
                    "execution_mode": "auto",
                    "execution_mode_label": "自动闭环优先",
                    "group_reason_label": "该动作支持直接执行自动闭环收口。",
                    "project_count": 1,
                    "gate_count_total": 2,
                    "summary": "当前有 1 个项目、2 个未通过门建议通过该动作收口。",
                    "detail": "涉及项目：项目A 建议优先从“项目A”开始。",
                }
            ],
            "global_action_group_summaries": [
                {
                    "action_group": "auto",
                    "action_group_label": "可自动收口动作",
                    "count": 1,
                    "status": "active",
                    "summary": "当前已有 1 条可自动收口动作，可优先通过自动闭环压缩系统缺口。",
                    "empty_reason_label": "",
                },
                {
                    "action_group": "readonly",
                    "action_group_label": "只读诊断动作",
                    "count": 1,
                    "status": "active",
                    "summary": "当前已有 1 条只读诊断动作，适合先诊断再决定是否进入人工或自动收口。",
                    "empty_reason_label": "",
                },
                {
                    "action_group": "manual",
                    "action_group_label": "必须人工处理动作",
                    "count": 1,
                    "status": "active",
                    "summary": "当前已有 1 条必须人工处理动作，需人工录入、复核或治理后继续推进。",
                    "empty_reason_label": "",
                },
            ],
            "global_auto_action_gap_details": [
                {
                    "id": "global_action:auto_run_reflection",
                    "kind": "global_action_gap",
                    "kind_label": "系统级优先动作",
                    "project_id": "p1",
                    "project_name": "项目A",
                    "entrypoint_key": "auto_run_reflection",
                    "entrypoint_label": "前往「5) 自我学习与进化」执行“一键闭环执行”",
                    "action_label": "执行一键闭环",
                    "priority_reason_label": "该项目是当前系统总封关优先收口项目。",
                    "priority_sort_label": "系统总封关优先项目。",
                    "execution_mode": "auto",
                    "execution_mode_label": "自动闭环优先",
                    "action_group": "auto",
                    "action_group_label": "可自动收口动作",
                    "group_reason_label": "该动作支持直接执行自动闭环收口。",
                    "project_count": 1,
                    "gate_count_total": 2,
                    "summary": "当前有 1 个项目、2 个未通过门建议通过该动作收口。",
                    "detail": "涉及项目：项目A 建议优先从“项目A”开始。",
                }
            ],
            "global_readonly_action_gap_details": [
                {
                    "id": "global_action:evaluation_summary",
                    "kind": "global_action_gap",
                    "kind_label": "系统级优先动作",
                    "project_id": "p1",
                    "project_name": "项目A",
                    "entrypoint_key": "evaluation_summary",
                    "entrypoint_label": "前往「5) 自我学习与进化」执行“跨项目汇总评估”",
                    "action_label": "查看跨项目汇总",
                    "priority_reason_label": "该项目是当前系统总封关优先收口项目。",
                    "priority_sort_label": "系统总封关优先项目。",
                    "execution_mode": "readonly",
                    "execution_mode_label": "只读诊断",
                    "action_group": "readonly",
                    "action_group_label": "只读诊断动作",
                    "group_reason_label": "该动作仅用于只读诊断，不直接改写项目状态。",
                    "project_count": 1,
                    "gate_count_total": 0,
                    "summary": "当前有 1 条系统级诊断工作流建议先执行该动作。",
                    "detail": "关联工作流：跨项目学习对齐 先查看跨项目汇总，再决定是否继续闭环或治理。 建议优先从“项目A”开始。",
                }
            ],
            "global_manual_action_gap_details": [
                {
                    "id": "global_action:ground_truth",
                    "kind": "global_action_gap",
                    "kind_label": "系统级优先动作",
                    "project_id": "p1",
                    "project_name": "项目A",
                    "entrypoint_key": "ground_truth",
                    "entrypoint_label": "前往「5) 自我学习与进化」录入真实评标",
                    "action_label": "录入真实评标",
                    "priority_reason_label": "该项目是当前系统总封关优先收口项目。",
                    "priority_sort_label": "系统总封关优先项目。",
                    "execution_mode": "manual",
                    "execution_mode_label": "人工处理优先",
                    "action_group": "manual",
                    "action_group_label": "必须人工处理动作",
                    "group_reason_label": "该动作需要人工录入、判断或复核后才能继续收口。",
                    "project_count": 1,
                    "gate_count_total": 1,
                    "summary": "当前有 1 个项目、1 个未通过门建议通过该动作收口。",
                    "detail": "涉及项目：项目A 建议优先从“项目A”开始。",
                }
            ],
            "strengths": ["系统自检通过，核心运行项正常。"],
            "blockers": [],
            "warnings": ["系统总封关前置条件尚未全部满足，仍需继续收口跨项目闭环。"],
            "recommendations": ["优先收口项目“项目A”：先处理当前分与青天未完全对齐的问题。"],
        }

        response = client.get("/api/v1/system/improvement_overview")
        assert response.status_code == 200
        data = response.json()
        assert data["overall_ready"] is False
        assert data["status"] == "watch"
        assert data["metrics"]["project_count"] == 3
        assert data["ops_agent_quality_summary"]["overall_status"] == "warn"
        assert data["ops_agent_quality_summary"]["manual_confirmation_required_count"] == 1
        assert data["metrics"]["system_closure_failed_gates"] == ["minimum_ready_projects"]
        assert data["focus_workstreams"][0]["title"] == "运行稳定性"
        assert data["focus_workstream_status_summaries"][1]["workstream_status"] == "warn"
        assert data["focus_workstream_status_summaries"][1]["count"] == 2
        assert (
            data["focus_workstream_status_summaries"][1]["priority_workstream_title"]
            == "系统总封关"
        )
        assert (
            data["focus_workstream_status_summaries"][1]["priority_entrypoint_key"]
            == "evaluation_summary"
        )
        assert data["focus_workstreams"][1]["project_id"] == "p1"
        assert (
            data["focus_workstreams"][1]["entrypoint_label"]
            == "前往「5) 自我学习与进化」执行“跨项目汇总评估”"
        )
        assert data["closure_gate_details"][0]["id"] == "minimum_ready_projects"
        assert data["project_gap_details"][0]["kind"] == "phase1_ready_gap"
        assert data["project_gap_details"][0]["entrypoint_key"] == "auto_run_reflection"
        assert data["project_gate_gap_details"][0]["gate_id"] == "current_display_matches_qt"
        assert data["project_gate_gap_details"][0]["entrypoint_key"] == "auto_run_reflection"
        assert data["project_action_gap_details"][0]["entrypoint_key"] == "auto_run_reflection"
        assert data["project_action_gap_details"][0]["gate_count"] == 2
        assert data["global_action_gap_details"][0]["entrypoint_key"] == "auto_run_reflection"
        assert data["global_action_group_summaries"][0]["action_group"] == "auto"
        assert data["global_action_group_summaries"][0]["status"] == "active"
        assert data["global_action_gap_details"][0]["execution_mode"] == "auto"
        assert (
            data["global_action_gap_details"][0]["priority_reason_label"]
            == "该项目是当前系统总封关优先收口项目。"
        )
        assert data["global_action_gap_details"][0]["priority_sort_label"] == "系统总封关优先项目。"
        assert data["global_action_gap_details"][0]["gate_count_total"] == 2
        assert data["global_auto_action_gap_details"][0]["action_group"] == "auto"
        assert data["global_auto_action_gap_details"][0]["action_group_label"] == "可自动收口动作"
        assert (
            data["global_auto_action_gap_details"][0]["group_reason_label"]
            == "该动作支持直接执行自动闭环收口。"
        )
        assert data["global_readonly_action_gap_details"][0]["action_group"] == "readonly"
        assert data["global_readonly_action_gap_details"][0]["action_group_label"] == "只读诊断动作"
        assert (
            data["global_readonly_action_gap_details"][0]["group_reason_label"]
            == "该动作仅用于只读诊断，不直接改写项目状态。"
        )
        assert data["global_manual_action_gap_details"][0]["action_group"] == "manual"
        assert (
            data["global_manual_action_gap_details"][0]["action_group_label"] == "必须人工处理动作"
        )
        assert (
            data["global_manual_action_gap_details"][0]["group_reason_label"]
            == "该动作需要人工录入、判断或复核后才能继续收口。"
        )

    @patch("app.main.render_trial_preflight_markdown")
    @patch("app.main._build_project_trial_preflight")
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    def test_get_project_trial_preflight_markdown_and_download(
        self,
        mock_ensure,
        mock_load_projects,
        mock_build_preflight,
        mock_render_markdown,
        client,
    ):
        mock_load_projects.return_value = [{"id": "p1", "name": "项目1", "meta": {}}]
        mock_build_preflight.return_value = {
            "generated_at": "2026-03-31T10:00:00+08:00",
            "base_url": "/api/v1",
            "project_id": "p1",
            "project_name": "项目1",
            "trial_run_ready": True,
            "status": "watch",
            "status_label": "可试车，但建议先关注警告项",
            "metrics": {"ground_truth_count": 2},
            "signoff": {
                "decision": "approve_with_watch",
                "decision_label": "建议试车（带警告）",
                "risk_level": "medium",
                "risk_label": "中风险",
                "summary_label": "建议试车（带警告） / 中风险 / 阻断 0 / 警告 0 / 状态 可试车，但建议先关注警告项",
                "verification_checklist": [],
            },
            "warning_details": {
                "high_severity_material_conflict_count": 0,
                "high_severity_material_conflicts": [],
                "material_conflict_recommendations": [],
            },
            "record_draft": {
                "status": "pending_manual_confirmation",
                "status_label": "待人工确认",
                "summary_label": "待人工确认 / 建议试车（带警告） / 中风险",
                "suggested_executed_at": "2026-03-31T10:00:00+08:00",
                "executor_name": "待填写",
                "recommended_conclusion": "建议试车（带警告）",
                "recommended_risk_label": "中风险",
                "warning_ack_required": False,
                "warning_ack_items": [],
                "confirmation_hint": "试车完成后请补记执行人与结论确认。",
                "next_recommended_action": "",
            },
            "strengths": ["系统自检通过，运行时必需项全部正常。"],
            "blockers": [],
            "warnings": [],
            "recommendations": [],
        }
        mock_render_markdown.return_value = "# 项目试车前综合体检报告\n\n- 项目ID：p1\n"

        md_resp = client.get("/api/v1/projects/p1/trial_preflight/markdown")
        assert md_resp.status_code == 200
        md_data = md_resp.json()
        assert md_data["project_id"] == "p1"
        assert md_data["generated_at"] == "2026-03-31T10:00:00+08:00"
        assert md_data["markdown"].startswith("# 项目试车前综合体检报告")

        file_resp = client.get("/api/v1/projects/p1/trial_preflight.md")
        assert file_resp.status_code == 200
        assert file_resp.text.startswith("# 项目试车前综合体检报告")
        assert "text/markdown" in file_resp.headers.get("content-type", "")
        disposition = file_resp.headers.get("content-disposition", "")
        assert "attachment; filename=" in disposition
        assert "trial_preflight_p1.md" in disposition

    @patch("app.main.render_trial_preflight_docx")
    @patch("app.main._build_project_trial_preflight")
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    def test_download_project_trial_preflight_docx(
        self,
        mock_ensure,
        mock_load_projects,
        mock_build_preflight,
        mock_render_docx,
        client,
    ):
        mock_load_projects.return_value = [{"id": "p1", "name": "项目1", "meta": {}}]
        mock_build_preflight.return_value = {
            "generated_at": "2026-03-31T10:00:00+08:00",
            "base_url": "/api/v1",
            "project_id": "p1",
            "project_name": "项目1",
            "trial_run_ready": True,
            "status": "watch",
            "status_label": "可试车，但建议先关注警告项",
            "metrics": {"ground_truth_count": 2},
            "signoff": {
                "decision": "approve_with_watch",
                "decision_label": "建议试车（带警告）",
                "risk_level": "medium",
                "risk_label": "中风险",
                "summary_label": "建议试车（带警告） / 中风险 / 阻断 0 / 警告 0 / 状态 可试车，但建议先关注警告项",
                "verification_checklist": [],
            },
            "warning_details": {
                "high_severity_material_conflict_count": 0,
                "high_severity_material_conflicts": [],
                "material_conflict_recommendations": [],
            },
            "record_draft": {
                "status": "pending_manual_confirmation",
                "status_label": "待人工确认",
                "summary_label": "待人工确认 / 建议试车（带警告） / 中风险",
                "suggested_executed_at": "2026-03-31T10:00:00+08:00",
                "executor_name": "待填写",
                "recommended_conclusion": "建议试车（带警告）",
                "recommended_risk_label": "中风险",
                "warning_ack_required": False,
                "warning_ack_items": [],
                "confirmation_hint": "试车完成后请补记执行人与结论确认。",
                "next_recommended_action": "",
            },
            "strengths": ["系统自检通过，运行时必需项全部正常。"],
            "blockers": [],
            "warnings": [],
            "recommendations": [],
        }
        mock_render_docx.return_value = b"PK\x03\x04trial-preflight-docx"

        response = client.get("/api/v1/projects/p1/trial_preflight.docx")

        assert response.status_code == 200
        assert response.content.startswith(b"PK\x03\x04")
        assert (
            response.headers.get("content-type", "")
            == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        )
        disposition = response.headers.get("content-disposition", "")
        assert "attachment; filename=" in disposition
        assert "trial_preflight_p1.docx" in disposition

    @patch("app.main.load_calibration_models")
    @patch("app.main._resolve_project_scoring_context")
    @patch("app.main.load_evolution_reports")
    @patch("app.main.load_ground_truth")
    @patch("app.main.load_submissions")
    def test_build_evolution_health_report_computes_error_windows(
        self,
        mock_load_submissions,
        mock_load_ground_truth,
        mock_load_evo_reports,
        mock_resolve_scoring_context,
        mock_load_calibration_models,
    ):
        from app.main import _build_evolution_health_report

        mock_load_calibration_models.return_value = []
        mock_load_submissions.return_value = [
            {
                "id": "s1",
                "project_id": "p1",
                "report": {"pred_total_score": 70.0, "score_scale_max": 100},
            }
        ]
        mock_load_ground_truth.return_value = [
            {
                "id": "gt1",
                "project_id": "p1",
                "source_submission_id": "s1",
                "judge_scores": [75, 76, 77, 78, 79],
                "final_score": 78.0,
                "score_scale_max": 100,
                "created_at": "2026-02-26T00:00:00+00:00",
            }
        ]
        mock_load_evo_reports.return_value = {
            "p1": {
                "updated_at": "2026-02-26T08:00:00+00:00",
                "scoring_evolution": {"dimension_multipliers": {"01": 1.1}},
            }
        }
        mock_resolve_scoring_context.return_value = (
            {"01": 1.1, "02": 0.9},
            None,
            {"id": "p1", "meta": {"score_scale_max": 100}},
        )

        payload = _build_evolution_health_report(
            "p1",
            {"id": "p1", "meta": {"score_scale_max": 100}},
        )
        assert payload["project_id"] == "p1"
        assert payload["summary"]["ground_truth_count"] == 1
        assert payload["summary"]["matched_prediction_count"] == 1
        assert payload["summary"]["matched_score_record_count"] == 1
        assert payload["windows"]["all"]["mae"] == pytest.approx(8.0, abs=1e-4)
        assert payload["windows"]["all"]["count"] == 1
        assert payload["drift"]["level"] in {"insufficient_data", "watch", "low", "medium", "high"}

    @patch("app.main.load_calibration_models")
    @patch("app.main._resolve_project_scoring_context")
    @patch("app.main.load_evolution_reports")
    @patch("app.main.load_ground_truth")
    @patch("app.main.load_submissions")
    def test_build_evolution_health_report_treats_perfect_recent_matches_as_low_drift(
        self,
        mock_load_submissions,
        mock_load_ground_truth,
        mock_load_evo_reports,
        mock_resolve_scoring_context,
        mock_load_calibration_models,
    ):
        from app.main import _build_evolution_health_report

        mock_load_calibration_models.return_value = []
        mock_load_submissions.return_value = [
            {
                "id": "s1",
                "project_id": "p1",
                "report": {"pred_total_score": 82.2, "score_scale_max": 100},
            },
            {
                "id": "s2",
                "project_id": "p1",
                "report": {"pred_total_score": 80.02, "score_scale_max": 100},
            },
            {
                "id": "s3",
                "project_id": "p1",
                "report": {"pred_total_score": 80.74, "score_scale_max": 100},
            },
        ]
        mock_load_ground_truth.return_value = [
            {
                "id": "gt1",
                "project_id": "p1",
                "source_submission_id": "s1",
                "judge_scores": [82.16, 82.19, 82.16, 82.18, 82.22, 82.19, 82.28],
                "final_score": 82.2,
                "score_scale_max": 100,
                "created_at": "2026-03-20T00:00:00+00:00",
            },
            {
                "id": "gt2",
                "project_id": "p1",
                "source_submission_id": "s2",
                "judge_scores": [79.99, 80.02, 79.97, 80.01, 80.02, 80.00, 80.10],
                "final_score": 80.02,
                "score_scale_max": 100,
                "created_at": "2026-03-21T00:00:00+00:00",
            },
            {
                "id": "gt3",
                "project_id": "p1",
                "source_submission_id": "s3",
                "judge_scores": [80.73, 80.73, 80.72, 80.72, 80.76, 80.73, 80.81],
                "final_score": 80.74,
                "score_scale_max": 100,
                "created_at": "2026-03-22T00:00:00+00:00",
            },
        ]
        mock_load_evo_reports.return_value = {
            "p1": {
                "updated_at": "2026-03-22T08:00:00+00:00",
                "scoring_evolution": {"dimension_multipliers": {"01": 1.1}},
            }
        }
        mock_resolve_scoring_context.return_value = (
            {"01": 1.1, "02": 0.9},
            None,
            {"id": "p1", "meta": {"score_scale_max": 100}},
        )

        payload = _build_evolution_health_report(
            "p1",
            {"id": "p1", "meta": {"score_scale_max": 100}},
        )

        assert payload["summary"]["matched_prediction_count"] == 3
        assert payload["summary"]["matched_score_record_count"] == 3
        assert payload["windows"]["recent_30d"]["mae"] == pytest.approx(0.0, abs=1e-6)
        assert payload["windows"]["prev_30_90d"]["count"] == 0
        assert payload["drift"]["level"] == "low"

    @patch("app.main.load_calibration_models")
    @patch("app.main._resolve_project_scoring_context")
    @patch("app.main.load_evolution_reports")
    @patch("app.main.load_ground_truth")
    @patch("app.main.load_submissions")
    def test_build_evolution_health_report_matches_ground_truth_by_filename(
        self,
        mock_load_submissions,
        mock_load_ground_truth,
        mock_load_evo_reports,
        mock_resolve_scoring_context,
        mock_load_calibration_models,
    ):
        from app.main import _build_evolution_health_report

        mock_load_calibration_models.return_value = []
        mock_load_submissions.return_value = [
            {
                "id": "s1",
                "project_id": "p1",
                "filename": "安徽中江建投-道路工程(1).pdf",
                "text": "重新上传后的施组文本",
                "report": {"pred_total_score": 77.9, "score_scale_max": 100},
            }
        ]
        mock_load_ground_truth.return_value = [
            {
                "id": "gt1",
                "project_id": "p1",
                "source_submission_filename": "/tmp/安徽中江建投-道路工程(1).pdf",
                "shigong_text": "历史录入时保存的旧文本",
                "judge_scores": [77.85, 77.97, 77.86, 77.86, 77.91, 77.89, 77.94],
                "final_score": 77.9,
                "score_scale_max": 100,
                "created_at": "2026-03-22T00:00:00+00:00",
            }
        ]
        mock_load_evo_reports.return_value = {"p1": {}}
        mock_resolve_scoring_context.return_value = (
            {"01": 1.0},
            None,
            {"id": "p1", "meta": {"score_scale_max": 100}},
        )

        payload = _build_evolution_health_report(
            "p1",
            {"id": "p1", "meta": {"score_scale_max": 100}},
        )

        assert payload["summary"]["ground_truth_count"] == 1
        assert payload["summary"]["matched_prediction_count"] == 1
        assert payload["summary"]["matched_score_record_count"] == 1
        assert payload["summary"]["unmatched_ground_truth_count"] == 0
        assert payload["windows"]["all"]["mae"] == pytest.approx(0.0, abs=1e-6)

    @patch("app.main.load_calibration_models")
    @patch("app.main.load_projects")
    @patch("app.main._resolve_project_scoring_context")
    @patch("app.main.load_evolution_reports")
    @patch("app.main.load_ground_truth")
    @patch("app.main.load_submissions")
    def test_build_evolution_health_report_exposes_enhancement_review_state(
        self,
        mock_load_submissions,
        mock_load_ground_truth,
        mock_load_evo_reports,
        mock_resolve_scoring_context,
        mock_load_projects,
        mock_load_calibration_models,
    ):
        from app.main import _build_evolution_health_report

        mock_load_calibration_models.return_value = []
        mock_load_projects.return_value = [{"id": "p1", "meta": {"score_scale_max": 100}}]
        mock_load_submissions.return_value = []
        mock_load_ground_truth.return_value = []
        mock_load_evo_reports.return_value = {
            "p1": {
                "updated_at": "2026-03-29T00:00:00+00:00",
                "enhancement_applied": False,
                "enhancement_governed": True,
                "enhancement_review_status": "diverged",
                "enhancement_review_provider": "gemini",
                "enhancement_review_similarity": 0.12,
            }
        }
        mock_resolve_scoring_context.return_value = (
            {"01": 1.0},
            None,
            {"id": "p1", "meta": {"score_scale_max": 100}},
        )

        payload = _build_evolution_health_report(
            "p1", {"id": "p1", "meta": {"score_scale_max": 100}}
        )

        assert payload["summary"]["enhancement_applied"] is False
        assert payload["summary"]["enhancement_governed"] is True
        assert payload["summary"]["enhancement_review_status"] == "diverged"
        assert payload["summary"]["enhancement_review_provider"] == "gemini"
        assert payload["summary"]["enhancement_review_similarity"] == pytest.approx(0.12, abs=1e-6)
        assert any("双模型复核分歧较大" in str(item) for item in payload["recommendations"])

    @patch("app.main.load_calibration_models")
    @patch("app.main._resolve_project_scoring_context")
    @patch("app.main.load_evolution_reports")
    @patch("app.main.load_ground_truth")
    @patch("app.main.load_submissions")
    def test_build_evolution_health_report_excludes_learning_quality_blocked_samples(
        self,
        mock_load_submissions,
        mock_load_ground_truth,
        mock_load_evo_reports,
        mock_resolve_scoring_context,
        mock_load_calibration_models,
    ):
        from app.main import _build_evolution_health_report

        mock_load_calibration_models.return_value = []
        mock_load_submissions.return_value = [
            {
                "id": "s1",
                "project_id": "p1",
                "report": {"pred_total_score": 70.0, "score_scale_max": 100},
            }
        ]
        mock_load_ground_truth.return_value = [
            {
                "id": "gt-good",
                "project_id": "p1",
                "source_submission_id": "s1",
                "judge_scores": [75, 76, 77, 78, 79],
                "final_score": 78.0,
                "score_scale_max": 100,
                "created_at": "2026-02-26T00:00:00+00:00",
            },
            {
                "id": "gt-low-quality",
                "project_id": "p1",
                "source_submission_id": "s1",
                "judge_scores": [80, 80, 80, 80, 80],
                "final_score": 80.0,
                "score_scale_max": 100,
                "created_at": "2026-02-27T00:00:00+00:00",
                "learning_quality_gate": {
                    "blocked": True,
                    "reasons": ["missing_evidence_hits"],
                },
            },
            {
                "id": "gt-guardrail",
                "project_id": "p1",
                "source_submission_id": "s1",
                "judge_scores": [82, 82, 82, 82, 82],
                "final_score": 82.0,
                "score_scale_max": 100,
                "created_at": "2026-02-28T00:00:00+00:00",
                "feedback_guardrail": {"blocked": True, "threshold_blocked": True},
            },
        ]
        mock_load_evo_reports.return_value = {"p1": {}}
        mock_resolve_scoring_context.return_value = (
            {"01": 1.0},
            None,
            {"id": "p1", "meta": {"score_scale_max": 100}},
        )

        payload = _build_evolution_health_report(
            "p1",
            {"id": "p1", "meta": {"score_scale_max": 100}},
        )

        summary = payload["summary"]
        assert summary["ground_truth_count"] == 3
        assert summary["eligible_learning_ground_truth_count"] == 1
        assert summary["matched_prediction_count"] == 1
        assert summary["matched_score_record_count"] == 1
        assert summary["guardrail_blocked_count"] == 1
        assert summary["learning_quality_blocked_count"] == 1
        assert summary["evolution_weight_min_samples"] == 1
        assert not any(
            "可纳入自动学习的真实评标样本不足" in str(item) for item in payload["recommendations"]
        )
        assert any("被排除在自动学习之外" in str(item) for item in payload["recommendations"])

    @patch("app.main.load_calibration_models")
    @patch("app.main.load_projects")
    @patch("app.main._resolve_project_scoring_context")
    @patch("app.main.load_evolution_reports")
    @patch("app.main.load_ground_truth")
    @patch("app.main.load_submissions")
    def test_build_evolution_health_report_distinguishes_stored_vs_active_weights(
        self,
        mock_load_submissions,
        mock_load_ground_truth,
        mock_load_evo_reports,
        mock_resolve_scoring_context,
        mock_load_projects,
        mock_load_calibration_models,
    ):
        from app.main import _build_evolution_health_report

        mock_load_calibration_models.return_value = []
        mock_load_projects.return_value = [
            {
                "id": "p1",
                "meta": {
                    "score_scale_max": 100,
                    "learning_min_samples": 3,
                    "evolution_weight_min_samples": 3,
                    "evolution_weight_max_age_days": 90,
                },
            }
        ]
        mock_load_submissions.return_value = []
        mock_load_ground_truth.return_value = []
        mock_load_evo_reports.return_value = {
            "p1": {
                "sample_count": 1,
                "updated_at": "2026-03-01T00:00:00+00:00",
                "scoring_evolution": {"dimension_multipliers": {"01": 1.2}},
            }
        }
        mock_resolve_scoring_context.return_value = (
            {"01": 1.0},
            {"id": "ep1", "weights_norm": {"01": 1.0}},
            {"id": "p1", "meta": {"score_scale_max": 100}},
        )

        payload = _build_evolution_health_report(
            "p1",
            {
                "id": "p1",
                "meta": {
                    "score_scale_max": 100,
                    "learning_min_samples": 3,
                    "evolution_weight_min_samples": 3,
                },
            },
        )
        summary = payload["summary"]
        assert summary["stored_evolved_multipliers"] is True
        assert summary["has_evolved_multipliers"] is False
        assert summary["current_weights_source"] == "expert_profile"
        assert summary["evolution_weights_inactive_reason"] == "sample_count_below_min"
        assert any("样本量未达到生效阈值" in str(x) for x in payload["recommendations"])

    @patch("app.main.load_calibration_models")
    @patch("app.main._resolve_project_scoring_context")
    @patch("app.main.load_evolution_reports")
    @patch("app.main.load_ground_truth")
    @patch("app.main.load_submissions")
    def test_build_evolution_health_report_flags_degraded_current_project_calibrator(
        self,
        mock_load_submissions,
        mock_load_ground_truth,
        mock_load_evo_reports,
        mock_resolve_scoring_context,
        mock_load_calibration_models,
    ):
        from app.main import _build_evolution_health_report

        mock_load_calibration_models.return_value = [
            {
                "calibrator_version": "calib_auto_existing",
                "train_filter": {"project_id": "p1"},
                "deployed": True,
                "calibrator_summary": {
                    "deployment_mode": "cv_validated",
                    "cv_metrics": {"mae": 1.8},
                },
                "metrics": {"cv_mae": 1.8},
            },
            {
                "calibrator_version": "calib_auto_prev",
                "train_filter": {"project_id": "p1"},
                "deployed": False,
                "calibrator_summary": {
                    "deployment_mode": "cv_validated",
                    "cv_metrics": {"mae": 1.1},
                },
                "metrics": {"cv_mae": 1.1},
            },
        ]
        mock_load_submissions.return_value = [
            {
                "id": "s1",
                "project_id": "p1",
                "report": {
                    "total_score": 85.0,
                    "pred_total_score": 85.0,
                    "rule_total_score": 80.0,
                    "score_scale_max": 100,
                },
            },
            {
                "id": "s2",
                "project_id": "p1",
                "report": {
                    "total_score": 84.6,
                    "pred_total_score": 84.6,
                    "rule_total_score": 80.2,
                    "score_scale_max": 100,
                },
            },
            {
                "id": "s3",
                "project_id": "p1",
                "report": {
                    "total_score": 84.8,
                    "pred_total_score": 84.8,
                    "rule_total_score": 79.9,
                    "score_scale_max": 100,
                },
            },
        ]
        mock_load_ground_truth.return_value = [
            {
                "id": "gt1",
                "project_id": "p1",
                "source_submission_id": "s1",
                "final_score": 80.0,
                "score_scale_max": 100,
                "created_at": "2026-03-20T00:00:00+00:00",
            },
            {
                "id": "gt2",
                "project_id": "p1",
                "source_submission_id": "s2",
                "final_score": 80.2,
                "score_scale_max": 100,
                "created_at": "2026-03-21T00:00:00+00:00",
            },
            {
                "id": "gt3",
                "project_id": "p1",
                "source_submission_id": "s3",
                "final_score": 79.9,
                "score_scale_max": 100,
                "created_at": "2026-03-22T00:00:00+00:00",
            },
        ]
        mock_load_evo_reports.return_value = {"p1": {}}
        mock_resolve_scoring_context.return_value = (
            {"01": 1.0},
            None,
            {"id": "p1", "meta": {"score_scale_max": 100}},
        )

        payload = _build_evolution_health_report(
            "p1",
            {"id": "p1", "meta": {"score_scale_max": 100}},
        )

        assert payload["summary"]["current_calibrator_version"] == "calib_auto_existing"
        assert payload["summary"]["current_calibrator_source"] == "project"
        assert payload["summary"]["current_calibrator_deployment_mode"] == "cv_validated"
        assert payload["summary"]["current_calibrator_degraded"] is True
        assert (
            payload["summary"]["current_calibrator_degradation_reason"]
            == "current_calibrator_recent_mae_worse_than_rule"
        )
        assert payload["summary"]["current_calibrator_recent_mae"] == pytest.approx(
            4.7667, abs=1e-4
        )
        assert payload["summary"]["current_calibrator_recent_rule_mae"] == pytest.approx(
            0.0, abs=1e-6
        )
        assert payload["summary"]["current_calibrator_recent_mae_delta_vs_rule"] == pytest.approx(
            4.7667, abs=1e-4
        )
        assert payload["summary"]["current_calibrator_has_rollback_candidate"] is True
        assert (
            payload["summary"]["current_calibrator_rollback_candidate_version"] == "calib_auto_prev"
        )
        assert payload["summary"]["current_calibrator_rollback_candidate_model_type"] is None
        assert (
            payload["summary"]["current_calibrator_rollback_candidate_deployment_mode"]
            == "cv_validated"
        )
        assert payload["summary"]["current_calibrator_rollback_candidate_cv_mae"] == pytest.approx(
            1.1, abs=1e-6
        )
        assert any(
            "当前项目级校准器近期误差已明显劣于规则基线" in str(item)
            for item in payload["recommendations"]
        )
        assert any("calib_auto_prev" in str(item) for item in payload["recommendations"])

    @patch("app.main.load_calibration_models")
    @patch("app.main._resolve_project_scoring_context")
    @patch("app.main.load_submissions")
    @patch("app.main.load_ground_truth")
    @patch("app.main.load_evolution_reports")
    def test_build_evolution_health_report_exposes_last_runtime_governance(
        self,
        mock_load_evo_reports,
        mock_load_ground_truth,
        mock_load_submissions,
        mock_resolve_scoring_context,
        mock_load_calibration_models,
    ):
        from app.main import _build_evolution_health_report

        mock_load_calibration_models.return_value = []
        mock_load_submissions.return_value = []
        mock_load_ground_truth.return_value = []
        mock_load_evo_reports.return_value = {
            "p1": {
                "calibrator_runtime_governance": {
                    "action": "rollback",
                    "reason": "rollback_preview_improved",
                    "rollback_candidate_version": "calib_prev",
                    "active_calibrator_version_after": "calib_prev",
                    "degraded_after": False,
                    "recovered_after": True,
                    "updated_reports": 7,
                    "updated_submissions": 7,
                    "recorded_at": "2026-03-30T12:00:00+00:00",
                }
            }
        }
        mock_resolve_scoring_context.return_value = (
            {"01": 1.0},
            None,
            {"id": "p1", "meta": {"score_scale_max": 100}},
        )

        payload = _build_evolution_health_report(
            "p1",
            {"id": "p1", "meta": {"score_scale_max": 100}},
        )

        assert payload["summary"]["last_calibrator_runtime_governance_action"] == "rollback"
        assert (
            payload["summary"]["last_calibrator_runtime_governance_reason"]
            == "rollback_preview_improved"
        )
        assert payload["summary"]["last_calibrator_runtime_governance_candidate"] == "calib_prev"
        assert payload["summary"]["last_calibrator_runtime_governance_updated_reports"] == 7
        assert payload["summary"]["last_calibrator_runtime_governance_updated_submissions"] == 7
        assert payload["summary"]["last_calibrator_runtime_governance_recorded_at"] == (
            "2026-03-30T12:00:00+00:00"
        )
        assert payload["summary"]["last_calibrator_runtime_governance_active_version_after"] == (
            "calib_prev"
        )
        assert payload["summary"]["last_calibrator_runtime_governance_degraded_after"] is False
        assert payload["summary"]["last_calibrator_runtime_governance_recovered_after"] is True
        assert any(
            "最近一次运行时校准治理已自动切回历史更稳版本并完成评分回填，当前项目级校准器已恢复稳定。"
            in str(item)
            for item in payload["recommendations"]
        )

    @patch("app.main._resolve_dwg_converter_binaries", return_value=[])
    @patch("app.main.load_materials")
    @patch("app.main._build_scoring_readiness")
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    @patch("app.main.pytesseract", None)
    @patch("app.main.Image", None)
    def test_get_material_depth_report_includes_capability_warnings(
        self,
        mock_ensure,
        mock_load_projects,
        mock_build_readiness,
        mock_load_materials,
        mock_resolve_dwg_bins,
        client,
    ):
        mock_load_projects.return_value = [{"id": "p1", "name": "项目1", "meta": {}}]
        mock_build_readiness.return_value = {
            "project_id": "p1",
            "ready": False,
            "material_quality": {
                "total_files": 2,
                "parsed_ok_files": 2,
                "parsed_failed_files": 0,
                "total_parsed_chars": 5000,
                "total_parsed_chunks": 8,
                "total_numeric_terms": 6,
                "parse_fail_ratio": 0.0,
                "counts_by_type": {"drawing": 1, "site_photo": 1},
                "chars_by_type": {"drawing": 4000, "site_photo": 1000},
                "chunks_by_type": {"drawing": 6, "site_photo": 2},
                "numeric_terms_by_type": {"drawing": 5, "site_photo": 1},
                "parsed_ok_by_type": {"drawing": 1, "site_photo": 1},
                "parsed_fail_by_type": {"drawing": 0, "site_photo": 0},
                "parsed_fail_details": [],
            },
            "material_gate": {"required_types": ["drawing", "site_photo"], "passed": True},
            "material_depth_gate": {"passed": True},
            "issues": [],
            "warnings": [],
        }
        mock_load_materials.return_value = [
            {
                "project_id": "p1",
                "material_type": "drawing",
                "filename": "总图.dwg",
            },
            {
                "project_id": "p1",
                "material_type": "site_photo",
                "filename": "现场照片1.jpg",
            },
        ]

        response = client.get("/api/v1/projects/p1/materials/depth_report")
        assert response.status_code == 200
        data = response.json()
        assert data["capabilities"]["ocr_available"] is False
        assert data["capabilities"]["dwg_converter_available"] is False
        assert data["capabilities"]["dwg_file_count"] == 1
        assert data["capabilities"]["site_photo_file_count"] == 1
        rec_text = " ".join(data.get("recommendations") or [])
        assert "OCR" in rec_text
        assert "DWG" in rec_text

    @patch("app.main._resolve_dwg_converter_binaries", return_value=["/usr/local/bin/dwg2dxf"])
    @patch("app.main.load_materials")
    @patch("app.main._build_scoring_readiness")
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    def test_get_material_depth_report_markdown_contains_capability_section(
        self,
        mock_ensure,
        mock_load_projects,
        mock_build_readiness,
        mock_load_materials,
        mock_resolve_dwg_bins,
        client,
    ):
        mock_load_projects.return_value = [{"id": "p1", "name": "项目1", "meta": {}}]
        mock_build_readiness.return_value = {
            "project_id": "p1",
            "ready": True,
            "material_quality": {
                "total_files": 1,
                "parsed_ok_files": 1,
                "parsed_failed_files": 0,
                "total_parsed_chars": 3000,
                "total_parsed_chunks": 5,
                "total_numeric_terms": 3,
                "parse_fail_ratio": 0.0,
                "counts_by_type": {"drawing": 1},
                "chars_by_type": {"drawing": 3000},
                "chunks_by_type": {"drawing": 5},
                "numeric_terms_by_type": {"drawing": 3},
                "parsed_ok_by_type": {"drawing": 1},
                "parsed_fail_by_type": {"drawing": 0},
                "parsed_fail_details": [],
            },
            "material_gate": {"required_types": ["drawing"], "passed": True},
            "material_depth_gate": {"passed": True},
            "issues": [],
            "warnings": [],
        }
        mock_load_materials.return_value = [
            {"project_id": "p1", "material_type": "drawing", "filename": "总图.dwg"}
        ]

        response = client.get("/api/v1/projects/p1/materials/depth_report/markdown")
        assert response.status_code == 200
        markdown = response.json()["markdown"]
        assert "## 解析能力" in markdown
        assert "DWG 转换器可用" in markdown

    @patch("app.main._build_material_knowledge_profile")
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    def test_get_material_knowledge_profile_success(
        self,
        mock_ensure,
        mock_load_projects,
        mock_build_knowledge,
        client,
    ):
        mock_load_projects.return_value = [{"id": "p1", "name": "项目1", "meta": {}}]
        mock_build_knowledge.return_value = {
            "project_id": "p1",
            "generated_at": "2026-02-27T00:00:00+00:00",
            "capabilities": {"ocr_available": True, "dwg_converter_available": True},
            "summary": {
                "total_files": 4,
                "parsed_ok_files": 4,
                "numeric_category_summary": ["工期/节点：90", "数量/工程量：1200"],
                "covered_dimensions": 12,
                "dimension_coverage_rate": 0.75,
            },
            "by_type": [
                {
                    "material_type": "tender_qa",
                    "files": 1,
                    "numeric_category_summary": ["工期/节点：90"],
                }
            ],
            "by_dimension": [
                {
                    "dimension_id": "01",
                    "dimension_name": "工程项目整体理解",
                    "coverage_score": 0.8,
                    "coverage_level": "high",
                }
            ],
            "recommendations": ["建议补充危大工程专项方案约束。"],
        }

        response = client.get("/api/v1/projects/p1/materials/knowledge_profile")
        assert response.status_code == 200
        data = response.json()
        assert data["project_id"] == "p1"
        assert data["summary"]["total_files"] == 4
        assert data["summary"]["numeric_category_summary"] == ["工期/节点：90", "数量/工程量：1200"]
        assert data["by_dimension"][0]["dimension_id"] == "01"
        assert data["recommendations"]

    @patch("app.main._build_material_knowledge_profile")
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    def test_get_material_knowledge_profile_markdown_contains_sections(
        self,
        mock_ensure,
        mock_load_projects,
        mock_build_knowledge,
        client,
    ):
        mock_load_projects.return_value = [{"id": "p1", "name": "项目1", "meta": {}}]
        mock_build_knowledge.return_value = {
            "project_id": "p1",
            "generated_at": "2026-02-27T00:00:00+00:00",
            "capabilities": {"ocr_available": False, "dwg_converter_available": False},
            "summary": {
                "total_files": 2,
                "parsed_ok_files": 2,
                "parsed_failed_files": 0,
                "total_parsed_chars": 1000,
                "total_parsed_chunks": 6,
                "total_numeric_terms": 12,
                "numeric_category_summary": ["工期/节点：90", "规格/参数：1200", "阈值/偏差：48"],
                "dimension_coverage_rate": 0.5,
            },
            "by_type": [
                {
                    "material_type_label": "招标文件和答疑",
                    "files": 1,
                    "parsed_chars": 600,
                    "parsed_chunks": 3,
                    "unique_terms": 18,
                    "numeric_terms": 4,
                    "numeric_category_summary": ["工期/节点：90", "阈值/偏差：48"],
                },
            ],
            "by_dimension": [
                {
                    "dimension_id": "01",
                    "dimension_name": "工程项目整体理解",
                    "keyword_hits": 6,
                    "source_types": ["tender_qa"],
                    "coverage_score": 0.62,
                    "coverage_level": "medium",
                }
            ],
            "recommendations": ["建议补充图纸节点与危大工程量化指标。"],
        }

        response = client.get("/api/v1/projects/p1/materials/knowledge_profile/markdown")
        assert response.status_code == 200
        markdown = response.json()["markdown"]
        assert "## 按资料类型" in markdown
        assert "## 按评分维度" in markdown
        assert "## 建议动作" in markdown
        assert "数值约束簇" in markdown

    @patch("app.main._resolve_dwg_converter_binaries", return_value=["/usr/local/bin/dwg2dxf"])
    @patch("app.main._now_iso", return_value="2026-02-27T00:00:00+00:00")
    @patch("app.main.load_materials")
    def test_build_material_knowledge_profile_extracts_dimension_coverage(
        self,
        mock_load_materials,
        mock_now_iso,
        mock_dwg_bins,
        tmp_path,
    ):
        from app.main import _build_material_knowledge_profile

        tender_path = tmp_path / "招标答疑.txt"
        boq_path = tmp_path / "工程量清单.txt"
        tender_path.write_text(
            "招标答疑要求：工期节点、质量标准、危大工程专项方案、应急预案必须明确。",
            encoding="utf-8",
        )
        boq_path.write_text(
            "清单工程量 1200m3，综合单价 580 元，措施费 30 万，设备进场计划。",
            encoding="utf-8",
        )
        mock_load_materials.return_value = [
            {
                "project_id": "p1",
                "material_type": "tender_qa",
                "filename": "招标答疑.txt",
                "path": str(tender_path),
                "created_at": "2026-02-26T00:00:00+00:00",
            },
            {
                "project_id": "p1",
                "material_type": "boq",
                "filename": "工程量清单.txt",
                "path": str(boq_path),
                "created_at": "2026-02-26T00:00:01+00:00",
            },
        ]

        payload = _build_material_knowledge_profile("p1")
        assert payload["project_id"] == "p1"
        assert payload["summary"]["total_files"] == 2
        assert payload["summary"]["parsed_ok_files"] == 2
        assert payload["summary"]["covered_dimensions"] >= 1
        assert payload["summary"]["structured_quality_avg"] > 0
        assert payload["summary"]["numeric_category_summary"]
        assert payload["summary"]["cross_type_consensus_score"] > 0
        assert payload["summary"]["cross_type_consensus_type_count"] >= 2
        assert len(payload["by_type"]) >= 2
        assert any(row.get("numeric_category_summary") for row in payload["by_type"])
        dim_ids = {row["dimension_id"] for row in payload["by_dimension"]}
        assert "01" in dim_ids

    @patch("app.main._resolve_dwg_converter_binaries", return_value=["/usr/local/bin/dwg2dxf"])
    @patch("app.main._now_iso", return_value="2026-02-27T00:00:00+00:00")
    @patch("app.main.load_materials")
    def test_build_material_knowledge_profile_includes_structured_material_signals(
        self,
        mock_load_materials,
        mock_now_iso,
        mock_dwg_bins,
        tmp_path,
    ):
        from app.main import _build_material_knowledge_profile

        drawing_path = tmp_path / "总图.dxf"
        site_photo_path = tmp_path / "现场照片.txt"
        drawing_path.write_text(
            "0\nSECTION\n2\nENTITIES\n"
            "0\nTEXT\n8\nM-EQPM\n1\n节点深化 BIM 综合管线 碰撞 净高 预留预埋 600 3.5\n"
            "0\nINSERT\n8\nP-PIPE\n2\nPUMP_TAG\n"
            "0\nENDSEC\n0\nEOF\n",
            encoding="utf-8",
        )
        site_photo_path.write_text(
            "[图像资料] 文件: 现场.jpg\n[OCR文本提取]\n临边防护 扬尘治理 围挡 样板 实测 48",
            encoding="utf-8",
        )
        mock_load_materials.return_value = [
            {
                "project_id": "p1",
                "material_type": "drawing",
                "filename": "总图.dxf",
                "path": str(drawing_path),
                "created_at": "2026-02-26T00:00:01+00:00",
            },
            {
                "project_id": "p1",
                "material_type": "site_photo",
                "filename": "现场照片.txt",
                "path": str(site_photo_path),
                "created_at": "2026-02-26T00:00:02+00:00",
            },
        ]

        payload = _build_material_knowledge_profile("p1")
        by_type = {row["material_type"]: row for row in payload["by_type"]}
        assert by_type["drawing"]["structured_signal_count"] > 0
        assert by_type["drawing"]["structured_quality_score"] > 0
        assert "机电综合" in (by_type["drawing"]["structured_terms_preview"] or [])
        assert "14" in (by_type["drawing"]["focused_dimensions"] or [])
        assert by_type["site_photo"]["structured_signal_count"] > 0
        assert by_type["site_photo"]["structured_quality_score"] > 0
        assert "高处临边" in (by_type["site_photo"]["structured_terms_preview"] or [])
        by_dimension = {row["dimension_id"]: row for row in payload["by_dimension"]}
        assert by_dimension["14"]["structured_signal_hits"] > 0
        assert by_dimension["03"]["structured_signal_hits"] > 0

    @patch("app.main._resolve_dwg_converter_binaries", return_value=["/usr/local/bin/dwg2dxf"])
    @patch("app.main._now_iso", return_value="2026-02-27T00:00:00+00:00")
    @patch("app.main.load_materials")
    def test_build_material_knowledge_profile_includes_tender_qa_structured_signals(
        self,
        mock_load_materials,
        mock_now_iso,
        mock_dwg_bins,
        tmp_path,
    ):
        from app.main import _build_material_knowledge_profile

        tender_path = tmp_path / "招标答疑.txt"
        tender_path.write_text(
            "答疑澄清：总工期120日历天，评分办法要求BIM深化、危大工程专项方案、质量验收标准。"
            "投标文件必须响应关键节点，不得缺少专项方案。",
            encoding="utf-8",
        )
        mock_load_materials.return_value = [
            {
                "project_id": "p1",
                "material_type": "tender_qa",
                "filename": "招标答疑.txt",
                "path": str(tender_path),
                "created_at": "2026-02-26T00:00:01+00:00",
            }
        ]

        payload = _build_material_knowledge_profile("p1")
        by_type = {row["material_type"]: row for row in payload["by_type"]}
        assert by_type["tender_qa"]["structured_signal_count"] > 0
        assert by_type["tender_qa"]["structured_quality_score"] > 0
        assert "工期/里程碑" in (by_type["tender_qa"]["structured_terms_preview"] or [])
        assert "09" in (by_type["tender_qa"]["focused_dimensions"] or [])
        assert by_type["tender_qa"]["mandatory_clause_terms_preview"]
        by_dimension = {row["dimension_id"]: row for row in payload["by_dimension"]}
        assert by_dimension["09"]["structured_signal_hits"] > 0

    @patch("app.main._now_iso", return_value="2026-02-27T00:00:00+00:00")
    @patch("app.main._build_project_material_index")
    def test_build_material_knowledge_profile_includes_outline_and_table_anchor_previews(
        self,
        mock_build_material_index,
        mock_now_iso,
    ):
        from app.main import _build_material_knowledge_profile

        mock_build_material_index.return_value = {
            "project_id": "p1",
            "available_types": ["tender_qa"],
            "files": [
                {
                    "material_type": "tender_qa",
                    "filename": "招标答疑.pdf",
                    "parsed_ok": True,
                    "text": "危大工程专项方案必须逐项响应，关键节点需闭环。",
                    "chunks": ["危大工程专项方案必须逐项响应。", "关键节点需闭环。"],
                    "created_at": "2026-02-26T00:00:01+00:00",
                    "lexical_terms": ["危大工程", "专项方案", "关键节点"],
                    "numeric_terms_norm": ["3", "120"],
                    "tender_qa_structured_summary": {
                        "structured_quality_score": 0.86,
                        "structured_terms": ["危大工程", "专项方案"],
                        "section_titles": ["评分办法", "专项方案要求"],
                        "scoring_point_terms": ["节点闭环"],
                        "mandatory_clause_terms": ["必须逐项响应"],
                        "focused_dimensions": ["09", "16"],
                        "top_numeric_terms": ["120"],
                        "section_title_paths": ["第一章", "危大工程专项方案"],
                        "document_outline": [
                            {
                                "page_no": 2,
                                "page_type": "scoring_rules",
                                "section_title": "危大工程专项方案",
                                "section_level": 1,
                                "section_path": ["第一章", "危大工程专项方案"],
                                "parse_confidence": 0.91,
                            }
                        ],
                        "table_constraint_rows": [
                            {
                                "page_no": 3,
                                "label": "专项方案响应表",
                                "value": "危大工程需逐项响应",
                                "numbers": ["3"],
                            }
                        ],
                        "table_numeric_constraints": ["3"],
                        "page_type_summary": [{"page_type": "scoring_rules", "count": 1}],
                    },
                }
            ],
            "quality_snapshot": {},
        }

        payload = _build_material_knowledge_profile("p1")
        by_type = {row["material_type"]: row for row in payload["by_type"]}
        assert "危大工程专项方案" in (by_type["tender_qa"]["outline_terms_preview"] or [])
        assert "专项方案响应表" in (by_type["tender_qa"]["table_constraint_terms_preview"] or [])
        assert by_type["tender_qa"]["structured_signal_count"] >= 8

    @patch("app.main.save_materials")
    @patch("app.main.load_materials")
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    @patch("app.main.MATERIALS_DIR")
    def test_upload_material_accepts_doc_extension(
        self, mock_dir, mock_ensure, mock_load_proj, mock_load_mat, mock_save, client, tmp_path
    ):
        mock_dir.__truediv__ = lambda self, x: tmp_path / x
        mock_load_proj.return_value = [{"id": "p1", "scoring_engine_version_locked": "v1"}]
        mock_load_mat.return_value = []

        response = client.post(
            "/api/v1/projects/p1/materials",
            files={"file": ("招标文件.DOC ", BytesIO(b"doc-bytes"), "application/msword")},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["material"]["filename"] == "招标文件.DOC"

    @patch("app.main.save_materials")
    @patch("app.main.load_materials")
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    @patch("app.main.MATERIALS_DIR")
    def test_upload_material_accepts_by_mime_without_extension(
        self, mock_dir, mock_ensure, mock_load_proj, mock_load_mat, mock_save, client, tmp_path
    ):
        mock_dir.__truediv__ = lambda self, x: tmp_path / x
        mock_load_proj.return_value = [{"id": "p1"}]
        mock_load_mat.return_value = []

        response = client.post(
            "/api/v1/projects/p1/materials",
            files={"file": ("招标文件", BytesIO(b"%PDF-1.4\\n"), "application/pdf")},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["material"]["filename"] == "招标文件"

    @patch("app.main.save_materials")
    @patch("app.main.load_materials")
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    @patch("app.main.MATERIALS_DIR")
    def test_upload_material_accepts_dxf_extension(
        self, mock_dir, mock_ensure, mock_load_proj, mock_load_mat, mock_save, client, tmp_path
    ):
        mock_dir.__truediv__ = lambda self, x: tmp_path / x
        mock_load_proj.return_value = [{"id": "p1"}]
        mock_load_mat.return_value = []

        dxf_content = (
            "0\nSECTION\n2\nENTITIES\n0\nTEXT\n8\nA-TEXT\n1\n总平面布置图\n0\nENDSEC\n0\nEOF\n"
        ).encode("utf-8")
        response = client.post(
            "/api/v1/projects/p1/materials",
            files={"file": ("总图.dxf", BytesIO(dxf_content), "application/dxf")},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["material"]["filename"] == "总图.dxf"

    @patch("app.main.save_materials")
    @patch("app.main.load_materials")
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    @patch("app.main.MATERIALS_DIR")
    def test_upload_material_replaces_existing_same_filename(
        self, mock_dir, mock_ensure, mock_load_proj, mock_load_mat, mock_save, client, tmp_path
    ):
        mock_dir.__truediv__ = lambda self, x: tmp_path / x
        mock_load_proj.return_value = [{"id": "p1"}]
        mock_load_mat.return_value = [
            {
                "id": "m-old",
                "project_id": "p1",
                "filename": "test.txt",
                "path": str(tmp_path / "old.txt"),
                "created_at": "2026-02-17T00:00:00+00:00",
            },
            {
                "id": "m-dup",
                "project_id": "p1",
                "filename": " test.txt ",
                "path": str(tmp_path / "dup.txt"),
                "created_at": "2026-02-17T00:00:01+00:00",
            },
            {
                "id": "m-other",
                "project_id": "p1",
                "filename": "other.txt",
                "path": str(tmp_path / "other.txt"),
                "created_at": "2026-02-17T00:00:02+00:00",
            },
        ]

        response = client.post(
            "/api/v1/projects/p1/materials",
            files={"file": ("test.txt", BytesIO(b"new content"), "text/plain")},
        )
        assert response.status_code == 200
        saved_materials = mock_save.call_args[0][0]
        same_name = [
            m
            for m in saved_materials
            if m.get("project_id") == "p1" and m.get("filename") == "test.txt"
        ]
        assert len(same_name) == 1
        assert same_name[0]["id"] == "m-old"


class TestShigongEndpoint:
    """Tests for /projects/{project_id}/shigong endpoint."""

    @patch("app.main.save_submissions")
    @patch("app.main.load_submissions")
    @patch("app.main.load_learning_profiles")
    @patch("app.main.load_config")
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    @patch("app.main.score_text")
    def test_upload_shigong_success(
        self,
        mock_score,
        mock_ensure,
        mock_load_proj,
        mock_config,
        mock_profiles,
        mock_load_sub,
        mock_save_sub,
        client,
    ):
        """Upload shigong should only persist as pending without immediate scoring."""
        mock_load_proj.return_value = [{"id": "p1"}]
        mock_config.return_value = MagicMock(rubric={}, lexicon={})
        mock_profiles.return_value = []
        mock_load_sub.return_value = []
        mock_score.return_value = MagicMock(
            model_dump=lambda: {"total_score": 80.0, "dimension_scores": {}, "penalties": []}
        )

        response = client.post(
            "/api/v1/projects/p1/shigong",
            files={"file": ("test.txt", BytesIO(b"test content"), "text/plain")},
        )
        assert response.status_code == 200
        data = response.json()
        assert "id" in data
        assert data["project_id"] == "p1"
        assert data["total_score"] == 0.0
        assert data["report"]["scoring_status"] == "pending"
        mock_score.assert_not_called()

    @patch("app.main.save_submissions")
    @patch("app.main.load_submissions")
    @patch("app.main.load_learning_profiles")
    @patch("app.main.load_config")
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    @patch("app.main.score_text")
    def test_upload_shigong_with_learning_profile(
        self,
        mock_score,
        mock_ensure,
        mock_load_proj,
        mock_config,
        mock_profiles,
        mock_load_sub,
        mock_save_sub,
        client,
    ):
        """Upload shigong should stay pending even when project has learning profile."""
        mock_load_proj.return_value = [{"id": "p1"}]
        mock_config.return_value = MagicMock(rubric={}, lexicon={})
        mock_profiles.return_value = [{"project_id": "p1", "dimension_multipliers": {"D01": 1.2}}]
        mock_load_sub.return_value = []
        mock_score.return_value = MagicMock(model_dump=lambda: {"total_score": 80.0})

        response = client.post(
            "/api/v1/projects/p1/shigong",
            files={"file": ("test.txt", BytesIO(b"test"), "text/plain")},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["report"]["scoring_status"] == "pending"
        mock_score.assert_not_called()

    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    def test_upload_shigong_project_not_found(self, mock_ensure, mock_load, client):
        """Upload shigong should return 404 if project not found."""
        mock_load.return_value = []
        response = client.post(
            "/api/v1/projects/nonexistent/shigong",
            files={"file": ("test.txt", BytesIO(b"test"), "text/plain")},
        )
        assert response.status_code == 404

    @patch("app.main.record_history_score")
    @patch("app.main.save_submissions")
    @patch("app.main.load_submissions")
    @patch("app.main.load_learning_profiles")
    @patch("app.main.load_config")
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    @patch("app.main.score_text")
    def test_upload_shigong_deduplicates_recent_same_file(
        self,
        mock_score,
        mock_ensure,
        mock_load_proj,
        mock_config,
        mock_profiles,
        mock_load_sub,
        mock_save_sub,
        mock_record_history,
        client,
    ):
        mock_load_proj.return_value = [{"id": "p1"}]
        mock_config.return_value = MagicMock(rubric={}, lexicon={})
        mock_profiles.return_value = []
        saved_state = []

        def _load_state():
            return list(saved_state)

        def _save_state(submissions):
            saved_state[:] = list(submissions)

        mock_load_sub.side_effect = _load_state
        mock_save_sub.side_effect = _save_state
        mock_score.return_value = MagicMock(
            model_dump=lambda: {"total_score": 81.0, "dimension_scores": {}, "penalties": []}
        )

        first = client.post(
            "/api/v1/projects/p1/shigong",
            files={"file": ("dup.txt", BytesIO(b"same content"), "text/plain")},
        )
        second = client.post(
            "/api/v1/projects/p1/shigong",
            files={"file": ("dup.txt", BytesIO(b"same content"), "text/plain")},
        )
        assert first.status_code == 200
        assert second.status_code == 200
        first_data = first.json()
        second_data = second.json()
        assert second_data["id"] == first_data["id"]
        assert len(saved_state) == 1
        assert mock_save_sub.call_count == 1
        assert mock_score.call_count == 0
        mock_record_history.assert_not_called()


class TestDXFParser:
    def test_read_uploaded_file_content_extracts_text_from_dxf(self):
        from app.main import _read_uploaded_file_content

        dxf_content = (
            "0\nSECTION\n2\nHEADER\n9\n$ACADVER\n1\nAC1032\n9\n$INSUNITS\n70\n6\n0\nENDSEC\n"
            "0\nSECTION\n2\nENTITIES\n0\nTEXT\n8\nA-TEXT\n1\n总平面布置图\n"
            "0\nMTEXT\n8\nA-NOTE\n3\n施工进度\n1\n计划节点与验收\n0\nENDSEC\n0\nEOF\n"
        ).encode("utf-8")
        text = _read_uploaded_file_content(dxf_content, "sample.dxf")
        assert "DXF解析摘要" in text
        assert "ACAD版本: AC1032" in text
        assert "插入单位: 6(米)" in text
        assert "总平面布置图" in text
        assert "计划节点与验收" in text


class TestMaterialAdvancedParsing:
    @patch("app.main.load_project_requirements", return_value=[])
    @patch("app.main.load_project_anchors", return_value=[])
    def test_build_material_query_features_includes_material_profile_boosts(
        self,
        mock_load_anchors,
        mock_load_requirements,
    ):
        from app.main import _build_material_query_features

        profile = {
            "by_type": [
                {
                    "material_type": "boq",
                    "top_terms": ["工程量", "综合单价", "措施费", "项目"],
                    "top_numeric_terms": ["120", "30", "580"],
                    "outline_terms_preview": ["资源配置章节", "机械投入计划"],
                    "table_constraint_terms_preview": ["主要机械投入表", "劳动力峰值计划"],
                }
            ],
            "by_dimension": [
                {
                    "dimension_id": "15",
                    "coverage_score": 0.76,
                    "source_file_count": 2,
                    "source_types": ["boq"],
                }
            ],
        }

        out = _build_material_query_features(
            project_id="p1",
            submission_text="计划调配与资源组织。",
            required_sections=[],
            required_charts=[],
            mandatory_elements=[],
            custom_text_items=[],
            context_text="",
            material_knowledge_profile=profile,
        )

        assert out["material_profile_query_terms_count"] >= 2
        assert out["material_profile_query_numeric_terms_count"] >= 1
        assert "15" in out["material_profile_focus_dimensions"]
        assert "工程量" in out["query_terms"]
        assert "主要机械投入表" in out["query_terms"]
        assert "120" in out["query_numeric_terms"]

    def test_build_material_dimension_requirements_from_knowledge_profile(self):
        from app.main import _build_material_dimension_requirements

        profile = {
            "by_type": [
                {
                    "material_type": "tender_qa",
                    "top_terms": ["工期", "质量标准", "危大工程", "项目"],
                    "top_numeric_terms": ["120", "30"],
                    "structured_terms_preview": ["里程碑", "总控计划"],
                    "section_titles_preview": ["施工组织设计总体部署"],
                    "outline_terms_preview": ["施工部署章节", "危大工程专项方案"],
                    "table_constraint_terms_preview": ["关键节点响应表", "危大工程清单响应表"],
                    "scoring_point_terms_preview": ["bim", "深化"],
                    "mandatory_clause_terms_preview": ["投标文件必须响应关键节点"],
                    "structured_quality_score": 0.78,
                    "focused_dimensions": ["09"],
                    "numeric_category_summary": ["工期/节点：120、30"],
                },
                {
                    "material_type": "drawing",
                    "top_terms": ["节点", "剖面", "深化", "图纸"],
                    "top_numeric_terms": ["3.5", "600"],
                    "structured_terms_preview": ["机电综合", "专业碰撞", "预留预埋"],
                    "structured_quality_score": 0.62,
                    "focused_dimensions": ["14", "06", "12"],
                    "numeric_category_summary": ["规格/参数：3.5、600"],
                },
            ],
            "by_dimension": [
                {
                    "dimension_id": "09",
                    "dimension_name": "进度计划体系与纠偏阈值",
                    "coverage_score": 0.81,
                    "source_file_count": 2,
                    "source_types": ["tender_qa"],
                },
                {
                    "dimension_id": "14",
                    "dimension_name": "图纸会审、深化设计与变更闭环",
                    "coverage_score": 0.72,
                    "source_file_count": 1,
                    "source_types": ["drawing"],
                },
            ],
        }

        reqs = _build_material_dimension_requirements(
            "p1",
            profile,
            available_material_types=["tender_qa", "drawing"],
        )

        assert len(reqs) == 2
        dim09 = next(r for r in reqs if r.get("dimension_id") == "09")
        patterns = dim09.get("patterns") or {}
        assert dim09.get("source_pack_id") == "runtime_material_dimension"
        assert dim09.get("source_pack_version") == "v2-material-dimension-4"
        assert patterns.get("source_mode") == "material_knowledge_profile"
        assert "tender_qa" in (patterns.get("source_types") or [])
        assert "120" in (patterns.get("top_numeric_terms") or [])
        assert "里程碑" in (patterns.get("structured_terms") or [])
        assert "施工组织设计总体部署" in (patterns.get("section_titles") or [])
        assert "危大工程专项方案" in (patterns.get("outline_terms") or [])
        assert "关键节点响应表" in (patterns.get("table_constraint_terms") or [])
        assert "bim" in [str(x).lower() for x in (patterns.get("scoring_point_terms") or [])]
        assert patterns.get("mandatory_clause_terms")
        assert int(patterns.get("minimum_hint_hits") or 0) >= 3
        assert float(patterns.get("strongest_structured_quality") or 0.0) >= 0.7
        dim14 = next(r for r in reqs if r.get("dimension_id") == "14")
        dim14_patterns = dim14.get("patterns") or {}
        assert "专业碰撞" in (dim14_patterns.get("structured_terms") or [])
        assert "14" in (dim14_patterns.get("focused_dimensions") or [])
        assert int(dim14_patterns.get("structured_alignment_hits") or 0) >= 1

    def test_build_material_consensus_requirements_from_knowledge_profile(self):
        from app.main import _build_material_consensus_requirements

        profile = {
            "summary": {
                "cross_type_consensus_score": 0.66,
                "cross_type_focus_dimensions": ["09", "14"],
                "cross_type_consensus_terms": ["关键节点", "深化设计", "BIM"],
                "cross_type_numeric_category_summary": [
                    "工期/节点：120、30",
                    "规格/参数：600、3.5",
                ],
            },
            "by_type": [
                {
                    "material_type": "tender_qa",
                    "parsed_ok_files": 1,
                    "structured_quality_score": 0.82,
                },
                {
                    "material_type": "drawing",
                    "parsed_ok_files": 1,
                    "structured_quality_score": 0.74,
                },
                {
                    "material_type": "boq",
                    "parsed_ok_files": 1,
                    "structured_quality_score": 0.68,
                },
            ],
        }

        reqs = _build_material_consensus_requirements(
            "p1",
            profile,
            available_material_types=["tender_qa", "drawing", "boq"],
        )

        assert len(reqs) >= 2
        duration_req = next(row for row in reqs if row.get("dimension_id") == "09")
        duration_patterns = duration_req.get("patterns") or {}
        assert duration_req.get("source_pack_id") == "runtime_material_consensus"
        assert duration_patterns.get("source_mode") == "cross_material_consensus"
        assert "120" in (duration_patterns.get("must_hit_numbers") or [])
        assert "关键节点" in " ".join(duration_patterns.get("must_hit_terms") or [])
        assert float(duration_patterns.get("consensus_score") or 0.0) >= 0.6

    @patch("app.main.select_top_logic_skeletons")
    def test_build_runtime_feature_requirements_uses_high_confidence_skeletons(
        self,
        mock_select_top_logic_skeletons,
    ):
        from app.main import _build_runtime_feature_requirements
        from app.schemas import ExtractedFeature

        mock_select_top_logic_skeletons.side_effect = lambda dimension_ids, top_k=2: (
            [
                ExtractedFeature(
                    feature_id=f"feat-{dimension_ids[0]}",
                    dimension_id=dimension_ids[0],
                    logic_skeleton=[
                        "[前置条件] 风险边界清晰 + [技术/动作] 动作链与责任分工 + [量化指标类型] 频次阈值与闭环验收"
                    ],
                    confidence_score=0.82,
                    usage_count=5,
                    active=True,
                )
            ]
            if dimension_ids and dimension_ids[0] in {"09", "14"}
            else []
        )

        profile = {
            "by_dimension": [
                {
                    "dimension_id": "14",
                    "coverage_score": 0.76,
                    "source_file_count": 2,
                    "source_types": ["drawing"],
                }
            ]
        }
        reqs = _build_runtime_feature_requirements(
            "p1",
            material_knowledge_profile=profile,
            weights_norm={"09": 0.2, "14": 0.12, "01": 0.04},
        )
        assert reqs
        dim_ids = {str(row.get("dimension_id")) for row in reqs}
        assert {"09", "14"}.issubset(dim_ids)
        dim14 = next(row for row in reqs if str(row.get("dimension_id")) == "14")
        patterns = dim14.get("patterns") or {}
        assert patterns.get("source_mode") == "feature_confidence_loop"
        assert "feat-14" in (patterns.get("feature_ids") or [])
        assert "动作链与责任分工" in " ".join(patterns.get("hints") or [])
        assert float(dim14.get("weight") or 0.0) > 1.0

    @patch("app.main.load_evolution_reports")
    def test_build_runtime_feedback_requirements_uses_evolution_multipliers(
        self,
        mock_load_evolution_reports,
    ):
        from app.main import _build_runtime_feedback_requirements

        mock_load_evolution_reports.return_value = {
            "p1": {
                "sample_count": 4,
                "scoring_evolution": {
                    "dimension_multipliers": {"09": 1.16, "14": 1.08, "03": 0.97},
                    "rationale": {
                        "09": "高分施组在进度计划与里程碑闭环方面更强。",
                        "14": "高分施组在图纸深化、节点与碰撞复核方面更强。",
                    },
                },
            }
        }
        reqs = _build_runtime_feedback_requirements(
            "p1",
            material_knowledge_profile={
                "by_dimension": [
                    {
                        "dimension_id": "09",
                        "coverage_score": 0.31,
                        "suggested_keywords": ["关键节点", "里程碑", "纠偏"],
                    },
                    {
                        "dimension_id": "14",
                        "coverage_score": 0.76,
                        "suggested_keywords": ["图纸", "深化", "碰撞"],
                    },
                ]
            },
        )
        dim_ids = {str(row.get("dimension_id")) for row in reqs}
        assert {"09", "14"}.issubset(dim_ids)
        dim09 = next(row for row in reqs if str(row.get("dimension_id")) == "09")
        patterns = dim09.get("patterns") or {}
        assert patterns.get("source_mode") == "feedback_evolution"
        assert patterns.get("multiplier") == pytest.approx(1.16, abs=1e-6)
        assert patterns.get("sample_count") == 4
        assert "关键节点" in (patterns.get("hints") or [])

    @patch("app.main.load_ground_truth")
    @patch("app.main.load_evolution_reports")
    def test_build_runtime_feedback_requirements_uses_recent_ground_truth_feedback_without_evo(
        self,
        mock_load_evolution_reports,
        mock_load_ground_truth,
    ):
        from app.main import _build_runtime_feedback_requirements

        mock_load_evolution_reports.return_value = {"p1": {}}
        mock_load_ground_truth.return_value = [
            {
                "project_id": "p1",
                "created_at": "2026-03-08T12:00:00+08:00",
                "feature_confidence_update": {
                    "applied_dimension_ids": ["09"],
                    "applied_feature_ids": ["F-09"],
                    "delta_score_100": 6.0,
                    "updated": 1,
                },
            }
        ]
        reqs = _build_runtime_feedback_requirements(
            "p1",
            material_knowledge_profile={
                "by_dimension": [
                    {
                        "dimension_id": "09",
                        "coverage_score": 0.28,
                        "suggested_keywords": ["关键节点", "里程碑", "纠偏"],
                    }
                ]
            },
        )
        assert reqs
        dim09 = next(row for row in reqs if str(row.get("dimension_id")) == "09")
        patterns = dim09.get("patterns") or {}
        assert patterns.get("recent_feedback_count") == 1
        assert patterns.get("recent_feedback_positive_count") == 1
        assert float(patterns.get("recent_feedback_avg_delta_100") or 0.0) > 0.0
        assert float(dim09.get("weight") or 0.0) > 1.0
        assert float(dim09.get("weight") or 0.0) > 1.0

    @patch("app.main.load_feature_kb")
    def test_build_feature_confidence_summary_marks_project_focus_dimensions(
        self,
        mock_load_feature_kb,
    ):
        from app.main import _build_feature_confidence_summary
        from app.schemas import ExtractedFeature

        mock_load_feature_kb.return_value = [
            ExtractedFeature(
                feature_id="f-09",
                dimension_id="09",
                logic_skeleton=[
                    "[前置条件] 关键节点明确 + [技术/动作] 总控计划纠偏 + [量化指标类型] 偏差阈值与闭环"
                ],
                confidence_score=0.83,
                usage_count=6,
                active=True,
            ),
            ExtractedFeature(
                feature_id="f-14",
                dimension_id="14",
                logic_skeleton=[
                    "[前置条件] 图纸接口明确 + [技术/动作] 深化碰撞复核 + [量化指标类型] 节点闭环"
                ],
                confidence_score=0.72,
                usage_count=4,
                active=True,
            ),
            ExtractedFeature(
                feature_id="f-03-old",
                dimension_id="03",
                logic_skeleton=[
                    "[前置条件] 文明施工场景明确 + [技术/动作] 扬尘围挡联动 + [量化指标类型] 频次记录"
                ],
                confidence_score=0.18,
                usage_count=5,
                active=False,
            ),
        ]
        summary = _build_feature_confidence_summary(
            "p1",
            material_knowledge_profile={
                "by_dimension": [
                    {"dimension_id": "14", "coverage_score": 0.76},
                    {"dimension_id": "09", "coverage_score": 0.61},
                ]
            },
        )
        assert summary["active_count"] == 2
        assert summary["retired_count"] == 1
        assert summary["high_confidence_count"] == 2
        assert summary["focus_dimensions"] == ["14", "09"]
        top_dim_ids = [row["dimension_id"] for row in summary["top_dimensions"]]
        assert top_dim_ids[:2] == ["09", "14"] or top_dim_ids[:2] == ["14", "09"]

    def test_build_material_utilization_summary_tracks_dimension_requirements(self):
        from app.main import _build_material_utilization_summary

        report = {
            "requirement_hits": [
                {
                    "source_pack_id": "runtime_material_dimension",
                    "dimension_id": "09",
                    "label": "资料维度约束：进度计划",
                    "hit": True,
                    "source_types": ["tender_qa", "boq"],
                },
                {
                    "source_pack_id": "runtime_material_dimension",
                    "dimension_id": "14",
                    "label": "资料维度约束：图纸深化",
                    "hit": False,
                    "source_types": ["drawing"],
                },
            ]
        }
        runtime_req_meta = {
            "material_profile_query_terms_count": 6,
            "material_profile_query_numeric_terms_count": 3,
            "material_profile_focus_dimensions": ["09", "14"],
        }

        summary = _build_material_utilization_summary(report, runtime_req_meta)

        assert summary["material_dimension_total"] == 2
        assert summary["material_dimension_hit"] == 1
        assert summary["material_dimension_hit_rate"] == pytest.approx(0.5, abs=1e-6)
        assert summary["material_profile_query_terms_count"] == 6
        assert summary["material_profile_focus_dimensions"] == ["09", "14"]
        first_dim = summary["material_dimension_by_dimension"][0]
        assert first_dim["dimension_id"] in {"09", "14"}

    def test_build_boq_structured_summary_from_csv(self):
        from app.main import _build_boq_structured_summary

        csv_content = (
            "项目编码,项目名称,单位,工程量,综合单价,合价\n"
            "010101001,土方开挖,m3,100,35.5,3550\n"
            "010201001,钢筋制作,t,12.5,4300,53750\n"
        ).encode("utf-8")
        summary = _build_boq_structured_summary(
            csv_content,
            "boq.csv",
            parsed_text="安全文明措施 人工 工日 机械 措施项目 夜间施工",
        )
        assert summary["detected_format"] == "csv"
        assert summary["total_parsed_items"] == 2
        assert summary["total_amount"] == 57300.0
        first_sheet = summary["sheets"][0]
        assert first_sheet["detected_columns"]["code"] == 0
        assert first_sheet["detected_columns"]["amount"] == 5
        assert "工程量" in (summary.get("structured_terms") or [])
        assert "安全文明/绿色措施" in (summary.get("cost_structure_tags") or [])
        assert "人工与班组投入" in (summary.get("cost_structure_tags") or [])
        assert "措施项目/抢工" in (summary.get("cost_structure_tags") or [])
        assert "13" in (summary.get("focused_dimensions") or [])
        assert "11" in (summary.get("focused_dimensions") or [])
        assert summary["structured_quality_score"] > 0

    def test_build_boq_structured_summary_stops_after_summary_tail(self):
        from app.main import _build_boq_structured_summary

        rows = ["项目编码,项目名称,单位,工程量,综合单价,合价"]
        for idx in range(1, 41):
            rows.append(
                f"{idx:09d},土方开挖{idx},m3,{100 + idx},{35.5 + idx / 10:.1f},{(100 + idx) * (35.5 + idx / 10):.1f}"
            )
        rows.extend(
            [
                ",本页小计,,0,0,99999",
                ",本章合计,,0,0,199999",
            ]
        )
        rows.extend("" for _ in range(12))
        rows.append("999999999,尾部噪音大额项目,m3,1,999999,999999")
        summary = _build_boq_structured_summary("\n".join(rows).encode("utf-8"), "heavy_boq.csv")

        first_sheet = summary["sheets"][0]
        assert first_sheet["row_scan_stopped_early"] is True
        assert first_sheet["scanned_rows"] < len(rows)
        assert summary["total_parsed_items"] == 40
        assert "尾部噪音大额项目" not in {
            str(item.get("name") or "") for item in (first_sheet.get("top_items_by_amount") or [])
        }

    def test_build_boq_structured_summary_keeps_bounded_top_items_by_amount(self):
        from app.main import _build_boq_structured_summary

        rows = ["项目编码,项目名称,单位,工程量,综合单价,合价"]
        for idx in range(1, 16):
            rows.append(f"{idx:09d},清单项{idx},m3,1,{1000 + idx},{1000 + idx}")

        summary = _build_boq_structured_summary("\n".join(rows).encode("utf-8"), "topk_boq.csv")

        top_names = [
            str(item.get("name") or "")
            for item in (summary["sheets"][0].get("top_items_by_amount") or [])
        ]
        assert len(top_names) == 10
        assert top_names[0] == "清单项15"
        assert top_names[-1] == "清单项6"
        assert "清单项5" not in top_names

    def test_build_boq_structured_summary_records_pdf_preview_progress(self):
        from app.main import _build_boq_structured_summary

        summary = _build_boq_structured_summary(
            b"%PDF-1.4\n",
            "工程量清单.pdf",
            parsed_text="[PDF_BACKEND:pymupdf]\n[PAGE:1]\n第一页\n\n[PAGE:2]\n第二页",
            preview_only=True,
        )

        assert summary["detected_format"] == "pdf"
        assert summary["parse_stage"] == "preview"
        assert summary["preview_last_page"] == 2
        assert summary["parsed_page_count"] == 2

    def test_build_boq_structured_summary_records_pdf_resume_metadata(self):
        from app.main import _build_boq_structured_summary

        summary = _build_boq_structured_summary(
            b"%PDF-1.4\n",
            "工程量清单.pdf",
            parsed_text=(
                "[PDF_BACKEND:pymupdf]\n"
                "[PAGE:1]\n第一页\n\n"
                "[PAGE:2]\n第二页\n\n"
                "[PAGE:3]\n第三页"
            ),
            prior_summary={"parse_stage": "preview", "preview_last_page": 2},
        )

        assert summary["detected_format"] == "pdf"
        assert summary["scan_strategy"] == "preview_guided_full_pdf"
        assert summary["resume_from_page"] == 3
        assert summary["saved_page_count"] == 2
        assert summary["parsed_page_count"] == 3

    def test_build_boq_structured_summary_prioritizes_main_sheet_and_limits_aux_scan(self):
        from app.main import (
            DEFAULT_BOQ_PARSE_AUX_HEADER_SCAN_MAX_ROWS,
            _build_boq_structured_summary,
        )

        class _FakeSheet:
            def __init__(self, title, rows):
                self.title = title
                self._rows = rows

            def iter_rows(self, values_only=True):
                assert values_only is True
                for row in self._rows:
                    yield row

        class _FakeWorkbook:
            def __init__(self, worksheets):
                self.worksheets = worksheets

            def close(self):
                return None

        aux_rows = [("", "", "", "", "", "")] * 25
        main_rows = [
            ("项目编码", "项目名称", "单位", "工程量", "综合单价", "合价"),
            ("010101001", "土方开挖", "m3", 100, 35.5, 3550),
        ]
        fake_wb = _FakeWorkbook(
            [
                _FakeSheet("封面说明", aux_rows),
                _FakeSheet("分部分项工程量清单", main_rows),
            ]
        )
        fake_openpyxl = SimpleNamespace(load_workbook=lambda *args, **kwargs: fake_wb)

        with patch.dict(sys.modules, {"openpyxl": fake_openpyxl}):
            summary = _build_boq_structured_summary(b"fake-xlsx", "boq.xlsx")

        assert [sheet["sheet"] for sheet in summary["sheets"]] == ["分部分项工程量清单", "封面说明"]
        main_sheet = summary["sheets"][0]
        aux_sheet = summary["sheets"][1]
        assert main_sheet["parsed_items"] == 1
        assert aux_sheet["parsed_items"] == 0
        assert aux_sheet["scanned_rows"] == DEFAULT_BOQ_PARSE_AUX_HEADER_SCAN_MAX_ROWS

    def test_build_boq_structured_summary_preview_only_limits_sheet_count_and_rows(self):
        from app.main import (
            DEFAULT_MATERIAL_PARSE_PREVIEW_MAX_ROWS_BY_TYPE,
            _build_boq_structured_summary,
        )

        class _FakeSheet:
            def __init__(self, title, rows):
                self.title = title
                self._rows = rows

            def iter_rows(self, values_only=True):
                assert values_only is True
                for row in self._rows:
                    yield row

        class _FakeWorkbook:
            def __init__(self, worksheets):
                self.worksheets = worksheets

            def close(self):
                return None

        heavy_rows = [("项目编码", "项目名称", "单位", "工程量", "综合单价", "合价")]
        heavy_rows.extend(
            (f"{idx:09d}", f"清单项{idx}", "m3", idx, 10 + idx, (10 + idx) * idx)
            for idx in range(1, 260)
        )
        fake_wb = _FakeWorkbook(
            [
                _FakeSheet("分部分项工程量清单", heavy_rows),
                _FakeSheet("措施项目清单", heavy_rows),
                _FakeSheet("其他项目清单", heavy_rows),
            ]
        )
        fake_openpyxl = SimpleNamespace(load_workbook=lambda *args, **kwargs: fake_wb)

        with patch.dict(sys.modules, {"openpyxl": fake_openpyxl}):
            summary = _build_boq_structured_summary(
                b"fake-xlsx",
                "heavy_boq.xlsx",
                parsed_text="工程量清单 预解析",
                preview_only=True,
            )

        assert summary["parse_stage"] == "preview"
        assert len(summary["sheets"]) == 2
        assert summary["sheets"][0]["sheet"] == "分部分项工程量清单"
        assert (
            summary["sheets"][0]["scanned_rows"]
            == DEFAULT_MATERIAL_PARSE_PREVIEW_MAX_ROWS_BY_TYPE["boq"]
        )

    def test_build_boq_structured_summary_full_mode_uses_preview_guidance_for_unconfirmed_sheets(
        self,
    ):
        from app.main import (
            DEFAULT_BOQ_PARSE_FULL_GUIDED_UNCONFIRMED_PRIMARY_SHEET_MAX_ROWS,
            DEFAULT_BOQ_PARSE_FULL_GUIDED_WEAK_SHEET_MAX_ROWS,
            DEFAULT_BOQ_PARSE_MAX_ROWS_PER_SHEET,
            _build_boq_structured_summary,
        )

        class _FakeSheet:
            def __init__(self, title, rows):
                self.title = title
                self._rows = rows

            def iter_rows(self, values_only=True):
                assert values_only is True
                for row in self._rows:
                    yield row

        class _FakeWorkbook:
            def __init__(self, worksheets):
                self.worksheets = worksheets

            def close(self):
                return None

        heavy_rows = [("项目编码", "项目名称", "单位", "工程量", "综合单价", "合价")]
        heavy_rows.extend(
            (f"{idx:09d}", f"清单项{idx}", "m3", idx, 10 + idx, (10 + idx) * idx)
            for idx in range(1, 1605)
        )
        aux_rows = [("", "", "", "", "", "")] * 80
        fake_wb = _FakeWorkbook(
            [
                _FakeSheet("封面说明", aux_rows),
                _FakeSheet("其他项目清单", heavy_rows),
                _FakeSheet("措施项目清单", heavy_rows),
                _FakeSheet("分部分项工程量清单", heavy_rows),
            ]
        )
        preview_summary = {
            "parse_stage": "preview",
            "total_parsed_items": 40,
            "sheets": [
                {
                    "sheet": "分部分项工程量清单",
                    "parsed_items": 36,
                    "detected_columns": {"code": 0, "name": 1},
                },
                {
                    "sheet": "措施项目清单",
                    "parsed_items": 28,
                    "detected_columns": {"code": 0, "name": 1},
                },
            ],
        }
        fake_openpyxl = SimpleNamespace(load_workbook=lambda *args, **kwargs: fake_wb)

        with patch.dict(sys.modules, {"openpyxl": fake_openpyxl}):
            summary = _build_boq_structured_summary(
                b"fake-xlsx",
                "heavy_boq.xlsx",
                parsed_text="工程量清单 full parse",
                prior_summary=preview_summary,
            )

        assert summary["scan_strategy"] == "preview_guided_full"
        assert summary["scan_guidance_strength"] == "standard"
        assert [sheet["sheet"] for sheet in summary["sheets"]] == [
            "分部分项工程量清单",
            "措施项目清单",
            "其他项目清单",
            "封面说明",
        ]
        assert summary["sheets"][0]["row_scan_budget"] == DEFAULT_BOQ_PARSE_MAX_ROWS_PER_SHEET
        assert summary["sheets"][1]["row_scan_budget"] == DEFAULT_BOQ_PARSE_MAX_ROWS_PER_SHEET
        assert (
            summary["sheets"][2]["row_scan_budget"]
            == DEFAULT_BOQ_PARSE_FULL_GUIDED_UNCONFIRMED_PRIMARY_SHEET_MAX_ROWS
        )
        assert (
            summary["sheets"][2]["scanned_rows"]
            == DEFAULT_BOQ_PARSE_FULL_GUIDED_UNCONFIRMED_PRIMARY_SHEET_MAX_ROWS
        )
        assert (
            summary["sheets"][3]["row_scan_budget"]
            == DEFAULT_BOQ_PARSE_FULL_GUIDED_WEAK_SHEET_MAX_ROWS
        )

    def test_build_boq_structured_summary_full_mode_uses_stronger_preview_guidance_for_high_confidence(
        self,
    ):
        from app.main import (
            DEFAULT_BOQ_PARSE_FULL_GUIDED_STRONG_UNCONFIRMED_PRIMARY_SHEET_MAX_ROWS,
            DEFAULT_BOQ_PARSE_FULL_GUIDED_STRONG_WEAK_SHEET_MAX_ROWS,
            DEFAULT_BOQ_PARSE_MAX_ROWS_PER_SHEET,
            _build_boq_structured_summary,
        )

        class _FakeSheet:
            def __init__(self, title, rows):
                self.title = title
                self._rows = rows

            def iter_rows(self, values_only=True):
                assert values_only is True
                for row in self._rows:
                    yield row

        class _FakeWorkbook:
            def __init__(self, worksheets):
                self.worksheets = worksheets

            def close(self):
                return None

        heavy_rows = [("项目编码", "项目名称", "单位", "工程量", "综合单价", "合价")]
        heavy_rows.extend(
            (f"{idx:09d}", f"清单项{idx}", "m3", idx, 10 + idx, (10 + idx) * idx)
            for idx in range(1, 1605)
        )
        aux_rows = [("", "", "", "", "", "")] * 80
        fake_wb = _FakeWorkbook(
            [
                _FakeSheet("封面说明", aux_rows),
                _FakeSheet("其他项目清单", heavy_rows),
                _FakeSheet("措施项目清单", heavy_rows),
                _FakeSheet("分部分项工程量清单", heavy_rows),
            ]
        )
        preview_summary = {
            "parse_stage": "preview",
            "total_parsed_items": 64,
            "sheets": [
                {
                    "sheet": "分部分项工程量清单",
                    "parsed_items": 36,
                    "detected_columns": {"code": 0, "name": 1},
                },
                {
                    "sheet": "措施项目清单",
                    "parsed_items": 28,
                    "detected_columns": {"code": 0, "name": 1},
                },
            ],
        }
        fake_openpyxl = SimpleNamespace(load_workbook=lambda *args, **kwargs: fake_wb)

        with patch.dict(sys.modules, {"openpyxl": fake_openpyxl}):
            summary = _build_boq_structured_summary(
                b"fake-xlsx",
                "heavy_boq.xlsx",
                parsed_text="工程量清单 full parse",
                prior_summary=preview_summary,
            )

        assert summary["scan_strategy"] == "preview_guided_full"
        assert summary["scan_guidance_strength"] == "strong"
        assert summary["sheets"][0]["row_scan_budget"] == DEFAULT_BOQ_PARSE_MAX_ROWS_PER_SHEET
        assert summary["sheets"][1]["row_scan_budget"] == DEFAULT_BOQ_PARSE_MAX_ROWS_PER_SHEET
        assert (
            summary["sheets"][2]["row_scan_budget"]
            == DEFAULT_BOQ_PARSE_FULL_GUIDED_STRONG_UNCONFIRMED_PRIMARY_SHEET_MAX_ROWS
        )
        assert (
            summary["sheets"][2]["scanned_rows"]
            == DEFAULT_BOQ_PARSE_FULL_GUIDED_STRONG_UNCONFIRMED_PRIMARY_SHEET_MAX_ROWS
        )
        assert (
            summary["sheets"][3]["row_scan_budget"]
            == DEFAULT_BOQ_PARSE_FULL_GUIDED_STRONG_WEAK_SHEET_MAX_ROWS
        )

    def test_build_boq_structured_summary_full_mode_stops_after_empty_aux_tail_when_preview_is_strong(
        self,
    ):
        from app.main import _build_boq_structured_summary

        class _FakeSheet:
            def __init__(self, title, rows):
                self.title = title
                self._rows = rows

            def iter_rows(self, values_only=True):
                assert values_only is True
                for row in self._rows:
                    yield row

        class _FakeWorkbook:
            def __init__(self, worksheets):
                self.worksheets = worksheets

            def close(self):
                return None

        heavy_rows = [("项目编码", "项目名称", "单位", "工程量", "综合单价", "合价")]
        heavy_rows.extend(
            (f"{idx:09d}", f"清单项{idx}", "m3", idx, 10 + idx, (10 + idx) * idx)
            for idx in range(1, 120)
        )
        aux_rows = [("", "", "", "", "", "")] * 80
        fake_wb = _FakeWorkbook(
            [
                _FakeSheet("分部分项工程量清单", heavy_rows),
                _FakeSheet("措施项目清单", heavy_rows),
                _FakeSheet("封面说明", aux_rows),
                _FakeSheet("目录说明", aux_rows),
                _FakeSheet("附录说明", aux_rows),
            ]
        )
        preview_summary = {
            "parse_stage": "preview",
            "total_parsed_items": 64,
            "sheets": [
                {
                    "sheet": "分部分项工程量清单",
                    "parsed_items": 36,
                    "detected_columns": {"code": 0, "name": 1},
                },
                {
                    "sheet": "措施项目清单",
                    "parsed_items": 28,
                    "detected_columns": {"code": 0, "name": 1},
                },
            ],
        }
        fake_openpyxl = SimpleNamespace(load_workbook=lambda *args, **kwargs: fake_wb)

        with patch.dict(sys.modules, {"openpyxl": fake_openpyxl}):
            summary = _build_boq_structured_summary(
                b"fake-xlsx",
                "heavy_boq.xlsx",
                parsed_text="工程量清单 full parse",
                prior_summary=preview_summary,
            )

        assert summary["scan_strategy"] == "preview_guided_full"
        assert summary["scan_guidance_strength"] == "strong"
        assert summary["scan_tail_stop_reason"] == "strong_preview_empty_aux_tail"
        assert summary["skipped_tail_sheets"] == 1
        assert [sheet["sheet"] for sheet in summary["sheets"]] == [
            "分部分项工程量清单",
            "措施项目清单",
            "封面说明",
            "目录说明",
        ]

    def test_build_boq_structured_summary_full_mode_uses_preview_guidance_for_csv_standard(
        self,
    ):
        from app.main import (
            DEFAULT_BOQ_PARSE_FULL_GUIDED_CSV_MAX_ROWS,
            _build_boq_structured_summary,
        )

        rows = ["项目编码,项目名称,单位,工程量,综合单价,合价"]
        rows.extend(
            f"{idx:09d},清单项{idx},m3,{idx},{10 + idx},{(10 + idx) * idx}"
            for idx in range(1, 2205)
        )
        preview_summary = {
            "parse_stage": "preview",
            "total_parsed_items": 24,
            "sheets": [{"sheet": "csv", "parsed_items": 24, "detected_columns": {"code": 0}}],
        }

        summary = _build_boq_structured_summary(
            "\n".join(rows).encode("utf-8"),
            "heavy_boq.csv",
            parsed_text="工程量清单 full parse",
            prior_summary=preview_summary,
        )

        assert summary["scan_strategy"] == "preview_guided_full"
        assert summary["scan_guidance_strength"] == "standard"
        assert summary["sheets"][0]["row_scan_budget"] == DEFAULT_BOQ_PARSE_FULL_GUIDED_CSV_MAX_ROWS
        assert summary["sheets"][0]["scanned_rows"] == DEFAULT_BOQ_PARSE_FULL_GUIDED_CSV_MAX_ROWS

    def test_build_boq_structured_summary_full_mode_uses_preview_guidance_for_csv_strong(
        self,
    ):
        from app.main import (
            DEFAULT_BOQ_PARSE_FULL_GUIDED_STRONG_CSV_MAX_ROWS,
            _build_boq_structured_summary,
        )

        rows = ["项目编码,项目名称,单位,工程量,综合单价,合价"]
        rows.extend(
            f"{idx:09d},清单项{idx},m3,{idx},{10 + idx},{(10 + idx) * idx}"
            for idx in range(1, 2205)
        )
        preview_summary = {
            "parse_stage": "preview",
            "total_parsed_items": 64,
            "sheets": [{"sheet": "csv", "parsed_items": 64, "detected_columns": {"code": 0}}],
        }

        summary = _build_boq_structured_summary(
            "\n".join(rows).encode("utf-8"),
            "heavy_boq.csv",
            parsed_text="工程量清单 full parse",
            prior_summary=preview_summary,
        )

        assert summary["scan_strategy"] == "preview_guided_full"
        assert summary["scan_guidance_strength"] == "strong"
        assert (
            summary["sheets"][0]["row_scan_budget"]
            == DEFAULT_BOQ_PARSE_FULL_GUIDED_STRONG_CSV_MAX_ROWS
        )
        assert (
            summary["sheets"][0]["scanned_rows"]
            == DEFAULT_BOQ_PARSE_FULL_GUIDED_STRONG_CSV_MAX_ROWS
        )

    def test_build_boq_structured_summary_full_mode_resumes_xlsx_from_preview_rows(
        self,
    ):
        from app.main import _build_boq_structured_summary

        class _FakeSheet:
            def __init__(self, title, rows):
                self.title = title
                self._rows = rows
                self.min_row_calls = []

            def iter_rows(self, values_only=True, min_row=1):
                assert values_only is True
                self.min_row_calls.append(min_row)
                for row in self._rows[min_row - 1 :]:
                    yield row

        class _FakeWorkbook:
            def __init__(self, worksheets):
                self.worksheets = worksheets

            def close(self):
                return None

        heavy_rows = [("项目编码", "项目名称", "单位", "工程量", "综合单价", "合价")]
        heavy_rows.extend(
            (f"{idx:09d}", f"清单项{idx}", "m3", idx, 10 + idx, (10 + idx) * idx)
            for idx in range(1, 520)
        )
        sheet = _FakeSheet("分部分项工程量清单", heavy_rows)
        fake_wb = _FakeWorkbook([sheet])
        preview_summary = {
            "parse_stage": "preview",
            "total_parsed_items": 179,
            "sheets": [
                {
                    "sheet": "分部分项工程量清单",
                    "scanned_rows": 180,
                    "parsed_items": 179,
                    "detected_columns": {
                        "code": 0,
                        "name": 1,
                        "unit": 2,
                        "quantity": 3,
                        "amount": 5,
                    },
                    "quantity_sum": 16110.0,
                    "amount_sum": 2573910.0,
                    "quantity_rows": 179,
                    "amount_rows": 179,
                    "units": ["m3"],
                    "top_items_by_amount": [],
                }
            ],
        }
        fake_openpyxl = SimpleNamespace(load_workbook=lambda *args, **kwargs: fake_wb)

        with patch.dict(sys.modules, {"openpyxl": fake_openpyxl}):
            summary = _build_boq_structured_summary(
                b"fake-xlsx",
                "heavy_boq.xlsx",
                parsed_text="工程量清单 full parse",
                prior_summary=preview_summary,
            )

        assert sheet.min_row_calls == [181]
        assert summary["sheets"][0]["resumed_from_prior_summary"] is True
        assert summary["sheets"][0]["resume_from_row"] == 181

    def test_build_boq_structured_summary_full_mode_resumes_csv_from_preview_rows(
        self,
    ):
        from app.main import (
            DEFAULT_BOQ_PARSE_FULL_GUIDED_STRONG_CSV_MAX_ROWS,
            _build_boq_structured_summary,
        )

        rows = ["项目编码,项目名称,单位,工程量,综合单价,合价"]
        rows.extend(
            f"{idx:09d},清单项{idx},m3,{idx},{10 + idx},{(10 + idx) * idx}"
            for idx in range(1, 2205)
        )
        preview_summary = {
            "parse_stage": "preview",
            "total_parsed_items": 179,
            "sheets": [
                {
                    "sheet": "csv",
                    "scanned_rows": 180,
                    "parsed_items": 179,
                    "detected_columns": {
                        "code": 0,
                        "name": 1,
                        "unit": 2,
                        "quantity": 3,
                        "amount": 5,
                    },
                    "quantity_sum": 16110.0,
                    "amount_sum": 2573910.0,
                    "quantity_rows": 179,
                    "amount_rows": 179,
                    "units": ["m3"],
                    "top_items_by_amount": [],
                }
            ],
        }

        summary = _build_boq_structured_summary(
            "\n".join(rows).encode("utf-8"),
            "heavy_boq.csv",
            parsed_text="工程量清单 full parse",
            prior_summary=preview_summary,
        )

        assert summary["scan_guidance_strength"] == "strong"
        assert summary["sheets"][0]["resumed_from_prior_summary"] is True
        assert summary["sheets"][0]["resume_from_row"] == 181
        assert (
            summary["sheets"][0]["scanned_rows"]
            == DEFAULT_BOQ_PARSE_FULL_GUIDED_STRONG_CSV_MAX_ROWS
        )

    def test_extract_boq_tabular_resume_text_skips_previewed_rows_for_csv(self):
        from app.main import _extract_boq_tabular_resume_text

        rows = ["项目编码,项目名称,单位,工程量,综合单价,合价"]
        rows.extend(
            f"{idx:09d},清单项{idx},m3,{idx},{10 + idx},{(10 + idx) * idx}" for idx in range(1, 260)
        )
        resume_text = _extract_boq_tabular_resume_text(
            "\n".join(rows).encode("utf-8"),
            "heavy_boq.csv",
            prior_summary={
                "parse_stage": "preview",
                "total_parsed_items": 179,
                "sheets": [
                    {
                        "sheet": "csv",
                        "scanned_rows": 180,
                        "parsed_items": 179,
                        "detected_columns": {"code": 0},
                    }
                ],
            },
            max_sheets=4,
            max_rows_per_sheet=600,
        )

        non_empty_lines = [line for line in resume_text.splitlines() if line.strip()]
        assert non_empty_lines[0].startswith("000000180\t清单项180")
        assert "000000001\t清单项1\t" not in resume_text

    @patch("app.main._read_uploaded_file_content_for_parse_mode")
    @patch("app.main._build_boq_full_parse_text")
    @patch("app.main.read_bytes")
    def test_parse_material_record_payload_uses_boq_resume_text_in_full_mode(
        self,
        mock_read_bytes,
        mock_build_boq_full_parse_text,
        mock_read_uploaded_file_content_for_parse_mode,
    ):
        from app.main import _parse_material_record_payload

        mock_read_bytes.return_value = b"fake-boq-content"
        mock_build_boq_full_parse_text.return_value = "preview-head\nresume-tail"

        with tempfile.NamedTemporaryFile(suffix=".xlsx") as handle:
            _parse_material_record_payload(
                {
                    "id": "m3",
                    "project_id": "p1",
                    "material_type": "boq",
                    "filename": "工程量清单.xlsx",
                    "path": handle.name,
                    "parsed_text": "preview-head",
                    "boq_structured_summary": {
                        "parse_stage": "preview",
                        "total_parsed_items": 24,
                        "sheets": [{"sheet": "分部分项工程量清单", "parsed_items": 24}],
                    },
                },
                parse_mode="full",
            )

        mock_build_boq_full_parse_text.assert_called_once()
        mock_read_uploaded_file_content_for_parse_mode.assert_not_called()

    @patch("app.main.read_bytes")
    def test_parse_material_record_payload_marks_boq_preview_as_not_gate_ready(
        self, mock_read_bytes
    ):
        from app.main import _parse_material_record_payload

        csv_content = (
            "项目编码,项目名称,单位,工程量,综合单价,合价\n"
            "010101001,土方开挖,m3,100,35.5,3550\n"
            "010201001,钢筋制作,t,12.5,4300,53750\n"
        ).encode("utf-8")
        mock_read_bytes.return_value = csv_content
        with tempfile.NamedTemporaryFile(suffix=".csv") as handle:
            payload = _parse_material_record_payload(
                {
                    "id": "m1",
                    "project_id": "p1",
                    "material_type": "boq",
                    "filename": "工程量清单.csv",
                    "path": handle.name,
                },
                parse_mode="preview",
            )

            assert payload["parse_backend"] == "local_preview"
            assert payload["parse_phase"] == "preview"
            assert payload["parse_ready_for_gate"] is False
            assert payload["boq_structured_summary"]["parse_stage"] == "preview"

    @patch("app.main._build_boq_full_parse_text")
    @patch("app.main._build_boq_structured_summary")
    @patch("app.main._read_uploaded_file_content_for_parse_mode")
    @patch("app.main.read_bytes")
    def test_parse_material_record_payload_passes_preview_boq_summary_into_full_parse(
        self,
        mock_read_bytes,
        mock_read_uploaded_file_content_for_parse_mode,
        mock_build_boq_structured_summary,
        mock_build_boq_full_parse_text,
    ):
        from app.main import _parse_material_record_payload

        mock_read_bytes.return_value = b"fake-boq-content"
        mock_build_boq_full_parse_text.return_value = "boq full excerpt"
        preview_summary = {
            "parse_stage": "preview",
            "total_parsed_items": 24,
            "sheets": [{"sheet": "分部分项工程量清单", "parsed_items": 24}],
        }
        mock_build_boq_structured_summary.return_value = {
            "structured_quality_score": 0.7,
            "sheets": [],
            "total_parsed_items": 0,
        }

        with tempfile.NamedTemporaryFile(suffix=".xlsx") as handle:
            _parse_material_record_payload(
                {
                    "id": "m2",
                    "project_id": "p1",
                    "material_type": "boq",
                    "filename": "工程量清单.xlsx",
                    "path": handle.name,
                    "parse_phase": "preview",
                    "parse_ready_for_gate": False,
                    "boq_structured_summary": preview_summary,
                },
                parse_mode="full",
            )

        mock_build_boq_full_parse_text.assert_called_once()
        mock_read_uploaded_file_content_for_parse_mode.assert_not_called()
        mock_build_boq_structured_summary.assert_called_once()
        assert (
            mock_build_boq_structured_summary.call_args.kwargs["prior_summary"] == preview_summary
        )

    @patch("app.main._read_uploaded_file_content")
    @patch("app.main._extract_boq_tabular_preview_text")
    def test_read_uploaded_file_content_for_parse_mode_uses_boq_excerpt_in_full_mode(
        self,
        mock_extract_boq_excerpt,
        mock_read_uploaded_file_content,
    ):
        from app.main import (
            DEFAULT_MATERIAL_PARSE_TEXT_MAX_ROWS_BY_TYPE,
            DEFAULT_MATERIAL_PARSE_TEXT_MAX_SHEETS_BY_TYPE,
            _read_uploaded_file_content_for_parse_mode,
        )

        mock_extract_boq_excerpt.return_value = "boq-full-excerpt"

        result = _read_uploaded_file_content_for_parse_mode(
            b"fake-boq-content",
            "工程量清单.xlsx",
            material_type="boq",
            parse_mode="full",
        )

        assert result == "boq-full-excerpt"
        mock_extract_boq_excerpt.assert_called_once_with(
            b"fake-boq-content",
            "工程量清单.xlsx",
            max_sheets=DEFAULT_MATERIAL_PARSE_TEXT_MAX_SHEETS_BY_TYPE["boq"],
            max_rows_per_sheet=DEFAULT_MATERIAL_PARSE_TEXT_MAX_ROWS_BY_TYPE["boq"],
        )
        mock_read_uploaded_file_content.assert_not_called()

    def test_build_tender_qa_structured_summary_extracts_constraint_tags(self):
        from app.main import _build_tender_qa_structured_summary

        parsed_text = (
            "第一章 施工组织设计总体部署\n"
            "第二章 质量管理与验收标准\n"
            "招标范围包含装修及机电改造，答疑澄清明确总工期120日历天。\n"
            "评分办法要求体现BIM深化、危大工程专项方案、绿色施工与质量验收标准。\n"
            "投标文件必须响应关键节点，不得缺少专项方案。"
        )
        summary = _build_tender_qa_structured_summary(
            b"TENDERDATA",
            "招标答疑.pdf",
            parsed_text=parsed_text,
        )
        assert "工期/里程碑" in (summary.get("constraint_tags") or [])
        assert "信息化/BIM" in (summary.get("constraint_tags") or [])
        assert "危大工程/专项方案" in (summary.get("constraint_tags") or [])
        assert "120" in (summary.get("top_numeric_terms") or [])
        assert "09" in (summary.get("focused_dimensions") or [])
        assert "05" in (summary.get("focused_dimensions") or [])
        assert "施工组织设计总体部署" in (summary.get("section_titles") or [])
        assert "bim" in [str(x).lower() for x in (summary.get("scoring_point_terms") or [])]
        assert summary["structured_quality_score"] > 0
        assert any(
            "必须" in str(x) or "不得" in str(x)
            for x in (summary.get("mandatory_clause_terms") or [])
        )

    def test_extract_tender_qa_section_titles_tolerates_ocr_spacing(self):
        from app.main import _extract_tender_qa_section_titles

        parsed_text = (
            "第 一 章 施 工 组 织 设 计 总 体 部 署\n"
            "1 . 2 质 量 管 理 与 验 收 标 准\n"
            "二 、 进 度 计 划 与 节 点 控 制\n"
        )
        titles = _extract_tender_qa_section_titles(parsed_text, limit=6)
        assert any("施工组织设计总体部署" in item for item in titles)
        assert any("质量管理与验收标准" in item for item in titles)
        assert any("进度计划与节点控制" in item for item in titles)

    def test_filter_structured_signal_terms_prefers_material_specific_terms(self):
        from app.main import _filter_structured_signal_terms

        terms = _filter_structured_signal_terms(
            [
                "文件",
                "资料",
                "text",
                "layer1",
                "综合管线",
                "综合管线净高复核",
                "节点详图",
                "A1",
                "BIM",
            ],
            limit=6,
            material_type="drawing",
        )

        assert "综合管线净高复核" in terms
        assert "节点详图" in terms
        assert "BIM" in terms
        assert "文件" not in terms
        assert "资料" not in terms
        assert "layer1" not in terms
        assert "综合管线" not in terms

    def test_build_drawing_structured_summary_extracts_structured_signals(self):
        from app.main import _build_drawing_structured_summary

        parsed_text = (
            "[DWG预处理] 文件: 总图.dwg\n"
            "实体统计: LINE:12、TEXT:3、INSERT:2\n"
            "图层: A-ANNO-TEXT、M-EQPM、P-PIPE\n"
            "块参照: DOOR_TAG、PUMP_TAG\n"
            "布局/空间: 首层平面、屋面层\n"
            "标注值: 600、3500\n"
            "二进制标识提取: PLAN_VIEW、MEP_ROUTE\n"
            "节点 深化 BIM 综合管线 碰撞 净高 预留预埋 消防 管径 600 3.5"
        )
        summary = _build_drawing_structured_summary(b"DWGDATA", "总图.dwg", parsed_text=parsed_text)
        assert summary["detected_format"] == "dwg"
        assert "A-ANNO-TEXT" in (summary.get("top_layers") or [])
        assert "首层平面" in (summary.get("layout_tags") or [])
        assert "3500" in (summary.get("dimension_markers") or [])
        assert "PLAN_VIEW" in (summary.get("binary_marker_terms") or [])
        assert "机电综合" in (summary.get("discipline_keywords") or [])
        assert "总平面/平面布置" in (summary.get("sheet_type_tags") or [])
        assert "专业碰撞" in (summary.get("risk_keywords") or [])
        assert "14" in (summary.get("focused_dimensions") or [])
        assert "12" in (summary.get("focused_dimensions") or [])
        assert "600" in (summary.get("top_numeric_terms") or [])
        assert summary["structured_quality_score"] > 0

    def test_build_site_photo_structured_summary_extracts_scene_tags(self):
        from app.main import _build_site_photo_structured_summary

        parsed_text = (
            "[图像资料] 文件: 现场.jpg\n"
            "格式: JPEG\n"
            "OCR模式: gray_2x:psm6\n"
            "OCR质量分: 4.8\n"
            "[OCR文本提取]\n"
            "临边防护 扬尘治理 围挡 道路冲洗 样板 实测 夜间施工 材料进场 48 3"
        )
        summary = _build_site_photo_structured_summary(
            b"IMGDATA",
            "现场.jpg",
            parsed_text=parsed_text,
        )
        assert summary["visual_capability"] == "ocr_multistage"
        assert summary["ocr_mode"] == "gray_2x:psm6"
        assert float(summary["ocr_quality_score"]) > 0
        assert "高处临边" in (summary.get("safety_scene_tags") or [])
        assert "扬尘治理" in (summary.get("civilization_scene_tags") or [])
        assert "样板实测" in (summary.get("quality_scene_tags") or [])
        assert "夜间施工" in (summary.get("progress_scene_tags") or [])
        assert "03" in (summary.get("focused_dimensions") or [])
        assert "09" in (summary.get("focused_dimensions") or [])
        assert summary["structured_quality_score"] > 0

    @patch("app.main.load_materials")
    def test_merge_materials_text_includes_tender_structured_summary(
        self, mock_load_materials, tmp_path
    ):
        from app.main import _merge_materials_text

        tender_path = tmp_path / "招标答疑.txt"
        tender_path.write_text(
            "答疑澄清：总工期120日历天，评分办法要求BIM深化和专项方案。",
            encoding="utf-8",
        )
        mock_load_materials.return_value = [
            {
                "project_id": "p1",
                "material_type": "tender_qa",
                "filename": "招标答疑.txt",
                "path": str(tender_path),
                "created_at": "2026-02-26T00:00:01+00:00",
            }
        ]

        merged = _merge_materials_text("p1")
        assert "[招标答疑结构化摘要]" in merged
        assert "工期/里程碑" in merged

    @patch("app.main.shutil.which")
    def test_read_uploaded_file_content_dwg_uses_preprocess_chain(self, mock_which):
        from app.main import _read_uploaded_file_content

        mock_which.return_value = None
        text = _read_uploaded_file_content(b"DWGDATA", "sample.dwg")
        assert "[DWG图纸]" in text
        assert "DWG预处理" in text
        assert "建议同时上传 PDF 或 ASCII DXF" in text

    @patch("app.main.shutil.which")
    def test_resolve_dwg_converter_binaries_uses_env_and_defaults(self, mock_which):
        from app.main import _resolve_dwg_converter_binaries

        def _fake_which(name: str):
            mapping = {
                "my_dwg_converter": "/opt/tools/my_dwg_converter",
                "dwg2dxf": "/usr/local/bin/dwg2dxf",
            }
            return mapping.get(name)

        mock_which.side_effect = _fake_which
        with patch.dict(os.environ, {"DWG_CONVERTER_BIN": "my_dwg_converter"}, clear=False):
            bins = _resolve_dwg_converter_binaries()
        assert "/opt/tools/my_dwg_converter" in bins
        assert "/usr/local/bin/dwg2dxf" in bins

    def test_read_uploaded_file_content_dwg_detects_ascii_dxf_payload(self):
        from app.main import _read_uploaded_file_content

        dxf_content = (
            "0\nSECTION\n2\nHEADER\n9\n$ACADVER\n1\nAC1032\n0\nENDSEC\n"
            "0\nSECTION\n2\nENTITIES\n0\nTEXT\n8\nA-TEXT\n1\n机电综合节点\n0\nENDSEC\n0\nEOF\n"
        ).encode("utf-8")
        text = _read_uploaded_file_content(dxf_content, "looks_like.dwg")
        assert "检测到ASCII DXF内容" in text
        assert "DXF解析摘要" in text

    def test_build_material_consistency_requirements_from_retrieval_chunks(self):
        from app.main import _build_material_consistency_requirements

        retrieval_chunks = [
            {
                "material_type": "boq",
                "dimension_id": "13",
                "filename": "清单.xlsx",
                "chunk_id": "清单.xlsx#c001",
                "matched_terms": ["工程量", "综合单价", "措施费"],
                "chunk_preview": "工程量与综合单价控制要求。",
            },
            {
                "material_type": "drawing",
                "dimension_id": "14",
                "filename": "总图.dxf",
                "chunk_id": "总图.dxf#c002",
                "matched_terms": ["节点", "剖面", "深化"],
                "chunk_preview": "节点做法与剖面表达。",
            },
        ]
        reqs = _build_material_consistency_requirements("p1", retrieval_chunks)
        assert len(reqs) == 2
        boq_req = next(r for r in reqs if r.get("material_type") == "boq")
        assert boq_req.get("req_type") == "material_consistency"
        assert boq_req.get("source_pack_id") == "runtime_material_consistency"

    def test_build_material_consistency_requirements_fallback_for_available_types(self):
        from app.main import _build_material_consistency_requirements

        reqs = _build_material_consistency_requirements(
            "p1",
            [],
            available_material_types=["tender_qa", "boq", "drawing"],
        )
        assert len(reqs) == 3
        boq_req = next(r for r in reqs if r.get("material_type") == "boq")
        terms = (boq_req.get("patterns") or {}).get("must_hit_terms") or []
        assert "工程量" in terms
        assert (boq_req.get("patterns") or {}).get("source_mode") == "fallback_keywords"

    def test_build_material_consistency_requirements_includes_numeric_constraints(self):
        from app.main import _build_material_consistency_requirements

        retrieval_chunks = [
            {
                "material_type": "tender_qa",
                "dimension_id": "09",
                "filename": "答疑.txt",
                "chunk_id": "答疑.txt#c001",
                "matched_terms": ["工期", "节点"],
                "matched_numeric_terms": ["120", "30"],
                "chunk_preview": "总工期120日历天，里程碑节点30天。",
            }
        ]
        reqs = _build_material_consistency_requirements("p1", retrieval_chunks)
        tender_req = next(r for r in reqs if r.get("material_type") == "tender_qa")
        patterns = tender_req.get("patterns") or {}
        numbers = patterns.get("must_hit_numbers") or []
        assert "120" in numbers
        assert int(patterns.get("minimum_numbers") or 0) >= 1

    def test_build_material_consistency_requirements_relaxes_drawing_numeric_requirement(self):
        from app.main import _build_material_consistency_requirements

        retrieval_chunks = [
            {
                "material_type": "drawing",
                "dimension_id": "14",
                "filename": "排水工程-延边路.pdf",
                "chunk_id": "排水工程-延边路.pdf#c001",
                "matched_terms": ["图纸"],
                "matched_file_anchor_terms": ["排水工程", "照明工程设计说明"],
                "matched_numeric_terms": ["28"],
                "chunk_preview": "排水工程设计说明与节点做法。",
            }
        ]
        reqs = _build_material_consistency_requirements("p1", retrieval_chunks)
        drawing_req = next(r for r in reqs if r.get("material_type") == "drawing")
        patterns = drawing_req.get("patterns") or {}
        terms = patterns.get("must_hit_terms") or []
        assert "排水工程" in terms
        assert int(patterns.get("minimum_terms") or 0) == 1
        assert int(patterns.get("minimum_numbers") or 0) == 0

    def test_build_material_retrieval_requirements_keeps_drawing_anchor_terms(self):
        from app.main import _build_material_retrieval_requirements

        reqs = _build_material_retrieval_requirements(
            "p1",
            [
                {
                    "material_type": "drawing",
                    "dimension_id": "14",
                    "filename": "排水工程-延边路.pdf",
                    "chunk_id": "排水工程-延边路.pdf#c001",
                    "matched_terms": ["图纸"],
                    "matched_file_anchor_terms": ["排水工程", "照明工程设计说明"],
                    "chunk_preview": "排水工程设计说明与节点做法。",
                }
            ],
        )
        assert len(reqs) == 1
        patterns = reqs[0].get("patterns") or {}
        hints = patterns.get("hints") or []
        assert "排水工程" in hints
        assert int(patterns.get("minimum_hint_hits") or 0) == 1

    @patch("app.main.load_materials")
    @patch("app.main._read_uploaded_file_content")
    def test_select_material_retrieval_chunks_keeps_type_diversity(
        self, mock_read_uploaded_file_content, mock_load_materials
    ):
        from app.main import _select_material_retrieval_chunks

        with tempfile.TemporaryDirectory() as tmp:
            tender = Path(tmp) / "tender.txt"
            boq = Path(tmp) / "boq.txt"
            drawing = Path(tmp) / "drawing.txt"
            tender.write_text("答疑 工期 质量标准 节点 闭环", encoding="utf-8")
            boq.write_text("工程量 综合单价 措施费", encoding="utf-8")
            drawing.write_text("图纸 节点 剖面 深化 BIM", encoding="utf-8")
            mock_load_materials.return_value = [
                {
                    "project_id": "p1",
                    "material_type": "tender_qa",
                    "filename": "tender.txt",
                    "path": str(tender),
                    "created_at": "2026-02-26T00:00:01+00:00",
                },
                {
                    "project_id": "p1",
                    "material_type": "boq",
                    "filename": "boq.txt",
                    "path": str(boq),
                    "created_at": "2026-02-26T00:00:02+00:00",
                },
                {
                    "project_id": "p1",
                    "material_type": "drawing",
                    "filename": "drawing.txt",
                    "path": str(drawing),
                    "created_at": "2026-02-26T00:00:03+00:00",
                },
            ]
            mock_read_uploaded_file_content.side_effect = lambda _content, filename: Path(
                tmp, filename
            ).read_text(encoding="utf-8")
            chunks = _select_material_retrieval_chunks("p1", "工期 节点 工程量", top_k=6)
            types = {str(c.get("material_type")) for c in chunks}
            assert {"tender_qa", "boq", "drawing"}.issubset(types)

    @patch("app.main.load_materials")
    @patch("app.main._split_material_text_chunks")
    @patch("app.main._read_uploaded_file_content")
    def test_select_material_retrieval_chunks_respects_per_type_quota(
        self, mock_read_uploaded_file_content, mock_split_chunks, mock_load_materials
    ):
        from app.main import _select_material_retrieval_chunks

        with tempfile.TemporaryDirectory() as tmp:
            tender = Path(tmp) / "tender.txt"
            boq = Path(tmp) / "boq.txt"
            drawing = Path(tmp) / "drawing.txt"
            tender.write_text(
                "答疑 工期 节点 质量。\n\n招标条件 工期 质量 节点 约束。",
                encoding="utf-8",
            )
            boq.write_text(
                "工程量 综合单价 措施费。\n\n工程量 清单 措施项目 计量规则。",
                encoding="utf-8",
            )
            drawing.write_text(
                "图纸 节点 剖面 深化。\n\nBIM 节点 深化 剖面 图纸。",
                encoding="utf-8",
            )
            mock_load_materials.return_value = [
                {
                    "project_id": "p1",
                    "material_type": "tender_qa",
                    "filename": "tender.txt",
                    "path": str(tender),
                    "created_at": "2026-02-26T00:00:01+00:00",
                },
                {
                    "project_id": "p1",
                    "material_type": "boq",
                    "filename": "boq.txt",
                    "path": str(boq),
                    "created_at": "2026-02-26T00:00:02+00:00",
                },
                {
                    "project_id": "p1",
                    "material_type": "drawing",
                    "filename": "drawing.txt",
                    "path": str(drawing),
                    "created_at": "2026-02-26T00:00:03+00:00",
                },
            ]
            mock_read_uploaded_file_content.side_effect = lambda _content, filename: Path(
                tmp, filename
            ).read_text(encoding="utf-8")
            mock_split_chunks.return_value = [
                "工期 节点 工程量 质量 安全",
                "BIM 节点 进度 工程量 清单",
            ]
            chunks = _select_material_retrieval_chunks(
                "p1",
                "工期 节点 工程量",
                top_k=9,
                per_type_quota=2,
            )
            by_type = Counter(str(c.get("material_type")) for c in chunks)
            assert by_type["tender_qa"] >= 2
            assert by_type["boq"] >= 2
            assert by_type["drawing"] >= 2

    @patch("app.main.load_materials")
    @patch("app.main._split_material_text_chunks")
    @patch("app.main._read_uploaded_file_content")
    def test_select_material_retrieval_chunks_respects_per_file_quota(
        self, mock_read_uploaded_file_content, mock_split_chunks, mock_load_materials
    ):
        from app.main import _select_material_retrieval_chunks

        with tempfile.TemporaryDirectory() as tmp:
            tender = Path(tmp) / "tender.txt"
            boq = Path(tmp) / "boq.txt"
            drawing = Path(tmp) / "drawing.txt"
            tender.write_text("答疑 工期 节点 质量", encoding="utf-8")
            boq.write_text("工程量 综合单价 措施费", encoding="utf-8")
            drawing.write_text("图纸 节点 剖面 深化", encoding="utf-8")
            mock_load_materials.return_value = [
                {
                    "project_id": "p1",
                    "material_type": "tender_qa",
                    "filename": "tender.txt",
                    "path": str(tender),
                    "created_at": "2026-02-26T00:00:01+00:00",
                },
                {
                    "project_id": "p1",
                    "material_type": "boq",
                    "filename": "boq.txt",
                    "path": str(boq),
                    "created_at": "2026-02-26T00:00:02+00:00",
                },
                {
                    "project_id": "p1",
                    "material_type": "drawing",
                    "filename": "drawing.txt",
                    "path": str(drawing),
                    "created_at": "2026-02-26T00:00:03+00:00",
                },
            ]
            mock_read_uploaded_file_content.side_effect = lambda _content, filename: Path(
                tmp, filename
            ).read_text(encoding="utf-8")
            mock_split_chunks.return_value = [
                "工期 节点 工程量 质量 安全",
                "BIM 节点 进度 工程量 清单",
                "剖面 节点 深化 质量 施工",
            ]
            chunks = _select_material_retrieval_chunks(
                "p1",
                "工期 节点 工程量",
                top_k=9,
                per_type_quota=2,
                per_file_quota=1,
            )
            by_filename = Counter(str(c.get("filename")) for c in chunks)
            assert all(cnt <= 1 for cnt in by_filename.values())
            assert len(by_filename) == 3

    @patch("app.main.load_materials")
    @patch("app.main._split_material_text_chunks")
    @patch("app.main._read_uploaded_file_content")
    def test_select_material_retrieval_chunks_supports_numeric_query_hits(
        self, mock_read_uploaded_file_content, mock_split_chunks, mock_load_materials
    ):
        from app.main import _select_material_retrieval_chunks

        with tempfile.TemporaryDirectory() as tmp:
            boq = Path(tmp) / "boq.txt"
            boq.write_text("120 30 8000", encoding="utf-8")
            mock_load_materials.return_value = [
                {
                    "project_id": "p1",
                    "material_type": "boq",
                    "filename": "boq.txt",
                    "path": str(boq),
                    "created_at": "2026-02-26T00:00:02+00:00",
                },
            ]
            mock_read_uploaded_file_content.return_value = boq.read_text(encoding="utf-8")
            mock_split_chunks.return_value = ["120 30 8000"]
            chunks = _select_material_retrieval_chunks(
                "p1",
                "120 30",
                top_k=3,
                per_type_quota=1,
                per_file_quota=2,
                query_terms_extra=[],
                query_numeric_terms=["120", "30"],
            )
            assert chunks
            numeric_hits = chunks[0].get("matched_numeric_terms") or []
            assert "120" in numeric_hits or "30" in numeric_hits

    def test_select_material_retrieval_chunks_prefers_high_quality_structured_file(self):
        from app.main import _select_material_retrieval_chunks

        material_index = {
            "available_types": ["drawing"],
            "files": [
                {
                    "material_type": "drawing",
                    "filename": "low_quality.dwg",
                    "parsed_ok": True,
                    "chunks": ["综合管线 节点 深化"],
                    "drawing_structured_summary": {
                        "structured_quality_score": 0.15,
                        "structured_terms": ["综合管线", "文件", "资料"],
                    },
                },
                {
                    "material_type": "drawing",
                    "filename": "high_quality.dwg",
                    "parsed_ok": True,
                    "chunks": ["综合管线净高复核 节点详图 BIM 深化"],
                    "drawing_structured_summary": {
                        "structured_quality_score": 0.88,
                        "structured_terms": ["综合管线净高复核", "节点详图", "BIM"],
                    },
                },
            ],
        }

        chunks = _select_material_retrieval_chunks(
            "p1",
            "综合管线 节点 BIM",
            top_k=1,
            per_type_quota=1,
            per_file_quota=1,
            material_index=material_index,
        )

        assert chunks
        assert chunks[0]["filename"] == "high_quality.dwg"
        assert float(chunks[0].get("file_structured_quality") or 0.0) >= 0.88

    def test_select_material_retrieval_chunks_uses_file_level_deep_read_anchors(self):
        from app.main import _select_material_retrieval_chunks

        material_index = {
            "available_types": ["tender_qa"],
            "files": [
                {
                    "material_type": "tender_qa",
                    "filename": "generic.pdf",
                    "parsed_ok": True,
                    "chunks": ["详见附件执行。"],
                    "tender_qa_structured_summary": {
                        "structured_quality_score": 0.72,
                        "structured_terms": ["一般要求"],
                    },
                },
                {
                    "material_type": "tender_qa",
                    "filename": "anchor.pdf",
                    "parsed_ok": True,
                    "chunks": ["详见附表执行。"],
                    "tender_qa_structured_summary": {
                        "structured_quality_score": 0.9,
                        "structured_terms": ["专项方案"],
                        "section_title_paths": ["第一章", "危大工程专项方案"],
                        "document_outline": [
                            {
                                "page_no": 2,
                                "page_type": "scoring_rules",
                                "section_title": "危大工程专项方案",
                                "section_level": 1,
                                "section_path": ["第一章", "危大工程专项方案"],
                            }
                        ],
                        "table_constraint_rows": [
                            {
                                "page_no": 3,
                                "label": "专项方案响应表",
                                "value": "危大工程需逐项响应",
                                "numbers": ["3"],
                            }
                        ],
                        "table_numeric_constraints": ["3"],
                        "page_type_summary": [{"page_type": "scoring_rules", "count": 1}],
                    },
                },
            ],
        }

        chunks = _select_material_retrieval_chunks(
            "p1",
            "危大工程专项方案 响应表",
            top_k=1,
            per_type_quota=1,
            per_file_quota=1,
            material_index=material_index,
        )

        assert chunks
        assert chunks[0]["filename"] == "anchor.pdf"
        assert "危大工程专项方案" in " ".join(chunks[0].get("matched_terms") or [])
        assert chunks[0].get("matched_file_anchor_terms")

    def test_build_material_utilization_summary_reports_uncovered_types(self):
        from app.main import _build_material_utilization_summary

        report = {
            "requirement_hits": [
                {
                    "source_pack_id": "runtime_material_rag",
                    "material_type": "tender_qa",
                    "source_mode": "type_quota",
                    "source_filename": "tender.txt",
                    "chunk_id": "tender.txt#c001",
                    "hit": True,
                    "label": "资料检索证据：招标文件和答疑 / tender.txt / c1",
                },
                {
                    "source_pack_id": "runtime_material_consistency",
                    "material_type": "boq",
                    "source_mode": "fallback_keywords",
                    "hit": False,
                    "label": "跨资料一致性：施组需体现清单关键约束",
                },
            ]
        }
        runtime_meta = {
            "material_available_types": ["tender_qa", "boq", "drawing"],
            "material_retrieval_selected_filenames": ["tender.txt", "boq.xlsx", "drawing.pdf"],
            "material_retrieval_selected_via_counts": {
                "type_quota": 1,
                "type_backfill": 2,
            },
            "material_retrieval_top_k": 18,
            "material_retrieval_per_type_quota": 2,
            "material_retrieval_per_file_quota": 3,
            "material_retrieval_base_top_k": 12,
            "material_retrieval_base_per_type_quota": 1,
            "material_retrieval_base_per_file_quota": 2,
            "material_retrieval_budget_reasons": ["top_k:12->18", "per_type_quota:1->2"],
            "material_total_size_mb": 12.5,
            "material_type_count": 3,
            "material_available_files": 3,
        }
        summary = _build_material_utilization_summary(report, runtime_meta)
        assert summary["retrieval_total"] == 1
        assert summary["retrieval_hit"] == 1
        assert summary["retrieval_file_total"] == 3
        assert summary["retrieval_file_hit"] == 1
        assert summary["retrieval_file_coverage_rate"] == pytest.approx(0.3333, abs=1e-4)
        assert summary["retrieval_unhit_file_count"] == 2
        assert "boq.xlsx" in (summary.get("retrieval_unhit_filenames") or [])
        assert "boq" in (summary.get("uncovered_types") or [])
        assert "drawing" in (summary.get("uncovered_types") or [])
        assert summary["fallback_total"] == 1
        assert summary["fallback_hit"] == 0
        assert summary["retrieval_selected_via_counts"] == {"type_quota": 1, "type_backfill": 2}
        assert summary["retrieval_total_via_counts"]["type_quota"] == 1
        assert summary["retrieval_hit_via_counts"]["type_quota"] == 1
        assert summary["retrieval_top_k"] == 18
        assert summary["retrieval_base_top_k"] == 12

    def test_compute_dynamic_retrieval_budget_scales_with_material_volume(self):
        from app.main import _compute_dynamic_retrieval_budget

        with tempfile.TemporaryDirectory() as tmp:
            rows = []
            material_types = ["tender_qa", "boq", "drawing", "site_photo"]
            for idx in range(8):
                p = Path(tmp) / f"m{idx}.txt"
                p.write_text("x" * 200_000, encoding="utf-8")
                rows.append(
                    {
                        "project_id": "p1",
                        "material_type": material_types[idx % len(material_types)],
                        "filename": p.name,
                        "path": str(p),
                        "created_at": "2026-02-26T00:00:00+00:00",
                    }
                )

            budget = _compute_dynamic_retrieval_budget(
                "p1",
                {"top_k": 12, "per_type_quota": 1, "per_file_quota": 2},
                rows,
                available_material_types=material_types,
            )
            assert budget["top_k"] >= 12
            assert budget["per_type_quota"] >= 2
            assert budget["per_file_quota"] >= 3
            assert budget["material_file_count"] == 8
            assert budget["material_type_count"] == 4
            assert budget["material_total_size_mb"] > 0

    def test_compute_dynamic_retrieval_budget_keeps_base_for_small_inputs(self):
        from app.main import _compute_dynamic_retrieval_budget

        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "a.txt"
            p.write_text("x" * 20, encoding="utf-8")
            rows = [
                {
                    "project_id": "p1",
                    "material_type": "tender_qa",
                    "filename": "a.txt",
                    "path": str(p),
                    "created_at": "2026-02-26T00:00:00+00:00",
                }
            ]
            budget = _compute_dynamic_retrieval_budget(
                "p1",
                {"top_k": 14, "per_type_quota": 2, "per_file_quota": 3},
                rows,
                available_material_types=["tender_qa"],
            )
            assert budget["top_k"] == 14
            assert budget["per_type_quota"] == 2
            assert budget["per_file_quota"] == 3
            assert "base_budget_kept" in (budget.get("budget_reasons") or [])

    def test_aggregate_material_utilization_summaries(self):
        from app.main import _aggregate_material_utilization_summaries

        merged = _aggregate_material_utilization_summaries(
            [
                {
                    "retrieval_total": 2,
                    "retrieval_hit": 1,
                    "retrieval_file_total": 2,
                    "retrieval_file_hit": 1,
                    "retrieval_selected_filenames": ["a.txt", "b.txt"],
                    "retrieval_hit_filenames": ["a.txt"],
                    "retrieval_selected_via_counts": {"type_quota": 2, "global_backfill": 1},
                    "retrieval_total_via_counts": {"type_quota": 2},
                    "retrieval_hit_via_counts": {"type_quota": 1},
                    "retrieval_top_k": 18,
                    "retrieval_per_type_quota": 2,
                    "retrieval_per_file_quota": 3,
                    "retrieval_base_top_k": 12,
                    "retrieval_base_per_type_quota": 1,
                    "retrieval_base_per_file_quota": 2,
                    "retrieval_budget_reasons": ["top_k:12->18"],
                    "material_total_size_mb": 15.2,
                    "material_type_count": 2,
                    "material_file_count": 2,
                    "consistency_total": 2,
                    "consistency_hit": 1,
                    "fallback_total": 1,
                    "fallback_hit": 0,
                    "available_types": ["tender_qa", "boq"],
                    "uncovered_types": ["boq"],
                    "by_type": {
                        "tender_qa": {
                            "retrieval_total": 2,
                            "retrieval_hit": 1,
                            "consistency_total": 1,
                            "consistency_hit": 1,
                            "fallback_total": 0,
                            "fallback_hit": 0,
                        },
                        "boq": {
                            "retrieval_total": 0,
                            "retrieval_hit": 0,
                            "consistency_total": 1,
                            "consistency_hit": 0,
                            "fallback_total": 1,
                            "fallback_hit": 0,
                        },
                    },
                },
                {
                    "retrieval_total": 1,
                    "retrieval_hit": 1,
                    "retrieval_file_total": 1,
                    "retrieval_file_hit": 1,
                    "retrieval_selected_filenames": ["c.txt"],
                    "retrieval_hit_filenames": ["c.txt"],
                    "retrieval_selected_via_counts": {"global_rank": 1},
                    "retrieval_total_via_counts": {"global_rank": 1},
                    "retrieval_hit_via_counts": {"global_rank": 1},
                    "retrieval_top_k": 20,
                    "retrieval_per_type_quota": 2,
                    "retrieval_per_file_quota": 3,
                    "retrieval_base_top_k": 12,
                    "retrieval_base_per_type_quota": 1,
                    "retrieval_base_per_file_quota": 2,
                    "retrieval_budget_reasons": ["top_k:12->20"],
                    "material_total_size_mb": 20.0,
                    "material_type_count": 3,
                    "material_file_count": 3,
                    "consistency_total": 1,
                    "consistency_hit": 1,
                    "fallback_total": 0,
                    "fallback_hit": 0,
                    "available_types": ["drawing"],
                    "uncovered_types": [],
                    "by_type": {
                        "drawing": {
                            "retrieval_total": 1,
                            "retrieval_hit": 1,
                            "consistency_total": 1,
                            "consistency_hit": 1,
                            "fallback_total": 0,
                            "fallback_hit": 0,
                        }
                    },
                },
            ]
        )
        assert merged["retrieval_total"] == 3
        assert merged["retrieval_hit"] == 2
        assert merged["retrieval_file_total"] == 3
        assert merged["retrieval_file_hit"] == 2
        assert merged["retrieval_file_coverage_rate"] == pytest.approx(0.6667, abs=1e-4)
        assert merged["retrieval_unhit_file_count"] == 1
        assert "b.txt" in (merged.get("retrieval_unhit_filenames") or [])
        assert merged["consistency_total"] == 3
        assert merged["consistency_hit"] == 2
        assert merged["fallback_total"] == 1
        assert merged["fallback_hit"] == 0
        assert "boq" in (merged.get("uncovered_types") or [])
        assert "drawing" in (merged.get("available_types") or [])
        assert merged["retrieval_top_k"] == 20
        assert merged["retrieval_base_top_k"] == 12
        assert merged["retrieval_selected_via_counts"]["type_quota"] == 2
        assert merged["retrieval_selected_via_counts"]["global_rank"] == 1
        assert merged["retrieval_total_via_counts"]["type_quota"] == 2
        assert merged["retrieval_hit_via_counts"]["global_rank"] == 1

    def test_evaluate_material_utilization_gate_blocks_when_required_types_uncovered(self):
        from app.main import _evaluate_material_utilization_gate

        summary = {
            "retrieval_total": 6,
            "retrieval_hit_rate": 0.6,
            "consistency_total": 4,
            "consistency_hit_rate": 0.5,
            "available_types": ["tender_qa", "boq", "drawing"],
            "uncovered_types": ["drawing"],
        }
        policy = {
            "enabled": True,
            "mode": "block",
            "min_retrieval_hit_rate": 0.2,
            "min_consistency_hit_rate": 0.2,
            "max_uncovered_required_types": 0,
            "min_required_type_coverage_rate": 1.0,
        }
        gate = _evaluate_material_utilization_gate(
            summary,
            policy=policy,
            required_types=["tender_qa", "boq", "drawing"],
        )
        assert gate["enabled"] is True
        assert gate["blocked"] is True
        assert gate["passed"] is False
        assert "drawing" in (gate.get("uncovered_required_types") or [])

    def test_evaluate_material_utilization_gate_warn_mode(self):
        from app.main import _evaluate_material_utilization_gate

        summary = {
            "retrieval_total": 6,
            "retrieval_hit_rate": 0.1,
            "retrieval_file_total": 4,
            "retrieval_file_coverage_rate": 0.25,
            "consistency_total": 4,
            "consistency_hit_rate": 0.1,
            "available_types": ["tender_qa", "boq", "drawing"],
            "uncovered_types": [],
        }
        policy = {
            "enabled": True,
            "mode": "warn",
            "min_retrieval_hit_rate": 0.2,
            "min_retrieval_file_coverage_rate": 0.6,
            "min_consistency_hit_rate": 0.2,
            "max_uncovered_required_types": 0,
            "min_required_type_coverage_rate": 0.6,
        }
        gate = _evaluate_material_utilization_gate(
            summary,
            policy=policy,
            required_types=["tender_qa", "boq", "drawing"],
        )
        assert gate["warned"] is True
        assert gate["blocked"] is False
        assert gate["level"] == "warn"

    def test_evaluate_material_utilization_gate_blocks_when_file_coverage_low(self):
        from app.main import _evaluate_material_utilization_gate

        summary = {
            "retrieval_total": 9,
            "retrieval_hit_rate": 0.8,
            "retrieval_file_total": 5,
            "retrieval_file_coverage_rate": 0.2,
            "consistency_total": 5,
            "consistency_hit_rate": 0.7,
            "available_types": ["tender_qa", "boq", "drawing"],
            "uncovered_types": [],
        }
        policy = {
            "enabled": True,
            "mode": "block",
            "min_retrieval_total": 2,
            "min_retrieval_hit_rate": 0.2,
            "min_retrieval_file_coverage_rate": 0.6,
            "min_consistency_hit_rate": 0.2,
            "max_uncovered_required_types": 0,
            "min_required_type_presence_rate": 0.6,
            "min_required_type_coverage_rate": 0.6,
        }
        gate = _evaluate_material_utilization_gate(
            summary,
            policy=policy,
            required_types=["tender_qa", "boq", "drawing"],
        )
        assert gate["blocked"] is True
        assert any("文件覆盖率" in str(x) for x in (gate.get("reasons") or []))

    def test_evaluate_material_utilization_gate_blocks_when_required_upload_missing(self):
        from app.main import _evaluate_material_utilization_gate

        summary = {
            "retrieval_total": 8,
            "retrieval_hit_rate": 0.8,
            "consistency_total": 4,
            "consistency_hit_rate": 0.7,
            "available_types": ["tender_qa"],
            "uncovered_types": [],
        }
        policy = {
            "enabled": True,
            "mode": "block",
            "min_retrieval_total": 2,
            "min_retrieval_hit_rate": 0.2,
            "min_consistency_hit_rate": 0.2,
            "max_uncovered_required_types": 0,
            "min_required_type_presence_rate": 1.0,
            "min_required_type_coverage_rate": 0.6,
        }
        gate = _evaluate_material_utilization_gate(
            summary,
            policy=policy,
            required_types=["tender_qa", "boq", "drawing"],
        )
        assert gate["blocked"] is True
        assert gate["required_type_presence_rate"] == pytest.approx(0.3333, abs=1e-4)
        missing = gate.get("required_types_missing_upload") or []
        assert "boq" in missing
        assert "drawing" in missing

    def test_evaluate_material_utilization_gate_blocks_when_retrieval_total_low(self):
        from app.main import _evaluate_material_utilization_gate

        summary = {
            "retrieval_total": 1,
            "retrieval_hit_rate": 1.0,
            "consistency_total": 4,
            "consistency_hit_rate": 0.7,
            "available_types": ["tender_qa", "boq", "drawing"],
            "uncovered_types": [],
        }
        policy = {
            "enabled": True,
            "mode": "block",
            "min_retrieval_total": 3,
            "min_retrieval_hit_rate": 0.2,
            "min_consistency_hit_rate": 0.2,
            "max_uncovered_required_types": 0,
            "min_required_type_presence_rate": 0.6,
            "min_required_type_coverage_rate": 0.6,
        }
        gate = _evaluate_material_utilization_gate(
            summary,
            policy=policy,
            required_types=["tender_qa", "boq", "drawing"],
        )
        assert gate["blocked"] is True
        assert any("资料检索证据数量" in str(x) for x in (gate.get("reasons") or []))

    def test_evaluate_material_utilization_gate_warns_when_only_optional_uploaded_type_uncovered(
        self,
    ):
        from app.main import _evaluate_material_utilization_gate

        summary = {
            "retrieval_total": 10,
            "retrieval_hit_rate": 0.8,
            "retrieval_file_total": 6,
            "retrieval_file_coverage_rate": 0.8,
            "consistency_total": 6,
            "consistency_hit_rate": 0.8,
            "available_types": ["tender_qa", "boq", "drawing", "site_photo"],
            "uncovered_types": ["site_photo"],
        }
        policy = {
            "enabled": True,
            "mode": "block",
            "min_retrieval_total": 2,
            "min_retrieval_hit_rate": 0.2,
            "min_retrieval_file_coverage_rate": 0.2,
            "min_consistency_hit_rate": 0.2,
            "max_uncovered_required_types": 0,
            "min_required_type_presence_rate": 0.6,
            "min_required_type_coverage_rate": 0.6,
            "enforce_uploaded_type_coverage": True,
            "min_uploaded_type_coverage_rate": 1.0,
        }
        gate = _evaluate_material_utilization_gate(
            summary,
            policy=policy,
            required_types=["tender_qa", "boq", "drawing"],
        )
        assert gate["blocked"] is False
        assert gate["warned"] is True
        assert gate["level"] == "warn"
        assert gate["uploaded_type_coverage_rate"] == pytest.approx(0.75, abs=1e-4)
        assert "site_photo" in (gate.get("uncovered_uploaded_types") or [])
        assert "site_photo" in (gate.get("optional_uncovered_uploaded_types") or [])
        assert any("已上传的补充资料暂未形成证据" in str(x) for x in (gate.get("reasons") or []))

    def test_evaluate_material_utilization_gate_tolerates_two_of_three_required_types(self):
        from app.main import _evaluate_material_utilization_gate

        summary = {
            "retrieval_total": 10,
            "retrieval_hit_rate": 0.8,
            "retrieval_file_total": 6,
            "retrieval_file_coverage_rate": 0.8,
            "consistency_total": 6,
            "consistency_hit_rate": 0.8,
            "available_types": ["tender_qa", "boq", "drawing"],
            "uncovered_types": ["boq"],
        }
        policy = {
            "enabled": True,
            "mode": "block",
            "min_retrieval_total": 2,
            "min_retrieval_hit_rate": 0.2,
            "min_retrieval_file_coverage_rate": 0.2,
            "min_consistency_hit_rate": 0.2,
            "max_uncovered_required_types": 0,
            "min_required_type_presence_rate": 0.0,
            "min_required_type_coverage_rate": 0.67,
        }
        gate = _evaluate_material_utilization_gate(
            summary,
            policy=policy,
            required_types=["tender_qa", "boq", "drawing"],
        )
        assert gate["required_type_coverage_rate"] == pytest.approx(0.6667, abs=1e-4)
        assert gate["blocked"] is False
        assert gate["passed"] is True
        assert gate["required_coverage_failed"] is False
        assert gate["uncovered_required_failed"] is False

    def test_ensure_report_score_self_awareness_recomputes_when_gate_state_changes(self):
        from app.main import _ensure_report_score_self_awareness

        report = {
            "total_score": 85.39,
            "rule_total_score": 13.89,
            "meta": {
                "material_utilization_gate": {
                    "enabled": True,
                    "mode": "block",
                    "thresholds": {
                        "min_retrieval_total": 0,
                        "min_retrieval_file_coverage_rate": 0.35,
                        "min_retrieval_hit_rate": 0.25,
                        "min_consistency_hit_rate": 0.25,
                        "max_uncovered_required_types": 0,
                        "min_required_type_presence_rate": 0.0,
                        "min_required_type_coverage_rate": 0.67,
                        "min_uploaded_type_coverage_rate": 1.0,
                    },
                    "metrics": {
                        "retrieval_total": 34,
                        "retrieval_hit_rate": 0.3,
                        "retrieval_file_total": 6,
                        "retrieval_file_coverage_rate": 0.6,
                        "consistency_total": 4,
                        "consistency_hit_rate": 0.5,
                    },
                    "required_types": ["tender_qa", "boq", "drawing"],
                    "uploaded_types": ["tender_qa", "boq", "drawing", "site_photo"],
                    "uncovered_uploaded_types": ["boq"],
                },
                "score_self_awareness": {
                    "level": "low",
                    "score_0_100": 18.0,
                    "state": "blocked",
                    "reasons": ["资料利用门禁阻断"],
                },
                "evidence_trace": {
                    "mandatory_hit_rate": 0.3146,
                    "source_files_hit_count": 6,
                },
            },
        }

        awareness = _ensure_report_score_self_awareness(report, material_knowledge_snapshot={})

        assert awareness["state"] == "normal"
        assert "资料利用门禁阻断" not in (awareness.get("reasons") or [])
        assert report["meta"]["material_utilization_gate"]["blocked"] is False

    def test_resolve_material_utilization_policy_defaults_to_nonzero_file_coverage(self):
        from app.main import _resolve_material_utilization_policy

        policy = _resolve_material_utilization_policy({"id": "p1", "meta": {}})
        assert policy["min_retrieval_file_coverage_rate"] == pytest.approx(0.35, abs=1e-6)


class TestScoreForProjectEndpoint:
    """Tests for /projects/{project_id}/score endpoint."""

    @patch("app.main.save_submissions")
    @patch("app.main.load_submissions")
    @patch("app.main.load_learning_profiles")
    @patch("app.main.load_config")
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    @patch("app.main.score_text")
    def test_score_for_project_success(
        self,
        mock_score,
        mock_ensure,
        mock_load_proj,
        mock_config,
        mock_profiles,
        mock_load_sub,
        mock_save_sub,
        client,
    ):
        """Score for project should return submission record."""
        mock_load_proj.return_value = [{"id": "p1"}]
        mock_config.return_value = MagicMock(rubric={}, lexicon={})
        mock_profiles.return_value = []
        mock_load_sub.return_value = []
        mock_score.return_value = MagicMock(model_dump=lambda: {"total_score": 75.0})

        response = client.post("/api/v1/projects/p1/score", json={"text": "测试文本"})
        assert response.status_code == 200
        data = response.json()
        assert data["filename"] == "inline"

    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    def test_score_for_project_not_found(self, mock_ensure, mock_load, client):
        """Score for project should return 404 if project not found."""
        mock_load.return_value = []
        response = client.post("/api/v1/projects/nonexistent/score", json={"text": "test"})
        assert response.status_code == 404

    @patch("app.main.save_submissions")
    @patch("app.main.load_submissions")
    @patch("app.main.load_learning_profiles")
    @patch("app.main.load_config")
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    @patch("app.main.score_text")
    def test_score_for_project_with_learning_profile(
        self,
        mock_score,
        mock_ensure,
        mock_load_proj,
        mock_config,
        mock_profiles,
        mock_load_sub,
        mock_save_sub,
        client,
    ):
        """Score for project should use learning profile multipliers."""
        mock_load_proj.return_value = [{"id": "p1"}]
        mock_config.return_value = MagicMock(rubric={}, lexicon={})
        mock_profiles.return_value = [{"project_id": "p1", "dimension_multipliers": {"D01": 1.3}}]
        mock_load_sub.return_value = []
        mock_score.return_value = MagicMock(model_dump=lambda: {"total_score": 78.0})

        with (
            patch("app.main.get_cached_score", return_value=None),
            patch("app.main.cache_score_result"),
            patch("app.main.load_evolution_reports", return_value={}),
        ):
            response = client.post("/api/v1/projects/p1/score", json={"text": "测试文本"})
        assert response.status_code == 200
        # Check multipliers were passed
        call_args = mock_score.call_args
        assert call_args.kwargs.get("dimension_multipliers") == {"D01": 1.3}


class TestSubmissionsEndpoint:
    """Tests for /projects/{project_id}/submissions endpoint."""

    @patch("app.main.load_submissions")
    @patch("app.main.ensure_data_dirs")
    def test_list_submissions_empty(self, mock_ensure, mock_load, client):
        """List submissions should return empty list when no submissions."""
        mock_load.return_value = []
        response = client.get("/api/v1/projects/p1/submissions")
        assert response.status_code == 200
        assert response.json() == []

    @patch("app.main.load_submissions")
    @patch("app.main.ensure_data_dirs")
    def test_list_submissions_filtered(self, mock_ensure, mock_load, client):
        """List submissions should filter by project_id."""
        mock_load.return_value = [
            {
                "id": "s1",
                "project_id": "p1",
                "filename": "f1.txt",
                "total_score": 80.0,
                "report": {},
                "text": "t1",
                "created_at": "2026-01-01T00:00:00Z",
            },
            {
                "id": "s2",
                "project_id": "p2",
                "filename": "f2.txt",
                "total_score": 70.0,
                "report": {},
                "text": "t2",
                "created_at": "2026-01-01T00:00:00Z",
            },
        ]
        response = client.get("/api/v1/projects/p1/submissions")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["id"] == "s1"

    @patch("app.main.load_projects")
    @patch("app.main.load_submissions")
    @patch("app.main.ensure_data_dirs")
    def test_list_submissions_applies_project_score_scale(
        self, mock_ensure, mock_load_submissions, mock_load_projects, client
    ):
        """List submissions should render scaled score values based on project meta score_scale_max."""
        mock_load_projects.return_value = [{"id": "p1", "meta": {"score_scale_max": 5}}]
        mock_load_submissions.return_value = [
            {
                "id": "s1",
                "project_id": "p1",
                "filename": "f1.txt",
                "total_score": 80.0,
                "report": {
                    "scoring_status": "scored",
                    "total_score": 80.0,
                    "rule_total_score": 70.0,
                    "pred_total_score": 80.0,
                    "llm_total_score": 78.0,
                },
                "text": "t1",
                "created_at": "2026-01-01T00:00:00Z",
            }
        ]
        response = client.get("/api/v1/projects/p1/submissions")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["total_score"] == 3.5
        assert data[0]["report"]["pred_total_score"] is None
        assert data[0]["report"]["rule_total_score"] == 3.5
        assert data[0]["report"]["llm_total_score"] is None
        assert data[0]["report"]["score_scale_max"] == 5
        assert data[0]["report"]["score_scale_label"] == "5分制"
        assert data[0]["report"]["raw_total_score_100"] == 70.0

    @patch("app.main.load_qingtian_results")
    @patch("app.main.load_projects")
    @patch("app.main.load_submissions")
    @patch("app.main.ensure_data_dirs")
    def test_list_submissions_uses_five_scale_global_prior_when_rule_score_below_one(
        self,
        mock_ensure,
        mock_load_submissions,
        mock_load_projects,
        mock_load_qingtian_results,
        client,
    ):
        mock_load_projects.return_value = [{"id": "p1", "meta": {"score_scale_max": 5}}]
        mock_load_submissions.return_value = [
            {
                "id": "s1",
                "project_id": "p1",
                "filename": "current.pdf",
                "total_score": 8.54,
                "report": {
                    "scoring_status": "blocked",
                    "total_score": 8.54,
                    "rule_total_score": 8.54,
                    "meta": {"material_utilization_gate": {"blocked": True, "reasons": ["图纸"]}},
                },
                "text": "current text",
                "created_at": "2026-01-02T00:00:00Z",
            },
            {
                "id": "hist1",
                "project_id": "hist",
                "filename": "hist.txt",
                "total_score": 6.81,
                "report": {
                    "scoring_status": "scored",
                    "total_score": 6.81,
                    "rule_total_score": 6.81,
                    "meta": {},
                },
                "text": "historical text",
                "created_at": "2026-01-01T00:00:00Z",
            },
        ]
        mock_load_qingtian_results.return_value = [
            {
                "id": "qt1",
                "submission_id": "hist1",
                "qt_total_score": 84.0,
                "created_at": "2026-01-03T00:00:00Z",
            }
        ]

        response = client.get("/api/v1/projects/p1/submissions")

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["total_score"] == 4.2865
        assert data[0]["report"]["pred_total_score"] == 4.2865
        assert data[0]["report"]["rule_total_score"] == 0.427
        assert data[0]["report"]["raw_rule_total_score_100"] == 8.54
        assert data[0]["report"]["dual_track_summary"]["display_score_label"] == "当前分"
        assert data[0]["report"]["dual_track_summary"]["display_total_score"] == 4.2865

    @patch("app.main.load_qingtian_results")
    @patch("app.main.load_projects")
    @patch("app.main.load_submissions")
    @patch("app.main.ensure_data_dirs")
    def test_list_submissions_uses_hundred_scale_global_prior_when_rule_score_too_low(
        self,
        mock_ensure,
        mock_load_submissions,
        mock_load_projects,
        mock_load_qingtian_results,
        client,
    ):
        mock_load_projects.return_value = [{"id": "p1"}]
        mock_load_submissions.return_value = [
            {
                "id": "s1",
                "project_id": "p1",
                "filename": "current.pdf",
                "total_score": 14.54,
                "report": {
                    "scoring_status": "scored",
                    "total_score": 14.54,
                    "rule_total_score": 14.54,
                    "meta": {},
                },
                "text": "current text",
                "created_at": "2026-01-02T00:00:00Z",
            },
            {
                "id": "hist1",
                "project_id": "hist",
                "filename": "hist.txt",
                "total_score": 6.81,
                "report": {
                    "scoring_status": "scored",
                    "total_score": 6.81,
                    "rule_total_score": 6.81,
                    "meta": {},
                },
                "text": "historical text",
                "created_at": "2026-01-01T00:00:00Z",
            },
        ]
        mock_load_qingtian_results.return_value = [
            {
                "id": "qt1",
                "submission_id": "hist1",
                "qt_total_score": 84.0,
                "created_at": "2026-01-03T00:00:00Z",
            }
        ]

        response = client.get("/api/v1/projects/p1/submissions")

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert round(data[0]["total_score"], 2) == 91.73
        assert round(data[0]["report"]["pred_total_score"], 2) == 91.73
        assert data[0]["report"]["rule_total_score"] == 14.54
        assert data[0]["report"]["raw_rule_total_score_100"] == 14.54
        assert data[0]["report"]["dual_track_summary"]["display_score_label"] == "当前分"
        assert round(data[0]["report"]["dual_track_summary"]["display_total_score"], 2) == 91.73

    @patch("app.main.load_qingtian_results")
    @patch("app.main.load_projects")
    @patch("app.main.load_submissions")
    @patch("app.main.ensure_data_dirs")
    def test_list_submissions_skips_hundred_scale_global_prior_for_normal_rule_scores(
        self,
        mock_ensure,
        mock_load_submissions,
        mock_load_projects,
        mock_load_qingtian_results,
        client,
    ):
        mock_load_projects.return_value = [{"id": "p1"}]
        mock_load_submissions.return_value = [
            {
                "id": "s1",
                "project_id": "p1",
                "filename": "current.pdf",
                "total_score": 62.0,
                "report": {
                    "scoring_status": "scored",
                    "total_score": 62.0,
                    "rule_total_score": 62.0,
                    "meta": {},
                },
                "text": "current text",
                "created_at": "2026-01-02T00:00:00Z",
            },
            {
                "id": "hist1",
                "project_id": "hist",
                "filename": "hist.txt",
                "total_score": 6.81,
                "report": {
                    "scoring_status": "scored",
                    "total_score": 6.81,
                    "rule_total_score": 6.81,
                    "meta": {},
                },
                "text": "historical text",
                "created_at": "2026-01-01T00:00:00Z",
            },
        ]
        mock_load_qingtian_results.return_value = [
            {
                "id": "qt1",
                "submission_id": "hist1",
                "qt_total_score": 84.0,
                "created_at": "2026-01-03T00:00:00Z",
            }
        ]

        response = client.get("/api/v1/projects/p1/submissions")

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["total_score"] == 62.0
        assert data[0]["report"]["pred_total_score"] is None
        assert data[0]["report"]["rule_total_score"] == 62.0
        assert data[0]["report"]["dual_track_summary"]["display_score_label"] == "独立分"

    def test_list_submissions_includes_dual_track_summary(self, client):
        submission = {
            "id": "s1",
            "project_id": "p1",
            "filename": "f1.txt",
            "total_score": 82.0,
            "report": {
                "scoring_status": "scored",
                "total_score": 82.0,
                "rule_total_score": 78.0,
                "pred_total_score": 82.0,
                "llm_total_score": 81.5,
                "meta": {},
            },
            "text": "t1",
            "created_at": "2026-01-01T00:00:00Z",
        }
        with (
            patch("app.main.ensure_data_dirs"),
            patch("app.main.load_projects", return_value=[{"id": "p1"}]),
            patch("app.main.load_submissions", return_value=[submission]),
            patch(
                "app.main.load_qingtian_results",
                return_value=[{"submission_id": "s1", "qt_total_score": 84.0}],
            ),
            patch(
                "app.main._select_calibrator_model",
                return_value={"calibrator_version": "calib_v1"},
            ),
            patch("app.main._build_material_knowledge_profile", return_value={}),
            patch("app.main._ensure_report_score_self_awareness"),
        ):
            response = client.get("/api/v1/projects/p1/submissions")

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        summary = data[0]["report"]["dual_track_summary"]
        assert summary["display_score_label"] == "当前分"
        assert summary["independent_score"] == 78.0
        assert summary["approximation_score"] == 82.0
        assert summary["qingtian_score"] == 84.0
        assert summary["independent_abs_delta"] == 6.0
        assert summary["approximation_abs_delta"] == 2.0
        assert summary["abs_delta_improvement"] == 4.0
        assert summary["independent_abs_delta_100"] == 6.0
        assert summary["approximation_abs_delta_100"] == 2.0
        assert summary["abs_delta_improvement_100"] == 4.0
        assert summary["alignment_status"] == "approximation_better"
        assert "当前分层当前更接近青天" in summary["governance_hint"]

    def test_list_submissions_dual_track_summary_keeps_five_scale_native_metrics(self, client):
        submission = {
            "id": "s1",
            "project_id": "p1",
            "filename": "f1.txt",
            "total_score": 82.0,
            "report": {
                "scoring_status": "scored",
                "total_score": 82.0,
                "rule_total_score": 78.0,
                "pred_total_score": 82.0,
                "meta": {},
            },
            "text": "t1",
            "created_at": "2026-01-01T00:00:00Z",
        }
        with (
            patch("app.main.ensure_data_dirs"),
            patch(
                "app.main.load_projects",
                return_value=[{"id": "p1", "meta": {"score_scale_max": 5}}],
            ),
            patch("app.main.load_submissions", return_value=[submission]),
            patch(
                "app.main.load_qingtian_results",
                return_value=[{"submission_id": "s1", "qt_total_score": 84.0}],
            ),
            patch(
                "app.main._select_calibrator_model",
                return_value={"calibrator_version": "calib_v1"},
            ),
            patch("app.main._build_material_knowledge_profile", return_value={}),
            patch("app.main._ensure_report_score_self_awareness"),
        ):
            response = client.get("/api/v1/projects/p1/submissions")

        assert response.status_code == 200
        data = response.json()
        summary = data[0]["report"]["dual_track_summary"]
        assert summary["scale_label"] == "5分制"
        assert summary["display_total_score"] == 4.1
        assert summary["independent_score"] == 3.9
        assert summary["approximation_score"] == 4.1
        assert summary["qingtian_score"] == 4.2
        assert summary["independent_abs_delta"] == 0.3
        assert summary["approximation_abs_delta"] == 0.1
        assert summary["abs_delta_improvement"] == 0.2

    def test_list_submissions_marks_exact_ground_truth_as_real_score_in_dual_track_summary(
        self, client
    ):
        submission = {
            "id": "s1",
            "project_id": "p1",
            "filename": "f1.txt",
            "total_score": 82.2,
            "report": {
                "scoring_status": "scored",
                "total_score": 82.2,
                "rule_total_score": 78.0,
                "pred_total_score": 82.2,
                "score_blend": {"mode": "ground_truth_exact"},
                "meta": {"ground_truth_exact_match": True},
            },
            "text": "t1",
            "created_at": "2026-01-01T00:00:00Z",
        }
        with (
            patch("app.main.ensure_data_dirs"),
            patch("app.main.load_projects", return_value=[{"id": "p1"}]),
            patch("app.main.load_submissions", return_value=[submission]),
            patch("app.main.load_qingtian_results", return_value=[]),
            patch(
                "app.main._select_calibrator_model",
                return_value={"calibrator_version": "calib_v1"},
            ),
            patch("app.main._build_material_knowledge_profile", return_value={}),
            patch("app.main._ensure_report_score_self_awareness"),
        ):
            response = client.get("/api/v1/projects/p1/submissions")

        assert response.status_code == 200
        data = response.json()
        summary = data[0]["report"]["dual_track_summary"]
        assert summary["display_score_source"] == "ground_truth"
        assert summary["display_score_label"] == "真实分"
        assert summary["approximation_score"] is None
        assert summary["has_approximation_score"] is False
        assert summary["has_exact_ground_truth_score"] is True
        assert summary["alignment_status"] == "ground_truth_exact"
        assert "真实评标结果" in summary["governance_hint"]

    def test_build_submission_dual_track_overview_prefers_real_score_headline(self):
        from app.qingtian_dual_track import build_submission_dual_track_overview

        overview = build_submission_dual_track_overview(
            [
                {
                    "display_score_source": "ground_truth",
                    "scale_label": "5分制",
                    "has_approximation_score": False,
                    "has_exact_ground_truth_score": True,
                    "has_ground_truth": True,
                    "independent_score": 78.0,
                    "qingtian_score": 82.2,
                    "independent_abs_delta": 0.21,
                    "independent_abs_delta_100": 4.2,
                    "approximation_abs_delta_100": None,
                    "abs_delta_improvement_100": None,
                }
            ]
        )

        assert overview["exact_ground_truth_count"] == 1
        assert overview["dual_track_count"] == 0
        assert overview["scale_label"] == "5分制"
        assert overview["independent_abs_delta_avg"] == 0.21
        assert overview["headline"] == "当前默认展示真实分，并保留独立分作审计基线。"

    def test_list_submissions_with_latest_report_returns_ranked_rows(self, client):
        submissions = [
            {
                "id": "s1",
                "project_id": "p1",
                "filename": "A.docx",
                "total_score": 82.0,
                "report": {"scoring_status": "scored", "total_score": 82.0, "meta": {}},
                "text": "t1",
                "created_at": "2026-01-01T00:00:00Z",
            },
            {
                "id": "s2",
                "project_id": "p1",
                "filename": "B.docx",
                "total_score": 79.0,
                "report": {"scoring_status": "scored", "total_score": 79.0, "meta": {}},
                "text": "t2",
                "created_at": "2026-01-02T00:00:00Z",
            },
        ]
        latest_reports = [
            {
                "id": "r1",
                "project_id": "p1",
                "submission_id": "s1",
                "created_at": "2026-01-03T00:00:00Z",
                "rule_total_score": 78.0,
                "pred_total_score": 83.0,
                "suggestions": [{"expected_gain": 3.0}],
            },
            {
                "id": "r2",
                "project_id": "p1",
                "submission_id": "s2",
                "created_at": "2026-01-04T00:00:00Z",
                "rule_total_score": 80.0,
                "pred_total_score": 81.0,
                "suggestions": [{"expected_gain": 1.0}],
            },
        ]

        with (
            patch("app.main.ensure_data_dirs"),
            patch("app.main.load_projects", return_value=[{"id": "p1"}]),
            patch("app.main.load_submissions", return_value=submissions),
            patch("app.main.load_score_reports", return_value=latest_reports),
            patch("app.main.load_qingtian_results", return_value=[]),
            patch(
                "app.main._select_calibrator_model",
                return_value={"calibrator_version": "calib_v1"},
            ),
            patch("app.main._build_material_knowledge_profile", return_value={}),
            patch("app.main._ensure_report_score_self_awareness"),
        ):
            response = client.get("/api/v1/projects/p1/submissions?with=latest_report")

        assert response.status_code == 200
        data = response.json()
        assert data["project_id"] == "p1"
        assert len(data["submissions"]) == 2
        first = next(row for row in data["submissions"] if row["submission_id"] == "s1")
        second = next(row for row in data["submissions"] if row["submission_id"] == "s2")
        assert first["latest_report"]["rank_by_pred"] == 1
        assert second["latest_report"]["rank_by_pred"] == 2
        assert first["latest_report"]["rank_by_rule"] == 2
        assert second["latest_report"]["rank_by_rule"] == 1
        assert first["latest_report"]["top_expected_gain"] == 3.0

    def test_score_scale_round_trip_preserves_precision_for_5_scale(self):
        from app.main import _convert_score_from_100, _convert_score_to_100

        scaled = _convert_score_from_100(66.67, 5)

        assert scaled == 3.3335
        assert _convert_score_to_100(scaled, 5) == 66.67


class TestCompareEndpoints:
    """Tests for /projects/{project_id}/compare endpoints."""

    @patch("app.main.load_submissions")
    @patch("app.main.ensure_data_dirs")
    def test_compare_no_submissions(self, mock_ensure, mock_load, client):
        """Compare should return 404 if no submissions."""
        mock_load.return_value = []
        response = client.get("/api/v1/projects/p1/compare")
        assert response.status_code == 404
        assert "暂无施组记录" in response.json()["detail"]

    @patch("app.main.load_submissions")
    @patch("app.main.ensure_data_dirs")
    def test_compare_with_submissions(self, mock_ensure, mock_load, client):
        """Compare should return rankings and stats."""
        mock_load.return_value = [
            {
                "id": "s1",
                "project_id": "p1",
                "filename": "f1.txt",
                "total_score": 80.0,
                "report": {
                    "dimension_scores": {"D01": {"score": 20.0}},
                    "penalties": [{"code": "P001"}],
                },
                "created_at": "2026-01-01T00:00:00Z",
            },
            {
                "id": "s2",
                "project_id": "p1",
                "filename": "f2.txt",
                "total_score": 70.0,
                "report": {
                    "dimension_scores": {"D01": {"score": 18.0}},
                    "penalties": [],
                },
                "created_at": "2026-01-02T00:00:00Z",
            },
        ]
        response = client.get("/api/v1/projects/p1/compare")
        assert response.status_code == 200
        data = response.json()
        assert data["project_id"] == "p1"
        assert len(data["rankings"]) == 2
        # First should be the higher ranked submission; displayed total may be scaled by project score scale.
        assert data["rankings"][0]["submission_id"] == "s1"
        assert (
            data["rankings"][0]["ranking_sort_score"] >= data["rankings"][1]["ranking_sort_score"]
        )
        assert "ranking_evidence_bonus" in data["rankings"][0]

    @patch("app.main.load_submissions")
    @patch("app.main.ensure_data_dirs")
    def test_compare_uses_evidence_bonus_for_near_ties(self, mock_ensure, mock_load, client):
        mock_load.return_value = [
            {
                "id": "s1",
                "project_id": "p1",
                "filename": "raw_high_low_conf.txt",
                "total_score": 85.0,
                "report": {
                    "dimension_scores": {"09": {"score": 7.0}},
                    "penalties": [],
                    "meta": {
                        "score_confidence_level": "low",
                        "score_self_awareness": {
                            "level": "low",
                            "score_0_100": 24.0,
                            "structured_quality_avg": 0.12,
                            "structured_quality_type_rate": 0.0,
                            "retrieval_file_coverage_rate": 0.18,
                            "dimension_coverage_rate": 0.2,
                        },
                    },
                },
                "created_at": "2026-01-01T00:00:00Z",
            },
            {
                "id": "s2",
                "project_id": "p1",
                "filename": "raw_low_high_conf.txt",
                "total_score": 84.8,
                "report": {
                    "dimension_scores": {"09": {"score": 7.2}},
                    "penalties": [],
                    "meta": {
                        "score_confidence_level": "high",
                        "score_self_awareness": {
                            "level": "high",
                            "score_0_100": 89.0,
                            "structured_quality_avg": 0.88,
                            "structured_quality_type_rate": 1.0,
                            "retrieval_file_coverage_rate": 0.92,
                            "dimension_coverage_rate": 0.86,
                        },
                    },
                },
                "created_at": "2026-01-02T00:00:00Z",
            },
        ]
        response = client.get("/api/v1/projects/p1/compare")
        assert response.status_code == 200
        data = response.json()
        assert data["rankings"][0]["submission_id"] == "s2"
        assert data["rankings"][0]["ranking_sort_score"] > data["rankings"][1]["ranking_sort_score"]
        assert (
            data["rankings"][0]["ranking_evidence_bonus"]
            > data["rankings"][1]["ranking_evidence_bonus"]
        )

    @patch("app.main.load_calibration_models")
    @patch("app.main.load_projects")
    @patch("app.main.load_submissions")
    @patch("app.main.ensure_data_dirs")
    def test_compare_prefers_pred_total_score(
        self, mock_ensure, mock_load, mock_load_projects, mock_load_models, client
    ):
        """Compare ranking should prioritize pred_total_score and keep rule_total_score trace."""
        mock_load_projects.return_value = [{"id": "p1", "calibrator_version_locked": "calib1"}]
        mock_load_models.return_value = [
            {
                "calibrator_version": "calib1",
                "deployed": True,
                "created_at": "2026-01-02T00:00:00Z",
                "train_filter": {"project_id": "p1"},
            }
        ]
        mock_load.return_value = [
            {
                "id": "s1",
                "project_id": "p1",
                "filename": "f1.txt",
                "total_score": 95.0,
                "report": {
                    "rule_total_score": 70.0,
                    "pred_total_score": 65.0,
                    "meta": {
                        "score_confidence_level": "low",
                        "score_self_awareness": {"level": "low", "score_0_100": 21.0},
                    },
                    "dimension_scores": {},
                    "penalties": [],
                },
                "created_at": "2026-01-01T00:00:00Z",
            },
            {
                "id": "s2",
                "project_id": "p1",
                "filename": "f2.txt",
                "total_score": 80.0,
                "report": {
                    "rule_total_score": 75.0,
                    "pred_total_score": 90.0,
                    "meta": {
                        "score_confidence_level": "high",
                        "score_self_awareness": {"level": "high", "score_0_100": 82.0},
                    },
                    "dimension_scores": {},
                    "penalties": [],
                },
                "created_at": "2026-01-02T00:00:00Z",
            },
        ]
        response = client.get("/api/v1/projects/p1/compare")
        assert response.status_code == 200
        data = response.json()
        assert data["rankings"][0]["submission_id"] == "s2"
        assert data["rankings"][0]["total_score"] == 90.0
        assert data["rankings"][0]["rule_total_score"] == 75.0
        assert data["rankings"][0]["score_source"] == "pred"
        assert data["rankings"][0]["score_confidence_level"] == "high"
        assert data["rankings"][0]["score_self_awareness"]["score_0_100"] == 82.0

    @patch("app.main.build_compare_narrative")
    @patch("app.main.load_submissions")
    @patch("app.main.ensure_data_dirs")
    def test_compare_report_success(self, mock_ensure, mock_load, mock_narrative, client):
        """Compare report should return narrative."""
        mock_load.return_value = [
            {
                "id": "s1",
                "project_id": "p1",
                "total_score": 81.0,
                "report": {"total_score": 81.0, "scoring_status": "scored"},
            }
        ]
        mock_narrative.return_value = {
            "summary": "test summary",
            "top_submission": {"id": "s1"},
            "bottom_submission": {"id": "s1"},
            "key_diffs": [],
        }
        response = client.get("/api/v1/projects/p1/compare_report")
        assert response.status_code == 200
        data = response.json()
        assert data["project_id"] == "p1"

    @patch("app.main.build_compare_narrative")
    @patch("app.main.load_submissions")
    @patch("app.main.ensure_data_dirs")
    def test_compare_report_includes_blocked_generated_scores(
        self,
        mock_ensure,
        mock_load,
        mock_narrative,
        client,
    ):
        mock_load.return_value = [
            {
                "id": "s1",
                "project_id": "p1",
                "filename": "blocked.pdf",
                "total_score": 74.65,
                "report": {
                    "scoring_status": "blocked",
                    "total_score": 74.65,
                    "rule_total_score": 74.65,
                    "meta": {
                        "material_utilization_gate": {
                            "blocked": True,
                            "reasons": ["关键资料未形成证据：图纸"],
                        }
                    },
                },
            },
            {
                "id": "s2",
                "project_id": "p1",
                "filename": "scored.pdf",
                "total_score": 82.0,
                "report": {
                    "scoring_status": "scored",
                    "total_score": 82.0,
                    "rule_total_score": 82.0,
                },
            },
        ]

        def _fake_narrative(rows):
            row_map = {str(row.get("id")): row for row in rows}
            assert "s1" in row_map
            assert "s2" in row_map
            return {
                "summary": "ok",
                "top_submission": {"id": "s2", "filename": "scored.pdf", "total_score": 82.0},
                "bottom_submission": {"id": "s1", "filename": "blocked.pdf", "total_score": 74.65},
                "key_diffs": [],
            }

        mock_narrative.side_effect = _fake_narrative
        response = client.get("/api/v1/projects/p1/compare_report")
        assert response.status_code == 200
        data = response.json()
        assert data["bottom_submission"]["filename"] == "blocked.pdf"

    @patch("app.main.build_compare_narrative")
    @patch("app.main.load_calibration_models")
    @patch("app.main.load_projects")
    @patch("app.main.load_submissions")
    @patch("app.main.ensure_data_dirs")
    def test_compare_report_enriches_pred_and_rule(
        self,
        mock_ensure,
        mock_load,
        mock_load_projects,
        mock_load_models,
        mock_narrative,
        client,
    ):
        """Compare narrative should be built with pred-priority total and expose trace scores."""
        mock_load_projects.return_value = [{"id": "p1", "calibrator_version_locked": "calib1"}]
        mock_load_models.return_value = [
            {
                "calibrator_version": "calib1",
                "deployed": True,
                "created_at": "2026-01-02T00:00:00Z",
                "train_filter": {"project_id": "p1"},
            }
        ]
        mock_load.return_value = [
            {
                "id": "s1",
                "project_id": "p1",
                "filename": "f1.txt",
                "total_score": 91.0,
                "report": {
                    "rule_total_score": 70.0,
                    "pred_total_score": 65.0,
                    "meta": {
                        "score_confidence_level": "low",
                        "score_self_awareness": {"level": "low", "score_0_100": 28.0},
                    },
                },
            },
            {
                "id": "s2",
                "project_id": "p1",
                "filename": "f2.txt",
                "total_score": 82.0,
                "report": {
                    "rule_total_score": 80.0,
                    "pred_total_score": 90.0,
                    "meta": {
                        "score_confidence_level": "high",
                        "score_self_awareness": {"level": "high", "score_0_100": 84.0},
                    },
                },
            },
        ]

        def _fake_narrative(rows):
            row_map = {str(x.get("id")): x for x in rows}
            assert float(row_map["s1"]["total_score"]) == 65.0
            assert float(row_map["s2"]["total_score"]) == 90.0
            return {
                "summary": "ok",
                "top_submission": {"id": "s2", "filename": "f2.txt", "total_score": 90.0},
                "bottom_submission": {"id": "s1", "filename": "f1.txt", "total_score": 65.0},
                "key_diffs": [],
            }

        mock_narrative.side_effect = _fake_narrative
        response = client.get("/api/v1/projects/p1/compare_report")
        assert response.status_code == 200
        data = response.json()
        assert data["top_submission"]["pred_total_score"] == 90.0
        assert data["top_submission"]["rule_total_score"] == 80.0
        assert data["top_submission"]["score_source"] == "pred"
        assert data["top_submission"]["score_confidence_level"] == "high"
        assert data["top_submission"]["score_self_awareness"]["score_0_100"] == 84.0

    @patch("app.main.load_submissions")
    @patch("app.main.ensure_data_dirs")
    def test_compare_report_no_submissions(self, mock_ensure, mock_load, client):
        """Compare report should return 404 if no submissions."""
        mock_load.return_value = []
        response = client.get("/api/v1/projects/p1/compare_report")
        assert response.status_code == 404

    @patch("app.main.load_submissions")
    @patch("app.main.ensure_data_dirs")
    def test_compare_requires_scored_submissions(self, mock_ensure, mock_load, client):
        """Compare should require scored submissions."""
        mock_load.return_value = [
            {
                "id": "s1",
                "project_id": "p1",
                "total_score": 0.0,
                "report": {"scoring_status": "pending"},
            }
        ]
        response = client.get("/api/v1/projects/p1/compare")
        assert response.status_code == 404
        assert "请先点击“评分施组”" in response.json()["detail"]

    @patch("app.main.load_submissions")
    @patch("app.main.ensure_data_dirs")
    def test_compare_includes_blocked_generated_scores(self, mock_ensure, mock_load, client):
        mock_load.return_value = [
            {
                "id": "s1",
                "project_id": "p1",
                "filename": "blocked.pdf",
                "total_score": 74.65,
                "report": {
                    "scoring_status": "blocked",
                    "total_score": 74.65,
                    "rule_total_score": 74.65,
                    "meta": {
                        "material_utilization_gate": {
                            "blocked": True,
                            "reasons": ["关键资料未形成证据：图纸"],
                        }
                    },
                },
            }
        ]

        response = client.get("/api/v1/projects/p1/compare")
        assert response.status_code == 200
        data = response.json()
        assert data["rankings"][0]["filename"] == "blocked.pdf"

    @patch("app.main.load_qingtian_results")
    @patch("app.main.load_projects")
    @patch("app.main.load_submissions")
    @patch("app.main.ensure_data_dirs")
    def test_compare_uses_five_scale_global_prior_for_blocked_low_scores(
        self,
        mock_ensure,
        mock_load_submissions,
        mock_load_projects,
        mock_load_qingtian_results,
        client,
    ):
        mock_load_projects.return_value = [{"id": "p1", "meta": {"score_scale_max": 5}}]
        mock_load_submissions.return_value = [
            {
                "id": "s1",
                "project_id": "p1",
                "filename": "blocked.pdf",
                "total_score": 8.54,
                "report": {
                    "scoring_status": "blocked",
                    "total_score": 8.54,
                    "rule_total_score": 8.54,
                    "meta": {"material_utilization_gate": {"blocked": True, "reasons": ["图纸"]}},
                    "dimension_scores": {},
                    "penalties": [],
                },
                "text": "current text",
                "created_at": "2026-01-02T00:00:00Z",
            },
            {
                "id": "hist1",
                "project_id": "hist",
                "filename": "hist.txt",
                "total_score": 6.81,
                "report": {
                    "scoring_status": "scored",
                    "total_score": 6.81,
                    "rule_total_score": 6.81,
                    "meta": {},
                    "dimension_scores": {},
                    "penalties": [],
                },
                "text": "historical text",
                "created_at": "2026-01-01T00:00:00Z",
            },
        ]
        mock_load_qingtian_results.return_value = [
            {
                "id": "qt1",
                "submission_id": "hist1",
                "qt_total_score": 84.0,
                "created_at": "2026-01-03T00:00:00Z",
            }
        ]

        response = client.get("/api/v1/projects/p1/compare")

        assert response.status_code == 200
        data = response.json()
        assert data["rankings"][0]["filename"] == "blocked.pdf"
        assert data["rankings"][0]["total_score"] == 4.2865
        assert data["rankings"][0]["pred_total_score"] == 4.2865
        assert data["rankings"][0]["rule_total_score"] == 0.427
        assert data["rankings"][0]["score_source"] == "pred"

    @patch("app.main.load_qingtian_results")
    @patch("app.main.load_projects")
    @patch("app.main.load_submissions")
    @patch("app.main.ensure_data_dirs")
    def test_compare_uses_hundred_scale_global_prior_for_low_scores(
        self,
        mock_ensure,
        mock_load_submissions,
        mock_load_projects,
        mock_load_qingtian_results,
        client,
    ):
        mock_load_projects.return_value = [{"id": "p1"}]
        mock_load_submissions.return_value = [
            {
                "id": "s1",
                "project_id": "p1",
                "filename": "blocked.pdf",
                "total_score": 14.54,
                "report": {
                    "scoring_status": "blocked",
                    "total_score": 14.54,
                    "rule_total_score": 14.54,
                    "meta": {},
                    "dimension_scores": {},
                    "penalties": [],
                },
                "text": "current text",
                "created_at": "2026-01-02T00:00:00Z",
            },
            {
                "id": "hist1",
                "project_id": "hist",
                "filename": "hist.txt",
                "total_score": 6.81,
                "report": {
                    "scoring_status": "scored",
                    "total_score": 6.81,
                    "rule_total_score": 6.81,
                    "meta": {},
                    "dimension_scores": {},
                    "penalties": [],
                },
                "text": "historical text",
                "created_at": "2026-01-01T00:00:00Z",
            },
        ]
        mock_load_qingtian_results.return_value = [
            {
                "id": "qt1",
                "submission_id": "hist1",
                "qt_total_score": 84.0,
                "created_at": "2026-01-03T00:00:00Z",
            }
        ]

        response = client.get("/api/v1/projects/p1/compare")

        assert response.status_code == 200
        data = response.json()
        assert data["rankings"][0]["filename"] == "blocked.pdf"
        assert round(data["rankings"][0]["total_score"], 2) == 91.73
        assert round(data["rankings"][0]["pred_total_score"], 2) == 91.73
        assert data["rankings"][0]["rule_total_score"] == 14.54
        assert data["rankings"][0]["score_source"] == "pred"


class TestEvidenceTraceEndpoints:
    """Tests for /projects/{project_id}/submissions/{submission_id}/evidence_trace endpoints."""

    @patch("app.main.load_materials")
    @patch("app.main.load_evidence_units")
    @patch("app.main.load_submissions")
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    def test_get_submission_evidence_trace_success(
        self,
        mock_ensure,
        mock_load_projects,
        mock_load_submissions,
        mock_load_units,
        mock_load_materials,
        client,
    ):
        mock_load_projects.return_value = [{"id": "p1", "name": "项目1", "meta": {}}]
        mock_load_submissions.return_value = [
            {
                "id": "s1",
                "project_id": "p1",
                "filename": "施工组织设计.pdf",
                "text": "工期120天，关键节点分三段推进。",
                "created_at": "2026-02-27T00:00:00+00:00",
                "report": {
                    "requirement_hits": [
                        {
                            "dimension_id": "01",
                            "label": "总工期约束",
                            "hit": True,
                            "mandatory": True,
                            "reason": "命中 t2/2 n1/1",
                            "source_pack_id": "runtime_material_rag",
                            "material_type": "tender_qa",
                            "source_filename": "招标文件.pdf",
                            "chunk_id": "招标文件.pdf#c1",
                        },
                        {
                            "dimension_id": "09",
                            "label": "进度里程碑",
                            "hit": False,
                            "mandatory": True,
                            "reason": "命中不足 t1/3 n0/1",
                            "source_pack_id": "runtime_material_consistency",
                            "material_type": "boq",
                            "source_filename": "工程量清单.xlsx",
                            "chunk_id": "工程量清单.xlsx#c2",
                        },
                    ],
                    "material_consistency": {
                        "by_material_type": {
                            "boq": {
                                "total": 1,
                                "hit": 0,
                                "mandatory_total": 1,
                                "mandatory_hit": 0,
                                "hit_rate": 0.0,
                            }
                        }
                    },
                    "meta": {},
                },
            }
        ]
        mock_load_units.return_value = [
            {
                "id": "u1",
                "submission_id": "s1",
                "dimension_id": "01",
                "source_locator": "P12-L30",
                "source_filename": "招标文件.pdf",
                "confidence": 0.91,
                "text_snippet": "项目总工期120天。",
            }
        ]
        mock_load_materials.return_value = []

        response = client.get("/api/v1/projects/p1/submissions/s1/evidence_trace")
        assert response.status_code == 200
        data = response.json()
        assert data["project_id"] == "p1"
        assert data["submission_id"] == "s1"
        assert data["summary"]["total_requirements"] == 2
        assert data["material_conflicts"]["has_conflicts"] is True
        assert len(data["evidence_units"]) == 1

    @patch("app.main.load_materials")
    @patch("app.main.load_evidence_units")
    @patch("app.main.load_submissions")
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    def test_get_submission_evidence_trace_markdown_and_download(
        self,
        mock_ensure,
        mock_load_projects,
        mock_load_submissions,
        mock_load_units,
        mock_load_materials,
        client,
    ):
        mock_load_projects.return_value = [{"id": "p1", "name": "项目1", "meta": {}}]
        mock_load_submissions.return_value = [
            {
                "id": "s1",
                "project_id": "p1",
                "filename": "施工组织设计.pdf",
                "text": "工期120天。",
                "created_at": "2026-02-27T00:00:00+00:00",
                "report": {
                    "requirement_hits": [],
                    "material_consistency": {"by_material_type": {}},
                    "meta": {},
                },
            }
        ]
        mock_load_units.return_value = []
        mock_load_materials.return_value = []

        md_resp = client.get("/api/v1/projects/p1/submissions/s1/evidence_trace/markdown")
        assert md_resp.status_code == 200
        markdown = md_resp.json()["markdown"]
        assert "# 评分证据追溯报告" in markdown
        assert "施组ID" in markdown

        file_resp = client.get("/api/v1/projects/p1/submissions/s1/evidence_trace.md")
        assert file_resp.status_code == 200
        assert "text/markdown" in file_resp.headers.get("content-type", "")
        assert "attachment; filename=" in file_resp.headers.get("content-disposition", "")

    @patch("app.main.load_materials")
    @patch("app.main.load_evidence_units")
    @patch("app.main._submission_is_scored")
    @patch("app.main.load_submissions")
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    def test_get_latest_submission_evidence_trace_prefers_scored(
        self,
        mock_ensure,
        mock_load_projects,
        mock_load_submissions,
        mock_submission_scored,
        mock_load_units,
        mock_load_materials,
        client,
    ):
        mock_load_projects.return_value = [{"id": "p1", "name": "项目1", "meta": {}}]
        mock_load_submissions.return_value = [
            {
                "id": "s_new_pending",
                "project_id": "p1",
                "filename": "待评分施组.pdf",
                "created_at": "2026-02-27T10:00:00+00:00",
                "report": {"scoring_status": "pending"},
                "text": "",
            },
            {
                "id": "s_old_scored",
                "project_id": "p1",
                "filename": "已评分施组.pdf",
                "created_at": "2026-02-27T09:00:00+00:00",
                "report": {"scoring_status": "scored", "requirement_hits": [], "meta": {}},
                "text": "施组文本",
            },
        ]
        mock_submission_scored.side_effect = [False, True]
        mock_load_units.return_value = []
        mock_load_materials.return_value = []

        response = client.get("/api/v1/projects/p1/evidence_trace/latest")
        assert response.status_code == 200
        data = response.json()
        assert data["submission_id"] == "s_old_scored"
        assert data["filename"] == "已评分施组.pdf"

    @patch("app.main.load_submissions")
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    def test_get_latest_submission_scoring_basis_success(
        self,
        mock_ensure,
        mock_load_projects,
        mock_load_submissions,
        client,
    ):
        mock_load_projects.return_value = [{"id": "p1", "name": "项目1", "meta": {}}]
        mock_load_submissions.return_value = [
            {
                "id": "s1",
                "project_id": "p1",
                "filename": "施工组织设计.pdf",
                "text": "工期120天，关键节点分三段推进。",
                "created_at": "2026-02-27T00:00:00+00:00",
                "report": {
                    "scoring_status": "scored",
                    "requirement_hits": [],
                    "meta": {
                        "input_injection": {
                            "mece_inputs": {
                                "project_materials_extracted": True,
                                "shigong_parsed": True,
                                "bid_requirements_loaded": True,
                                "attention_16d_weights_injected": True,
                                "custom_instructions_injected": True,
                                "materials_quality_gate_passed": True,
                            }
                        },
                        "material_quality": {"total_files": 4, "total_parsed_chars": 22000},
                        "material_retrieval": {"chunks": 18, "requirements": 16},
                        "material_utilization": {
                            "retrieval_hit_rate": 0.72,
                            "retrieval_file_coverage_rate": 0.8,
                        },
                        "material_utilization_gate": {
                            "passed": True,
                            "blocked": False,
                            "reasons": [],
                        },
                        "evidence_trace": {
                            "total_requirements": 20,
                            "total_hits": 14,
                            "mandatory_hit_rate": 0.75,
                            "source_files_hit_count": 3,
                            "source_files_hit": ["招标文件.pdf", "工程量清单.xlsx", "图纸.dxf"],
                        },
                    },
                },
            }
        ]

        response = client.get("/api/v1/projects/p1/scoring_basis/latest")
        assert response.status_code == 200
        data = response.json()
        assert data["project_id"] == "p1"
        assert data["submission_id"] == "s1"
        assert data["mece_inputs"]["materials_quality_gate_passed"] is True
        assert data["material_utilization"]["retrieval_hit_rate"] == pytest.approx(0.72, abs=1e-6)
        assert data["evidence_trace"]["source_files_hit_count"] == 3
        assert data["material_retrieval"]["feedback_evolution_requirements"] == 0
        assert data["material_retrieval"]["feature_confidence_requirements"] == 0
        assert data["material_retrieval"]["feedback_evolution_preview"] == []
        assert data["material_retrieval"]["feature_confidence_preview"] == []
        assert isinstance(data["current_runtime_constraints"]["weights_source"], str)
        assert isinstance(
            data["current_runtime_constraints"]["effective_multipliers_preview"], list
        )

    @patch("app.main._build_project_scoring_diagnostic")
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    def test_get_latest_project_scoring_diagnostic_success(
        self,
        mock_ensure,
        mock_load_projects,
        mock_build_diagnostic,
        client,
    ):
        mock_load_projects.return_value = [{"id": "p1", "name": "项目1", "meta": {}}]
        mock_build_diagnostic.return_value = {
            "project_id": "p1",
            "generated_at": "2026-03-07T10:00:00+08:00",
            "readiness": {
                "project_id": "p1",
                "ready": True,
                "score_button_enabled": True,
                "gate_passed": True,
                "issues": [],
                "warnings": [],
                "material_quality": {"total_files": 4},
                "material_gate": {"required_types": ["tender_qa", "boq", "drawing"]},
                "material_depth_gate": {"passed": True, "enforce": True},
                "submissions": {"non_empty": 1},
                "retrieval_policy": {},
                "generated_at": "2026-03-07T10:00:00+08:00",
            },
            "material_depth": {
                "project_id": "p1",
                "generated_at": "2026-03-07T10:00:00+08:00",
                "ready_to_score": True,
                "capabilities": {"ocr_available": True},
                "gate": {"passed": True},
                "depth_gate": {"passed": True, "enforce": True},
                "quality_summary": {"total_files": 4, "total_parsed_chars": 28000},
                "by_type": [],
                "recommendations": [],
            },
            "latest_submission": {
                "exists": True,
                "submission_id": "s1",
                "filename": "施工组织设计.pdf",
                "created_at": "2026-03-07T09:00:00+08:00",
                "scoring_status": "scored",
                "is_scored": True,
            },
            "evidence_trace": {
                "project_id": "p1",
                "submission_id": "s1",
                "filename": "施工组织设计.pdf",
                "generated_at": "2026-03-07T10:00:00+08:00",
                "summary": {
                    "total_requirements": 18,
                    "total_hits": 12,
                    "mandatory_hit_rate": 0.8,
                    "source_files_hit_count": 3,
                    "source_files_hit": ["招标文件.pdf", "清单.xlsx", "图纸.dxf"],
                },
                "by_dimension": [],
                "requirement_hits": [],
                "evidence_units": [],
                "material_conflicts": {"conflict_count": 1, "high_severity_count": 0},
                "recommendations": [],
            },
            "scoring_basis": {
                "project_id": "p1",
                "submission_id": "s1",
                "filename": "施工组织设计.pdf",
                "generated_at": "2026-03-07T10:00:00+08:00",
                "scoring_status": "scored",
                "mece_inputs": {"materials_quality_gate_passed": True},
                "material_quality": {"total_files": 4},
                "material_retrieval": {
                    "chunks": 20,
                    "feedback_evolution_requirements": 1,
                    "feature_confidence_requirements": 1,
                    "feedback_evolution_preview": [
                        {
                            "dimension_id": "09",
                            "label": "进度计划体系与纠偏阈值",
                            "hints": ["关键节点", "偏差阈值"],
                            "multiplier": 1.16,
                            "sample_count": 4,
                        }
                    ],
                    "feature_confidence_preview": [
                        {
                            "dimension_id": "14",
                            "label": "图纸会审、深化设计与变更闭环",
                            "hints": ["会审问题单", "关闭条件"],
                            "feature_ids": ["feat-1"],
                            "feature_confidence_scores": [0.88],
                        }
                    ],
                },
                "material_utilization": {"retrieval_hit_rate": 0.75},
                "material_utilization_gate": {"blocked": False, "reasons": []},
                "evidence_trace": {"mandatory_hit_rate": 0.8},
                "current_runtime_constraints": {
                    "weights_source": "evolution",
                    "effective_multipliers_preview": [
                        {
                            "dimension_id": "09",
                            "dimension_name": "进度计划体系与纠偏阈值",
                            "multiplier": 1.16,
                        }
                    ],
                    "feedback_evolution_requirements": 1,
                    "feature_confidence_requirements": 1,
                },
                "recommendations": [],
            },
            "material_type_cards": [
                {
                    "material_type": "tender_qa",
                    "material_type_label": "招标文件和答疑",
                    "required": True,
                    "in_scope": True,
                    "status": "active",
                    "status_label": "已参与评分",
                    "files": 1,
                    "parse_status_counts": {"parsed": 2},
                    "queued_count": 0,
                    "processing_count": 0,
                    "parsed_count": 2,
                    "failed_count": 0,
                    "parse_backend_summary": ["GPT-5.4×2"],
                    "parse_confidence_avg": 0.86,
                    "parse_confidence_max": 0.93,
                    "parse_error_preview": [],
                    "parsed_chars": 12000,
                    "parsed_chunks": 12,
                    "numeric_terms": 8,
                    "meets_chars": True,
                    "meets_chunks": True,
                    "meets_numeric_terms": True,
                    "retrieval_hit": 4,
                    "retrieval_total": 6,
                    "consistency_hit": 2,
                    "consistency_total": 3,
                    "fallback_hit": 0,
                    "fallback_total": 0,
                    "has_evidence": True,
                    "uploaded_filenames": ["招标文件.pdf", "答疑纪要.docx"],
                    "uploaded_filename_count": 2,
                    "hit_filenames": ["招标文件.pdf"],
                    "hit_filename_count": 1,
                    "hit_requirement_labels": ["工期节点", "质量目标"],
                    "hit_requirement_count": 2,
                    "miss_requirement_labels": ["危大工程清单"],
                    "miss_requirement_count": 1,
                    "hit_evidence_preview": [
                        {
                            "label": "工期节点",
                            "source_filename": "招标文件.pdf",
                            "source_mode": "retrieval",
                            "reason": "keywords",
                        },
                        {
                            "label": "质量目标",
                            "source_filename": "答疑纪要.docx",
                            "source_mode": "retrieval",
                            "reason": "keywords",
                        },
                    ],
                    "miss_evidence_preview": [
                        {
                            "label": "危大工程清单",
                            "source_filename": "招标文件.pdf",
                            "source_mode": "material_consistency",
                            "reason": "material_consistency:t1/2;n0/1",
                        },
                    ],
                    "conflict_labels": ["危大工程清单"],
                    "conflict_label_count": 1,
                    "project_numeric_terms": ["90", "1200", "48"],
                    "project_numeric_term_count": 3,
                    "project_numeric_category_summary": [
                        "工期/节点：90",
                        "规格/参数：1200",
                        "阈值/偏差：48",
                    ],
                    "hit_numeric_terms": ["90", "1200", "7"],
                    "hit_numeric_term_count": 3,
                    "expected_numeric_terms": ["90", "1200", "7", "48"],
                    "expected_numeric_term_count": 4,
                    "missing_numeric_terms": ["48"],
                    "missing_numeric_term_count": 1,
                    "hit_numeric_category_summary": ["工期/节点：90", "规格/参数：1200、7"],
                    "expected_numeric_category_summary": [
                        "工期/节点：90",
                        "规格/参数：1200、7",
                        "阈值/偏差：48",
                    ],
                    "missing_numeric_category_summary": ["阈值/偏差：48"],
                    "guidance": ["招标文件和答疑已进入评分证据链。"],
                }
            ],
            "dimension_support_cards": [
                {
                    "dimension_id": "01",
                    "dimension_name": "总体部署与信息化管理",
                    "coverage_score": 0.82,
                    "coverage_level": "high",
                    "keyword_hits": 10,
                    "numeric_signal_hits": 2,
                    "source_types": ["tender_qa", "drawing"],
                    "source_file_count": 2,
                    "source_files_preview": ["招标文件.pdf", "总图.dxf"],
                    "suggested_keywords": ["工程范围", "项目理解"],
                },
                {
                    "dimension_id": "07",
                    "dimension_name": "危大工程闭环管理",
                    "coverage_score": 0.21,
                    "coverage_level": "low",
                    "keyword_hits": 1,
                    "numeric_signal_hits": 0,
                    "source_types": [],
                    "source_file_count": 0,
                    "source_files_preview": [],
                    "suggested_keywords": ["危大工程", "专项方案", "监测"],
                },
            ],
            "summary": {
                "latest_submission_exists": True,
                "latest_submission_scored": True,
                "evidence_total_hits": 12,
                "retrieval_hit_rate": 0.75,
                "material_dimension_coverage_rate": 0.625,
                "material_low_coverage_dimensions": 6,
                "material_covered_dimensions": 10,
                "material_numeric_category_summary": ["工期/节点：90", "规格/参数：1200"],
                "parse_job_summary": {
                    "total_jobs": 2,
                    "status_counts": {"parsed": 2},
                    "backlog": 0,
                    "failed_jobs": 0,
                    "gpt_jobs": 2,
                    "gpt_ratio": 1.0,
                },
                "parse_total_jobs": 2,
                "parse_backlog": 0,
                "parse_failed_jobs": 0,
                "parse_gpt_ratio": 1.0,
                "parsed_materials": 2,
                "queued_materials": 0,
                "processing_materials": 0,
                "failed_materials": 0,
                "current_weights_source": "evolution",
                "current_feedback_evolution_requirements": 1,
                "current_feature_confidence_requirements": 1,
                "recent_feedback_context_active": True,
            },
            "recommendations": ["补充图纸中的设备型号锚点。"],
        }

        response = client.get("/api/v1/projects/p1/scoring_diagnostic/latest")
        assert response.status_code == 200
        data = response.json()
        assert data["project_id"] == "p1"
        assert data["latest_submission"]["filename"] == "施工组织设计.pdf"
        assert data["summary"]["evidence_total_hits"] == 12
        assert data["summary"]["retrieval_hit_rate"] == pytest.approx(0.75, abs=1e-6)
        assert data["evidence_trace"]["summary"]["source_files_hit_count"] == 3
        assert data["scoring_basis"]["material_retrieval"]["feedback_evolution_requirements"] == 1
        assert data["scoring_basis"]["material_retrieval"]["feature_confidence_requirements"] == 1
        assert (
            data["scoring_basis"]["material_retrieval"]["feedback_evolution_preview"][0][
                "dimension_id"
            ]
            == "09"
        )
        assert (
            data["scoring_basis"]["material_retrieval"]["feature_confidence_preview"][0][
                "dimension_id"
            ]
            == "14"
        )
        assert data["scoring_basis"]["current_runtime_constraints"]["weights_source"] == "evolution"
        assert data["summary"]["current_weights_source"] == "evolution"
        assert data["scoring_basis"]["material_utilization"]["retrieval_hit_rate"] == pytest.approx(
            0.75, abs=1e-6
        )
        assert len(data["material_type_cards"]) == 1
        assert data["material_type_cards"][0]["status"] == "active"
        assert data["material_type_cards"][0]["parse_backend_summary"] == ["GPT-5.4×2"]
        assert data["material_type_cards"][0]["parse_confidence_avg"] == pytest.approx(
            0.86, abs=1e-6
        )
        assert data["material_type_cards"][0]["uploaded_filenames"] == [
            "招标文件.pdf",
            "答疑纪要.docx",
        ]
        assert data["material_type_cards"][0]["hit_filenames"] == ["招标文件.pdf"]
        assert data["material_type_cards"][0]["hit_requirement_labels"] == ["工期节点", "质量目标"]
        assert data["material_type_cards"][0]["hit_evidence_preview"][0]["label"] == "工期节点"
        assert data["material_type_cards"][0]["miss_evidence_preview"][0]["label"] == "危大工程清单"
        assert data["material_type_cards"][0]["conflict_labels"] == ["危大工程清单"]
        assert data["material_type_cards"][0]["project_numeric_terms"] == ["90", "1200", "48"]
        assert data["material_type_cards"][0]["project_numeric_category_summary"] == [
            "工期/节点：90",
            "规格/参数：1200",
            "阈值/偏差：48",
        ]
        assert data["material_type_cards"][0]["hit_numeric_terms"] == ["90", "1200", "7"]
        assert data["material_type_cards"][0]["missing_numeric_terms"] == ["48"]
        assert data["material_type_cards"][0]["hit_numeric_category_summary"] == [
            "工期/节点：90",
            "规格/参数：1200、7",
        ]
        assert data["material_type_cards"][0]["missing_numeric_category_summary"] == [
            "阈值/偏差：48"
        ]
        assert data["dimension_support_cards"][0]["dimension_id"] == "01"
        assert data["dimension_support_cards"][1]["coverage_level"] == "low"
        assert data["summary"]["material_dimension_coverage_rate"] == pytest.approx(0.625, abs=1e-6)
        assert data["summary"]["material_low_coverage_dimensions"] == 6
        assert data["summary"]["parse_total_jobs"] == 2
        assert data["summary"]["parse_gpt_ratio"] == pytest.approx(1.0, abs=1e-6)
        assert data["summary"]["recent_feedback_context_active"] is True

    @patch("app.main._build_project_scoring_diagnostic")
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    def test_get_latest_project_scoring_diagnostic_without_submission(
        self,
        mock_ensure,
        mock_load_projects,
        mock_build_diagnostic,
        client,
    ):
        mock_load_projects.return_value = [{"id": "p1", "name": "项目1", "meta": {}}]
        mock_build_diagnostic.return_value = {
            "project_id": "p1",
            "generated_at": "2026-03-07T10:00:00+08:00",
            "readiness": {
                "project_id": "p1",
                "ready": False,
                "score_button_enabled": False,
                "gate_passed": False,
                "issues": ["缺少施组文件"],
                "warnings": [],
                "material_quality": {},
                "material_gate": {},
                "material_depth_gate": {},
                "submissions": {"non_empty": 0},
                "retrieval_policy": {},
                "generated_at": "2026-03-07T10:00:00+08:00",
            },
            "material_depth": {
                "project_id": "p1",
                "generated_at": "2026-03-07T10:00:00+08:00",
                "ready_to_score": False,
                "capabilities": {},
                "gate": {},
                "depth_gate": {},
                "quality_summary": {"total_files": 0},
                "by_type": [],
                "recommendations": ["请先上传项目资料。"],
            },
            "latest_submission": {
                "exists": False,
                "submission_id": None,
                "filename": None,
                "created_at": None,
                "scoring_status": None,
                "is_scored": False,
            },
            "evidence_trace": None,
            "scoring_basis": None,
            "material_type_cards": [],
            "summary": {
                "latest_submission_exists": False,
                "latest_submission_scored": False,
                "evidence_total_hits": 0,
                "retrieval_hit_rate": None,
            },
            "recommendations": ["暂无施组评分证据链，请先上传并评分至少 1 份施组。"],
        }

        response = client.get("/api/v1/projects/p1/scoring_diagnostic/latest")
        assert response.status_code == 200
        data = response.json()
        assert data["latest_submission"]["exists"] is False
        assert data["evidence_trace"] is None
        assert data["scoring_basis"] is None
        assert data["summary"]["latest_submission_exists"] is False

    def test_build_material_conflict_summary_from_report(self):
        from app.main import _build_material_conflict_summary_from_report

        report = {
            "requirement_hits": [
                {
                    "dimension_id": "09",
                    "label": "工期里程碑",
                    "hit": False,
                    "mandatory": True,
                    "reason": "命中不足 t1/3 n0/2",
                    "source_pack_id": "runtime_material_consistency",
                    "material_type": "boq",
                    "source_filename": "工程量清单.xlsx",
                    "chunk_id": "工程量清单.xlsx#c3",
                    "source_mode": "consistency_check",
                }
            ],
            "material_consistency": {
                "by_material_type": {
                    "boq": {
                        "total": 1,
                        "hit": 0,
                        "mandatory_total": 1,
                        "mandatory_hit": 0,
                        "hit_rate": 0.0,
                    }
                }
            },
        }
        payload = _build_material_conflict_summary_from_report(report)
        assert payload["has_conflicts"] is True
        assert payload["conflict_count"] == 1
        assert payload["high_severity_count"] >= 1
        assert payload["conflicts"][0]["conflict_kind"] in {
            "numeric_mismatch",
            "term_coverage_missing",
            "material_consistency_missing",
        }

    @patch("app.main.save_patch_deployments")
    @patch("app.main.load_patch_deployments")
    @patch("app.main.save_patch_packages")
    @patch("app.main.evaluate_patch_shadow")
    @patch("app.main.load_patch_packages")
    def test_auto_govern_deployed_patch_rolls_back_when_shadow_failed(
        self,
        mock_load_patch_packages,
        mock_eval_shadow,
        mock_save_patch_packages,
        mock_load_patch_deployments,
        mock_save_patch_deployments,
    ):
        from app.main import _auto_govern_deployed_patch

        mock_load_patch_packages.return_value = [
            {
                "id": "patch_new",
                "project_id": "p1",
                "status": "deployed",
                "rollback_pointer": "patch_old",
                "patch_payload": {"penalty_multiplier": {"P-EMPTY-002": 1.1}},
                "updated_at": "2026-02-27T10:00:00+00:00",
            },
            {
                "id": "patch_old",
                "project_id": "p1",
                "status": "shadow_pass",
                "patch_payload": {},
                "updated_at": "2026-02-27T09:00:00+00:00",
            },
        ]
        mock_eval_shadow.return_value = {
            "ok": True,
            "patch_id": "patch_new",
            "gate_passed": False,
            "metrics_before_after": {
                "mae_before": 2.0,
                "mae_after": 2.5,
                "sample_count": 5,
                "delta_mae": 0.5,
            },
        }
        mock_load_patch_deployments.return_value = []

        result = _auto_govern_deployed_patch(
            project_id="p1",
            delta_cases=[{"id": "d1"}, {"id": "d2"}, {"id": "d3"}, {"id": "d4"}, {"id": "d5"}],
        )
        assert result["checked"] is True
        assert result["action"] == "rollback"
        assert result["rolled_back"] is True
        assert result["rollback_to_patch_id"] == "patch_old"

        saved_packages = mock_save_patch_packages.call_args[0][0]
        status_map = {str(p.get("id")): str(p.get("status")) for p in saved_packages}
        assert status_map["patch_new"] == "rolled_back"
        assert status_map["patch_old"] == "deployed"

        saved_deploys = mock_save_patch_deployments.call_args[0][0]
        actions = [str(d.get("action")) for d in saved_deploys]
        assert "auto_rollback" in actions
        assert "auto_promote_rollback_pointer" in actions


class TestAdaptiveEndpoints:
    """Tests for /projects/{project_id}/adaptive* endpoints."""

    @patch("app.main.build_adaptive_suggestions")
    @patch("app.main.load_config")
    @patch("app.main.load_submissions")
    @patch("app.main.ensure_data_dirs")
    def test_adaptive_suggestions_success(
        self, mock_ensure, mock_load, mock_config, mock_build, client
    ):
        """Adaptive suggestions should return suggestions."""
        mock_load.return_value = [
            {
                "id": "s1",
                "project_id": "p1",
                "total_score": 86.0,
                "report": {"total_score": 86.0, "scoring_status": "scored"},
            }
        ]
        mock_config.return_value = MagicMock(lexicon={})
        mock_build.return_value = {"penalty_stats": {}, "suggestions": []}
        response = client.get("/api/v1/projects/p1/adaptive")
        assert response.status_code == 200

    @patch("app.main.load_submissions")
    @patch("app.main.ensure_data_dirs")
    def test_adaptive_no_submissions(self, mock_ensure, mock_load, client):
        """Adaptive should return 404 if no submissions."""
        mock_load.return_value = []
        response = client.get("/api/v1/projects/p1/adaptive")
        assert response.status_code == 404

    @patch("app.main.build_adaptive_patch")
    @patch("app.main.build_adaptive_suggestions")
    @patch("app.main.load_config")
    @patch("app.main.load_submissions")
    @patch("app.main.ensure_data_dirs")
    def test_adaptive_patch_success(
        self, mock_ensure, mock_load, mock_config, mock_suggestions, mock_patch, client
    ):
        """Adaptive patch should return patch."""
        mock_load.return_value = [{"id": "s1", "project_id": "p1"}]
        mock_config.return_value = MagicMock(lexicon={})
        mock_suggestions.return_value = {"penalty_stats": {}}
        mock_patch.return_value = {"lexicon_additions": {}, "rubric_adjustments": {}}
        response = client.get("/api/v1/projects/p1/adaptive_patch")
        assert response.status_code == 200

    @patch("app.main.load_submissions")
    @patch("app.main.ensure_data_dirs")
    def test_adaptive_patch_no_submissions(self, mock_ensure, mock_load, client):
        """Adaptive patch should return 404 if no submissions."""
        mock_load.return_value = []
        response = client.get("/api/v1/projects/p1/adaptive_patch")
        assert response.status_code == 404

    @patch("app.main.load_submissions")
    @patch("app.main.ensure_data_dirs")
    def test_adaptive_validate_no_submissions(self, mock_ensure, mock_load, client):
        """Adaptive validate should return 404 if no submissions."""
        mock_load.return_value = []
        response = client.get("/api/v1/projects/p1/adaptive_validate")
        assert response.status_code == 404

    @patch("app.main.score_text")
    @patch("app.main.load_config")
    @patch("app.main.load_submissions")
    @patch("app.main.ensure_data_dirs")
    def test_adaptive_validate_success(
        self, mock_ensure, mock_load, mock_config, mock_score, client
    ):
        """Adaptive validate should compare old and new scores."""
        mock_load.return_value = [
            {
                "id": "s1",
                "project_id": "p1",
                "filename": "f1.txt",
                "total_score": 80.0,
                "text": "test text",
            }
        ]
        mock_config.return_value = MagicMock(rubric={}, lexicon={})
        mock_score.return_value = MagicMock(model_dump=lambda: {"total_score": 82.0})
        response = client.get("/api/v1/projects/p1/adaptive_validate")
        assert response.status_code == 200
        data = response.json()
        assert data["avg_delta"] == 2.0
        assert len(data["comparisons"]) == 1

    @patch("app.main.score_text")
    @patch("app.main.load_config")
    @patch("app.main.load_submissions")
    @patch("app.main.ensure_data_dirs")
    def test_adaptive_validate_no_text(
        self, mock_ensure, mock_load, mock_config, mock_score, client
    ):
        """Adaptive validate should skip submissions without text."""
        mock_load.return_value = [
            {"id": "s1", "project_id": "p1", "filename": "f1.txt", "total_score": 80.0}
        ]
        mock_config.return_value = MagicMock(rubric={}, lexicon={})
        response = client.get("/api/v1/projects/p1/adaptive_validate")
        assert response.status_code == 200
        data = response.json()
        assert data["avg_delta"] == 0.0
        assert len(data["comparisons"]) == 0


class TestInsightsEndpoint:
    """Tests for /projects/{project_id}/insights endpoint."""

    @patch("app.main.build_project_insights")
    @patch("app.main.load_submissions")
    @patch("app.main.ensure_data_dirs")
    def test_insights_success(self, mock_ensure, mock_load, mock_insights, client):
        """Insights should return project insights."""
        mock_load.return_value = [
            {
                "id": "s1",
                "project_id": "p1",
                "total_score": 88.0,
                "report": {"total_score": 88.0, "scoring_status": "scored"},
            }
        ]
        mock_insights.return_value = {
            "dimension_avg": {},
            "weakest_dims": [],
            "frequent_penalties": [],
            "recommendations": [],
        }
        response = client.get("/api/v1/projects/p1/insights")
        assert response.status_code == 200
        data = response.json()
        assert data["project_id"] == "p1"

    @patch("app.main.load_submissions")
    @patch("app.main.ensure_data_dirs")
    def test_insights_no_submissions(self, mock_ensure, mock_load, client):
        """Insights should return 404 if no submissions."""
        mock_load.return_value = []
        response = client.get("/api/v1/projects/p1/insights")
        assert response.status_code == 404

    @patch("app.main.load_submissions")
    @patch("app.main.ensure_data_dirs")
    def test_insights_requires_scored_submissions(self, mock_ensure, mock_load, client):
        """Insights should require scored submissions."""
        mock_load.return_value = [
            {
                "id": "s1",
                "project_id": "p1",
                "total_score": 0.0,
                "report": {"scoring_status": "pending"},
            }
        ]
        response = client.get("/api/v1/projects/p1/insights")
        assert response.status_code == 404
        assert "请先点击“评分施组”" in response.json()["detail"]


class TestLearningEndpoints:
    """Tests for /projects/{project_id}/learning endpoints."""

    @patch("app.main.save_learning_profiles")
    @patch("app.main.load_learning_profiles")
    @patch("app.main.build_learning_profile")
    @patch("app.main.load_submissions")
    @patch("app.main.ensure_data_dirs")
    def test_update_learning_profile_success(
        self, mock_ensure, mock_load_sub, mock_build, mock_load_prof, mock_save, client
    ):
        """Update learning profile should save and return profile."""
        mock_load_sub.return_value = [
            {
                "id": "s1",
                "project_id": "p1",
                "total_score": 90.0,
                "report": {"total_score": 90.0, "scoring_status": "scored"},
            }
        ]
        mock_build.return_value = {
            "dimension_multipliers": {"D01": 1.1},
            "rationale": {"D01": "test rationale"},
        }
        mock_load_prof.return_value = []
        response = client.post("/api/v1/projects/p1/learning")
        assert response.status_code == 200
        data = response.json()
        assert data["project_id"] == "p1"
        mock_save.assert_called_once()

    @patch("app.main.save_learning_profiles")
    @patch("app.main.load_learning_profiles")
    @patch("app.main.build_learning_profile")
    @patch("app.main.load_submissions")
    @patch("app.main.ensure_data_dirs")
    def test_update_learning_profile_replaces_existing(
        self, mock_ensure, mock_load_sub, mock_build, mock_load_prof, mock_save, client
    ):
        """Update learning profile should replace existing profile."""
        mock_load_sub.return_value = [
            {
                "id": "s1",
                "project_id": "p1",
                "total_score": 90.0,
                "report": {"total_score": 90.0, "scoring_status": "scored"},
            }
        ]
        mock_build.return_value = {
            "dimension_multipliers": {"D01": 1.2},
            "rationale": {"D01": "new rationale"},
        }
        mock_load_prof.return_value = [{"project_id": "p1", "dimension_multipliers": {"D01": 1.0}}]
        response = client.post("/api/v1/projects/p1/learning")
        assert response.status_code == 200
        # Verify old profile was removed
        call_args = mock_save.call_args[0][0]
        p1_profiles = [p for p in call_args if p.get("project_id") == "p1"]
        assert len(p1_profiles) == 1

    @patch("app.main.load_submissions")
    @patch("app.main.ensure_data_dirs")
    def test_update_learning_profile_no_submissions(self, mock_ensure, mock_load, client):
        """Update learning profile should return 404 if no submissions."""
        mock_load.return_value = []
        response = client.post("/api/v1/projects/p1/learning")
        assert response.status_code == 404

    @patch("app.main.load_submissions")
    @patch("app.main.ensure_data_dirs")
    def test_update_learning_profile_requires_scored_submissions(
        self, mock_ensure, mock_load, client
    ):
        """Learning profile update should require scored submissions."""
        mock_load.return_value = [
            {
                "id": "s1",
                "project_id": "p1",
                "total_score": 0.0,
                "report": {"scoring_status": "pending"},
            }
        ]
        response = client.post("/api/v1/projects/p1/learning")
        assert response.status_code == 404
        assert "请先点击“评分施组”" in response.json()["detail"]

    @patch("app.main.load_learning_profiles")
    @patch("app.main.ensure_data_dirs")
    def test_get_learning_profile_success(self, mock_ensure, mock_load, client):
        """Get learning profile should return existing profile."""
        mock_load.return_value = [
            {
                "project_id": "p1",
                "dimension_multipliers": {"D01": 1.1},
                "rationale": {"D01": "test"},
                "updated_at": "2026-01-01T00:00:00Z",
            }
        ]
        response = client.get("/api/v1/projects/p1/learning")
        assert response.status_code == 200
        data = response.json()
        assert data["project_id"] == "p1"

    @patch("app.main.load_learning_profiles")
    @patch("app.main.ensure_data_dirs")
    def test_get_learning_profile_not_found(self, mock_ensure, mock_load, client):
        """Get learning profile should return 404 if not found."""
        mock_load.return_value = []
        response = client.get("/api/v1/projects/p1/learning")
        assert response.status_code == 404
        assert "暂无学习画像" in response.json()["detail"]


class TestAdaptiveApplyEndpoint:
    """Tests for /projects/{project_id}/adaptive_apply endpoint."""

    @patch("app.main.load_submissions")
    @patch("app.main.ensure_data_dirs")
    def test_adaptive_apply_no_submissions(self, mock_ensure, mock_load, client):
        """Adaptive apply should return 404 if no submissions."""
        mock_load.return_value = []
        response = client.post("/api/v1/projects/p1/adaptive_apply")
        assert response.status_code == 404

    @patch("yaml.safe_dump")
    @patch("pathlib.Path")
    @patch("app.main.apply_adaptive_patch")
    @patch("app.main.build_adaptive_patch")
    @patch("app.main.build_adaptive_suggestions")
    @patch("app.main.load_config")
    @patch("app.main.load_submissions")
    @patch("app.main.ensure_data_dirs")
    def test_adaptive_apply_success(
        self,
        mock_ensure,
        mock_load_sub,
        mock_config,
        mock_suggestions,
        mock_patch,
        mock_apply,
        mock_path,
        mock_yaml_dump,
        client,
    ):
        """Adaptive apply should update lexicon and return result."""
        mock_load_sub.return_value = [{"id": "s1", "project_id": "p1"}]
        mock_config.return_value = MagicMock(lexicon={})
        mock_suggestions.return_value = {"penalty_stats": {}}
        mock_patch.return_value = {"lexicon_additions": {}, "rubric_adjustments": {}}
        mock_apply.return_value = ({"updated": True}, ["change1", "change2"])

        # Mock Path operations
        mock_lexicon_path = MagicMock()
        mock_lexicon_path.read_text.return_value = "old: content"
        mock_backup_path = MagicMock()
        mock_backup_path.__str__ = MagicMock(return_value="/backup/path.yaml")
        mock_lexicon_path.with_name.return_value = mock_backup_path

        # Configure Path mock chain
        mock_path_instance = MagicMock()
        mock_path_instance.resolve.return_value.parent.__truediv__ = lambda self, x: (
            mock_lexicon_path
        )
        mock_path.return_value = mock_path_instance

        response = client.post("/api/v1/projects/p1/adaptive_apply")
        assert response.status_code == 200
        data = response.json()
        assert data["project_id"] == "p1"
        assert data["applied"] is True
        assert "changes" in data


class TestMainExecution:
    """Tests for main module execution."""

    def test_app_title(self):
        """App should have correct title."""
        assert app.title == "青天评标系统 API"

    def test_app_version(self):
        """App should have correct version."""
        assert app.version == "1.0.0"


class TestApiI18n:
    """Tests for API i18n support via Accept-Language header."""

    def test_parse_accept_language_none(self):
        """parse_accept_language should return default locale for None."""
        from app.main import parse_accept_language

        assert parse_accept_language(None) == "zh"

    def test_parse_accept_language_empty(self):
        """parse_accept_language should return default locale for empty string."""
        from app.main import parse_accept_language

        assert parse_accept_language("") == "zh"

    def test_parse_accept_language_simple_zh(self):
        """parse_accept_language should parse simple zh."""
        from app.main import parse_accept_language

        assert parse_accept_language("zh") == "zh"

    def test_parse_accept_language_simple_en(self):
        """parse_accept_language should parse simple en."""
        from app.main import parse_accept_language

        assert parse_accept_language("en") == "en"

    def test_parse_accept_language_with_region(self):
        """parse_accept_language should handle regional variants."""
        from app.main import parse_accept_language

        assert parse_accept_language("zh-CN") == "zh"
        assert parse_accept_language("en-US") == "en"
        assert parse_accept_language("en-GB") == "en"

    def test_parse_accept_language_with_quality(self):
        """parse_accept_language should respect quality values."""
        from app.main import parse_accept_language

        # en has higher quality
        assert parse_accept_language("zh;q=0.8,en;q=0.9") == "en"
        # zh has higher quality
        assert parse_accept_language("en;q=0.8,zh;q=0.9") == "zh"

    def test_parse_accept_language_complex(self):
        """parse_accept_language should handle complex Accept-Language."""
        from app.main import parse_accept_language

        assert parse_accept_language("en-US,en;q=0.9,zh;q=0.8") == "en"
        assert parse_accept_language("zh-CN,zh;q=0.9,en;q=0.8") == "zh"

    def test_parse_accept_language_unsupported(self):
        """parse_accept_language should fall back for unsupported languages."""
        from app.main import parse_accept_language

        assert parse_accept_language("fr") == "zh"
        assert parse_accept_language("de-DE,fr;q=0.9") == "zh"

    def test_parse_accept_language_invalid_quality(self):
        """parse_accept_language should handle invalid quality values."""
        from app.main import parse_accept_language

        assert parse_accept_language("en;q=invalid") == "en"

    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    def test_upload_material_error_zh(self, mock_ensure, mock_load, client):
        """Upload material should return Chinese error with Accept-Language: zh."""
        mock_load.return_value = []
        response = client.post(
            "/api/v1/projects/nonexistent/materials",
            files={"file": ("test.txt", BytesIO(b"test"), "text/plain")},
            headers={"Accept-Language": "zh"},
        )
        assert response.status_code == 404
        assert "项目不存在" in response.json()["detail"]

    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    def test_upload_material_error_en(self, mock_ensure, mock_load, client):
        """Upload material should return English error with Accept-Language: en."""
        mock_load.return_value = []
        response = client.post(
            "/api/v1/projects/nonexistent/materials",
            files={"file": ("test.txt", BytesIO(b"test"), "text/plain")},
            headers={"Accept-Language": "en"},
        )
        assert response.status_code == 404
        assert "Project not found" in response.json()["detail"]

    @patch("app.main.load_submissions")
    @patch("app.main.ensure_data_dirs")
    def test_compare_error_zh(self, mock_ensure, mock_load, client):
        """Compare should return Chinese error with Accept-Language: zh."""
        mock_load.return_value = []
        response = client.get(
            "/api/v1/projects/p1/compare",
            headers={"Accept-Language": "zh"},
        )
        assert response.status_code == 404
        assert "暂无施组记录" in response.json()["detail"]

    @patch("app.main.load_submissions")
    @patch("app.main.ensure_data_dirs")
    def test_compare_error_en(self, mock_ensure, mock_load, client):
        """Compare should return English error with Accept-Language: en."""
        mock_load.return_value = []
        response = client.get(
            "/api/v1/projects/p1/compare",
            headers={"Accept-Language": "en"},
        )
        assert response.status_code == 404
        assert "No submission records" in response.json()["detail"]

    @patch("app.main.load_learning_profiles")
    @patch("app.main.ensure_data_dirs")
    def test_get_learning_profile_error_zh(self, mock_ensure, mock_load, client):
        """Get learning profile should return Chinese error."""
        mock_load.return_value = []
        response = client.get(
            "/api/v1/projects/p1/learning",
            headers={"Accept-Language": "zh"},
        )
        assert response.status_code == 404
        assert "暂无学习画像" in response.json()["detail"]

    @patch("app.main.load_learning_profiles")
    @patch("app.main.ensure_data_dirs")
    def test_get_learning_profile_error_en(self, mock_ensure, mock_load, client):
        """Get learning profile should return English error."""
        mock_load.return_value = []
        response = client.get(
            "/api/v1/projects/p1/learning",
            headers={"Accept-Language": "en"},
        )
        assert response.status_code == 404
        assert "No learning profile" in response.json()["detail"]

    @patch("app.main.reload_config")
    def test_config_reload_message_zh(self, mock_reload, client):
        """Config reload should return Chinese message."""
        response = client.post(
            "/api/v1/config/reload",
            headers={"Accept-Language": "zh"},
        )
        assert response.status_code == 200
        assert "配置已重新加载" in response.json()["message"]

    @patch("app.main.reload_config")
    def test_config_reload_message_en(self, mock_reload, client):
        """Config reload should return English message."""
        response = client.post(
            "/api/v1/config/reload",
            headers={"Accept-Language": "en"},
        )
        assert response.status_code == 200
        assert "Configuration reloaded" in response.json()["message"]

    def test_accept_language_with_region_code(self, client):
        """API should accept regional language codes like zh-CN."""
        from app.main import parse_accept_language

        assert parse_accept_language("zh-CN,zh;q=0.9,en;q=0.8") == "zh"
        assert parse_accept_language("en-US,en;q=0.9,zh;q=0.8") == "en"


class TestEdgeCases:
    """Tests for edge cases and error handling."""

    @patch("app.main.load_submissions")
    @patch("app.main.ensure_data_dirs")
    def test_compare_dimension_avg_calculation(self, mock_ensure, mock_load, client):
        """Compare should correctly calculate dimension averages."""
        mock_load.return_value = [
            {
                "id": "s1",
                "project_id": "p1",
                "filename": "f1.txt",
                "total_score": 80.0,
                "report": {
                    "dimension_scores": {"D01": {"score": 20.0}, "D02": {"score": 30.0}},
                    "penalties": [],
                },
                "created_at": "2026-01-01T00:00:00Z",
            },
            {
                "id": "s2",
                "project_id": "p1",
                "filename": "f2.txt",
                "total_score": 70.0,
                "report": {
                    "dimension_scores": {"D01": {"score": 24.0}, "D02": {"score": 26.0}},
                    "penalties": [],
                },
                "created_at": "2026-01-02T00:00:00Z",
            },
        ]
        response = client.get("/api/v1/projects/p1/compare")
        assert response.status_code == 200
        data = response.json()
        # D01 avg: (20 + 24) / 2 = 22
        assert data["dimension_avg"]["D01"] == 22.0
        # D02 avg: (30 + 26) / 2 = 28
        assert data["dimension_avg"]["D02"] == 28.0

    @patch("app.main.load_submissions")
    @patch("app.main.ensure_data_dirs")
    def test_compare_penalty_stats_aggregation(self, mock_ensure, mock_load, client):
        """Compare should correctly aggregate penalty stats."""
        mock_load.return_value = [
            {
                "id": "s1",
                "project_id": "p1",
                "filename": "f1.txt",
                "total_score": 80.0,
                "report": {
                    "dimension_scores": {},
                    "penalties": [{"code": "P001"}, {"code": "P002"}],
                },
                "created_at": "2026-01-01T00:00:00Z",
            },
            {
                "id": "s2",
                "project_id": "p1",
                "filename": "f2.txt",
                "total_score": 70.0,
                "report": {
                    "dimension_scores": {},
                    "penalties": [{"code": "P001"}],
                },
                "created_at": "2026-01-02T00:00:00Z",
            },
        ]
        response = client.get("/api/v1/projects/p1/compare")
        assert response.status_code == 200
        data = response.json()
        assert data["penalty_stats"]["P001"] == 2
        assert data["penalty_stats"]["P002"] == 1


class TestCacheEndpoints:
    """Tests for cache-related endpoints."""

    def test_cache_stats_returns_200(self, client):
        """GET /api/v1/cache/stats should return 200."""
        response = client.get("/api/v1/cache/stats")
        assert response.status_code == 200

    def test_cache_stats_structure(self, client):
        """Cache stats should have correct structure."""
        response = client.get("/api/v1/cache/stats")
        data = response.json()
        assert "total_requests" in data
        assert "hits" in data
        assert "misses" in data
        assert "evictions" in data
        assert "size" in data
        assert "hit_rate" in data

    def test_cache_stats_types(self, client):
        """Cache stats should have correct types."""
        response = client.get("/api/v1/cache/stats")
        data = response.json()
        assert isinstance(data["total_requests"], int)
        assert isinstance(data["hits"], int)
        assert isinstance(data["misses"], int)
        assert isinstance(data["evictions"], int)
        assert isinstance(data["size"], int)
        assert isinstance(data["hit_rate"], float)

    def test_cache_clear_returns_200(self, client):
        """POST /api/v1/cache/clear should return 200."""
        response = client.post("/api/v1/cache/clear")
        assert response.status_code == 200

    def test_cache_clear_structure(self, client):
        """Cache clear response should have correct structure."""
        response = client.post("/api/v1/cache/clear")
        data = response.json()
        assert "cleared" in data
        assert "count" in data
        assert "message" in data

    def test_cache_clear_zh_message(self, client):
        """Cache clear should return Chinese message with Accept-Language: zh."""
        response = client.post(
            "/api/v1/cache/clear",
            headers={"Accept-Language": "zh"},
        )
        data = response.json()
        # 当缓存为空时，应返回 "缓存为空" 或 "缓存已清空"
        assert "缓存" in data["message"]

    def test_cache_clear_en_message(self, client):
        """Cache clear should return English message with Accept-Language: en."""
        response = client.post(
            "/api/v1/cache/clear",
            headers={"Accept-Language": "en"},
        )
        data = response.json()
        # 应返回 "Cache cleared" 或 "Cache is empty"
        assert "Cache" in data["message"] or "cache" in data["message"]

    @patch("app.main.get_cached_score")
    @patch("app.main.cache_score_result")
    @patch("app.main.load_config")
    @patch("app.main.score_text")
    def test_score_endpoint_uses_cache(
        self, mock_score, mock_config, mock_cache_set, mock_cache_get, client
    ):
        """Score endpoint should check cache first."""
        from app.schemas import LogicLockResult, ScoreReport

        # 模拟缓存未命中
        mock_cache_get.return_value = None
        mock_config.return_value = MagicMock(rubric={}, lexicon={})
        mock_score.return_value = ScoreReport(
            total_score=85.0,
            dimension_scores={},
            logic_lock=LogicLockResult(
                definition_score=1.0,
                analysis_score=1.0,
                solution_score=1.0,
                breaks=[],
                evidence=[],
            ),
            penalties=[],
            suggestions=[],
            meta={},
            judge_mode="local",
            judge_source="scorer",
            fallback_reason="",
        )

        response = client.post("/api/v1/score", json={"text": "测试文本"})
        assert response.status_code == 200

        # 验证缓存被检查
        mock_cache_get.assert_called_once_with("测试文本")
        # 验证结果被缓存
        mock_cache_set.assert_called_once()

    @patch("app.main.get_cached_score")
    @patch("app.main.load_config")
    @patch("app.main.score_text")
    def test_score_endpoint_returns_cached_result(
        self, mock_score, mock_config, mock_cache_get, client
    ):
        """Score endpoint should return cached result when available."""
        # 模拟缓存命中
        cached_result = {
            "total_score": 90.0,
            "dimension_scores": {},
            "logic_lock": {
                "definition_score": 1.0,
                "analysis_score": 1.0,
                "solution_score": 1.0,
                "breaks": [],
                "evidence": [],
            },
            "penalties": [],
            "penalties_logic_lock": [],
            "penalties_empty_promises": [],
            "penalties_action_missing": [],
            "suggestions": [],
            "meta": {},
            "judge_mode": "local",
            "judge_source": "scorer",
            "fallback_reason": "",
        }
        mock_cache_get.return_value = cached_result

        response = client.post("/api/v1/score", json={"text": "缓存测试"})
        assert response.status_code == 200
        data = response.json()
        assert data["total_score"] == 90.0

        # 验证 score_text 没有被调用（使用了缓存）
        mock_score.assert_not_called()


class TestProjectLevelCacheIntegration:
    """Tests for project-level endpoint cache integration."""

    @patch("app.main.get_cached_score")
    @patch("app.main.cache_score_result")
    @patch("app.main.save_submissions")
    @patch("app.main.load_submissions")
    @patch("app.main.load_learning_profiles")
    @patch("app.main.load_config")
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    @patch("app.main.score_text")
    def test_upload_shigong_upload_only_skips_cache_and_scoring(
        self,
        mock_score,
        mock_ensure,
        mock_load_proj,
        mock_config,
        mock_profiles,
        mock_load_sub,
        mock_save_sub,
        mock_cache_set,
        mock_cache_get,
        client,
    ):
        """Upload shigong should not trigger scoring/cache in upload-only workflow."""
        # 模拟缓存未命中
        mock_cache_get.return_value = None
        mock_load_proj.return_value = [{"id": "p1"}]
        mock_config.return_value = MagicMock(rubric={}, lexicon={})
        mock_profiles.return_value = []
        mock_load_sub.return_value = []
        mock_score.return_value = MagicMock(
            model_dump=lambda: {"total_score": 80.0, "dimension_scores": {}, "penalties": []}
        )

        response = client.post(
            "/api/v1/projects/p1/shigong",
            files={"file": ("test.txt", BytesIO(b"test content"), "text/plain")},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["report"]["scoring_status"] == "pending"
        mock_cache_get.assert_not_called()
        mock_cache_set.assert_not_called()
        mock_score.assert_not_called()

    @patch("app.main.get_cached_score")
    @patch("app.main.save_submissions")
    @patch("app.main.load_submissions")
    @patch("app.main.load_learning_profiles")
    @patch("app.main.load_config")
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    @patch("app.main.score_text")
    def test_upload_shigong_upload_only_ignores_existing_cache(
        self,
        mock_score,
        mock_ensure,
        mock_load_proj,
        mock_config,
        mock_profiles,
        mock_load_sub,
        mock_save_sub,
        mock_cache_get,
        client,
    ):
        """Upload shigong should return cached result when available."""
        # 模拟缓存命中
        cached_result = {
            "total_score": 88.0,
            "dimension_scores": {},
            "penalties": [],
        }
        mock_cache_get.return_value = cached_result
        mock_load_proj.return_value = [{"id": "p1"}]
        mock_config.return_value = MagicMock(rubric={}, lexicon={})
        mock_profiles.return_value = []
        mock_load_sub.return_value = []

        response = client.post(
            "/api/v1/projects/p1/shigong",
            files={"file": ("test.txt", BytesIO(b"cached text"), "text/plain")},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["total_score"] == 0.0
        assert data["report"]["scoring_status"] == "pending"
        mock_cache_get.assert_not_called()
        mock_score.assert_not_called()

    @patch("app.main.get_cached_score")
    @patch("app.main.cache_score_result")
    @patch("app.main.save_submissions")
    @patch("app.main.load_submissions")
    @patch("app.main.load_learning_profiles")
    @patch("app.main.load_config")
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    @patch("app.main.score_text")
    def test_upload_shigong_upload_only_with_profile_still_skips_cache(
        self,
        mock_score,
        mock_ensure,
        mock_load_proj,
        mock_config,
        mock_profiles,
        mock_load_sub,
        mock_save_sub,
        mock_cache_set,
        mock_cache_get,
        client,
    ):
        """Upload shigong should stay pending even if profile multipliers exist."""
        # 模拟缓存未命中
        mock_cache_get.return_value = None
        mock_load_proj.return_value = [{"id": "p1"}]
        mock_config.return_value = MagicMock(rubric={}, lexicon={})
        mock_profiles.return_value = [{"project_id": "p1", "dimension_multipliers": {"D01": 1.2}}]
        mock_load_sub.return_value = []
        mock_score.return_value = MagicMock(
            model_dump=lambda: {"total_score": 82.0, "dimension_scores": {}, "penalties": []}
        )

        response = client.post(
            "/api/v1/projects/p1/shigong",
            files={"file": ("test.txt", BytesIO(b"test content"), "text/plain")},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["report"]["scoring_status"] == "pending"
        mock_cache_get.assert_not_called()
        mock_cache_set.assert_not_called()
        mock_score.assert_not_called()

    @patch("app.main.get_cached_score")
    @patch("app.main.cache_score_result")
    @patch("app.main.load_evolution_reports")
    @patch("app.main.save_submissions")
    @patch("app.main.load_submissions")
    @patch("app.main.load_learning_profiles")
    @patch("app.main.load_config")
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    @patch("app.main.score_text")
    def test_score_for_project_uses_cache(
        self,
        mock_score,
        mock_ensure,
        mock_load_proj,
        mock_config,
        mock_profiles,
        mock_load_sub,
        mock_save_sub,
        mock_load_evolution_reports,
        mock_cache_set,
        mock_cache_get,
        client,
    ):
        """Score for project should check cache first."""
        # 模拟缓存未命中
        mock_load_evolution_reports.return_value = {
            "p1": {"scoring_evolution": {"total_score_scale": 1.1}}
        }
        mock_cache_get.return_value = None
        mock_load_proj.return_value = [{"id": "p1"}]
        mock_config.return_value = MagicMock(rubric={}, lexicon={})
        mock_profiles.return_value = []
        mock_load_sub.return_value = []
        mock_score.return_value = MagicMock(
            model_dump=lambda: {"total_score": 75.0, "dimension_scores": {}, "penalties": []}
        )

        response = client.post("/api/v1/projects/p1/score", json={"text": "测试文本"})
        assert response.status_code == 200
        data = response.json()
        assert data["total_score"] == 82.5

        # 验证缓存被检查
        mock_cache_get.assert_called_once_with("测试文本", None)
        # 验证结果被缓存
        mock_cache_set.assert_called_once()
        cached_report = mock_cache_set.call_args[0][1]
        assert cached_report["total_score"] == 75.0
        assert cached_report["rule_total_score"] == 75.0

    @patch("app.main.get_cached_score")
    @patch("app.main.load_evolution_reports")
    @patch("app.main.save_submissions")
    @patch("app.main.load_submissions")
    @patch("app.main.load_learning_profiles")
    @patch("app.main.load_config")
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    @patch("app.main.score_text")
    def test_score_for_project_returns_cached_result(
        self,
        mock_score,
        mock_ensure,
        mock_load_proj,
        mock_config,
        mock_profiles,
        mock_load_sub,
        mock_save_sub,
        mock_load_evolution_reports,
        mock_cache_get,
        client,
    ):
        """Score for project should return cached result when available."""
        # 模拟缓存命中
        cached_result = {
            "total_score": 92.0,
            "dimension_scores": {},
            "penalties": [],
        }
        mock_load_evolution_reports.return_value = {
            "p1": {"scoring_evolution": {"total_score_scale": 1.1}}
        }
        mock_cache_get.return_value = cached_result
        mock_load_proj.return_value = [{"id": "p1"}]
        mock_config.return_value = MagicMock(rubric={}, lexicon={})
        mock_profiles.return_value = []
        mock_load_sub.return_value = []

        response = client.post("/api/v1/projects/p1/score", json={"text": "缓存测试"})
        assert response.status_code == 200
        data = response.json()
        assert data["total_score"] == 100.0

        # 验证 score_text 没有被调用（使用了缓存）
        mock_score.assert_not_called()

    @patch("app.main.load_evolution_reports")
    @patch("app.main.get_cached_score")
    @patch("app.main.save_submissions")
    @patch("app.main.load_submissions")
    @patch("app.main.load_learning_profiles")
    @patch("app.main.load_config")
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    @patch("app.main.score_text")
    def test_score_for_project_cached_result_applies_scale_once(
        self,
        mock_score,
        mock_ensure,
        mock_load_proj,
        mock_config,
        mock_profiles,
        mock_load_sub,
        mock_save_sub,
        mock_cache_get,
        mock_load_evolution_reports,
        client,
    ):
        """Cached raw report should apply evolution total scale only once at read time."""
        mock_load_evolution_reports.return_value = {
            "p1": {"scoring_evolution": {"total_score_scale": 1.1}}
        }
        mock_cache_get.return_value = {
            "total_score": 80.0,
            "rule_total_score": 80.0,
            "dimension_scores": {},
            "penalties": [],
        }
        mock_load_proj.return_value = [{"id": "p1"}]
        mock_config.return_value = MagicMock(rubric={}, lexicon={})
        mock_profiles.return_value = []
        mock_load_sub.return_value = []

        response = client.post("/api/v1/projects/p1/score", json={"text": "缓存缩放测试"})
        assert response.status_code == 200
        data = response.json()
        assert data["total_score"] == 88.0
        assert data["report"]["total_score"] == 88.0
        assert data["report"]["rule_total_score"] == 88.0
        mock_score.assert_not_called()

    @patch("app.main.get_cached_score")
    @patch("app.main.cache_score_result")
    @patch("app.main.save_submissions")
    @patch("app.main.load_submissions")
    @patch("app.main.load_learning_profiles")
    @patch("app.main.load_config")
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    @patch("app.main.score_text")
    def test_score_for_project_cache_with_multipliers(
        self,
        mock_score,
        mock_ensure,
        mock_load_proj,
        mock_config,
        mock_profiles,
        mock_load_sub,
        mock_save_sub,
        mock_cache_set,
        mock_cache_get,
        client,
    ):
        """Score for project should use config_hash when multipliers exist."""
        # 模拟缓存未命中
        mock_cache_get.return_value = None
        mock_load_proj.return_value = [{"id": "p1"}]
        mock_config.return_value = MagicMock(rubric={}, lexicon={})
        mock_profiles.return_value = [{"project_id": "p1", "dimension_multipliers": {"D01": 1.3}}]
        mock_load_sub.return_value = []
        mock_score.return_value = MagicMock(
            model_dump=lambda: {"total_score": 78.0, "dimension_scores": {}, "penalties": []}
        )

        response = client.post("/api/v1/projects/p1/score", json={"text": "测试文本"})
        assert response.status_code == 200

        # 验证缓存被检查时包含 config_hash
        call_args = mock_cache_get.call_args
        assert call_args[0][0] == "测试文本"
        # config_hash 应该不是 None（因为有 multipliers）
        assert call_args[0][1] is not None


class TestSystemSelfCheckCapabilities:
    @patch(
        "app.main._build_data_hygiene_report",
        return_value={"orphan_records_total": 0, "datasets": []},
    )
    @patch("app.main._resolve_dwg_converter_binaries", return_value=["/usr/local/bin/dwg2dxf"])
    @patch("app.main.get_rate_limit_status", return_value={"enabled": False})
    @patch("app.main.get_auth_status", return_value={"enabled": False})
    @patch("app.main.ensure_data_dirs")
    @patch("app.main.load_config")
    def test_system_self_check_reports_parser_capabilities(
        self,
        mock_load_config,
        mock_ensure_data_dirs,
        mock_auth_status,
        mock_rate_limit_status,
        mock_dwg_bins,
        mock_data_hygiene,
    ):
        from app.main import _run_system_self_check

        mock_load_config.return_value = MagicMock(rubric={}, lexicon={})
        payload = _run_system_self_check(None)
        names = {str(item.get("name")) for item in payload.get("items", [])}
        assert "parser_pdf" in names
        assert "parser_docx" in names
        assert "parser_ocr" in names
        assert "parser_dwg_converter" in names
        assert "data_hygiene" in names
        assert "required_ok" in payload
        assert "degraded" in payload
        assert "failed_required_count" in payload
        assert "failed_optional_count" in payload
        assert payload["checks"]["health"] is True
        assert payload["checks"]["parser_pdf"] is True
        assert payload["summary"]["parser_capability_total"] == 4
        assert payload["summary"]["data_hygiene_orphan_records"] == 0
        assert "openai_api_available" in payload["checks"]
        assert "parse_job_summary" in payload["summary"]
        assert "structured_summary_schema_ok" in payload["summary"]

    @patch(
        "app.main._build_data_hygiene_report",
        return_value={"orphan_records_total": 0, "datasets": []},
    )
    @patch("app.main._resolve_dwg_converter_binaries", return_value=[])
    @patch("app.main.get_rate_limit_status", return_value={"enabled": False})
    @patch("app.main.get_auth_status", return_value={"enabled": False})
    @patch("app.main.ensure_data_dirs")
    @patch("app.main.load_config")
    @patch("app.main.pymupdf", None)
    @patch("app.main.PdfReader", object())
    def test_system_self_check_dwg_converter_optional_not_block_overall_ok(
        self,
        mock_load_config,
        mock_ensure_data_dirs,
        mock_auth_status,
        mock_rate_limit_status,
        mock_dwg_bins,
        mock_data_hygiene,
    ):
        from app.main import _run_system_self_check

        mock_load_config.return_value = MagicMock(rubric={}, lexicon={})
        payload = _run_system_self_check(None)
        assert payload.get("ok") is True
        assert payload.get("required_ok") is True
        assert payload.get("degraded") is True
        assert payload.get("failed_required_count") == 0
        assert payload.get("failed_optional_count") >= 1
        assert payload["checks"]["parser_dwg_converter"] is False
        assert "parser_dwg_converter" in payload["summary"]["failed_optional_items"]
        assert "openai_api_available" in payload["checks"]
        items = payload.get("items") or []
        dwg_item = next((x for x in items if x.get("name") == "parser_dwg_converter"), {})
        assert dwg_item.get("ok") is False
        pdf_item = next((x for x in items if x.get("name") == "parser_pdf"), {})
        assert pdf_item.get("ok") is True


class TestLlmStatusEndpoint:
    @patch("app.main.get_llm_backend_status")
    def test_llm_status_reports_real_backend_and_legacy_alias(self, mock_get_status, client):
        mock_get_status.return_value = {
            "evolution_backend": "openai",
            "requested_backend": "spark",
            "backend_alias_applied": True,
            "auto_mode": False,
            "spark_configured": True,
            "legacy_spark_env_keys": ["SPARK_MODEL"],
            "openai_configured": True,
            "openai_account_count": 2,
            "openai_pool_health": {
                "total_accounts": 2,
                "healthy_accounts": 2,
                "cooling_accounts": 0,
            },
            "openai_pool_quality": {
                "total_accounts": 2.0,
                "rated_accounts": 1.0,
                "average_quality_score": 80.0,
                "best_quality_score": 95.0,
                "worst_quality_score": 65.0,
                "low_quality_accounts": 0.0,
            },
            "openai_model": "gpt-5.4",
            "gemini_configured": False,
            "gemini_account_count": 0,
            "gemini_pool_health": {},
            "gemini_pool_quality": {},
            "provider_quality": {"openai": "stable"},
            "provider_review_stats": {
                "openai": {
                    "confirmed_count": 2,
                    "diverged_count": 0,
                    "unavailable_count": 0,
                    "fallback_only_count": 0,
                    "last_status": "confirmed",
                    "last_at": 1.0,
                }
            },
            "provider_quality_score": {"openai": 75.0},
            "provider_chain": ["openai"],
            "fallback_providers": [],
        }

        response = client.get("/api/v1/config/llm_status")

        assert response.status_code == 200
        payload = response.json()
        assert payload["evolution_backend"] == "openai"
        assert payload["requested_backend"] == "spark"
        assert payload["backend_alias_applied"] is True
        assert payload["auto_mode"] is False
        assert payload["spark_configured"] is True
        assert payload["legacy_spark_env_keys"] == ["SPARK_MODEL"]
        assert payload["openai_configured"] is True
        assert payload["openai_account_count"] == 2
        assert payload["openai_pool_health"]["healthy_accounts"] == 2
        assert payload["openai_pool_quality"]["average_quality_score"] == 80.0
        assert payload["openai_pool_quality"]["low_quality_accounts"] == 0.0
        assert payload["openai_model"] == "gpt-5.4"
        assert payload["gemini_configured"] is False
        assert payload["gemini_account_count"] == 0
        assert payload["gemini_pool_health"] == {}
        assert payload["gemini_pool_quality"] == {}
        assert payload["provider_quality"] == {"openai": "stable"}
        assert payload["provider_review_stats"]["openai"]["confirmed_count"] == 2
        assert payload["provider_review_stats"]["openai"]["last_status"] == "confirmed"
        assert payload["provider_quality_score"] == {"openai": 75.0}
        assert payload["provider_chain"] == ["openai"]
        assert payload["fallback_providers"] == []


class TestDataHygieneEndpoints:
    @patch("app.main._build_data_hygiene_report")
    def test_system_data_hygiene_audit(self, mock_hygiene, client):
        mock_hygiene.return_value = {
            "generated_at": "2026-03-01T00:00:00+00:00",
            "apply_mode": False,
            "valid_project_count": 2,
            "orphan_records_total": 3,
            "cleaned_records_total": 0,
            "datasets": [
                {
                    "name": "materials",
                    "total": 10,
                    "orphan_count": 3,
                    "cleaned_count": 0,
                    "mode": "project_id",
                }
            ],
            "recommendations": ["发现孤儿记录 3 条，建议修复。"],
        }
        response = client.get("/api/v1/system/data_hygiene")
        assert response.status_code == 200
        data = response.json()
        assert data["apply_mode"] is False
        assert data["orphan_records_total"] == 3
        assert len(data["datasets"]) == 1
        mock_hygiene.assert_called_once_with(apply=False)

    @patch("app.main._build_data_hygiene_report")
    def test_system_data_hygiene_repair(self, mock_hygiene, client):
        mock_hygiene.return_value = {
            "generated_at": "2026-03-01T00:00:00+00:00",
            "apply_mode": True,
            "valid_project_count": 2,
            "orphan_records_total": 3,
            "cleaned_records_total": 3,
            "datasets": [
                {
                    "name": "materials",
                    "total": 10,
                    "orphan_count": 3,
                    "cleaned_count": 3,
                    "mode": "project_id",
                }
            ],
            "recommendations": ["已清理孤儿记录 3 条。"],
        }
        response = client.post("/api/v1/system/data_hygiene/repair")
        assert response.status_code == 200
        data = response.json()
        assert data["apply_mode"] is True
        assert data["cleaned_records_total"] == 3
        mock_hygiene.assert_called_once_with(apply=True)


def test_build_data_hygiene_report_repairs_legacy_project_schema(monkeypatch):
    import app.main as main_mod

    projects = [{"id": "legacy-p1"}]
    saved: dict[str, object] = {}

    monkeypatch.setattr(main_mod, "ensure_data_dirs", lambda: None)
    monkeypatch.setattr(main_mod, "load_projects", lambda: projects)
    monkeypatch.setattr(
        main_mod,
        "save_projects",
        lambda rows: saved.__setitem__("projects", copy.deepcopy(rows)),
    )

    for loader_name in (
        "load_submissions",
        "load_materials",
        "load_learning_profiles",
        "load_score_history",
        "load_ground_truth",
        "load_project_anchors",
        "load_project_requirements",
        "load_delta_cases",
        "load_calibration_samples",
        "load_patch_packages",
        "load_patch_deployments",
        "load_score_reports",
        "load_evidence_units",
        "load_qingtian_results",
    ):
        monkeypatch.setattr(main_mod, loader_name, lambda: [])

    for saver_name in (
        "save_submissions",
        "save_materials",
        "save_learning_profiles",
        "save_score_history",
        "save_ground_truth",
        "save_project_anchors",
        "save_project_requirements",
        "save_delta_cases",
        "save_calibration_samples",
        "save_patch_packages",
        "save_patch_deployments",
        "save_score_reports",
        "save_evidence_units",
        "save_qingtian_results",
    ):
        monkeypatch.setattr(main_mod, saver_name, lambda payload: None)

    for loader_name in ("load_project_context", "load_evolution_reports"):
        monkeypatch.setattr(main_mod, loader_name, lambda: {})
    for saver_name in ("save_project_context", "save_evolution_reports"):
        monkeypatch.setattr(main_mod, saver_name, lambda payload: None)

    payload = main_mod._build_data_hygiene_report(apply=True)

    dataset = next(row for row in payload["datasets"] if row["name"] == "projects_schema")
    assert dataset["orphan_count"] == 1
    assert dataset["cleaned_count"] == 1
    assert saved["projects"][0]["name"] == "恢复项目_legacy-p"
    assert any("项目记录结构异常" in str(text) for text in payload["recommendations"])


class TestComputeMultipliersHash:
    """Tests for _compute_multipliers_hash helper function."""

    def test_compute_multipliers_hash_same_input(self):
        """Same multipliers should produce same hash."""
        from app.main import _compute_multipliers_hash

        hash1 = _compute_multipliers_hash({"D01": 1.2, "D02": 1.1})
        hash2 = _compute_multipliers_hash({"D01": 1.2, "D02": 1.1})
        assert hash1 == hash2

    def test_compute_multipliers_hash_different_input(self):
        """Different multipliers should produce different hash."""
        from app.main import _compute_multipliers_hash

        hash1 = _compute_multipliers_hash({"D01": 1.2})
        hash2 = _compute_multipliers_hash({"D01": 1.3})
        assert hash1 != hash2

    def test_compute_multipliers_hash_order_independent(self):
        """Hash should be order independent."""
        from app.main import _compute_multipliers_hash

        hash1 = _compute_multipliers_hash({"D01": 1.2, "D02": 1.1})
        hash2 = _compute_multipliers_hash({"D02": 1.1, "D01": 1.2})
        assert hash1 == hash2


class TestEvolutionTotalScale:
    """Tests for evolution total-score scale helper."""

    def test_apply_evolution_total_scale_scales_pred_and_total_consistently(self):
        from app.main import _apply_evolution_total_scale

        report = {
            "total_score": 65.0,
            "rule_total_score": 70.0,
            "pred_total_score": 65.0,
            "llm_total_score": 60.0,
        }
        with patch(
            "app.main.load_evolution_reports",
            return_value={"p1": {"scoring_evolution": {"total_score_scale": 1.1}}},
        ):
            _apply_evolution_total_scale("p1", report)

        assert report["pred_total_score"] == 71.5
        assert report["rule_total_score"] == 77.0
        assert report["llm_total_score"] == 66.0
        assert report["total_score"] == 71.5

    def test_apply_evolution_total_scale_skips_direct_project_calibrator_scores(self):
        from app.main import _apply_evolution_total_scale

        report = {
            "total_score": 80.74,
            "rule_total_score": 17.96,
            "pred_total_score": 80.74,
            "llm_total_score": None,
            "score_blend": {
                "mode": "project_calibrator_direct",
                "reason": "cv_validated_project_calibrator",
            },
        }
        with patch(
            "app.main.load_evolution_reports",
            return_value={"p1": {"scoring_evolution": {"total_score_scale": 1.15}}},
        ):
            _apply_evolution_total_scale("p1", report)

        assert report["pred_total_score"] == 80.74
        assert report["rule_total_score"] == 17.96
        assert report["total_score"] == 80.74

    def test_apply_evolution_total_scale_skips_exact_ground_truth_scores(self):
        from app.main import _apply_evolution_total_scale

        report = {
            "total_score": 82.2,
            "rule_total_score": 11.05,
            "pred_total_score": 82.2,
            "llm_total_score": None,
            "score_blend": {
                "mode": "ground_truth_exact",
                "reason": "approved_exact_submission_match",
            },
        }
        with patch(
            "app.main.load_evolution_reports",
            return_value={"p1": {"scoring_evolution": {"total_score_scale": 1.15}}},
        ):
            _apply_evolution_total_scale("p1", report)

        assert report["pred_total_score"] == 82.2
        assert report["rule_total_score"] == 11.05
        assert report["total_score"] == 82.2

    def test_compute_multipliers_hash_empty(self):
        """Empty dict should produce valid hash."""
        from app.main import _compute_multipliers_hash

        hash_value = _compute_multipliers_hash({})
        assert hash_value is not None
        assert len(hash_value) == 16  # 截断为 16 字符


class TestProjectCalibratorDirectApply:
    @patch("app.main.predict_with_model", return_value=(80.74, {"sigma": 1.0}))
    @patch("app.main.build_feature_row", return_value={"x_features": {}})
    def test_apply_prediction_uses_direct_project_calibrator_when_cv_validated(
        self,
        mock_build_row,
        mock_predict,
    ):
        from app.main import _apply_prediction_to_report_with_model

        report = {"rule_total_score": 14.21, "total_score": 14.21, "meta": {}}
        submission = {"id": "s1", "project_id": "p1", "filename": "s1.pdf"}
        project = {"id": "p1", "meta": {}}
        model = {
            "calibrator_version": "calib-1",
            "deployed": True,
            "train_filter": {"project_id": "p1"},
            "calibrator_summary": {
                "deployment_mode": "cv_validated",
                "gate_passed": True,
                "bootstrap_small_sample": False,
                "sample_count": 7,
                "cv_metrics": {"mae": 1.7},
            },
            "model_artifact": {"model_type": "isotonic1d"},
        }

        version = _apply_prediction_to_report_with_model(
            report,
            submission_like=submission,
            project=project,
            model_override=model,
        )

        assert mock_build_row.called
        assert mock_predict.called
        assert version == "calib-1"
        assert report["pred_total_score"] == 80.74
        assert report["total_score"] == 80.74
        assert report["llm_total_score"] is None
        assert report["score_blend"]["mode"] == "project_calibrator_direct"
        assert report["score_blend"]["reason"] == "cv_validated_project_calibrator"
        assert submission["total_score"] == 80.74

    @patch("app.main.predict_with_model", return_value=(80.74, {"sigma": 1.0}))
    @patch("app.main.build_feature_row", return_value={"x_features": {}})
    def test_apply_prediction_keeps_conservative_blend_for_small_sample_calibrator(
        self,
        mock_build_row,
        mock_predict,
    ):
        from app.main import _apply_prediction_to_report_with_model

        report = {"rule_total_score": 14.21, "total_score": 14.21, "meta": {}}
        submission = {"id": "s1", "project_id": "p1", "filename": "s1.pdf"}
        project = {"id": "p1", "meta": {}}
        model = {
            "calibrator_version": "calib-small",
            "deployed": True,
            "train_filter": {"project_id": "p1"},
            "calibrator_summary": {
                "deployment_mode": "bootstrap_auto_deploy",
                "gate_passed": True,
                "bootstrap_small_sample": True,
                "sample_count": 1,
                "cv_metrics": {"mae": 1.0},
            },
            "model_artifact": {"model_type": "offset"},
        }

        _apply_prediction_to_report_with_model(
            report,
            submission_like=submission,
            project=project,
            model_override=model,
        )

        assert mock_build_row.called
        assert mock_predict.called
        assert report["pred_total_score"] < 80.74
        assert report["score_blend"].get("mode") != "project_calibrator_direct"


class TestExactGroundTruthOverride:
    def test_ensure_report_score_self_awareness_refreshes_stale_exact_ground_truth_state(self):
        from app.main import _ensure_report_score_self_awareness

        report = {
            "score_blend": {"mode": "ground_truth_exact"},
            "meta": {
                "ground_truth_exact_match": True,
                "material_utilization_gate": {"blocked": False, "warned": False},
                "score_self_awareness": {
                    "level": "medium",
                    "score_0_100": 68.1,
                    "state": "normal",
                    "reasons": ["历史旧状态"],
                },
                "score_confidence_level": "medium",
            },
        }

        awareness = _ensure_report_score_self_awareness(
            report,
            project_id=None,
            material_knowledge_snapshot={"summary": {"dimension_coverage_rate": 1.0}},
        )

        assert awareness["level"] == "high"
        assert awareness["score_0_100"] == 100.0
        assert awareness["state"] == "ground_truth_exact"
        assert report["meta"]["score_confidence_level"] == "high"

    def test_build_score_self_awareness_promotes_exact_ground_truth_to_high_confidence(self):
        from app.main import _build_score_self_awareness

        awareness = _build_score_self_awareness(
            {
                "score_blend": {"mode": "ground_truth_exact"},
                "meta": {
                    "material_utilization_gate": {"blocked": False, "warned": False},
                    "evidence_trace": {"source_files_hit_count": 2, "mandatory_hit_rate": 0.7},
                },
                "pred_confidence": {"sigma": 0.8, "fused_sigma": 0.8},
            },
            material_knowledge_snapshot={"summary": {"dimension_coverage_rate": 1.0}},
        )

        assert awareness["level"] == "high"
        assert awareness["score_0_100"] == 100.0
        assert awareness["state"] == "ground_truth_exact"
        assert "已命中真实评标" in awareness["reasons"][0]

    def test_apply_prediction_prefers_exact_ground_truth_match(self):
        from app.main import _apply_prediction_to_report_with_model

        report = {
            "total_score": 11.05,
            "rule_total_score": 11.05,
            "meta": {},
        }
        submission = {
            "id": "sub-1",
            "project_id": "p1",
            "text": "same text",
            "total_score": 11.05,
        }
        project = {"id": "p1", "score_scale_max": 100}
        ground_truth_rows = [
            {
                "id": "gt-1",
                "project_id": "p1",
                "source_submission_id": "sub-1",
                "final_score": 82.2,
                "score_scale_max": 100,
            }
        ]

        with (
            patch("app.main._list_project_ground_truth_records", return_value=ground_truth_rows),
            patch("app.main._learning_quality_gate_is_blocked", return_value=False),
            patch("app.main._ground_truth_record_for_learning", return_value={"final_score": 82.2}),
        ):
            version = _apply_prediction_to_report_with_model(
                report,
                submission_like=submission,
                project=project,
                model_override=None,
            )

        assert version is None
        assert report["pred_total_score"] == 82.2
        assert report["total_score"] == 82.2
        assert report["score_blend"]["mode"] == "ground_truth_exact"
        assert report["meta"]["ground_truth_exact_match"] is True

    def test_repair_ground_truth_record_final_score_if_needed_recomputes_zero_score(self):
        from app.main import _repair_ground_truth_record_final_score_if_needed

        record = {
            "id": "gt-1",
            "project_id": "p1",
            "judge_scores": [4.33, 4.36, 4.35, 4.36, 4.8],
            "score_scale_max": 5,
            "final_score": 0.0,
            "final_score_raw": 0.0,
            "final_score_100": 0.0,
        }
        persisted_row = dict(record)
        persisted_row.update(
            {
                "final_score": 4.44,
                "final_score_raw": 4.44,
                "final_score_100": 88.8,
            }
        )
        finalized_row = dict(persisted_row)
        finalized_row["feedback_guardrail"] = {"blocked": False}

        with (
            patch(
                "app.main._resolve_project_ground_truth_score_rule",
                return_value={
                    "formula": "simple_mean",
                    "auto_compute": True,
                    "rounding_digits": 2,
                },
            ),
            patch(
                "app.main._persist_ground_truth_record_fields",
                return_value=persisted_row,
            ) as mock_persist,
            patch(
                "app.main._finalize_ground_truth_learning_record",
                return_value=finalized_row,
            ) as mock_finalize,
        ):
            repaired = _repair_ground_truth_record_final_score_if_needed(
                "p1",
                record,
                project={"id": "p1", "meta": {"score_scale_max": 5}},
            )

        assert repaired["final_score"] == 4.44
        assert repaired["final_score_raw"] == 4.44
        assert repaired["final_score_100"] == 88.8
        assert mock_persist.call_args.kwargs["updates"]["final_score"] == 4.44
        assert mock_persist.call_args.kwargs["updates"]["final_score_raw"] == 4.44
        assert mock_persist.call_args.kwargs["updates"]["final_score_100"] == 88.8
        assert mock_finalize.call_args.args[1]["final_score"] == 4.44

    def test_repair_ground_truth_record_final_score_if_needed_resyncs_stale_qingtian_snapshot(self):
        from app.main import _repair_ground_truth_record_final_score_if_needed

        record = {
            "id": "gt-1",
            "project_id": "p1",
            "judge_scores": [4.33, 4.36, 4.35, 4.36, 4.8],
            "score_scale_max": 5,
            "final_score": 4.44,
            "final_score_raw": 4.44,
            "final_score_100": 88.8,
        }
        stale_qingtian_rows = [
            {
                "id": "qt-1",
                "submission_id": "sub-1",
                "qt_total_score": 0.0,
                "raw_payload": {
                    "ground_truth_record_id": "gt-1",
                    "final_score": 0.0,
                    "final_score_raw": 0.0,
                    "final_score_100": 0.0,
                    "score_scale_max": 5,
                },
            }
        ]

        with (
            patch("app.main.load_qingtian_results", return_value=stale_qingtian_rows),
            patch("app.main.load_ground_truth", return_value=[record]),
            patch("app.main._sync_ground_truth_record_to_qingtian", return_value={}) as mock_sync,
        ):
            repaired = _repair_ground_truth_record_final_score_if_needed(
                "p1",
                dict(record),
                project={"id": "p1", "meta": {"score_scale_max": 5}},
            )

        assert repaired["final_score"] == 4.44
        assert repaired["final_score_raw"] == 4.44
        assert repaired["final_score_100"] == 88.8
        mock_sync.assert_called_once()
        assert mock_sync.call_args.args[0] == "p1"
        assert mock_sync.call_args.args[1]["id"] == "gt-1"

    def test_apply_prediction_prefers_blocked_exact_ground_truth_match_after_auto_repair(self):
        from app.main import _apply_prediction_to_report_with_model

        report = {
            "total_score": 11.05,
            "rule_total_score": 11.05,
            "meta": {},
        }
        submission = {
            "id": "sub-1",
            "project_id": "p1",
            "text": "same text",
            "total_score": 11.05,
        }
        project = {"id": "p1", "score_scale_max": 100}
        ground_truth_rows = [
            {
                "id": "gt-1",
                "project_id": "p1",
                "source_submission_id": "sub-1",
                "final_score": 0.0,
                "final_score_raw": 0.0,
                "final_score_100": 0.0,
                "score_scale_max": 5,
                "judge_scores": [4.33, 4.36, 4.35, 4.36, 4.8],
                "feedback_guardrail": {"blocked": True},
            }
        ]

        with (
            patch(
                "app.main._list_project_ground_truth_records",
                return_value=ground_truth_rows,
            ) as mock_list_records,
            patch(
                "app.main._repair_ground_truth_record_final_score_if_needed",
                return_value={
                    **ground_truth_rows[0],
                    "final_score": 4.44,
                    "final_score_raw": 4.44,
                    "final_score_100": 88.8,
                },
            ),
            patch("app.main._ground_truth_record_for_learning", return_value={"final_score": 88.8}),
        ):
            version = _apply_prediction_to_report_with_model(
                report,
                submission_like=submission,
                project=project,
                model_override=None,
            )

        assert version is None
        assert report["pred_total_score"] == 88.8
        assert report["total_score"] == 88.8
        assert report["score_blend"]["mode"] == "ground_truth_exact"
        assert report["meta"]["ground_truth_exact_match"] is True
        mock_list_records.assert_called_once_with("p1", include_guardrail_blocked=True)

    def test_apply_prediction_prefers_exact_ground_truth_filename_match(self):
        from app.main import _apply_prediction_to_report_with_model

        report = {
            "total_score": 11.05,
            "rule_total_score": 11.05,
            "meta": {},
        }
        submission = {
            "id": "sub-2",
            "project_id": "p1",
            "filename": "安徽中江建投-道路工程(1).pdf",
            "text": "重新上传后的施组文本",
            "total_score": 11.05,
        }
        project = {"id": "p1", "score_scale_max": 100}
        ground_truth_rows = [
            {
                "id": "gt-1",
                "project_id": "p1",
                "source_submission_filename": "/tmp/安徽中江建投-道路工程(1).pdf",
                "shigong_text": "历史录入时保存的旧文本",
                "final_score": 77.9,
                "score_scale_max": 100,
            }
        ]

        with (
            patch("app.main._list_project_ground_truth_records", return_value=ground_truth_rows),
            patch("app.main._learning_quality_gate_is_blocked", return_value=False),
            patch("app.main._ground_truth_record_for_learning", return_value={"final_score": 77.9}),
        ):
            version = _apply_prediction_to_report_with_model(
                report,
                submission_like=submission,
                project=project,
                model_override=None,
            )

        assert version is None
        assert report["pred_total_score"] == 77.9
        assert report["total_score"] == 77.9
        assert report["score_blend"]["mode"] == "ground_truth_exact"
        assert report["meta"]["ground_truth_exact_match"] is True

    @patch("app.main.load_calibration_models")
    @patch("app.main.load_projects")
    @patch("app.main.load_submissions")
    @patch("app.main.ensure_data_dirs")
    def test_compare_marks_exact_ground_truth_scores_as_ground_truth_source(
        self, mock_ensure, mock_load_submissions, mock_load_projects, mock_load_models, client
    ):
        mock_load_projects.return_value = [{"id": "p1", "calibrator_version_locked": "calib1"}]
        mock_load_models.return_value = [
            {
                "calibrator_version": "calib1",
                "deployed": True,
                "created_at": "2026-01-02T00:00:00Z",
                "train_filter": {"project_id": "p1"},
            }
        ]
        mock_load_submissions.return_value = [
            {
                "id": "s1",
                "project_id": "p1",
                "filename": "exact.txt",
                "total_score": 82.2,
                "report": {
                    "rule_total_score": 12.7,
                    "pred_total_score": 82.2,
                    "score_blend": {"mode": "ground_truth_exact"},
                    "meta": {"ground_truth_exact_match": True},
                    "dimension_scores": {},
                    "penalties": [],
                },
                "created_at": "2026-01-02T00:00:00Z",
            }
        ]

        response = client.get("/api/v1/projects/p1/compare")

        assert response.status_code == 200
        data = response.json()
        assert data["rankings"][0]["score_source"] == "ground_truth"
        assert data["rankings"][0]["score_confidence_level"] == "high"
        assert data["rankings"][0]["score_self_awareness"]["state"] == "ground_truth_exact"


class TestPdfFallbackParser:
    @patch("app.main.pymupdf", None)
    @patch("app.main.PdfReader")
    def test_read_uploaded_file_content_pdf_uses_pypdf_fallback(self, mock_pdf_reader):
        from app.main import _read_uploaded_file_content

        class _FakePage:
            def extract_text(self):
                return "这是 PDF 提取文本"

        class _FakeReader:
            def __init__(self, _stream):
                self.pages = [_FakePage()]

        mock_pdf_reader.side_effect = _FakeReader
        text = _read_uploaded_file_content(b"%PDF-fallback", "sample.pdf")
        assert "[PDF_BACKEND:pypdf]" in text
        assert "这是 PDF 提取文本" in text

    @patch("app.main._score_ocr_text_candidate", return_value=4.8)
    @patch("app.main.pymupdf", None)
    @patch("app.main.PdfReader")
    def test_extract_pdf_text_with_pypdf_stops_early_when_tender_signals_are_sufficient(
        self,
        mock_pdf_reader,
        _mock_score,
    ):
        from app.main import _extract_pdf_text

        class _FakePage:
            def __init__(self, text: str):
                self._text = text
                self.calls = 0

            def extract_text(self):
                self.calls += 1
                return self._text

        class _FakeReader:
            def __init__(self, _stream):
                self.pages = pages

        repeated_intro = (
            "施工组织设计总体部署 质量管理与验收标准 进度计划与节点控制 安全文明施工 "
            "资源配置 危大工程专项方案 BIM 深化 "
        ) * 14
        scoring_page = (
            "第四章 评分办法\n"
            "评分细则：工期120日历天，质量90分，安全文明80分，专项方案加分。\n"
            "投标文件必须响应关键节点，不得缺少危大工程专项方案。\n"
        ) * 10
        pages = [
            _FakePage("第一章 招标公告\n" + repeated_intro),
            _FakePage("第二章 投标人须知\n" + repeated_intro),
            _FakePage("第三章 合同条款\n" + repeated_intro),
            _FakePage(scoring_page),
            _FakePage("不应继续扫描到这一页"),
        ]

        mock_pdf_reader.side_effect = _FakeReader
        text = _extract_pdf_text(
            b"%PDF-fallback",
            "招标文件.pdf",
            material_type="tender_qa",
        )

        assert "[PDF_BACKEND:pypdf]" in text
        assert "[PDF_EARLY_STOP_AFTER_PAGE:4] tender_qa_enough_signals" in text
        assert "[PAGE:5]" not in text
        assert "不应继续扫描到这一页" not in text
        assert pages[4].calls == 0

    @patch("app.main._score_ocr_text_candidate", return_value=4.8)
    @patch("app.main.pymupdf", None)
    @patch("app.main.PdfReader")
    def test_extract_pdf_text_with_pypdf_can_disable_early_stop_for_full_scan(
        self,
        mock_pdf_reader,
        _mock_score,
    ):
        from app.main import _extract_pdf_text

        class _FakePage:
            def __init__(self, text: str):
                self._text = text
                self.calls = 0

            def extract_text(self):
                self.calls += 1
                return self._text

        class _FakeReader:
            def __init__(self, _stream):
                self.pages = pages

        repeated_intro = (
            "施工组织设计总体部署 质量管理与验收标准 进度计划与节点控制 安全文明施工 "
            "资源配置 危大工程专项方案 BIM 深化 "
        ) * 14
        scoring_page = (
            "第四章 评分办法\n"
            "评分细则：工期120日历天，质量90分，安全文明80分，专项方案加分。\n"
            "投标文件必须响应关键节点，不得缺少危大工程专项方案。\n"
        ) * 10
        pages = [
            _FakePage("第一章 招标公告\n" + repeated_intro),
            _FakePage("第二章 投标人须知\n" + repeated_intro),
            _FakePage("第三章 合同条款\n" + repeated_intro),
            _FakePage(scoring_page),
            _FakePage(
                "第七章 评标办法\n"
                "本章第2.2.2(1)目属于技术文件详细评审内容，"
                "以评标委员会各成员打分平均值确定。"
            ),
        ]

        mock_pdf_reader.side_effect = _FakeReader
        text = _extract_pdf_text(
            b"%PDF-fallback",
            "招标文件.pdf",
            material_type="tender_qa",
            allow_early_stop=False,
        )

        assert "[PDF_BACKEND:pypdf]" in text
        assert "[PDF_EARLY_STOP_AFTER_PAGE:" not in text
        assert "[PAGE:5]" in text
        assert "评标委员会各成员打分平均值确定" in text
        assert pages[4].calls == 1

    @patch("app.main._score_ocr_text_candidate", return_value=4.8)
    @patch("app.main.pymupdf", None)
    @patch("app.main.PdfReader")
    def test_extract_pdf_text_preview_with_pypdf_stops_incrementally(
        self,
        mock_pdf_reader,
        _mock_score,
    ):
        from app.main import _extract_pdf_text_preview

        class _FakePage:
            def __init__(self, text: str):
                self._text = text
                self.calls = 0

            def extract_text(self):
                self.calls += 1
                return self._text

        class _FakeReader:
            def __init__(self, _stream):
                self.pages = pages

        pages = [
            _FakePage(
                "建筑总平面图 轴线1-8 标高3.500 净高2.900 机电综合 碰撞 净高复核 "
                "节点详图 梁 板 柱 消防 风管 桥架"
            ),
            _FakePage(
                "平面布置图 轴线A-F 标高4.200 管径DN65 综合管线 BIM 深化 节点 大样 "
                "给排水 电气 暖通 设备机房"
            ),
            _FakePage(
                "剖面图 立面图 标高6.000 节点详图 洞口 预留预埋 套管 " "消防喷淋 管径 DN100 梁板柱"
            ),
            _FakePage("不应继续扫描到这一页"),
        ]

        mock_pdf_reader.side_effect = _FakeReader
        text = _extract_pdf_text_preview(
            b"%PDF-fallback",
            "总图.pdf",
            material_type="drawing",
            max_pages=6,
            max_chars=16000,
        )

        assert "[PDF_BACKEND:pypdf]" in text
        assert "[PAGE:3]" in text
        assert "[PAGE:4]" not in text
        assert "不应继续扫描到这一页" not in text
        assert pages[3].calls == 0


class TestScoringContextEvolutionGuard:
    @patch("app.main.load_evolution_reports")
    @patch("app.main.load_expert_profiles")
    @patch("app.main.load_projects")
    def test_resolve_project_scoring_context_skips_stale_evolution_weights(
        self,
        mock_load_projects,
        mock_load_profiles,
        mock_load_evolution,
    ):
        from app.main import _resolve_project_scoring_context

        stale_at = (datetime.now(timezone.utc) - timedelta(days=120)).isoformat()
        mock_load_projects.return_value = [
            {
                "id": "p1",
                "meta": {
                    "evolution_weight_min_samples": 3,
                    "evolution_weight_max_age_days": 90,
                },
                "expert_profile_id": "ep1",
            }
        ]
        mock_load_profiles.return_value = [
            {
                "id": "ep1",
                "weights_norm": {"01": 0.9, "02": 0.1},
            }
        ]
        mock_load_evolution.return_value = {
            "p1": {
                "sample_count": 20,
                "updated_at": stale_at,
                "scoring_evolution": {"dimension_multipliers": {"01": 9.99}},
            }
        }

        multipliers, profile_snapshot, project = _resolve_project_scoring_context("p1")
        assert project.get("id") == "p1"
        assert isinstance(multipliers, dict)
        assert multipliers.get("01") != 9.99
        assert profile_snapshot is not None
        assert profile_snapshot.get("id") == "ep1"

    @patch("app.main.load_evolution_reports")
    @patch("app.main.load_expert_profiles")
    @patch("app.main.load_projects")
    def test_resolve_project_scoring_context_uses_fresh_evolution_weights(
        self,
        mock_load_projects,
        mock_load_profiles,
        mock_load_evolution,
    ):
        from app.main import _resolve_project_scoring_context

        fresh_at = datetime.now(timezone.utc).isoformat()
        mock_load_projects.return_value = [
            {
                "id": "p1",
                "meta": {
                    "evolution_weight_min_samples": 3,
                    "evolution_weight_max_age_days": 90,
                },
                "expert_profile_id": "ep1",
            }
        ]
        mock_load_profiles.return_value = [
            {
                "id": "ep1",
                "weights_norm": {"01": 0.5, "02": 0.5},
            }
        ]
        mock_load_evolution.return_value = {
            "p1": {
                "sample_count": 1,
                "updated_at": fresh_at,
                "scoring_evolution": {"dimension_multipliers": {"01": 1.3}},
            }
        }

        multipliers, profile_snapshot, _ = _resolve_project_scoring_context("p1")
        assert profile_snapshot is None
        assert multipliers.get("01") == pytest.approx(1.3, abs=1e-6)


class TestFeedbackClosedLoopSafety:
    @patch("app.main.load_ground_truth")
    @patch("app.main.load_submissions")
    @patch("app.main.load_projects")
    def test_build_feedback_records_for_project_skips_learning_quality_blocked(
        self,
        mock_load_projects,
        mock_load_submissions,
        mock_load_ground_truth,
    ):
        from app.main import _build_feedback_records_for_project

        mock_load_projects.return_value = [{"id": "p1", "meta": {"score_scale_max": 100}}]
        mock_load_submissions.return_value = [
            {
                "id": "s1",
                "project_id": "p1",
                "report": {"pred_total_score": 72.0, "score_scale_max": 100},
            }
        ]
        mock_load_ground_truth.return_value = [
            {
                "id": "gt-good",
                "project_id": "p1",
                "source_submission_id": "s1",
                "judge_scores": [80, 80, 80, 80, 80],
                "final_score": 78.0,
                "score_scale_max": 100,
            },
            {
                "id": "gt-low-quality",
                "project_id": "p1",
                "source_submission_id": "s1",
                "judge_scores": [82, 82, 82, 82, 82],
                "final_score": 82.0,
                "score_scale_max": 100,
                "learning_quality_gate": {
                    "blocked": True,
                    "reasons": ["missing_evidence_hits"],
                },
            },
        ]

        rows = _build_feedback_records_for_project("p1")

        assert [row["id"] for row in rows] == ["gt-good"]

    @patch("app.main._run_feedback_closed_loop")
    def test_run_feedback_closed_loop_safe_coerces_non_dict(self, mock_run):
        from app.main import _run_feedback_closed_loop_safe

        mock_run.return_value = MagicMock(ok=True)
        payload = _run_feedback_closed_loop_safe("p1", locale="zh", trigger="rescore")
        assert isinstance(payload, dict)
        assert payload.get("project_id") == "p1"
        assert payload.get("trigger") == "rescore"

    @patch("app.main._rescore_project_submissions_internal")
    @patch("app.main.load_submissions")
    @patch("app.main.load_projects")
    @patch("app.main.auto_run_reflection_pipeline")
    @patch("app.main._sync_feedback_weights_to_evolution")
    @patch("app.main._auto_update_project_weights_from_delta_cases")
    @patch("app.main._refresh_evolution_report_from_ground_truth")
    @patch("app.main._refresh_project_reflection_objects")
    @patch("app.main.load_ground_truth")
    def test_run_feedback_closed_loop_skips_auto_update_when_guardrail_blocked(
        self,
        mock_load_ground_truth,
        mock_refresh_reflection,
        mock_refresh_evo,
        mock_auto_update,
        mock_sync_weights,
        mock_auto_run,
        mock_load_projects,
        mock_load_submissions,
        mock_auto_rescore,
    ):
        from app.main import _run_feedback_closed_loop

        mock_load_ground_truth.return_value = [
            {
                "id": "gt-1",
                "project_id": "p1",
                "feedback_guardrail": {
                    "blocked": True,
                    "abs_delta_100": 45.0,
                    "warning_message": "当前分与真实总分偏差过大，已暂停自动调权/自动校准。",
                },
            }
        ]
        mock_load_projects.return_value = [{"id": "p1", "scoring_engine_version_locked": "v2"}]
        mock_load_submissions.return_value = [{"id": "s1", "project_id": "p1"}]
        mock_refresh_evo.return_value = {"refreshed": True, "sample_count": 0}

        payload = _run_feedback_closed_loop(
            "p1",
            locale="zh",
            trigger="ground_truth_add",
            ground_truth_record_ids=["gt-1"],
        )

        assert payload["ok"] is True
        assert payload["guardrail_triggered"] is True
        assert payload["requires_manual_confirmation"] is True
        assert payload["auto_update_skipped"] is True
        assert payload["feedback_guardrail"]["blocked"] is True
        mock_refresh_reflection.assert_called_once()
        mock_refresh_evo.assert_called_once()
        mock_auto_update.assert_not_called()
        mock_sync_weights.assert_not_called()
        mock_auto_run.assert_not_called()
        mock_auto_rescore.assert_not_called()

    @patch("app.main._rescore_project_submissions_internal")
    @patch("app.main.load_submissions")
    @patch("app.main.load_projects")
    @patch("app.main.auto_run_reflection_pipeline")
    @patch("app.main._sync_feedback_weights_to_evolution")
    @patch("app.main._auto_update_project_weights_from_delta_cases")
    @patch("app.main._refresh_evolution_report_from_ground_truth")
    @patch("app.main._refresh_project_reflection_objects")
    @patch("app.main.load_ground_truth")
    def test_run_feedback_closed_loop_auto_rescores_project_before_auto_run_when_weights_sync(
        self,
        mock_load_ground_truth,
        mock_refresh_reflection,
        mock_refresh_evo,
        mock_auto_update,
        mock_sync_weights,
        mock_auto_run,
        mock_load_projects,
        mock_load_submissions,
        mock_auto_rescore,
    ):
        from app.main import _run_feedback_closed_loop

        events = []
        mock_load_ground_truth.return_value = [
            {"id": "gt-1", "project_id": "p1", "feedback_guardrail": {"blocked": False}}
        ]
        mock_load_projects.return_value = [
            {
                "id": "p1",
                "meta": {"score_scale_max": 100},
                "scoring_engine_version_locked": "v2",
            }
        ]
        mock_load_submissions.return_value = [{"id": "s1", "project_id": "p1", "text": "示例"}]
        mock_auto_update.return_value = {
            "updated": True,
            "new_dimension_multipliers": {"01": 1.1},
            "new_profile_id": "ep-1",
        }
        mock_sync_weights.return_value = {"synced": True, "candidate_profile_id": "ep-1"}
        mock_auto_rescore.side_effect = lambda *args, **kwargs: events.append("rescore") or {
            "ok": True,
            "reports_generated": 1,
            "submission_count": 1,
        }
        mock_auto_run.side_effect = lambda *args, **kwargs: events.append("auto_run") or {
            "ok": True,
            "calibrator_deployed": False,
        }
        mock_refresh_evo.return_value = {"refreshed": True, "sample_count": 1}

        payload = _run_feedback_closed_loop("p1", locale="zh", trigger="ground_truth_add")

        assert payload["ok"] is True
        assert payload["auto_rescore"]["ok"] is True
        assert payload["auto_rescore"]["reports_generated"] == 1
        assert events == ["rescore", "auto_run"]
        rescore_payload = mock_auto_rescore.call_args.args[1]
        assert rescore_payload.scope == "project"
        assert rescore_payload.force_unlock is True
        assert mock_auto_rescore.call_args.kwargs["run_feedback_closed_loop"] is False
        assert (
            mock_auto_rescore.call_args.kwargs["history_trigger"] == "feedback_closed_loop_rescore"
        )

    @patch("app.main.save_evolution_reports")
    @patch("app.main.load_evolution_reports")
    @patch("app.main._build_feature_confidence_summary")
    @patch("app.main._build_material_knowledge_profile")
    @patch("app.main.build_evolution_report")
    @patch("app.main._merge_materials_text")
    @patch("app.main.load_project_context")
    @patch("app.main.load_ground_truth")
    @patch("app.main.load_projects")
    def test_refresh_evolution_report_keeps_learning_quality_blocked_samples(
        self,
        mock_load_projects,
        mock_load_ground_truth,
        mock_load_project_context,
        mock_merge_materials_text,
        mock_build_evolution_report,
        mock_build_material_knowledge_profile,
        mock_build_feature_confidence_summary,
        mock_load_evolution_reports,
        mock_save_evolution_reports,
    ):
        from app.main import _refresh_evolution_report_from_ground_truth

        mock_load_projects.return_value = [{"id": "p1", "meta": {"score_scale_max": 5}}]
        mock_load_ground_truth.return_value = [
            {
                "id": "gt-1",
                "project_id": "p1",
                "shigong_text": "示例施组文本",
                "judge_scores": [4.2, 4.3, 4.3, 4.4, 4.4, 4.4, 4.5],
                "final_score": 4.31,
                "score_scale_max": 5,
                "learning_quality_gate": {
                    "blocked": True,
                    "reasons": ["material_gate_blocked", "low_score_self_awareness"],
                },
            }
        ]
        mock_load_project_context.return_value = {}
        mock_merge_materials_text.return_value = ""
        mock_build_material_knowledge_profile.return_value = {}
        mock_build_feature_confidence_summary.return_value = {}
        mock_load_evolution_reports.return_value = {}

        def _build(project_id, records, project_context):
            assert project_id == "p1"
            assert project_context == ""
            assert len(records) == 1
            assert records[0]["final_score"] == pytest.approx(86.2, abs=1e-6)
            return {
                "project_id": "p1",
                "high_score_logic": ["逻辑A"],
                "writing_guidance": ["建议A"],
                "sample_count": len(records),
                "updated_at": "2026-03-25T06:30:00+00:00",
                "scoring_evolution": {},
                "compilation_instructions": {},
            }

        mock_build_evolution_report.side_effect = _build

        payload = _refresh_evolution_report_from_ground_truth("p1")

        assert payload["refreshed"] is True
        assert payload["sample_count"] == 1
        mock_save_evolution_reports.assert_called_once()

    @patch("app.main.save_evolution_reports")
    @patch("app.main.load_evolution_reports")
    @patch("app.main._build_feature_confidence_summary")
    @patch("app.main._build_material_knowledge_profile")
    @patch("app.main.build_evolution_report")
    @patch("app.main._merge_materials_text")
    @patch("app.main.load_project_context")
    @patch("app.main.load_ground_truth")
    @patch("app.main.load_projects")
    def test_refresh_evolution_report_preserves_runtime_governance_metadata(
        self,
        mock_load_projects,
        mock_load_ground_truth,
        mock_load_project_context,
        mock_merge_materials_text,
        mock_build_evolution_report,
        mock_build_material_knowledge_profile,
        mock_build_feature_confidence_summary,
        mock_load_evolution_reports,
        mock_save_evolution_reports,
    ):
        from app.main import _refresh_evolution_report_from_ground_truth

        mock_load_projects.return_value = [{"id": "p1", "meta": {"score_scale_max": 5}}]
        mock_load_ground_truth.return_value = []
        mock_load_project_context.return_value = {}
        mock_merge_materials_text.return_value = ""
        mock_build_material_knowledge_profile.return_value = {}
        mock_build_feature_confidence_summary.return_value = {}
        mock_build_evolution_report.return_value = {
            "project_id": "p1",
            "high_score_logic": [],
            "writing_guidance": [],
            "sample_count": 0,
            "updated_at": "2026-03-30T12:00:00+00:00",
            "scoring_evolution": {},
            "compilation_instructions": {},
        }
        mock_load_evolution_reports.return_value = {
            "p1": {
                "calibrator_runtime_governance": {
                    "action": "rollback",
                    "reason": "rollback_preview_improved",
                    "rollback_candidate_version": "calib_prev",
                    "active_calibrator_version_after": "calib_prev",
                    "degraded_after": False,
                    "recovered_after": True,
                }
            }
        }

        _refresh_evolution_report_from_ground_truth("p1")

        saved_reports = mock_save_evolution_reports.call_args.args[0]
        assert saved_reports["p1"]["calibrator_runtime_governance"]["action"] == "rollback"
        assert (
            saved_reports["p1"]["calibrator_runtime_governance"]["rollback_candidate_version"]
            == "calib_prev"
        )
        assert (
            saved_reports["p1"]["calibrator_runtime_governance"]["active_calibrator_version_after"]
            == "calib_prev"
        )
        assert saved_reports["p1"]["calibrator_runtime_governance"]["recovered_after"] is True


class TestGroundTruthGuardrailRoutes:
    @patch("app.main.load_ground_truth")
    def test_build_manual_confirmation_detail_uses_project_scale_delta(
        self,
        mock_load_ground_truth,
    ):
        from app.main import _build_manual_confirmation_detail

        mock_load_ground_truth.return_value = [
            {
                "id": "gt-1",
                "project_id": "p1",
                "final_score": 4.31,
                "score_scale_max": 5,
                "feedback_guardrail": {
                    "blocked": True,
                    "threshold_blocked": True,
                    "abs_delta_100": 74.02,
                    "relative_delta_ratio": 0.7402,
                    "warning_message": "当前分与真实总分偏差 74.02 分（100分口径，74.0%）。",
                },
            }
        ]

        detail = _build_manual_confirmation_detail("p1", action_label="学习进化")

        assert "5分制" in detail
        assert "100分口径" not in detail
        assert "confirm_extreme_sample=1" in detail

    @patch("app.main.load_submissions")
    @patch("app.main.load_ground_truth")
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    def test_list_ground_truth_enriches_submission_filename_for_legacy_rows(
        self,
        mock_ensure,
        mock_load_projects,
        mock_load_ground_truth,
        mock_load_submissions,
        client,
    ):
        mock_load_projects.return_value = [{"id": "p1"}]
        mock_load_ground_truth.return_value = [
            {
                "id": "gt-1",
                "project_id": "p1",
                "shigong_text": "示例施组文本" * 20,
                "judge_scores": [4.1, 4.2, 4.3, 4.4, 4.5],
                "final_score": 4.3,
                "source": "青天大模型",
                "created_at": "2026-03-24T10:16:57+00:00",
            }
        ]
        mock_load_submissions.return_value = [
            {
                "id": "s1",
                "project_id": "p1",
                "filename": "安徽省豪伟建设集团有限公司-包河区档案馆提升改造项目施工总承包(1).pdf",
                "text": "示例施组文本" * 20,
                "created_at": "2026-03-24T10:16:57+00:00",
            }
        ]

        response = client.get("/api/v1/projects/p1/ground_truth")

        assert response.status_code == 200
        data = response.json()
        assert data[0]["source_submission_id"] == "s1"
        assert (
            data[0]["source_submission_filename"]
            == "安徽省豪伟建设集团有限公司-包河区档案馆提升改造项目施工总承包(1).pdf"
        )
        assert data[0]["source_submission_created_at"] == "2026-03-24T10:16:57+00:00"

    @patch("app.main._run_feedback_closed_loop_safe")
    @patch("app.main._sync_ground_truth_record_to_qingtian")
    @patch("app.main.save_ground_truth")
    @patch("app.main.load_ground_truth")
    @patch("app.main.load_submissions")
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    def test_add_ground_truth_from_submission_returns_guardrail_metadata(
        self,
        mock_ensure,
        mock_load_projects,
        mock_load_submissions,
        mock_load_ground_truth,
        mock_save_ground_truth,
        mock_sync_gt,
        mock_run_closed_loop,
        client,
    ):
        mock_load_projects.return_value = [{"id": "p1", "meta": {"score_scale_max": 100}}]
        mock_load_submissions.return_value = [
            {
                "id": "s1",
                "project_id": "p1",
                "filename": "施组一.docx",
                "created_at": "2026-03-24T10:16:57+00:00",
                "text": "示例施组文本" * 20,
            }
        ]
        mock_load_ground_truth.return_value = []
        mock_sync_gt.return_value = {
            "feedback_guardrail": {
                "blocked": True,
                "warning_message": "当前分与真实总分偏差 45.00 分（100分口径，45.0%）。",
            },
            "learning_quality_gate": {
                "blocked": True,
                "reasons": ["missing_evidence_hits"],
                "warning_message": "当前真实评分样本未纳入自动学习。",
            },
            "few_shot_distillation": {"captured": 0, "reason": "guardrail_blocked"},
        }
        mock_run_closed_loop.return_value = {
            "ok": True,
            "guardrail_triggered": True,
            "requires_manual_confirmation": True,
            "feedback_guardrail": {"blocked": True},
        }

        response = client.post(
            "/api/v1/projects/p1/ground_truth/from_submission",
            json={
                "submission_id": "s1",
                "judge_scores": [80, 81, 82, 83, 84],
                "final_score": 80,
                "source": "青天大模型",
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["feedback_guardrail"]["blocked"] is True
        assert data["learning_quality_gate"]["blocked"] is True
        assert data["few_shot_distillation"]["reason"] == "guardrail_blocked"
        assert data["feedback_closed_loop"]["guardrail_triggered"] is True
        assert data["source_submission_id"] == "s1"
        assert data["source_submission_filename"] == "施组一.docx"
        assert data["source_submission_created_at"] == "2026-03-24T10:16:57+00:00"
        mock_run_closed_loop.assert_called_once()
        assert mock_run_closed_loop.call_args.kwargs["ground_truth_record_ids"]

    @patch("app.main._run_feedback_closed_loop_safe")
    @patch("app.main._sync_ground_truth_record_to_qingtian")
    @patch("app.main.save_ground_truth")
    @patch("app.main.load_ground_truth")
    @patch("app.main.load_submissions")
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    def test_add_ground_truth_from_submission_auto_computes_zero_final_score_from_rule(
        self,
        mock_ensure,
        mock_load_projects,
        mock_load_submissions,
        mock_load_ground_truth,
        mock_save_ground_truth,
        mock_sync_gt,
        mock_run_closed_loop,
        client,
    ):
        mock_load_projects.return_value = [
            {
                "id": "p1",
                "meta": {
                    "score_scale_max": 5,
                    "ground_truth_final_score_formula": "simple_mean",
                },
            }
        ]
        mock_load_submissions.return_value = [
            {
                "id": "s1",
                "project_id": "p1",
                "filename": "施组一.docx",
                "created_at": "2026-03-24T10:16:57+00:00",
                "text": "示例施组文本" * 20,
            }
        ]
        mock_load_ground_truth.return_value = []
        mock_sync_gt.return_value = {
            "feedback_guardrail": {"blocked": False},
            "learning_quality_gate": {"blocked": False},
            "few_shot_distillation": {"captured": 0, "reason": "not_executed"},
        }
        mock_run_closed_loop.return_value = {"ok": True}

        response = client.post(
            "/api/v1/projects/p1/ground_truth/from_submission",
            json={
                "submission_id": "s1",
                "judge_scores": [4.33, 4.36, 4.35, 4.36, 4.8],
                "final_score": 0,
                "source": "青天大模型",
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["final_score"] == 4.44
        assert data["final_score_raw"] == 4.44
        assert data["final_score_100"] == 88.8
        saved_rows = mock_save_ground_truth.call_args_list[0].args[0]
        assert saved_rows[0]["final_score"] == 4.44
        assert saved_rows[0]["final_score_raw"] == 4.44
        assert saved_rows[0]["final_score_100"] == 88.8

    @patch("app.main._refresh_project_reflection_objects")
    @patch("app.main.save_qingtian_results")
    @patch("app.main.load_qingtian_results")
    @patch("app.main.save_ground_truth")
    @patch("app.main.load_ground_truth")
    @patch("app.main.load_submissions")
    @patch("app.main.load_config")
    @patch("app.main._resolve_project_scoring_context")
    @patch("app.main.load_projects")
    def test_sync_ground_truth_record_to_qingtian_updates_existing_qingtian_score_after_repair(
        self,
        mock_load_projects,
        mock_resolve_context,
        mock_load_config,
        mock_load_submissions,
        mock_load_ground_truth,
        mock_save_ground_truth,
        mock_load_qingtian_results,
        mock_save_qingtian_results,
        mock_refresh_reflection,
    ):
        from app.main import _sync_ground_truth_record_to_qingtian

        gt_record = {
            "id": "gt-1",
            "project_id": "p1",
            "source": "青天大模型",
            "shigong_text": "示例施组文本" * 20,
            "judge_scores": [4.33, 4.36, 4.35, 4.36, 4.8],
            "final_score": 4.44,
            "final_score_raw": 4.44,
            "final_score_100": 88.8,
            "score_scale_max": 5,
        }
        mock_load_projects.return_value = [
            {
                "id": "p1",
                "meta": {"score_scale_max": 5},
                "scoring_engine_version_locked": "v2",
            }
        ]
        mock_resolve_context.return_value = ({}, None, None)
        mock_load_config.return_value = MagicMock(rubric={}, lexicon={})
        mock_load_submissions.return_value = [
            {
                "id": "s1",
                "project_id": "p1",
                "filename": "施组一.docx",
                "text": "示例施组文本" * 20,
                "source_ground_truth_id": "gt-1",
                "report": {
                    "total_score": 88.8,
                    "pred_total_score": 88.8,
                    "rule_total_score": 2.93,
                    "score_blend": {"mode": "ground_truth_exact"},
                },
            }
        ]
        mock_load_ground_truth.return_value = [{"id": "gt-1", "project_id": "p1"}]
        mock_load_qingtian_results.return_value = [
            {
                "id": "qt-1",
                "submission_id": "s1",
                "qingtian_model_version": "old",
                "qt_total_score": 0.0,
                "qt_dim_scores": {"旧": 1},
                "qt_reasons": [{"kind": "legacy", "text": "旧值"}],
                "raw_payload": {
                    "ground_truth_record_id": "gt-1",
                    "final_score": 0.0,
                    "final_score_raw": 0.0,
                    "final_score_100": 0.0,
                },
                "created_at": "2026-03-31T00:00:00+00:00",
            }
        ]

        with (
            patch(
                "app.feedback_learning.build_ground_truth_feedback_guardrail",
                return_value={"blocked": False},
            ),
            patch(
                "app.feedback_learning.build_ground_truth_learning_quality_gate",
                return_value={"blocked": False},
            ),
            patch(
                "app.feedback_learning.auto_update_feature_confidence_on_ground_truth",
                return_value={"updated": 0, "retired": 0},
            ),
            patch(
                "app.feedback_learning.capture_ground_truth_few_shot_features",
                return_value={"captured": 0, "reason": "not_executed"},
            ),
        ):
            _sync_ground_truth_record_to_qingtian("p1", gt_record)

        saved_rows = mock_save_qingtian_results.call_args.args[0]
        assert saved_rows[0]["qt_total_score"] == 88.8
        assert saved_rows[0]["qt_dim_scores"] is None
        assert saved_rows[0]["qt_reasons"][0]["kind"] == "ground_truth"
        assert saved_rows[0]["raw_payload"]["final_score"] == 4.44
        assert saved_rows[0]["raw_payload"]["final_score_raw"] == 4.44
        assert saved_rows[0]["raw_payload"]["final_score_100"] == 88.8
        assert saved_rows[0]["raw_payload"]["score_scale_max"] == 5
        mock_save_ground_truth.assert_called_once()
        mock_refresh_reflection.assert_called_once()

    def test_build_ground_truth_feedback_guardrail_auto_approves_high_consensus_sample(self):
        from app.main import _build_ground_truth_feedback_guardrail

        report = {"pred_total_score": 22.97}
        gt_record = {
            "final_score": 82.2,
            "score_scale_max": 100,
            "judge_scores": [82.16, 82.19, 82.16, 82.18, 82.22, 82.19, 82.28],
        }

        guardrail = _build_ground_truth_feedback_guardrail(
            report=report,
            gt_record=gt_record,
            project_score_scale_max=100,
        )

        assert guardrail["threshold_blocked"] is True
        assert guardrail["blocked"] is False
        assert guardrail["manual_review_status"] == "approved"
        assert guardrail["auto_approved_consensus"] is True
        assert guardrail["judge_score_span"] == 0.12
        assert guardrail["judge_score_stddev"] <= 0.04

    def test_build_ground_truth_feedback_guardrail_keeps_blocked_for_low_consensus_sample(self):
        from app.main import _build_ground_truth_feedback_guardrail

        report = {"pred_total_score": 22.97}
        gt_record = {
            "final_score": 82.2,
            "score_scale_max": 100,
            "judge_scores": [80.5, 81.4, 82.3, 83.1, 84.2, 85.0, 86.1],
        }

        guardrail = _build_ground_truth_feedback_guardrail(
            report=report,
            gt_record=gt_record,
            project_score_scale_max=100,
        )

        assert guardrail["threshold_blocked"] is True
        assert guardrail["blocked"] is True
        assert guardrail["manual_review_status"] == "pending"
        assert guardrail.get("auto_approved_consensus") is not True

    @patch("app.main.upsert_distilled_features", return_value={"upserted": 1})
    @patch("app.main.distill_feature_from_text")
    @patch("app.main._collect_dimension_guidance_texts", return_value=["编制提示"])
    @patch("app.main._collect_dimension_evidence_texts", return_value=["高分证据"])
    @patch("app.main._flatten_ground_truth_qualitative_tags", return_value=["评委反馈"])
    @patch("app.main._select_ground_truth_few_shot_dimensions", return_value=["01"])
    def test_capture_ground_truth_few_shot_features_auto_adopts_high_consensus_sample(
        self,
        mock_select_dims,
        mock_tags,
        mock_evidence,
        mock_guidance,
        mock_distill,
        mock_upsert,
    ):
        from app.feedback_learning import capture_ground_truth_few_shot_features

        mock_distill.return_value = MagicMock(feature_id="F-1")
        report = {"dimension_scores": {"01": {"score": 5.0}}}
        gt_record = {
            "final_score": 82.2,
            "score_scale_max": 100,
            "judge_scores": [82.16, 82.19, 82.16, 82.18, 82.22, 82.19, 82.28],
        }

        result = capture_ground_truth_few_shot_features(
            report=report,
            gt_record=gt_record,
            project_score_scale_max=100,
            feedback_guardrail={"blocked": False},
            learning_quality_gate={"blocked": False},
            feature_confidence_update={"updated": 1},
        )

        assert result["captured"] == 1
        assert result["manual_review"]["status"] == "adopted"
        assert result["manual_review"]["note"] == "high_consensus_auto_adopted"
        assert result["auto_adopted_consensus"] is True
        mock_select_dims.assert_called_once()
        mock_upsert.assert_called_once()

    @patch("app.main._sync_ground_truth_record_to_qingtian")
    @patch("app.main.load_ground_truth")
    def test_refresh_project_ground_truth_learning_records_refreshes_pending_few_shot_rows(
        self,
        mock_load_ground_truth,
        mock_sync_gt,
    ):
        from app.feedback_learning import refresh_project_ground_truth_learning_records

        mock_load_ground_truth.return_value = [
            {
                "id": "gt-1",
                "project_id": "p1",
                "few_shot_distillation": {
                    "captured": 2,
                    "reason": "captured",
                    "manual_review": {"status": "pending"},
                },
            }
        ]
        mock_sync_gt.return_value = {
            "feedback_guardrail": {"blocked": False},
            "few_shot_distillation": {
                "captured": 2,
                "reason": "captured",
                "manual_review": {"status": "adopted"},
            },
        }

        result = refresh_project_ground_truth_learning_records("p1")

        assert result["refreshed"] == 1
        assert result["blocked_after"] == 0
        assert result["auto_approved_after"] == 0
        mock_sync_gt.assert_called_once_with("p1", mock_load_ground_truth.return_value[0])

    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    @patch("app.main._collect_blocked_ground_truth_guardrails")
    def test_auto_run_reflection_requires_manual_confirm_when_guardrail_blocked(
        self,
        mock_collect_blocked,
        mock_ensure,
        mock_load_projects,
        client,
    ):
        mock_load_projects.return_value = [{"id": "p1"}]
        mock_collect_blocked.return_value = [
            {"record_id": "gt-1", "feedback_guardrail": {"blocked": True, "abs_delta_100": 42.0}}
        ]

        response = client.post("/api/v1/projects/p1/reflection/auto_run")

        assert response.status_code == 409
        assert "confirm_extreme_sample=1" in response.json()["detail"]

    @patch("app.main._refresh_project_reflection_objects")
    @patch("app.main._build_feedback_governance_report")
    @patch("app.main._collect_blocked_ground_truth_guardrails")
    @patch("app.main._refresh_project_ground_truth_learning_records")
    @patch("app.main.load_calibration_samples")
    @patch("app.main.load_delta_cases")
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    def test_auto_run_reflection_refreshes_ground_truth_guardrails_before_block_check(
        self,
        mock_ensure,
        mock_load_projects,
        mock_load_delta_cases,
        mock_load_calibration_samples,
        mock_refresh_ground_truth,
        mock_collect_blocked,
        mock_build_governance,
        mock_refresh_reflection,
        client,
    ):
        events = []
        mock_load_projects.return_value = [{"id": "p1"}]
        mock_load_delta_cases.return_value = []
        mock_load_calibration_samples.return_value = []
        mock_build_governance.return_value = {
            "summary": {
                "manual_confirmation_required": False,
                "pending_extreme_ground_truth_count": 0,
                "blocked_ground_truth_count": 0,
                "approved_extreme_ground_truth_count": 0,
                "manual_override_hint": None,
                "current_calibrator_deployment_mode": "prior_fallback",
            },
            "recommendations": ["当前链路未命中人工确认阻塞。"],
        }
        mock_refresh_ground_truth.side_effect = lambda project_id: events.append(
            ("refresh", project_id)
        ) or {"refreshed": 0}
        mock_collect_blocked.side_effect = (
            lambda *args, **kwargs: events.append(
                ("collect", args[0] if args else kwargs.get("project_id"))
            )
            or []
        )
        mock_refresh_reflection.side_effect = lambda *args, **kwargs: events.append(
            ("reflection", args[0] if args else kwargs.get("project_id"))
        )

        response = client.post("/api/v1/projects/p1/reflection/auto_run")

        assert response.status_code == 200
        data = response.json()
        assert events[:2] == [("refresh", "p1"), ("collect", "p1")]
        assert data["manual_confirmation_audit"]["status"] == "clear"
        assert data["manual_confirmation_audit"]["override_used"] is False
        assert data["manual_confirmation_audit"]["before_pending_extreme_ground_truth_count"] == 0
        assert data["manual_confirmation_audit"]["delta_pending_extreme_ground_truth_count"] == 0
        assert data["manual_confirmation_audit"]["gate_cleared_after_reverify"] is False
        assert str(
            data["manual_confirmation_audit"]["current_calibrator_deployment_mode"] or ""
        ).strip()


class TestFeedbackGovernanceRoutes:
    @patch("app.main.load_expert_profiles")
    @patch("app.main.load_calibration_models")
    @patch("app.main.load_evolution_reports")
    @patch("app.main.load_high_score_features")
    @patch("app.main.load_json_version")
    @patch("app.main.list_json_versions")
    @patch("app.main.load_ground_truth")
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    def test_feedback_governance_route_summarizes_blocked_samples_and_few_shot(
        self,
        mock_ensure,
        mock_load_projects,
        mock_load_ground_truth,
        mock_list_versions,
        mock_load_json_version,
        mock_load_high_score_features,
        mock_load_evolution_reports,
        mock_load_calibration_models,
        mock_load_expert_profiles,
        client,
    ):
        mock_load_projects.return_value = [{"id": "p1", "meta": {"score_scale_max": 100}}]
        mock_load_high_score_features.return_value = [
            {
                "feature_id": "f-current-1",
                "dimension_id": "09",
                "confidence_score": 0.88,
                "active": True,
            }
        ]
        mock_load_evolution_reports.return_value = {"p1": {"high_score_logic": ["逻辑A"]}}
        mock_load_calibration_models.return_value = [
            {"calibrator_version": "calib-1", "model_type": "ridge"}
        ]
        mock_load_expert_profiles.return_value = [
            {"id": "ep-1", "name": "默认画像", "read_only": True}
        ]
        mock_load_ground_truth.return_value = [
            {
                "id": "gt-blocked",
                "project_id": "p1",
                "final_score": 80,
                "score_scale_max": 100,
                "judge_scores": [80, 80, 80, 80, 80],
                "source_submission_filename": "异常样本.docx",
                "created_at": "2026-03-14T00:00:00+00:00",
                "feedback_guardrail": {
                    "blocked": True,
                    "threshold_blocked": True,
                    "actual_score_100": 80,
                    "predicted_score_100": 30,
                    "abs_delta_100": 50,
                    "relative_delta_ratio": 0.5,
                    "warning_message": "当前分与真实总分偏差过大，已暂停自动调权/自动校准。",
                },
                "few_shot_distillation": {"captured": 0, "reason": "guardrail_blocked"},
            },
            {
                "id": "gt-good",
                "project_id": "p1",
                "final_score": 88,
                "score_scale_max": 100,
                "judge_scores": [88, 88, 88, 88, 88],
                "created_at": "2026-03-14T01:00:00+00:00",
                "few_shot_distillation": {
                    "captured": 2,
                    "reason": "captured",
                    "dimension_ids": ["09"],
                    "feature_ids": ["F-1", "F-2"],
                    "manual_review": {
                        "status": "adopted",
                        "reviewed_at": "2026-03-14T03:00:00+00:00",
                    },
                },
            },
            {
                "id": "gt-approved",
                "project_id": "p1",
                "final_score": 86,
                "score_scale_max": 100,
                "judge_scores": [86, 86, 86, 86, 86],
                "created_at": "2026-03-14T02:00:00+00:00",
                "feedback_guardrail": {
                    "blocked": False,
                    "threshold_blocked": True,
                    "actual_score_100": 86,
                    "predicted_score_100": 40,
                    "abs_delta_100": 46,
                    "manual_review": {
                        "status": "approved",
                        "reviewed_at": "2026-03-14T04:00:00+00:00",
                        "note": "人工放行",
                    },
                },
                "feedback_closed_loop": {
                    "weight_update": {"updated": True},
                    "auto_run": {
                        "delta_cases": 3,
                        "calibration_samples": 5,
                        "calibrator_version": "calib-2",
                    },
                    "evolution_refresh": {"sample_count": 4},
                },
            },
        ]

        def _versions(path):
            stem = getattr(path, "stem", "")
            if stem == "high_score_features":
                return [
                    {
                        "version_id": "20260314T010203000000Z",
                        "created_at": "2026-03-14T01:02:03+00:00",
                    }
                ]
            if stem == "evolution_reports":
                return [
                    {
                        "version_id": "20260314T020304000000Z",
                        "created_at": "2026-03-14T02:03:04+00:00",
                    }
                ]
            if stem == "calibration_models":
                return [
                    {
                        "version_id": "20260314T030405000000Z",
                        "created_at": "2026-03-14T03:04:05+00:00",
                    }
                ]
            if stem == "expert_profiles":
                return [
                    {
                        "version_id": "20260314T040506000000Z",
                        "created_at": "2026-03-14T04:05:06+00:00",
                    }
                ]
            return []

        mock_list_versions.side_effect = _versions
        mock_load_json_version.side_effect = lambda path, version_id, default: (
            [
                {
                    "feature_id": "f-snap-1",
                    "dimension_id": "09",
                    "confidence_score": 0.72,
                    "active": True,
                },
                {
                    "feature_id": "f-snap-2",
                    "dimension_id": "08",
                    "confidence_score": 0.66,
                    "active": True,
                },
            ]
            if getattr(path, "stem", "") == "high_score_features"
            else (
                {"p1": {"high_score_logic": []}}
                if getattr(path, "stem", "") == "evolution_reports"
                else (
                    [{"calibrator_version": "calib-0", "model_type": "offset"}]
                    if getattr(path, "stem", "") == "calibration_models"
                    else [{"id": "ep-old", "name": "旧画像", "read_only": False}]
                )
            )
        )

        with (
            patch("app.main.load_submissions", return_value=[]),
            patch("app.main.load_score_reports", return_value=[]),
            patch("app.main.load_qingtian_results", return_value=[]),
        ):
            response = client.get("/api/v1/projects/p1/feedback/governance")

        assert response.status_code == 200
        data = response.json()
        assert data["summary"]["ground_truth_count"] == 3
        assert data["summary"]["active_ground_truth_count"] == 2
        assert data["summary"]["blocked_ground_truth_count"] == 1
        assert data["summary"]["few_shot_recent_capture_count"] == 1
        assert data["summary"]["manual_confirmation_required"] is True
        assert data["summary"]["approved_extreme_ground_truth_count"] == 1
        assert data["summary"]["few_shot_adopted_count"] == 1
        assert data["summary"]["pending_extreme_ground_truth_count"] == 1
        assert data["summary"]["few_shot_pending_review_count"] == 0
        assert data["summary"]["current_calibrator_degraded"] is False
        assert data["summary"]["current_calibrator_rollback_candidate_version"] is None
        assert data["blocked_samples"][0]["record_id"] == "gt-blocked"
        assert data["blocked_samples"][0]["score_scale_label"] == "100分制"
        assert data["blocked_samples"][0]["current_score"] == 30
        assert data["blocked_samples"][0]["abs_delta"] == 50
        assert data["blocked_samples"][0]["current_score_100"] == 30
        assert data["few_shot_recent"][0]["record_id"] == "gt-good"
        assert data["approved_samples"][0]["record_id"] == "gt-approved"
        assert data["approved_samples"][0]["abs_delta"] == 46
        assert data["adopted_few_shot"][0]["record_id"] == "gt-good"
        assert (
            data["version_history"][0]["recent_versions"][0]["version_id"]
            == "20260314T010203000000Z"
        )
        assert data["artifact_impacts"][0]["artifact"] == "high_score_features"
        assert data["artifact_impacts"][0]["changed_since_latest_snapshot"] is True
        assert data["score_preview"]["matched_submission_count"] == 0

    @patch("app.main.load_expert_profiles", return_value=[])
    @patch("app.main.load_calibration_models", return_value=[])
    @patch("app.main.load_evolution_reports", return_value={})
    @patch("app.main.load_high_score_features", return_value=[])
    @patch("app.main.load_json_version", return_value=[])
    @patch("app.main.list_json_versions", return_value=[])
    @patch("app.main.load_ground_truth")
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    def test_feedback_governance_route_prefers_native_score_fields_for_five_scale_project(
        self,
        mock_ensure,
        mock_load_projects,
        mock_load_ground_truth,
        _mock_list_versions,
        _mock_load_json_version,
        _mock_load_high_score_features,
        _mock_load_evolution_reports,
        _mock_load_calibration_models,
        _mock_load_expert_profiles,
        client,
    ):
        mock_load_projects.return_value = [{"id": "p1", "meta": {"score_scale_max": 5}}]
        mock_load_ground_truth.return_value = [
            {
                "id": "gt-blocked",
                "project_id": "p1",
                "final_score": 4.14,
                "final_score_raw": 4.14,
                "final_score_100": 82.8,
                "score_scale_max": 5,
                "judge_scores": [4.15, 4.09, 4.15, 4.15, 4.21],
                "source_submission_filename": "档案馆施工总承包(2).pdf",
                "created_at": "2026-03-14T00:00:00+00:00",
                "feedback_guardrail": {
                    "blocked": True,
                    "threshold_blocked": True,
                    "actual_score_raw": 4.14,
                    "predicted_score_raw": 0.5955,
                    "current_score_raw": 0.5955,
                    "actual_score_100": 82.8,
                    "predicted_score_100": 11.91,
                    "current_score_100": 11.91,
                    "abs_delta_raw": 3.5445,
                    "abs_delta_100": 70.89,
                    "relative_delta_ratio": 0.7089,
                },
            }
        ]

        with (
            patch("app.main.load_submissions", return_value=[]),
            patch("app.main.load_score_reports", return_value=[]),
            patch("app.main.load_qingtian_results", return_value=[]),
        ):
            response = client.get("/api/v1/projects/p1/feedback/governance")

        assert response.status_code == 200
        data = response.json()
        blocked = data["blocked_samples"][0]
        assert blocked["score_scale_label"] == "5分制"
        assert blocked["actual_score"] == 4.14
        assert blocked["current_score"] == 0.5955
        assert blocked["abs_delta"] == 3.5445
        assert "100分口径" not in (blocked["warning_message"] or "")
        assert any(row["artifact"] == "high_score_features" for row in data["version_history"])

    @patch("app.main.load_json_version")
    @patch("app.main.list_json_versions")
    @patch("app.main.load_high_score_features")
    def test_load_governance_artifact_payload_falls_back_to_latest_snapshot_when_corrupted(
        self,
        mock_load_high_score_features,
        mock_list_versions,
        mock_load_json_version,
    ):
        from app.main import _load_governance_artifact_payload
        from app.storage import StorageDataError

        mock_load_high_score_features.side_effect = StorageDataError(
            Path("/tmp/high_score_features.json"),
            "json_parse_failed",
            "数据文件 JSON 格式损坏：high_score_features.json（第 1 行，第 1 列），请使用历史版本回滚。",
        )
        mock_list_versions.return_value = [
            {"version_id": "20260324T111500000000Z", "created_at": "2026-03-24T11:15:00+00:00"}
        ]
        mock_load_json_version.return_value = [
            {
                "feature_id": "f-1",
                "dimension_id": "09",
                "confidence_score": 0.86,
                "active": True,
            }
        ]

        payload = _load_governance_artifact_payload("high_score_features")

        assert isinstance(payload, list)
        assert payload[0]["feature_id"] == "f-1"

    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    def test_feedback_governance_route_includes_score_preview(
        self,
        mock_ensure,
        mock_load_projects,
        client,
    ):
        mock_load_projects.return_value = [
            {
                "id": "p1",
                "meta": {"score_scale_max": 100},
                "expert_profile_id": "ep-1",
            }
        ]

        def _apply_preview(report, *, submission_like, project, model_override=None):
            report["pred_total_score"] = 78.0
            report["total_score"] = 78.0
            report["llm_total_score"] = 78.0
            report["pred_confidence"] = {"sigma": 1.0}
            report.setdefault("meta", {})["calibrator_version"] = "calib-9"
            return "calib-9"

        with (
            patch(
                "app.main.load_ground_truth",
                return_value=[
                    {
                        "id": "gt-1",
                        "project_id": "p1",
                        "final_score": 84,
                        "score_scale_max": 100,
                        "judge_scores": [84, 84, 84, 84, 84],
                        "created_at": "2026-03-15T01:00:00+00:00",
                        "source_submission_filename": "投标文件A.docx",
                    }
                ],
            ),
            patch("app.main.load_high_score_features", return_value=[]),
            patch("app.main.load_evolution_reports", return_value={}),
            patch(
                "app.main.load_calibration_models",
                return_value=[
                    {
                        "calibrator_version": "calib-9",
                        "model_type": "ridge",
                        "deployed": True,
                        "train_filter": {"project_id": "p1"},
                        "updated_at": "2026-03-15T06:00:00+00:00",
                    }
                ],
            ),
            patch(
                "app.main.load_expert_profiles",
                return_value=[
                    {
                        "id": "ep-1",
                        "weights_norm": {f"{i:02d}": 1 / 16 for i in range(1, 17)},
                        "name": "默认画像",
                    }
                ],
            ),
            patch(
                "app.main.list_json_versions",
                side_effect=lambda path: (
                    [
                        {
                            "version_id": "20260315T050000000000Z",
                            "created_at": "2026-03-15T05:00:00+00:00",
                        }
                    ]
                    if getattr(path, "stem", "") == "calibration_models"
                    else []
                ),
            ),
            patch(
                "app.main.load_json_version",
                side_effect=lambda path, version_id, default: (
                    [{"calibrator_version": "calib-8", "model_type": "offset"}]
                    if getattr(path, "stem", "") == "calibration_models"
                    else default
                ),
            ),
            patch(
                "app.main.load_submissions",
                return_value=[
                    {
                        "id": "s1",
                        "project_id": "p1",
                        "filename": "投标文件A.docx",
                        "text": "测试内容",
                        "total_score": 72.0,
                    }
                ],
            ),
            patch(
                "app.main.load_score_reports",
                return_value=[
                    {
                        "id": "r1",
                        "project_id": "p1",
                        "submission_id": "s1",
                        "created_at": "2026-03-15T02:00:00+00:00",
                        "rule_total_score": 70.0,
                        "pred_total_score": 72.0,
                        "rule_dim_scores": {
                            "09": {"dim_score": 4.0},
                            "10": {"dim_score": 5.0},
                        },
                    }
                ],
            ),
            patch(
                "app.main.load_qingtian_results",
                return_value=[
                    {
                        "submission_id": "s1",
                        "qt_total_score": 84.0,
                        "created_at": "2026-03-15T03:00:00+00:00",
                        "raw_payload": {
                            "ground_truth_record_id": "gt-1",
                            "final_score_100": 84.0,
                        },
                    }
                ],
            ),
            patch(
                "app.main._apply_prediction_to_report_with_model",
                side_effect=_apply_preview,
            ),
            patch(
                "app.main.load_config",
                return_value=MagicMock(rubric={}, lexicon={}),
            ),
            patch(
                "app.main.load_learning_profiles",
                return_value=[],
            ),
            patch(
                "app.main._build_material_knowledge_profile",
                return_value={},
            ),
            patch(
                "app.main._build_material_quality_snapshot",
                return_value={},
            ),
            patch(
                "app.main._build_submission_sandbox_report",
                return_value={
                    "rule_total_score": 74.0,
                    "pred_total_score": 79.0,
                    "rule_dim_scores": {
                        "09": {"dim_score": 6.5},
                        "10": {"dim_score": 5.0},
                    },
                    "scoring_status": "scored",
                },
            ),
        ):
            response = client.get("/api/v1/projects/p1/feedback/governance")

        assert response.status_code == 200
        data = response.json()
        preview = data["score_preview"]
        assert preview["matched_submission_count"] == 1
        assert preview["current_calibrator_version"] == "calib-9"
        assert preview["current_calibrator_model_type"] == "ridge"
        assert preview["current_calibrator_source"] == "project"
        assert preview["current_calibrator_bootstrap_small_sample"] is False
        assert preview["current_calibrator_deployment_mode"] == "cv_validated"
        assert preview["calibrator_changed_since_latest_snapshot"] is True
        assert preview["requires_rule_rescore"] is False
        assert preview["dimension_preview_supported"] is False
        assert preview["avg_abs_delta_stored"] == 12.0
        assert preview["avg_abs_delta_preview"] == 6.0
        assert preview["avg_abs_delta_improvement"] == 6.0
        assert preview["improved_row_count"] == 1
        row = preview["rows"][0]
        assert row["submission_id"] == "s1"
        assert row["filename"] == "投标文件A.docx"
        assert row["rule_total_score"] == 70.0
        assert row["stored_total_score"] == 72.0
        assert row["preview_total_score"] == 78.0
        assert row["qt_total_score"] == 84.0
        assert row["stored_abs_delta_100"] == 12.0
        assert row["preview_abs_delta_100"] == 6.0
        assert row["abs_delta_improvement"] == 6.0
        sandbox = data["sandbox_preview"]
        assert sandbox["matched_submission_count"] == 1
        assert sandbox["executed_row_count"] == 1
        assert sandbox["weights_source"] == "expert_profile"
        assert sandbox["avg_abs_delta_stored"] == 12.0
        assert sandbox["avg_abs_delta_sandbox"] == 5.0
        assert sandbox["avg_abs_delta_improvement"] == 7.0
        sandbox_row = sandbox["rows"][0]
        assert sandbox_row["sandbox_total_score"] == 79.0
        assert sandbox_row["changed_dimension_count"] == 1
        assert sandbox_row["top_changed_dimensions"][0]["dimension_id"] == "09"
        assert sandbox_row["top_changed_dimensions"][0]["delta"] == 2.5
        assert any(
            "平均绝对偏差从 12.00 分收敛到 6.00 分" in item for item in data["recommendations"]
        )
        assert any(
            "沙箱重评分显示当前完整体系可将平均绝对偏差从 12.00 分收敛到 5.00 分" in item
            for item in data["recommendations"]
        )
        assert data["summary"]["current_calibrator_version"] == "calib-9"
        assert data["summary"]["current_calibrator_deployment_mode"] == "cv_validated"
        assert data["summary"]["latest_project_calibrator_version"] == "calib-9"
        assert data["summary"]["latest_project_calibrator_deployment_mode"] == "cv_validated"

    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    def test_feedback_governance_version_preview_route_uses_selected_snapshot(
        self,
        mock_ensure,
        mock_load_projects,
        client,
    ):
        mock_load_projects.return_value = [{"id": "p1", "meta": {"score_scale_max": 100}}]

        def _apply_preview(report, *, submission_like, project, model_override=None):
            version = str((model_override or {}).get("calibrator_version") or "calib-current")
            pred_total = 76.0 if version == "calib-preview" else 72.0
            report["pred_total_score"] = pred_total
            report["total_score"] = pred_total
            report["llm_total_score"] = pred_total
            report["pred_confidence"] = {"sigma": 1.0}
            report.setdefault("meta", {})["calibrator_version"] = version
            return version

        def _sandbox_preview(**kwargs):
            version = str(
                (
                    (kwargs.get("calibrator_model_override") or {}).get("calibrator_version")
                    or "calib-current"
                )
            )
            pred_total = 80.0 if version == "calib-preview" else 75.0
            return {
                "rule_total_score": 74.0,
                "pred_total_score": pred_total,
                "rule_dim_scores": {"09": {"dim_score": 6.0}},
                "scoring_status": "scored",
            }

        with (
            patch(
                "app.main.load_ground_truth",
                return_value=[
                    {
                        "id": "gt-1",
                        "project_id": "p1",
                        "final_score": 84,
                        "score_scale_max": 100,
                        "judge_scores": [84, 84, 84, 84, 84],
                        "created_at": "2026-03-15T01:00:00+00:00",
                    }
                ],
            ),
            patch("app.main.load_high_score_features", return_value=[]),
            patch("app.main.load_evolution_reports", return_value={}),
            patch(
                "app.main.load_calibration_models",
                return_value=[
                    {
                        "calibrator_version": "calib-current",
                        "model_type": "ridge",
                        "deployed": True,
                        "train_filter": {"project_id": "p1"},
                        "updated_at": "2026-03-15T06:00:00+00:00",
                    }
                ],
            ),
            patch("app.main.load_expert_profiles", return_value=[]),
            patch(
                "app.main.list_json_versions",
                side_effect=lambda path: (
                    [
                        {
                            "version_id": "20260315T090000000000Z",
                            "created_at": "2026-03-15T09:00:00+00:00",
                        }
                    ]
                    if getattr(path, "stem", "") == "calibration_models"
                    else []
                ),
            ),
            patch(
                "app.main.load_json_version",
                side_effect=lambda path, version_id, default: (
                    [
                        {
                            "calibrator_version": "calib-preview",
                            "model_type": "ridge",
                            "deployed": True,
                            "train_filter": {"project_id": "p1"},
                            "updated_at": "2026-03-15T09:00:00+00:00",
                        }
                    ]
                    if getattr(path, "stem", "") == "calibration_models"
                    else default
                ),
            ),
            patch(
                "app.main.load_submissions",
                return_value=[
                    {
                        "id": "s1",
                        "project_id": "p1",
                        "filename": "投标文件A.docx",
                        "text": "测试内容",
                        "total_score": 72.0,
                    }
                ],
            ),
            patch(
                "app.main.load_score_reports",
                return_value=[
                    {
                        "id": "r1",
                        "project_id": "p1",
                        "submission_id": "s1",
                        "created_at": "2026-03-15T02:00:00+00:00",
                        "rule_total_score": 70.0,
                        "pred_total_score": 72.0,
                        "rule_dim_scores": {"09": {"dim_score": 4.0}},
                    }
                ],
            ),
            patch(
                "app.main.load_qingtian_results",
                return_value=[
                    {
                        "submission_id": "s1",
                        "qt_total_score": 84.0,
                        "created_at": "2026-03-15T03:00:00+00:00",
                        "raw_payload": {"ground_truth_record_id": "gt-1"},
                    }
                ],
            ),
            patch(
                "app.main._apply_prediction_to_report_with_model",
                side_effect=_apply_preview,
            ),
            patch(
                "app.main._build_submission_sandbox_report",
                side_effect=_sandbox_preview,
            ),
            patch(
                "app.main.load_config",
                return_value=MagicMock(rubric={}, lexicon={}),
            ),
            patch(
                "app.main._build_material_knowledge_profile",
                return_value={},
            ),
            patch(
                "app.main._build_material_quality_snapshot",
                return_value={},
            ),
        ):
            response = client.post(
                "/api/v1/projects/p1/feedback/governance/version_preview",
                json={
                    "artifact": "calibration_models",
                    "version_id": "20260315T090000000000Z",
                },
            )

        assert response.status_code == 200
        data = response.json()
        assert data["artifact"] == "calibration_models"
        assert data["version_id"] == "20260315T090000000000Z"
        assert data["current_summary"]["latest_calibrator_version"] == "calib-current"
        assert data["preview_summary"]["latest_calibrator_version"] == "calib-preview"
        assert data["matches_current"] is False
        governance = data["governance"]
        assert governance["score_preview"]["current_calibrator_version"] == "calib-preview"
        assert governance["score_preview"]["current_calibrator_deployment_mode"] == "cv_validated"
        assert governance["score_preview"]["avg_abs_delta_preview"] == 8.0
        assert governance["sandbox_preview"]["avg_abs_delta_sandbox"] == 4.0
        assert any("只读预演" in item for item in data["recommendations"])

    @patch("app.main._run_feedback_closed_loop_safe")
    @patch("app.main.save_ground_truth")
    @patch("app.main._build_feedback_governance_report")
    @patch("app.main.load_ground_truth")
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    def test_feedback_guardrail_preview_route_is_read_only(
        self,
        mock_ensure,
        mock_load_projects,
        mock_load_ground_truth,
        mock_build_governance_report,
        mock_save_ground_truth,
        mock_run_closed_loop,
        client,
    ):
        store = [
            {
                "id": "gt-1",
                "project_id": "p1",
                "feedback_guardrail": {
                    "blocked": True,
                    "threshold_blocked": True,
                    "actual_score_100": 81,
                    "predicted_score_100": 33,
                    "abs_delta_100": 48,
                    "warning_message": "当前分与真实总分偏差过大",
                },
            }
        ]
        mock_load_projects.return_value = [{"id": "p1", "meta": {"score_scale_max": 100}}]
        mock_load_ground_truth.return_value = store

        def _build_report(project_id, project, **kwargs):
            rows = kwargs.get("ground_truth_rows_override") or []
            blocked_count = sum(
                1 for row in rows if bool(((row.get("feedback_guardrail") or {}).get("blocked")))
            )
            return {
                "project_id": project_id,
                "summary": {
                    "ground_truth_count": len(rows),
                    "blocked_ground_truth_count": blocked_count,
                    "manual_confirmation_required": blocked_count > 0,
                },
                "blocked_samples": [
                    {"record_id": str(row.get("id") or "")}
                    for row in rows
                    if bool(((row.get("feedback_guardrail") or {}).get("blocked")))
                ],
                "few_shot_recent": [],
                "approved_samples": [],
                "adopted_few_shot": [],
                "version_history": [],
                "artifact_impacts": [],
                "score_preview": {},
                "sandbox_preview": {},
                "recommendations": [],
            }

        mock_build_governance_report.side_effect = _build_report

        response = client.post(
            "/api/v1/projects/p1/feedback/governance/guardrail/gt-1/preview",
            json={
                "action": "approve",
                "note": "预演放行",
                "rerun_closed_loop": True,
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["preview_type"] == "guardrail"
        assert data["requested_action"] == "approve"
        assert data["current_state"]["blocked"] is True
        assert data["preview_state"]["blocked"] is False
        assert data["preview_state"]["manual_review_status"] == "approved"
        assert data["governance"]["summary"]["blocked_ground_truth_count"] == 0
        assert any("不会执行真实闭环" in item for item in data["recommendations"])
        assert store[0]["feedback_guardrail"]["blocked"] is True
        mock_save_ground_truth.assert_not_called()
        mock_run_closed_loop.assert_not_called()

    @patch("app.main.save_ground_truth")
    @patch("app.main._build_feedback_governance_report")
    @patch("app.main.load_ground_truth")
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    def test_feedback_few_shot_preview_route_is_read_only(
        self,
        mock_ensure,
        mock_load_projects,
        mock_load_ground_truth,
        mock_build_governance_report,
        mock_save_ground_truth,
        client,
    ):
        store = [
            {
                "id": "gt-2",
                "project_id": "p1",
                "few_shot_distillation": {
                    "captured": 2,
                    "reason": "captured",
                    "dimension_ids": ["09"],
                    "feature_ids": ["F-1"],
                },
            }
        ]
        mock_load_projects.return_value = [{"id": "p1", "meta": {"score_scale_max": 100}}]
        mock_load_ground_truth.return_value = store

        def _build_report(project_id, project, **kwargs):
            rows = kwargs.get("ground_truth_rows_override") or []
            adopted_count = sum(
                1
                for row in rows
                if str(
                    ((row.get("few_shot_distillation") or {}).get("manual_review_status") or "")
                ).lower()
                == "adopted"
            )
            recent_rows = []
            adopted_rows = []
            for row in rows:
                distillation = row.get("few_shot_distillation") or {}
                recent_rows.append(
                    {
                        "record_id": str(row.get("id") or ""),
                        "captured": distillation.get("captured") or 0,
                        "manual_review_status": distillation.get("manual_review_status")
                        or "pending",
                    }
                )
                if str(distillation.get("manual_review_status") or "").lower() == "adopted":
                    adopted_rows.append({"record_id": str(row.get("id") or "")})
            return {
                "project_id": project_id,
                "summary": {
                    "ground_truth_count": len(rows),
                    "few_shot_recent_capture_count": len(recent_rows),
                    "few_shot_adopted_count": adopted_count,
                    "manual_confirmation_required": False,
                },
                "blocked_samples": [],
                "few_shot_recent": recent_rows,
                "approved_samples": [],
                "adopted_few_shot": adopted_rows,
                "version_history": [],
                "artifact_impacts": [],
                "score_preview": {},
                "sandbox_preview": {},
                "recommendations": [],
            }

        mock_build_governance_report.side_effect = _build_report

        response = client.post(
            "/api/v1/projects/p1/feedback/governance/few_shot/gt-2/preview",
            json={
                "action": "adopt",
                "note": "预演采纳",
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["preview_type"] == "few_shot"
        assert data["requested_action"] == "adopt"
        assert data["current_state"]["manual_review_status"] == "pending"
        assert data["preview_state"]["manual_review_status"] == "adopted"
        assert data["governance"]["summary"]["few_shot_adopted_count"] == 1
        assert any(
            "不会直接改写当前 high_score_features" in item for item in data["recommendations"]
        )
        assert store[0]["few_shot_distillation"].get("manual_review_status") is None
        mock_save_ground_truth.assert_not_called()

    @patch("app.main._run_feedback_closed_loop_safe")
    @patch("app.main.save_ground_truth")
    @patch("app.main.load_ground_truth")
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    def test_feedback_guardrail_review_route_can_approve_and_rerun_closed_loop(
        self,
        mock_ensure,
        mock_load_projects,
        mock_load_ground_truth,
        mock_save_ground_truth,
        mock_run_closed_loop,
        client,
    ):
        store = [
            {
                "id": "gt-1",
                "project_id": "p1",
                "feedback_guardrail": {
                    "blocked": True,
                    "threshold_blocked": True,
                    "actual_score_100": 88,
                    "predicted_score_100": 30,
                    "abs_delta_100": 58,
                    "relative_delta_ratio": 0.58,
                },
            }
        ]

        mock_load_projects.return_value = [{"id": "p1"}]
        mock_load_ground_truth.side_effect = lambda: copy.deepcopy(store)

        def _save(rows):
            store[:] = copy.deepcopy(rows)

        mock_save_ground_truth.side_effect = _save
        mock_run_closed_loop.return_value = {"ok": True, "guardrail_triggered": False}

        response = client.post(
            "/api/v1/projects/p1/feedback/governance/guardrail/gt-1/review",
            json={"action": "approve", "note": "人工核验后允许纳入", "rerun_closed_loop": True},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["feedback_guardrail"]["manual_review_status"] == "approved"
        assert data["feedback_guardrail"]["blocked"] is False
        assert data["feedback_guardrail"]["current_score_100"] == 30
        assert data["feedback_closed_loop"]["ok"] is True
        assert store[0]["feedback_guardrail"]["manual_review_status"] == "approved"
        assert store[0]["feedback_closed_loop"]["ok"] is True
        mock_run_closed_loop.assert_called_once()

    @patch("app.main.save_ground_truth")
    @patch("app.main.load_ground_truth")
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    def test_feedback_few_shot_review_route_marks_sample_as_adopted(
        self,
        mock_ensure,
        mock_load_projects,
        mock_load_ground_truth,
        mock_save_ground_truth,
        client,
    ):
        store = [
            {
                "id": "gt-2",
                "project_id": "p1",
                "few_shot_distillation": {
                    "captured": 2,
                    "reason": "captured",
                    "feature_ids": ["F-1"],
                },
            }
        ]
        mock_load_projects.return_value = [{"id": "p1"}]
        mock_load_ground_truth.side_effect = lambda: copy.deepcopy(store)

        def _save(rows):
            store[:] = copy.deepcopy(rows)

        mock_save_ground_truth.side_effect = _save

        response = client.post(
            "/api/v1/projects/p1/feedback/governance/few_shot/gt-2/review",
            json={"action": "adopt", "note": "纳入标准样本"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["few_shot_distillation"]["manual_review_status"] == "adopted"
        assert store[0]["few_shot_distillation"]["manual_review_status"] == "adopted"

    @patch("app.main.list_json_versions")
    @patch("app.main.ensure_data_dirs")
    def test_versioned_json_history_supports_high_score_features(
        self,
        mock_ensure,
        mock_list_versions,
        client,
    ):
        mock_list_versions.return_value = [
            {
                "version_id": "20260314T010203000000Z",
                "filename": "high_score_features_v20260314T010203000000Z.json",
                "created_at": "2026-03-14T01:02:03+00:00",
                "size_bytes": 256,
            }
        ]

        response = client.get("/api/v1/ops/versioned-json/high_score_features")

        assert response.status_code == 200
        data = response.json()
        assert data["artifact"] == "high_score_features"
        assert data["versions"][0]["version_id"] == "20260314T010203000000Z"


class TestGroundTruthScoreRuleRoutes:
    def test_calculate_ground_truth_final_score_respects_detected_formula(self):
        from app.main import _calculate_ground_truth_final_score

        judge_scores = [4.15, 4.09, 4.15, 4.15, 4.21, 3.96, 4.24]

        assert _calculate_ground_truth_final_score(
            judge_scores,
            scoring_rule={"formula": "simple_mean", "rounding_digits": 2},
        ) == pytest.approx(4.14, abs=1e-6)
        assert _calculate_ground_truth_final_score(
            judge_scores,
            scoring_rule={"formula": "trim_one_each_mean", "rounding_digits": 2},
        ) == pytest.approx(4.15, abs=1e-6)

    @patch("app.main.load_materials")
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    def test_ground_truth_scoring_rule_endpoint_detects_simple_mean_from_tender_text(
        self,
        mock_ensure,
        mock_load_projects,
        mock_load_materials,
        client,
    ):
        mock_load_projects.return_value = [{"id": "p1", "meta": {"score_scale_max": 5}}]
        mock_load_materials.return_value = [
            {
                "project_id": "p1",
                "material_type": "tender_qa",
                "filename": "招标文件正文.pdf",
                "parsed_text": (
                    "本章第2.2.2(1)目属于技术文件详细评审内容,"
                    "以评标委员会各成员打分平均值确定(计算结果保留小数点后两位)。"
                    "[PAGE:80]"
                ),
            }
        ]

        response = client.get("/api/v1/projects/p1/ground_truth/scoring_rule")

        assert response.status_code == 200
        data = response.json()
        assert data["project_id"] == "p1"
        assert data["score_scale_max"] == 5
        assert data["formula"] == "simple_mean"
        assert data["auto_compute"] is True
        assert data["source_filename"] == "招标文件正文.pdf"
        assert data["source_page_hint"] == "第80页"
        assert "打分平均值" in data["label"]

    @patch("app.main.pymupdf", None)
    @patch("app.main.PdfReader")
    def test_extract_ground_truth_score_rule_from_pdf_content_stops_when_rule_page_is_found(
        self,
        mock_pdf_reader,
    ):
        from app.main import _extract_ground_truth_score_rule_from_pdf_content

        class _FakePage:
            def __init__(self, text: str):
                self._text = text
                self.calls = 0

            def extract_text(self):
                self.calls += 1
                return self._text

        class _FakeReader:
            def __init__(self, _stream):
                self.pages = pages

        pages = [
            _FakePage("第一章 招标公告"),
            _FakePage("第二章 投标人须知"),
            _FakePage(
                "第三章 商务文件详细评审\n"
                "去除1个较高有效评标价和1个较低有效评标价，"
                "取其他有效评标价进行算术平均。"
            ),
            _FakePage("第四章 详细评审标准"),
            _FakePage(
                "3.2.2 得分计算的确定\n"
                "本章第2.2.2（1）目属于技术文件详细评审内容，"
                "以评标委员会各成员打分平均值确定。"
            ),
            _FakePage("不应继续扫描到这一页"),
        ]

        mock_pdf_reader.side_effect = _FakeReader
        candidate = _extract_ground_truth_score_rule_from_pdf_content(
            b"%PDF-fallback",
            filename="招标文件正文.pdf",
        )

        assert candidate is not None
        assert candidate["formula"] == "simple_mean"
        assert candidate["source_page_hint"] == "第5页"
        assert pages[5].calls == 0

    @patch("app.main._extract_ground_truth_score_rule_from_pdf_content")
    @patch("app.main.read_bytes")
    @patch("app.main.load_materials")
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    def test_ground_truth_scoring_rule_endpoint_falls_back_to_full_pdf_when_parsed_text_was_early_stopped(
        self,
        mock_ensure,
        mock_load_projects,
        mock_load_materials,
        mock_read_bytes,
        mock_extract_rule_from_pdf,
        client,
    ):
        mock_load_projects.return_value = [{"id": "p1", "meta": {"score_scale_max": 5}}]
        mock_read_bytes.return_value = b"%PDF-1.7"
        mock_extract_rule_from_pdf.return_value = {
            "formula": "simple_mean",
            "label": "按招标文件：评标委员会各成员打分平均值",
            "rounding_digits": 2,
            "drop_highest_count": 0,
            "drop_lowest_count": 0,
            "detected": True,
            "confidence": 100,
            "source_filename": "招标文件正文.pdf",
            "source_page_hint": "第75页",
            "source_excerpt": "以评标委员会各成员打分平均值确定。",
        }
        with tempfile.NamedTemporaryFile(suffix=".pdf") as temp_pdf:
            mock_load_materials.return_value = [
                {
                    "project_id": "p1",
                    "material_type": "tender_qa",
                    "filename": "招标文件正文.pdf",
                    "path": temp_pdf.name,
                    "parsed_text": (
                        "[PAGE:1]\n前文摘要\n"
                        "[PDF_EARLY_STOP_AFTER_PAGE:16] tender_qa_enough_signals"
                    ),
                }
            ]
            response = client.get("/api/v1/projects/p1/ground_truth/scoring_rule")

        assert response.status_code == 200
        data = response.json()
        assert data["formula"] == "simple_mean"
        assert data["auto_compute"] is True
        assert data["source_filename"] == "招标文件正文.pdf"
        assert data["source_page_hint"] == "第75页"
        mock_extract_rule_from_pdf.assert_called_once_with(
            b"%PDF-1.7",
            filename="招标文件正文.pdf",
        )

    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    @patch("app.main._collect_blocked_ground_truth_guardrails")
    def test_evolve_requires_manual_confirm_when_guardrail_blocked(
        self,
        mock_collect_blocked,
        mock_ensure,
        mock_load_projects,
        client,
    ):
        mock_load_projects.return_value = [{"id": "p1", "meta": {"score_scale_max": 100}}]
        mock_collect_blocked.return_value = [
            {"record_id": "gt-1", "feedback_guardrail": {"blocked": True, "abs_delta_100": 42.0}}
        ]

        response = client.post("/api/v1/projects/p1/evolve")

        assert response.status_code == 409
        assert "confirm_extreme_sample=1" in response.json()["detail"]

    @patch("app.main._build_feedback_governance_report")
    @patch("app.main.save_evolution_reports")
    @patch("app.main.load_evolution_reports")
    @patch("app.main.enhance_evolution_report_with_llm")
    @patch("app.main.build_evolution_report")
    @patch("app.main._merge_materials_text")
    @patch("app.main.load_project_context")
    @patch("app.main.load_ground_truth")
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    def test_evolve_uses_learning_quality_blocked_ground_truth_samples_for_report(
        self,
        mock_ensure,
        mock_load_projects,
        mock_load_ground_truth,
        mock_load_project_context,
        mock_merge_materials_text,
        mock_build_evolution_report,
        mock_enhance_evolution_report,
        mock_load_evolution_reports,
        mock_save_evolution_reports,
        mock_build_governance,
        client,
    ):
        mock_load_projects.return_value = [{"id": "p1", "meta": {"score_scale_max": 5}}]
        mock_load_ground_truth.return_value = [
            {
                "id": "gt-1",
                "project_id": "p1",
                "shigong_text": "示例施组文本",
                "judge_scores": [4.27, 4.25, 4.27, 4.27, 4.41, 4.27, 4.46],
                "final_score": 4.31,
                "score_scale_max": 5,
                "learning_quality_gate": {
                    "blocked": True,
                    "reasons": ["material_gate_blocked", "low_score_self_awareness"],
                },
            }
        ]
        mock_load_project_context.return_value = {}
        mock_merge_materials_text.return_value = ""
        mock_load_evolution_reports.return_value = {}
        mock_enhance_evolution_report.return_value = None
        mock_build_governance.return_value = {
            "summary": {
                "manual_confirmation_required": False,
                "pending_extreme_ground_truth_count": 0,
                "blocked_ground_truth_count": 0,
                "approved_extreme_ground_truth_count": 0,
                "manual_override_hint": None,
                "current_calibrator_deployment_mode": "prior_fallback",
            },
            "recommendations": ["当前无人工确认阻塞。"],
        }

        def _build(project_id, records, project_context):
            assert project_id == "p1"
            assert project_context == ""
            assert len(records) == 1
            assert records[0]["final_score"] == pytest.approx(86.2, abs=1e-6)
            return {
                "project_id": "p1",
                "high_score_logic": ["逻辑A"],
                "writing_guidance": ["建议A"],
                "sample_count": len(records),
                "updated_at": "2026-03-25T06:35:00+00:00",
                "scoring_evolution": {},
                "compilation_instructions": {},
            }

        mock_build_evolution_report.side_effect = _build

        response = client.post("/api/v1/projects/p1/evolve")

        assert response.status_code == 200
        data = response.json()
        assert data["sample_count"] == 1
        assert data["high_score_logic"] == ["逻辑A"]
        assert data["manual_confirmation_audit"]["status"] == "clear"
        assert data["manual_confirmation_audit"]["override_used"] is False
        assert data["manual_confirmation_audit"]["before_pending_extreme_ground_truth_count"] == 0
        assert data["manual_confirmation_audit"]["delta_pending_extreme_ground_truth_count"] == 0
        mock_save_evolution_reports.assert_called_once()

    @patch("app.main._build_feedback_governance_report")
    @patch("app.main.save_evolution_reports")
    @patch("app.main.load_evolution_reports")
    @patch("app.main.enhance_evolution_report_with_llm")
    @patch("app.main.build_evolution_report")
    @patch("app.main._merge_materials_text")
    @patch("app.main.load_project_context")
    @patch("app.main._collect_blocked_ground_truth_guardrails")
    @patch("app.main.load_ground_truth")
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    def test_evolve_confirm_extreme_sample_returns_manual_confirmation_audit(
        self,
        mock_ensure,
        mock_load_projects,
        mock_load_ground_truth,
        mock_collect_blocked,
        mock_load_project_context,
        mock_merge_materials_text,
        mock_build_evolution_report,
        mock_enhance_evolution_report,
        mock_load_evolution_reports,
        mock_save_evolution_reports,
        mock_build_governance,
        client,
    ):
        mock_load_projects.return_value = [{"id": "p1", "meta": {"score_scale_max": 5}}]
        mock_collect_blocked.return_value = [{"record_id": "gt-1"}]
        mock_load_ground_truth.return_value = [
            {
                "id": "gt-1",
                "project_id": "p1",
                "judge_scores": [4.2, 4.2, 4.2, 4.2, 4.2],
                "final_score": 4.2,
                "score_scale_max": 5,
            }
        ]
        mock_load_project_context.return_value = {}
        mock_merge_materials_text.return_value = ""
        mock_load_evolution_reports.return_value = {}
        mock_enhance_evolution_report.return_value = None
        mock_build_governance.return_value = {
            "summary": {
                "manual_confirmation_required": False,
                "pending_extreme_ground_truth_count": 0,
                "blocked_ground_truth_count": 0,
                "approved_extreme_ground_truth_count": 1,
                "manual_override_hint": "confirm_extreme_sample=1",
                "current_calibrator_deployment_mode": "prior_fallback",
            },
            "recommendations": ["人工确认后可继续自动学习。"],
        }
        mock_build_evolution_report.return_value = {
            "project_id": "p1",
            "high_score_logic": ["逻辑A"],
            "writing_guidance": ["建议A"],
            "sample_count": 1,
            "updated_at": "2026-03-25T06:35:00+00:00",
            "scoring_evolution": {},
            "compilation_instructions": {},
        }

        response = client.post("/api/v1/projects/p1/evolve?confirm_extreme_sample=1")

        assert response.status_code == 200
        data = response.json()
        assert data["manual_confirmation_audit"]["status"] == "cleared"
        assert data["manual_confirmation_audit"]["override_used"] is True
        assert data["manual_confirmation_audit"]["before_pending_extreme_ground_truth_count"] == 1
        assert data["manual_confirmation_audit"]["before_blocked_ground_truth_count"] == 1
        assert data["manual_confirmation_audit"]["approved_extreme_ground_truth_count"] == 1
        assert data["manual_confirmation_audit"]["delta_pending_extreme_ground_truth_count"] == -1
        assert data["manual_confirmation_audit"]["delta_blocked_ground_truth_count"] == -1
        assert data["manual_confirmation_audit"]["delta_approved_extreme_ground_truth_count"] == 1
        assert data["manual_confirmation_audit"]["gate_cleared_after_reverify"] is True
        assert "复验前待人工审核 1 条" in data["manual_confirmation_audit"]["reverify_summary"]
        assert (
            data["manual_confirmation_audit"]["manual_override_hint"] == "confirm_extreme_sample=1"
        )

    @patch("app.main._build_pending_feedback_scoring_points")
    @patch("app.main.load_evolution_reports")
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    def test_get_writing_guidance_includes_enhancement_review_fields(
        self,
        mock_ensure,
        mock_load_projects,
        mock_load_evolution_reports,
        mock_build_pending_feedback,
        client,
    ):
        mock_load_projects.return_value = [{"id": "p1", "name": "项目1", "meta": {}}]
        mock_load_evolution_reports.return_value = {
            "p1": {
                "high_score_logic": ["逻辑A"],
                "writing_guidance": ["建议A"],
                "sample_count": 2,
                "updated_at": "2026-03-29T00:00:00+00:00",
                "enhancement_applied": False,
                "enhancement_governed": True,
                "enhancement_governance_notes": ["已回退到规则版建议"],
                "enhancement_review_provider": "gemini",
                "enhancement_review_status": "diverged",
                "enhancement_review_similarity": 0.21,
                "enhancement_review_notes": ["复核差异较大"],
            }
        }
        mock_build_pending_feedback.return_value = {
            "summary": {"pending_sample_count": 1, "pending_point_count": 2},
            "patch_bundle": {"section_count": 1, "sections": [{"copy_block": "补丁A"}]},
        }

        response = client.get("/api/v1/projects/p1/writing_guidance")

        assert response.status_code == 200
        data = response.json()
        assert data["enhancement_applied"] is False
        assert data["enhancement_governed"] is True
        assert data["enhancement_review_provider"] == "gemini"
        assert data["enhancement_review_status"] == "diverged"
        assert data["enhancement_review_similarity"] == pytest.approx(0.21, abs=1e-6)
        assert "已回退到规则版建议" in data["enhancement_governance_notes"][0]
        assert data["pending_feedback_summary"]["pending_sample_count"] == 1
        assert data["pending_feedback_patch_bundle"]["section_count"] == 1

    @patch("app.main.get_writing_guidance")
    def test_get_project_writing_guidance_markdown_and_download(
        self,
        mock_get_writing_guidance,
        client,
    ):
        from app.schemas import WritingGuidance

        mock_get_writing_guidance.return_value = WritingGuidance(
            project_id="p1",
            guidance=["建议A"],
            high_score_logic=["逻辑A"],
            pending_feedback_summary={"pending_sample_count": 2, "pending_point_count": 3},
            pending_feedback_patch_bundle={
                "section_count": 1,
                "sections": [
                    {
                        "section_title": "05 四新技术应用",
                        "operation_label": "在当前“盘扣式脚手架”相关段后插入该小节",
                        "target": "盘扣式脚手架",
                        "section_paragraphs": ["正文A", "正文B"],
                        "copy_block": "### 05 四新技术应用\n\n写入方式：在当前“盘扣式脚手架”相关段后插入该小节",
                    }
                ],
                "copy_markdown": "# 待确认真实评标改写补丁包\n\n## 1. 05 四新技术应用\n",
            },
            sample_count=1,
            updated_at="2026-04-02T10:00:00+08:00",
            enhancement_applied=True,
        )

        md_resp = client.get("/api/v1/projects/p1/writing_guidance/markdown")
        assert md_resp.status_code == 200
        md_data = md_resp.json()
        assert md_data["project_id"] == "p1"
        assert "## 编制指导" in md_data["markdown"]
        assert "## 待确认反馈改写补丁包" in md_data["markdown"]
        assert "待人工确认样本：`2`" in md_data["markdown"]

        file_resp = client.get("/api/v1/projects/p1/writing_guidance.md")
        assert file_resp.status_code == 200
        assert "text/markdown" in file_resp.headers.get("content-type", "")
        disposition = file_resp.headers.get("content-disposition", "")
        assert "attachment; filename=" in disposition
        assert "writing_guidance_p1.md" in disposition
        assert "待确认反馈改写补丁包" in file_resp.text

        patch_md_resp = client.get("/api/v1/projects/p1/writing_guidance_patch_bundle/markdown")
        assert patch_md_resp.status_code == 200
        patch_md_data = patch_md_resp.json()
        assert patch_md_data["project_id"] == "p1"
        assert "# 待确认反馈改写补丁包" in patch_md_data["markdown"]
        assert "## 项目级补丁正文" in patch_md_data["markdown"]
        assert "待人工确认样本：`2`" in patch_md_data["markdown"]

        patch_file_resp = client.get("/api/v1/projects/p1/writing_guidance_patch_bundle.md")
        assert patch_file_resp.status_code == 200
        assert "text/markdown" in patch_file_resp.headers.get("content-type", "")
        patch_disposition = patch_file_resp.headers.get("content-disposition", "")
        assert "attachment; filename=" in patch_disposition
        assert "writing_guidance_patch_bundle_p1.md" in patch_disposition
        assert "待确认反馈改写补丁包" in patch_file_resp.text

        patch_docx_resp = client.get("/api/v1/projects/p1/writing_guidance_patch_bundle.docx")
        assert patch_docx_resp.status_code == 200
        assert patch_docx_resp.content.startswith(b"PK")
        assert (
            patch_docx_resp.headers.get("content-type", "")
            == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        )
        patch_docx_disposition = patch_docx_resp.headers.get("content-disposition", "")
        assert "attachment; filename=" in patch_docx_disposition
        assert "writing_guidance_patch_bundle_p1.docx" in patch_docx_disposition
        if app_main.Document is not None:
            doc = app_main.Document(BytesIO(patch_docx_resp.content))
            doc_text = "\n".join(p.text for p in doc.paragraphs if p.text)
            assert "待确认反馈改写补丁包" in doc_text
            assert "项目级补丁正文" in doc_text
            assert "05 四新技术应用" in doc_text
            assert "写入方式：" in doc_text


class TestDynamicBlendAdjustment:
    def test_resolve_dynamic_blend_adjustment_no_signal_keeps_weights(self):
        from app.main import _resolve_dynamic_blend_adjustment

        scale, delta_scale, meta = _resolve_dynamic_blend_adjustment({"meta": {}})
        assert scale == pytest.approx(1.0, abs=1e-6)
        assert delta_scale == pytest.approx(1.0, abs=1e-6)
        assert meta.get("signal_state") == "no_material_signal"
        assert meta.get("reasons") == []

    def test_resolve_dynamic_blend_adjustment_low_coverage_downweights_llm(self):
        from app.main import _resolve_dynamic_blend_adjustment

        report = {
            "meta": {
                "material_utilization": {
                    "retrieval_file_coverage_rate": 0.2,
                    "retrieval_hit_rate": 0.1,
                },
                "material_utilization_gate": {"warned": True},
                "evidence_trace": {"mandatory_hit_rate": 0.2, "source_files_hit_count": 0},
            }
        }
        scale, delta_scale, meta = _resolve_dynamic_blend_adjustment(report)
        assert 0.0 < scale < 1.0
        assert 0.0 < delta_scale <= 1.0
        assert meta.get("signal_state") == "material_signal_detected"
        assert any("material_gate_warned" == reason for reason in (meta.get("reasons") or []))


class TestStructuredMaterialAdvancedParsing:
    def test_aggregate_tender_page_structures_builds_outline_and_table_constraints(self):
        from app.main import _aggregate_tender_page_structures

        payload = _aggregate_tender_page_structures(
            [
                {
                    "page_no": 1,
                    "page_type": "toc",
                    "section_title": "目录",
                    "section_level": 0,
                    "section_path": ["目录"],
                    "scoring_terms": [],
                    "mandatory_clauses": [],
                    "numeric_constraints": [],
                    "table_rows": [],
                    "focused_dimensions": ["01"],
                    "parse_confidence": 0.62,
                },
                {
                    "page_no": 2,
                    "page_type": "scoring_rules",
                    "section_title": "评分办法",
                    "section_level": 1,
                    "section_path": ["第一章", "评分办法"],
                    "scoring_terms": ["评分办法", "关键节点"],
                    "mandatory_clauses": ["必须满足节点工期"],
                    "numeric_constraints": ["90天"],
                    "table_rows": [{"label": "工期", "value": "90天", "numbers": ["90天"]}],
                    "focused_dimensions": ["09", "16"],
                    "parse_confidence": 0.84,
                },
            ]
        )

        assert payload["scoring_rule_pages"] == [2]
        assert payload["page_type_summary"][0]["page_type"] in {"scoring_rules", "toc"}
        assert "第一章" in payload["section_title_paths"]
        assert payload["table_constraint_rows"][0]["label"] == "工期"
        assert "90" in payload["table_numeric_constraints"]

    def test_build_drawing_structured_summary_extracts_dwg_binary_markers(self):
        from app.main import _build_drawing_structured_summary

        content = (
            b"AC1032 DWG PUMP_ROOM GRID_A1 DN200 SECTION-01 HVAC_PIPE FIRE_ALARM LEVEL_1 PLAN_A"
        )
        summary = _build_drawing_structured_summary(content, "sample.dwg", parsed_text="")
        assert summary["detected_format"] == "dwg"
        assert summary["binary_marker_terms"]
        assert summary["structured_quality_score"] > 0

    def test_build_site_photo_evidence_summary_deduplicates_objects(self):
        from app.main import _build_site_photo_evidence_summary

        payload = _build_site_photo_evidence_summary(
            [
                {
                    "scene_type": "安全巡查",
                    "risk_level": "high",
                    "safety_findings": ["临边防护缺失"],
                    "quality_findings": ["钢筋外露"],
                    "visible_objects": ["脚手架", "临边洞口"],
                    "numeric_markers": ["2处"],
                    "evidence_confidence": 0.72,
                },
                {
                    "scene_type": "安全巡查",
                    "risk_level": "high",
                    "safety_findings": ["临边防护缺失"],
                    "quality_findings": ["钢筋外露"],
                    "visible_objects": ["脚手架", "临边洞口"],
                    "numeric_markers": ["2处"],
                    "evidence_confidence": 0.7,
                },
                {
                    "scene_type": "进度实景",
                    "risk_level": "medium",
                    "progress_findings": ["主体施工"],
                    "visible_objects": ["塔吊", "模板"],
                    "numeric_markers": ["3层"],
                    "evidence_confidence": 0.65,
                },
            ]
        )

        assert payload["photo_count"] == 3
        assert payload["unique_evidence_count"] == 2
        assert payload["duplicate_evidence_count"] == 1
        assert payload["avg_evidence_confidence"] > 0.6

    def test_build_material_score_shaping_rewards_consensus_and_feedback(self):
        from app.main import _build_material_score_shaping

        report = {
            "rule_total_score": 78.0,
            "meta": {
                "evidence_trace": {"mandatory_hit_rate": 0.82},
            },
        }
        material_knowledge_snapshot = {
            "summary": {
                "cross_type_consensus_score": 0.48,
                "cross_type_consensus_type_count": 3,
                "structured_quality_avg": 0.63,
                "structured_quality_type_rate": 0.67,
                "strong_structured_types": 3,
            },
            "by_type": [
                {"mandatory_clause_terms_preview": ["必须提供节点工期", "不得缺少深化设计"]},
                {"mandatory_clause_terms_preview": ["必须满足质量验收"]},
            ],
        }
        shaping = _build_material_score_shaping(
            report,
            runtime_req_meta={
                "site_photo_visual_requirements": 1,
                "feedback_evolution_requirements": 3,
                "feature_confidence_requirements": 2,
            },
            material_knowledge_snapshot=material_knowledge_snapshot,
        )
        assert shaping["net_delta"] > 0
        assert "cross_material_consensus" in shaping["reasons"]

    def test_build_material_score_shaping_penalizes_weak_material_signals(self):
        from app.main import _build_material_score_shaping

        report = {
            "rule_total_score": 78.0,
            "meta": {
                "evidence_trace": {"mandatory_hit_rate": 0.22},
            },
        }
        material_knowledge_snapshot = {
            "summary": {
                "cross_type_consensus_score": 0.08,
                "cross_type_consensus_type_count": 1,
                "structured_quality_avg": 0.16,
                "structured_quality_type_rate": 0.12,
                "strong_structured_types": 1,
            },
            "by_type": [
                {
                    "mandatory_clause_terms_preview": [
                        "必须满足节点工期",
                        "不得缺少关键方案",
                        "须提供验收表",
                    ]
                },
            ],
        }
        shaping = _build_material_score_shaping(
            report,
            runtime_req_meta={"site_photo_visual_requirements": 0},
            material_knowledge_snapshot=material_knowledge_snapshot,
        )
        assert shaping["net_delta"] < 0
        assert "mandatory_clause_underhit" in shaping["reasons"]

    def test_build_material_score_shaping_stays_neutral_without_evidence_context(self):
        from app.main import _build_material_score_shaping

        report = {
            "rule_total_score": 81.2,
            "meta": {
                "evidence_trace": {"mandatory_hit_rate": 0.9},
            },
        }
        shaping = _build_material_score_shaping(
            report,
            runtime_req_meta={"runtime_custom_requirements": 1},
            material_knowledge_snapshot={
                "summary": {
                    "total_files": 1,
                    "parsed_ok_files": 0,
                    "structured_signal_total": 0,
                    "dimension_coverage_rate": 0.0,
                },
                "by_type": [],
            },
        )
        assert shaping["enabled"] is False
        assert shaping["net_delta"] == 0.0
        assert "insufficient_material_evidence_context" in shaping["reasons"]
