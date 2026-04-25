from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional

from fastapi import HTTPException, UploadFile
from starlette.concurrency import run_in_threadpool

from app.application.runtime_facade import RuntimeModuleFacade
from app.application.storage_access import StorageAccess
from app.application.task_runtime import emit_task_state, tracked_task
from app.bootstrap.storage import get_storage_access
from app.domain.material_parse_diagnostics import (
    build_material_parse_business_overview,
    build_material_parse_debug_info,
    build_material_parse_project_cache_request_delta,
)
from app.domain.material_parse_state import (
    normalize_material_parse_job,
    normalize_material_row_for_parse,
)
from app.domain.material_types import (
    is_allowed_material_upload,
    material_type_ext_hint,
    material_type_label,
    normalize_material_type,
    normalize_uploaded_filename,
    parse_material_type_or_422,
)
from app.domain.projects import (
    find_project_by_name,
    normalize_bid_method_input_or_422,
    normalize_project_name_key,
    normalize_project_type_input_or_422,
    project_exists,
)

logger = logging.getLogger(__name__)


def _runtime(storage: StorageAccess | None = None):
    from app.application import runtime as runtime_module

    return RuntimeModuleFacade(runtime_module, storage=storage)


def _cli_runtime():
    from app.interfaces.cli import runtime as cli_runtime

    return cli_runtime


def _event_key(prefix: str, *parts: object) -> str:
    clean_parts = [str(part).strip() for part in parts if str(part).strip()]
    return ":".join([prefix, *clean_parts])


def _run_sync_task(
    *,
    task_kind: str,
    task_name: str,
    project_id: str | None,
    fn: Callable[[], Any],
) -> Any:
    with tracked_task(
        logger,
        task_kind=task_kind,  # type: ignore[arg-type]
        task_name=task_name,
        project_id=project_id,
    ):
        return fn()


async def _run_async_task(
    *,
    task_kind: str,
    task_name: str,
    project_id: str | None,
    fn: Callable[[], Awaitable[Any]],
) -> Any:
    with tracked_task(
        logger,
        task_kind=task_kind,  # type: ignore[arg-type]
        task_name=task_name,
        project_id=project_id,
    ):
        return await fn()


@dataclass(frozen=True)
class CliScoreExecution:
    output: str
    report_json: Dict[str, Any]
    summary_text: Optional[str] = None
    summary_path: Optional[str] = None
    docx_path: Optional[str] = None


class _StorageAwareService:
    def __init__(self, *, storage: StorageAccess | None = None):
        self.storage = storage or get_storage_access()

    def _runtime(self):
        return _runtime(self.storage)


class ProjectApplicationService(_StorageAwareService):
    def create_project(self, payload: Any) -> Any:
        def _execute() -> Any:
            legacy = self._runtime()
            legacy.ensure_data_dirs()
            projects = legacy.load_projects()
            changed = False
            for project in projects:
                if isinstance(project, dict):
                    changed = legacy._ensure_project_v2_fields(project) or changed
            if changed:
                legacy.save_projects(projects)
            clean_name = normalize_project_name_key(payload.name)
            if not clean_name:
                raise HTTPException(status_code=422, detail="项目名称不能为空")
            if find_project_by_name(projects, clean_name) is not None:
                raise HTTPException(status_code=422, detail="项目名称已存在，请更换名称")
            normalized_project_type = normalize_project_type_input_or_422(payload.project_type)
            normalized_bid_method = normalize_bid_method_input_or_422(payload.bid_method)
            record = legacy._build_project_record(
                clean_name,
                payload.meta or {},
                project_type=normalized_project_type,
                bid_method=normalized_bid_method,
            )
            projects.append(record)
            legacy.save_projects(projects)
            self.storage.append_domain_event(
                event_type="ProjectCreated",
                aggregate_type="project",
                aggregate_id=str(record.get("id") or ""),
                payload={
                    "project_id": str(record.get("id") or ""),
                    "name": clean_name,
                    "project_type": normalized_project_type,
                    "bid_method": normalized_bid_method,
                },
                idempotency_key=_event_key("project-created", record.get("id")),
            )
            logger.info("project_created project_id=%s", record.get("id"))
            return legacy.ProjectRecord(**legacy._normalize_project_record(record)[0])

        return _run_sync_task(
            task_kind="ops",
            task_name="create_project",
            project_id=None,
            fn=_execute,
        )

    async def infer_project_name_from_tender(self, file: UploadFile) -> Any:
        legacy = self._runtime()
        legacy.ensure_data_dirs()
        raw_filename = file.filename or ""
        normalized_filename = normalize_uploaded_filename(raw_filename)
        if not normalized_filename:
            raise HTTPException(status_code=422, detail="招标文件名为空，请重试或重命名后上传。")
        if not is_allowed_material_upload(
            normalized_filename,
            file.content_type or "",
            "tender_qa",
        ):
            raise HTTPException(
                status_code=422,
                detail="招标文件和答疑支持：" + material_type_ext_hint("tender_qa"),
            )
        staged = await legacy._stage_upload_file_to_temp_path(file, filename=normalized_filename)
        try:
            if int(legacy._to_float_or_none(staged.get("size_bytes")) or 0) <= 0:
                raise HTTPException(status_code=422, detail="招标文件为空，请重新选择文件。")
            _, inferred_name = await run_in_threadpool(
                legacy._read_tender_upload_and_infer_project_name_from_path,
                staged["path"],
                filename=normalized_filename,
                project_name_override="",
            )
            return legacy.ProjectInferTenderNameResponse(
                inferred_name=inferred_name,
                filename=normalized_filename,
            )
        finally:
            legacy._remove_temp_file(staged.get("path"))

    async def create_project_from_tender(
        self,
        *,
        file: UploadFile,
        project_name_override: Optional[str],
        locale: str,
    ) -> Any:
        async def _execute() -> Any:
            legacy = self._runtime()
            legacy.ensure_data_dirs()
            raw_filename = file.filename or ""
            normalized_filename = normalize_uploaded_filename(raw_filename)
            if not normalized_filename:
                raise HTTPException(
                    status_code=422, detail="招标文件名为空，请重试或重命名后上传。"
                )
            if not is_allowed_material_upload(
                normalized_filename,
                file.content_type or "",
                "tender_qa",
            ):
                raise HTTPException(
                    status_code=422,
                    detail="招标文件和答疑支持：" + material_type_ext_hint("tender_qa"),
                )
            staged = await legacy._stage_upload_file_to_temp_path(
                file, filename=normalized_filename
            )
            try:
                if int(legacy._to_float_or_none(staged.get("size_bytes")) or 0) <= 0:
                    raise HTTPException(status_code=422, detail="招标文件为空，请重新选择文件。")
                _, inferred_name = await run_in_threadpool(
                    legacy._read_tender_upload_and_infer_project_name_from_path,
                    staged["path"],
                    filename=normalized_filename,
                    project_name_override=project_name_override or "",
                )

                projects = legacy.load_projects()
                changed = False
                for project in projects:
                    if isinstance(project, dict):
                        changed = legacy._ensure_project_v2_fields(project) or changed
                if changed:
                    legacy.save_projects(projects)

                existing = find_project_by_name(projects, inferred_name)
                created = False
                if existing is None:
                    project_row = legacy._build_project_record(inferred_name, {})
                    projects.append(project_row)
                    legacy.save_projects(projects)
                    created = True
                    self.storage.append_domain_event(
                        event_type="ProjectCreated",
                        aggregate_type="project",
                        aggregate_id=str(project_row.get("id") or ""),
                        payload={
                            "project_id": str(project_row.get("id") or ""),
                            "name": inferred_name,
                            "source": "tender_upload",
                        },
                        idempotency_key=_event_key("project-created", project_row.get("id")),
                    )
                else:
                    project_row = existing

                upload_result = await legacy._store_uploaded_material_from_local_path(
                    project_id=str(project_row.get("id") or ""),
                    source_path=Path(staged["path"]),
                    normalized_name=normalized_filename,
                    normalized_material_type="tender_qa",
                    locale=locale,
                )
                material_row = dict(upload_result.get("material") or {})
                self.storage.append_domain_event(
                    event_type="ArtifactUploaded",
                    aggregate_type="project",
                    aggregate_id=str(project_row.get("id") or ""),
                    payload={
                        "project_id": str(project_row.get("id") or ""),
                        "artifact_id": str(material_row.get("id") or ""),
                        "artifact_type": "tender_qa",
                        "filename": normalized_filename,
                        "path": str(material_row.get("path") or ""),
                    },
                    idempotency_key=_event_key("artifact-uploaded", material_row.get("id")),
                )
                return legacy.ProjectCreateFromTenderResponse(
                    project=legacy.ProjectRecord(
                        **legacy._normalize_project_record(project_row)[0]
                    ),
                    material=legacy.MaterialRecord(
                        **normalize_material_row_for_parse(
                            material_row,
                            parse_version=legacy.DEFAULT_MATERIAL_PARSE_VERSION,
                            now_iso=legacy._now_iso(),
                        )[0]
                    ),
                    inferred_name=inferred_name,
                    created=created,
                    reused_existing=not created,
                )
            finally:
                legacy._remove_temp_file(staged.get("path"))

        return await _run_async_task(
            task_kind="ops",
            task_name="create_project_from_tender",
            project_id=None,
            fn=_execute,
        )


class MaterialApplicationService(_StorageAwareService):
    async def upload_material(
        self,
        *,
        project_id: str,
        file: UploadFile,
        material_type: str,
        locale: str,
    ) -> Dict[str, Any]:
        async def _execute() -> Dict[str, Any]:
            legacy = self._runtime()
            legacy.ensure_data_dirs()
            raw_name = file.filename or ""
            normalized_name = normalize_uploaded_filename(raw_name)
            normalized_material_type = parse_material_type_or_422(
                material_type,
                filename=normalized_name,
            )
            if not normalized_name:
                raise HTTPException(
                    status_code=422, detail="资料文件名为空，请重试或重命名后上传。"
                )
            if not is_allowed_material_upload(
                normalized_name,
                file.content_type or "",
                normalized_material_type,
            ):
                raise HTTPException(
                    status_code=422,
                    detail=(
                        material_type_label(normalized_material_type)
                        + "支持："
                        + material_type_ext_hint(normalized_material_type)
                    ),
                )
            staged = await legacy._stage_upload_file_to_temp_path(file, filename=normalized_name)
            try:
                if int(legacy._to_float_or_none(staged.get("size_bytes")) or 0) <= 0:
                    raise HTTPException(status_code=422, detail="资料文件为空，请重新选择文件。")
                result = await legacy._store_uploaded_material_from_local_path(
                    project_id=project_id,
                    source_path=Path(staged["path"]),
                    normalized_name=normalized_name,
                    normalized_material_type=normalized_material_type,
                    locale=locale,
                )
                material_row = dict(result.get("material") or {})
                self.storage.append_domain_event(
                    event_type="ArtifactUploaded",
                    aggregate_type="project",
                    aggregate_id=project_id,
                    payload={
                        "project_id": project_id,
                        "artifact_id": str(material_row.get("id") or ""),
                        "artifact_type": normalized_material_type,
                        "filename": normalized_name,
                        "path": str(material_row.get("path") or ""),
                    },
                    idempotency_key=_event_key("artifact-uploaded", material_row.get("id")),
                )
                return result
            finally:
                legacy._remove_temp_file(staged.get("path"))

        return await _run_async_task(
            task_kind="ops",
            task_name="upload_material",
            project_id=project_id,
            fn=_execute,
        )

    def list_materials(self, *, project_id: str, locale: str) -> List[Any]:
        legacy = self._runtime()
        legacy.ensure_data_dirs()
        projects = legacy.load_projects()
        if not project_exists(project_id, projects):
            raise HTTPException(
                status_code=404, detail=legacy.t("api.project_not_found", locale=locale)
            )
        materials = [m for m in legacy._load_materials_safe() if m.get("project_id") == project_id]
        materials.sort(key=lambda row: str(row.get("created_at", "")), reverse=True)
        normalized_rows: List[dict] = []
        for material in materials:
            row, _ = normalize_material_row_for_parse(
                dict(material),
                parse_version=legacy.DEFAULT_MATERIAL_PARSE_VERSION,
                now_iso=legacy._now_iso(),
            )
            row["material_type"] = normalize_material_type(
                row.get("material_type"),
                filename=row.get("filename"),
            )
            normalized_rows.append(row)
        return [legacy.MaterialRecord(**row) for row in normalized_rows]

    def get_material_parse_status(self, *, project_id: str, locale: str) -> Any:
        legacy = self._runtime()
        legacy.ensure_data_dirs()
        projects = legacy.load_projects()
        if not project_exists(project_id, projects):
            raise HTTPException(
                status_code=404, detail=legacy.t("api.project_not_found", locale=locale)
            )
        project_cache_stats_before = legacy._material_parse_project_cache_stats_snapshot(project_id)
        core_payload = legacy._load_material_parse_status_core_payload_safe(project_id)
        project_cache_request_delta = build_material_parse_project_cache_request_delta(
            project_cache_stats_before,
            legacy._material_parse_project_cache_stats_snapshot(project_id),
            layer_work_units=legacy._MATERIAL_PARSE_PROJECT_CACHE_LAYER_WORK_UNITS,
        )
        legacy._record_material_parse_project_cache_request_delta(
            project_id,
            project_cache_request_delta,
        )
        jobs = list(core_payload.get("jobs") or [])
        enriched_materials = list(core_payload.get("materials") or [])
        summary = dict(core_payload.get("summary") or {})
        summary.update(
            legacy._build_material_parse_scheduler_summary(
                project_id,
                request_project_cache_delta=project_cache_request_delta,
            )
        )
        summary["worker_count"] = legacy._material_parse_total_worker_count()
        summary["preview_express_reserved_worker_count"] = int(
            max(0, int(legacy.DEFAULT_MATERIAL_PARSE_PREVIEW_EXPRESS_RESERVED_WORKER_COUNT))
        )
        summary["preview_reserved_worker_count"] = int(
            max(0, int(legacy.DEFAULT_MATERIAL_PARSE_PREVIEW_RESERVED_WORKER_COUNT))
        )
        summary["alive_worker_count"] = legacy._material_parse_worker_alive_count()
        overview = build_material_parse_business_overview(summary)
        debug_info = build_material_parse_debug_info(summary)
        return legacy.MaterialParseStatusResponse(
            project_id=project_id,
            overview=overview,
            summary=summary,
            debug_info=debug_info,
            jobs=[
                legacy.MaterialParseJobRecord(
                    **normalize_material_parse_job(dict(job), now_iso=legacy._now_iso())[0]
                )
                for job in jobs
            ],
            materials=[
                legacy.MaterialRecord(
                    **normalize_material_row_for_parse(
                        dict(row),
                        parse_version=legacy.DEFAULT_MATERIAL_PARSE_VERSION,
                        now_iso=legacy._now_iso(),
                    )[0]
                )
                for row in enriched_materials
            ],
            generated_at=legacy._now_iso(),
        )

    def get_materials_health(self, *, project_id: str, locale: str) -> Dict[str, Any]:
        legacy = self._runtime()
        legacy.ensure_data_dirs()
        projects = legacy.load_projects()
        try:
            project = legacy._find_project(project_id, projects)
        except HTTPException:
            raise HTTPException(
                status_code=404, detail=legacy.t("api.project_not_found", locale=locale)
            )
        snapshot, _ = legacy._validate_material_gate_for_scoring(
            project_id,
            project,
            raise_on_fail=False,
        )
        return snapshot

    def get_scoring_readiness(self, *, project_id: str, locale: str) -> Any:
        legacy = self._runtime()
        legacy.ensure_data_dirs()
        projects = legacy.load_projects()
        try:
            project = legacy._find_project(project_id, projects)
        except HTTPException:
            raise HTTPException(
                status_code=404, detail=legacy.t("api.project_not_found", locale=locale)
            )
        payload = legacy._build_scoring_readiness(project_id, project)
        return legacy.ScoringReadinessResponse(**payload)

    def get_project_trial_preflight(self, *, project_id: str, locale: str) -> Any:
        legacy = self._runtime()
        legacy.ensure_data_dirs()
        projects = legacy.load_projects()
        try:
            project = legacy._find_project(project_id, projects)
        except HTTPException:
            raise HTTPException(
                status_code=404, detail=legacy.t("api.project_not_found", locale=locale)
            )
        payload = legacy._build_project_trial_preflight(project_id, project)
        return legacy.ProjectTrialPreflightResponse(**payload)


class ScoringApplicationService(_StorageAwareService):
    async def upload_submission(
        self,
        *,
        project_id: str,
        file: UploadFile,
        locale: str,
    ) -> Any:
        async def _execute() -> Any:
            legacy = self._runtime()
            legacy.ensure_data_dirs()
            projects = legacy.load_projects()
            if not project_exists(project_id, projects):
                raise HTTPException(
                    status_code=404, detail=legacy.t("api.project_not_found", locale=locale)
                )
            raw_filename = file.filename or ""
            normalized_filename = normalize_uploaded_filename(raw_filename)
            if not normalized_filename:
                raise HTTPException(
                    status_code=422, detail="施组文件名为空，请重试或重命名后上传。"
                )
            staged = await legacy._stage_upload_file_to_temp_path(
                file, filename=normalized_filename
            )
            try:
                if int(legacy._to_float_or_none(staged.get("size_bytes")) or 0) <= 0:
                    raise HTTPException(status_code=422, detail="施组文件为空，请重新选择文件。")
                submission = await legacy._build_submission_record_from_local_path(
                    project_id,
                    source_path=Path(staged["path"]),
                    normalized_filename=normalized_filename,
                    locale=locale,
                )
                submission_payload = (
                    submission.model_dump() if hasattr(submission, "model_dump") else {}
                )
                self.storage.append_domain_event(
                    event_type="ArtifactUploaded",
                    aggregate_type="project",
                    aggregate_id=project_id,
                    payload={
                        "project_id": project_id,
                        "artifact_id": str(submission_payload.get("id") or ""),
                        "artifact_type": "submission",
                        "filename": normalized_filename,
                    },
                    idempotency_key=_event_key("artifact-uploaded", submission_payload.get("id")),
                )
                return submission
            finally:
                legacy._remove_temp_file(staged.get("path"))

        return await _run_async_task(
            task_kind="scoring",
            task_name="upload_submission",
            project_id=project_id,
            fn=_execute,
        )

    def score_submission_text(self, *, project_id: str, payload: Any, locale: str) -> Any:
        def _execute() -> Any:
            legacy = self._runtime()
            legacy.ensure_data_dirs()
            projects = legacy.load_projects()
            if not project_exists(project_id, projects):
                raise HTTPException(
                    status_code=404, detail=legacy.t("api.project_not_found", locale=locale)
                )
            config = legacy.load_config()
            multipliers, profile_snapshot, project = legacy._resolve_project_scoring_context(
                project_id
            )
            submission_id = str(legacy.uuid4())
            scoring_engine_version = str(project.get("scoring_engine_version_locked") or "v1")
            engine_version = legacy._determine_engine_version(project, scoring_engine_version)
            material_knowledge_snapshot = legacy._build_material_knowledge_profile(project_id)

            if engine_version == "v1":
                config_hash = legacy._compute_multipliers_hash(multipliers) if multipliers else None
                cached_result = legacy.get_cached_score(payload.text, config_hash)
                if cached_result is not None:
                    report, _ = legacy._normalize_score_report_payload(cached_result)
                else:
                    raw_report, _ = legacy._score_submission_for_project(
                        submission_id=submission_id,
                        text=payload.text,
                        project_id=project_id,
                        project=project,
                        config=config,
                        multipliers=multipliers,
                        profile_snapshot=profile_snapshot,
                        scoring_engine_version=scoring_engine_version,
                        material_knowledge_snapshot=material_knowledge_snapshot,
                    )
                    legacy.cache_score_result(payload.text, raw_report, config_hash)
                    report = dict(raw_report)
                legacy._apply_evolution_total_scale(project_id, report)
                evidence_units: List[Dict[str, object]] = []
            else:
                report, evidence_units = legacy._score_submission_for_project(
                    submission_id=submission_id,
                    text=payload.text,
                    project_id=project_id,
                    project=project,
                    config=config,
                    multipliers=multipliers,
                    profile_snapshot=profile_snapshot,
                    scoring_engine_version=scoring_engine_version,
                    material_knowledge_snapshot=material_knowledge_snapshot,
                )
                legacy._apply_evolution_total_scale(project_id, report)

            record = {
                "id": submission_id,
                "project_id": project_id,
                "filename": "inline",
                "total_score": float(
                    report.get("total_score", report.get("rule_total_score", 0.0))
                ),
                "report": report,
                "text": payload.text,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "expert_profile_id_used": profile_snapshot.get("id") if profile_snapshot else None,
            }
            submissions = legacy.load_submissions()
            submissions.append(record)
            legacy.save_submissions(submissions)

            snapshots = legacy.load_score_reports()
            snapshots.append(
                legacy._build_score_report_snapshot(
                    submission_id=submission_id,
                    project=project,
                    report=report,
                    profile_snapshot=profile_snapshot,
                    scoring_engine_version=scoring_engine_version,
                )
            )
            legacy.save_score_reports(snapshots)
            if evidence_units:
                all_units = legacy._load_evidence_units_safe()
                all_units = legacy._replace_submission_evidence_units(
                    all_units,
                    submission_id=submission_id,
                    new_units=evidence_units,
                )
                legacy.save_evidence_units(all_units)

            dimension_scores = {
                dim_id: dim.get("score", 0.0)
                for dim_id, dim in report.get("dimension_scores", {}).items()
            }
            penalty_count = len(report.get("penalties", []))
            legacy.record_history_score(
                project_id=project_id,
                submission_id=submission_id,
                filename="inline",
                total_score=float(report.get("total_score", report.get("rule_total_score", 0.0))),
                dimension_scores=dimension_scores,
                penalty_count=penalty_count,
            )
            self.storage.append_domain_event(
                event_type="ScoreComputed",
                aggregate_type="project",
                aggregate_id=project_id,
                payload={
                    "project_id": project_id,
                    "submission_id": submission_id,
                    "total_score": float(
                        report.get("total_score", report.get("rule_total_score", 0.0))
                    ),
                    "scoring_engine_version": scoring_engine_version,
                    "dimension_count": len(report.get("dimension_scores", {})),
                    "evidence_unit_count": len(evidence_units),
                },
                idempotency_key=_event_key("score-computed", submission_id),
            )
            return legacy.SubmissionRecord(
                **legacy._normalize_submission_record(record, project_id_fallback=project_id)[0]
            )

        return _run_sync_task(
            task_kind="scoring",
            task_name="score_submission_text",
            project_id=project_id,
            fn=_execute,
        )

    def list_submissions(self, *, project_id: str, with_: Optional[str]) -> Any:
        legacy = self._runtime()
        legacy.ensure_data_dirs()
        projects = legacy.load_projects()
        project = next((p for p in projects if str(p.get("id")) == project_id), {"id": project_id})
        submissions = [
            normalized_submission
            for row in legacy.load_submissions()
            for normalized_submission, _ in [
                legacy._normalize_submission_record(
                    dict(row) if isinstance(row, dict) else {},
                    project_id_fallback=None,
                )
            ]
            if str(normalized_submission.get("project_id") or "") == project_id
        ]
        bundle = legacy.submission_dual_track_views_module.build_project_submission_views(
            project_id=project_id,
            submissions=submissions,
            project=project,
        )
        submissions_view = bundle["submissions_view"]
        allow_pred_score = bool(bundle["allow_pred_score"])
        score_scale_max = int(bundle["score_scale_max"])
        if with_ != "latest_report":
            return [
                legacy.SubmissionRecord(
                    **legacy._normalize_submission_record(
                        submission,
                        project_id_fallback=project_id,
                    )[0]
                )
                for submission in submissions_view
            ]
        return legacy.ProjectPreScoreListResponse(
            project_id=project_id,
            expert_profile_id=project.get("expert_profile_id"),
            submissions=legacy.submission_dual_track_views_module.build_project_pre_score_rows(
                project_id,
                submissions_view,
                allow_pred_score=allow_pred_score,
                score_scale_max=score_scale_max,
            ),
        )

    def get_submission_evidence_trace(
        self,
        *,
        project_id: str,
        submission_id: str,
        locale: str,
    ) -> Any:
        legacy = self._runtime()
        legacy.ensure_data_dirs()
        projects = legacy.load_projects()
        legacy._find_project(project_id, projects)
        submissions = legacy.load_submissions()
        submission = next(
            (
                item
                for item in submissions
                if str(item.get("id") or "") == submission_id
                and str(item.get("project_id") or "") == project_id
            ),
            None,
        )
        if submission is None:
            raise HTTPException(
                status_code=404, detail=legacy.t("api.no_submissions", locale=locale)
            )
        payload = legacy._build_submission_evidence_trace_report(
            project_id=project_id,
            submission=submission,
        )
        return legacy.EvidenceTraceResponse(**payload)

    def get_latest_project_scoring_diagnostic(self, *, project_id: str, locale: str) -> Any:
        legacy = self._runtime()
        legacy.ensure_data_dirs()
        projects = legacy.load_projects()
        try:
            project = legacy._find_project(project_id, projects)
        except HTTPException:
            raise HTTPException(
                status_code=404, detail=legacy.t("api.project_not_found", locale=locale)
            )
        payload = legacy._build_project_scoring_diagnostic(project_id, project)
        return legacy.ProjectScoringDiagnosticResponse(**payload)

    def get_submission_scoring_basis(
        self,
        *,
        project_id: str,
        submission_id: str,
        locale: str,
    ) -> Any:
        legacy = self._runtime()
        legacy.ensure_data_dirs()
        projects = legacy.load_projects()
        legacy._find_project(project_id, projects)
        submissions = legacy.load_submissions()
        submission = next(
            (
                item
                for item in submissions
                if str(item.get("id") or "") == submission_id
                and str(item.get("project_id") or "") == project_id
            ),
            None,
        )
        if submission is None:
            raise HTTPException(
                status_code=404, detail=legacy.t("api.no_submissions", locale=locale)
            )
        payload = legacy._build_submission_scoring_basis_report(
            project_id=project_id,
            submission=submission,
        )
        return legacy.ScoringBasisResponse(**payload)

    def compare_submissions(self, *, project_id: str, locale: str) -> Any:
        legacy = self._runtime()
        legacy.ensure_data_dirs()
        projects = legacy.load_projects()
        project = next((p for p in projects if str(p.get("id")) == project_id), {"id": project_id})
        allow_pred_score = legacy._select_calibrator_model(project) is not None
        score_scale_max = legacy._resolve_project_score_scale_max(project)
        submissions_all = [
            submission
            for submission in legacy.load_submissions()
            if str(submission.get("project_id") or "") == project_id
        ]
        if not submissions_all:
            raise HTTPException(
                status_code=404, detail=legacy.t("api.no_submissions", locale=locale)
            )
        submissions = [
            submission
            for submission in submissions_all
            if legacy._submission_has_generated_score(submission)
        ]
        if not submissions:
            raise HTTPException(status_code=404, detail="暂无已评分施组，请先点击“评分施组”。")
        material_knowledge_snapshot = legacy._build_material_knowledge_profile(project_id)
        rankings = []
        for submission in submissions:
            display_submission = (
                legacy._preview_submission_with_live_prediction(submission, project=project)
                if allow_pred_score
                else dict(submission)
            )
            report_for_awareness = (
                display_submission.get("report")
                if isinstance(display_submission.get("report"), dict)
                else None
            )
            awareness = legacy._ensure_report_score_self_awareness(
                report_for_awareness,
                project_id=project_id,
                material_knowledge_snapshot=material_knowledge_snapshot,
            )
            score_fields = legacy._resolve_submission_score_fields(
                display_submission,
                allow_pred_score=allow_pred_score,
                score_scale_max=score_scale_max,
            )
            ranking_row = {
                "submission_id": display_submission["id"],
                "id": display_submission["id"],
                "filename": display_submission["filename"],
                "total_score": score_fields["total_score"],
                "pred_total_score": score_fields["pred_total_score"],
                "rule_total_score": score_fields["rule_total_score"],
                "score_source": score_fields["score_source"],
                "score_confidence_level": str(
                    ((report_for_awareness or {}).get("meta") or {}).get("score_confidence_level")
                    or ""
                ),
                "score_self_awareness": awareness if isinstance(awareness, dict) else {},
                "created_at": display_submission.get("created_at"),
            }
            ranking_row.update(legacy.build_compare_sort_fields(ranking_row))
            rankings.append(ranking_row)
        rankings = sorted(rankings, key=legacy.compare_sort_key, reverse=True)
        dimension_totals: Dict[str, float] = {}
        dimension_counts: Dict[str, int] = {}
        penalty_stats: Dict[str, int] = {}
        for submission in submissions:
            report = submission.get("report") if isinstance(submission.get("report"), dict) else {}
            for dim_id, dim in (report.get("dimension_scores") or {}).items():
                dimension_totals[dim_id] = dimension_totals.get(dim_id, 0.0) + float(
                    dim.get("score", 0.0)
                )
                dimension_counts[dim_id] = dimension_counts.get(dim_id, 0) + 1
            for penalty in report.get("penalties") or []:
                if not isinstance(penalty, dict):
                    continue
                code = penalty.get("code", "UNKNOWN")
                penalty_stats[code] = penalty_stats.get(code, 0) + 1
        dimension_avg = {
            dim_id: round(dimension_totals[dim_id] / dimension_counts[dim_id], 2)
            for dim_id in dimension_totals
        }
        return legacy.CompareReport(
            project_id=project_id,
            rankings=rankings,
            dimension_avg=dimension_avg,
            penalty_stats=penalty_stats,
        )

    def compare_report(
        self,
        *,
        project_id: str,
        score_scale_max: Optional[int],
        submission_id: Optional[str],
        locale: str,
    ) -> Any:
        legacy = self._runtime()
        legacy.ensure_data_dirs()
        projects = legacy.load_projects()
        project = next((p for p in projects if str(p.get("id")) == project_id), {"id": project_id})
        allow_pred_score = legacy._select_calibrator_model(project) is not None
        report_score_scale_max = legacy._normalize_score_scale_max(
            score_scale_max,
            default=legacy._resolve_project_score_scale_max(project),
        )
        submissions_all = [
            submission
            for submission in legacy.load_submissions()
            if str(submission.get("project_id") or "") == project_id
        ]
        if not submissions_all:
            raise HTTPException(
                status_code=404, detail=legacy.t("api.no_submissions", locale=locale)
            )
        submissions = [
            submission
            for submission in submissions_all
            if legacy._submission_has_generated_score(submission)
        ]
        if not submissions:
            raise HTTPException(status_code=404, detail="暂无已评分施组，请先点击“评分施组”。")
        focused_submission_id = str(submission_id or "").strip()
        if focused_submission_id and not any(
            str(submission.get("id") or "").strip() == focused_submission_id
            for submission in submissions
        ):
            raise HTTPException(status_code=404, detail="未找到指定施组的优化清单上下文。")
        material_knowledge_snapshot = legacy._build_material_knowledge_profile(project_id)
        submissions_for_compare = []
        by_id: Dict[str, Dict[str, object]] = {}
        for submission in submissions:
            display_submission = (
                legacy._preview_submission_with_live_prediction(submission, project=project)
                if allow_pred_score
                else dict(submission)
            )
            score_fields_display = legacy._resolve_submission_score_fields(
                display_submission,
                allow_pred_score=allow_pred_score,
                score_scale_max=report_score_scale_max,
            )
            item = dict(display_submission)
            item["total_score"] = float(score_fields_display["total_score"])
            report = item.get("report")
            report = dict(report) if isinstance(report, dict) else {}
            legacy._ensure_report_score_self_awareness(
                report,
                project_id=project_id,
                material_knowledge_snapshot=material_knowledge_snapshot,
            )
            report["pred_total_score"] = score_fields_display["pred_total_score"]
            report["rule_total_score"] = score_fields_display["rule_total_score"]
            report["score_scale_max"] = report_score_scale_max
            report["score_scale_label"] = legacy._score_scale_label(report_score_scale_max)
            item["report"] = report
            item["score_source"] = score_fields_display["score_source"]
            submissions_for_compare.append(item)
            by_id[str(item.get("id") or "")] = item
        narrative = legacy.build_compare_narrative(
            submissions_for_compare,
            score_scale_max=report_score_scale_max,
            focus_submission_id=focused_submission_id,
        )
        for key in ("top_submission", "bottom_submission"):
            row = narrative.get(key)
            if not isinstance(row, dict):
                continue
            sid = str(row.get("id") or "")
            source_submission = by_id.get(sid)
            if not source_submission:
                continue
            score_fields = legacy._resolve_submission_score_fields(
                source_submission,
                allow_pred_score=allow_pred_score,
                score_scale_max=report_score_scale_max,
            )
            source_report = source_submission.get("report")
            source_report_dict = source_report if isinstance(source_report, dict) else {}
            source_meta = source_report_dict.get("meta")
            source_meta_dict = source_meta if isinstance(source_meta, dict) else {}
            awareness_raw = source_meta_dict.get("score_self_awareness")
            awareness = awareness_raw if isinstance(awareness_raw, dict) else {}
            row["pred_total_score"] = score_fields["pred_total_score"]
            row["rule_total_score"] = score_fields["rule_total_score"]
            row["score_source"] = score_fields["score_source"]
            row["score_confidence_level"] = str(
                source_meta_dict.get("score_confidence_level") or awareness.get("level") or ""
            )
            row["score_self_awareness"] = awareness
        focus_submission = narrative.get("focus_submission")
        if isinstance(focus_submission, dict) and focus_submission:
            sid = str(focus_submission.get("id") or "")
            source_submission = by_id.get(sid)
            if source_submission:
                source_report = source_submission.get("report")
                source_report_dict = source_report if isinstance(source_report, dict) else {}
                source_meta = source_report_dict.get("meta")
                source_meta_dict = source_meta if isinstance(source_meta, dict) else {}
                awareness_raw = source_meta_dict.get("score_self_awareness")
                awareness = awareness_raw if isinstance(awareness_raw, dict) else {}
                focus_submission["score_confidence_level"] = str(
                    source_meta_dict.get("score_confidence_level") or ""
                )
                focus_submission["score_self_awareness"] = awareness
        return legacy.CompareNarrative(project_id=project_id, **narrative)

    def get_project_evaluation(self, *, project_id: str, locale: str) -> Any:
        legacy = self._runtime()
        legacy.ensure_data_dirs()
        projects = legacy.load_projects()
        project = next((p for p in projects if str(p.get("id")) == project_id), None)
        if project is None:
            raise HTTPException(
                status_code=404, detail=legacy.t("api.project_not_found", locale=locale)
            )
        data = legacy.evaluate_project_variants(
            project_id=project_id,
            submissions=legacy.load_submissions(),
            score_reports=legacy._load_score_reports_safe(),
            qingtian_results=legacy._load_qingtian_results_safe(),
        )
        evolution_health = legacy._build_evolution_health_report(project_id, project)
        governance_payload = legacy._build_feedback_governance_report(project_id, project)
        data["phase1_closure_readiness"] = legacy._build_phase1_closure_readiness(
            project_id=project_id,
            project=project,
            evaluation_payload=data,
            evolution_health=evolution_health,
            governance_payload=governance_payload,
        )
        return legacy.ProjectEvaluationResponse(**data)


class GovernanceApplicationService(_StorageAwareService):
    def get_feedback_governance(self, *, project_id: str, locale: str) -> Any:
        legacy = self._runtime()
        legacy.ensure_data_dirs()
        projects = legacy.load_projects()
        try:
            project = legacy._find_project(project_id, projects)
        except HTTPException:
            raise HTTPException(
                status_code=404, detail=legacy.t("api.project_not_found", locale=locale)
            )
        payload = legacy._build_feedback_governance_report(project_id, project)
        return legacy.FeedbackGovernanceResponse(**payload)

    def preview_feedback_governance_version(
        self,
        *,
        project_id: str,
        payload: Any,
        locale: str,
    ) -> Any:
        legacy = self._runtime()
        legacy.ensure_data_dirs()
        projects = legacy.load_projects()
        try:
            project = legacy._find_project(project_id, projects)
        except HTTPException:
            raise HTTPException(
                status_code=404, detail=legacy.t("api.project_not_found", locale=locale)
            )
        preview_payload = legacy._build_feedback_governance_version_preview(
            project_id,
            project,
            artifact=str(payload.artifact or "").strip(),
            version_id=str(payload.version_id or "").strip(),
        )
        return legacy.FeedbackGovernanceVersionPreviewResponse(**preview_payload)

    def preview_feedback_guardrail_review(
        self,
        *,
        project_id: str,
        record_id: str,
        payload: Any,
        locale: str,
    ) -> Any:
        legacy = self._runtime()
        legacy.ensure_data_dirs()
        projects = legacy.load_projects()
        try:
            project = legacy._find_project(project_id, projects)
        except HTTPException:
            raise HTTPException(
                status_code=404, detail=legacy.t("api.project_not_found", locale=locale)
            )
        preview_payload = legacy._build_feedback_governance_action_preview(
            project_id,
            project,
            record_id=record_id,
            preview_type="guardrail",
            action=str(payload.action or "").strip().lower(),
            note=str(payload.note or "").strip(),
            rerun_closed_loop=bool(payload.rerun_closed_loop),
        )
        return legacy.FeedbackGovernanceActionPreviewResponse(**preview_payload)

    def review_feedback_guardrail(
        self,
        *,
        project_id: str,
        record_id: str,
        payload: Any,
        locale: str,
    ) -> Any:
        def _execute() -> Any:
            legacy = self._runtime()
            legacy.ensure_data_dirs()
            projects = legacy.load_projects()
            if not any(str(project.get("id")) == str(project_id) for project in projects):
                raise HTTPException(
                    status_code=404, detail=legacy.t("api.project_not_found", locale=locale)
                )
            response_payload = legacy._execute_feedback_guardrail_review(
                project_id,
                record_id,
                action=str(payload.action or "").strip().lower(),
                note=str(payload.note or "").strip(),
                rerun_closed_loop=bool(payload.rerun_closed_loop),
                locale=locale,
            )
            self.storage.append_domain_event(
                event_type="GovernanceDecisionApplied",
                aggregate_type="project",
                aggregate_id=project_id,
                payload={
                    "project_id": project_id,
                    "record_id": record_id,
                    "review_type": "guardrail",
                    "action": str(payload.action or "").strip().lower(),
                },
                idempotency_key=_event_key(
                    "governance-guardrail", project_id, record_id, payload.action
                ),
            )
            return legacy.FeedbackGuardrailReviewResponse(**response_payload)

        return _run_sync_task(
            task_kind="governance",
            task_name="review_feedback_guardrail",
            project_id=project_id,
            fn=_execute,
        )

    def preview_feedback_few_shot_review(
        self,
        *,
        project_id: str,
        record_id: str,
        payload: Any,
        locale: str,
    ) -> Any:
        legacy = self._runtime()
        legacy.ensure_data_dirs()
        projects = legacy.load_projects()
        try:
            project = legacy._find_project(project_id, projects)
        except HTTPException:
            raise HTTPException(
                status_code=404, detail=legacy.t("api.project_not_found", locale=locale)
            )
        preview_payload = legacy._build_feedback_governance_action_preview(
            project_id,
            project,
            record_id=record_id,
            preview_type="few_shot",
            action=str(payload.action or "").strip().lower(),
            note=str(payload.note or "").strip(),
        )
        return legacy.FeedbackGovernanceActionPreviewResponse(**preview_payload)

    def review_feedback_few_shot(
        self,
        *,
        project_id: str,
        record_id: str,
        payload: Any,
        locale: str,
    ) -> Any:
        def _execute() -> Any:
            legacy = self._runtime()
            legacy.ensure_data_dirs()
            projects = legacy.load_projects()
            if not any(str(project.get("id")) == str(project_id) for project in projects):
                raise HTTPException(
                    status_code=404, detail=legacy.t("api.project_not_found", locale=locale)
                )
            response_payload = legacy._execute_feedback_few_shot_review(
                project_id,
                record_id,
                action=str(payload.action or "").strip().lower(),
                note=str(payload.note or "").strip(),
            )
            self.storage.append_domain_event(
                event_type="GovernanceDecisionApplied",
                aggregate_type="project",
                aggregate_id=project_id,
                payload={
                    "project_id": project_id,
                    "record_id": record_id,
                    "review_type": "few_shot",
                    "action": str(payload.action or "").strip().lower(),
                },
                idempotency_key=_event_key(
                    "governance-few-shot", project_id, record_id, payload.action
                ),
            )
            return legacy.FewShotReviewResponse(**response_payload)

        return _run_sync_task(
            task_kind="governance",
            task_name="review_feedback_few_shot",
            project_id=project_id,
            fn=_execute,
        )


class LearningApplicationService(_StorageAwareService):
    def add_ground_truth(self, *, project_id: str, payload: Any, locale: str) -> Any:
        def _execute() -> Any:
            legacy = self._runtime()
            legacy.ensure_data_dirs()
            record = legacy._build_ground_truth_record(
                project_id=project_id,
                shigong_text=payload.shigong_text,
                judge_scores=payload.judge_scores,
                final_score=payload.final_score,
                source=payload.source,
                locale=locale,
                judge_weights=payload.judge_weights,
                qualitative_tags_by_judge=payload.qualitative_tags_by_judge,
            )
            records = legacy.load_ground_truth()
            records.append(record)
            legacy.save_ground_truth(records)
            record = legacy._finalize_ground_truth_learning_record(
                project_id,
                record,
                locale=locale,
                trigger="ground_truth_add",
            )
            self.storage.append_domain_event(
                event_type="ActualResultRecorded",
                aggregate_type="project",
                aggregate_id=project_id,
                payload={
                    "project_id": project_id,
                    "ground_truth_id": str(record.get("id") or ""),
                    "source": str(record.get("source") or ""),
                    "final_score": record.get("final_score"),
                },
                idempotency_key=_event_key("ground-truth", record.get("id")),
            )
            return legacy.GroundTruthRecord(
                **legacy._normalize_ground_truth_record(
                    record,
                    project_id_fallback=project_id,
                    default_score_scale_max=legacy.DEFAULT_SCORE_SCALE_MAX,
                )[0]
            )

        return _run_sync_task(
            task_kind="learning",
            task_name="add_ground_truth",
            project_id=project_id,
            fn=_execute,
        )

    def add_ground_truth_from_submission(
        self,
        *,
        project_id: str,
        payload: Any,
        locale: str,
    ) -> Any:
        def _execute() -> Any:
            legacy = self._runtime()
            legacy.ensure_data_dirs()
            record = legacy._build_ground_truth_record_from_submission(
                project_id,
                submission_id=payload.submission_id,
                judge_scores=payload.judge_scores,
                final_score=payload.final_score,
                source=payload.source,
                qualitative_tags_by_judge=payload.qualitative_tags_by_judge,
                locale=locale,
            )
            records = legacy.load_ground_truth()
            records.append(record)
            legacy.save_ground_truth(records)
            record = legacy._finalize_ground_truth_learning_record(
                project_id,
                record,
                locale=locale,
                trigger="ground_truth_add",
            )
            self.storage.append_domain_event(
                event_type="ActualResultRecorded",
                aggregate_type="project",
                aggregate_id=project_id,
                payload={
                    "project_id": project_id,
                    "ground_truth_id": str(record.get("id") or ""),
                    "source": str(record.get("source") or ""),
                    "final_score": record.get("final_score"),
                    "submission_id": str(record.get("submission_id") or ""),
                },
                idempotency_key=_event_key("ground-truth", record.get("id")),
            )
            return legacy.GroundTruthRecord(
                **legacy._normalize_ground_truth_record(
                    record,
                    project_id_fallback=project_id,
                    default_score_scale_max=legacy.DEFAULT_SCORE_SCALE_MAX,
                )[0]
            )

        return _run_sync_task(
            task_kind="learning",
            task_name="add_ground_truth_from_submission",
            project_id=project_id,
            fn=_execute,
        )

    async def add_ground_truth_from_file(
        self,
        *,
        project_id: str,
        file: UploadFile,
        judge_scores: str,
        final_score: float,
        source: str,
        locale: str,
    ) -> Any:
        async def _execute() -> Any:
            legacy = self._runtime()
            legacy.ensure_data_dirs()
            normalized_filename = (
                normalize_uploaded_filename(file.filename or "") or "ground_truth_upload.bin"
            )
            staged = await legacy._stage_upload_file_to_temp_path(
                file, filename=normalized_filename
            )
            try:
                if int(legacy._to_float_or_none(staged.get("size_bytes")) or 0) <= 0:
                    raise HTTPException(status_code=422, detail="施组文件为空，请重新选择文件。")
                record = await run_in_threadpool(
                    legacy._build_ground_truth_record_from_uploaded_path,
                    project_id,
                    filename=normalized_filename,
                    file_path=Path(staged["path"]),
                    judge_scores_form=judge_scores,
                    final_score=final_score,
                    source=source,
                    locale=locale,
                )
            finally:
                legacy._remove_temp_file(staged.get("path"))
            records = legacy.load_ground_truth()
            records.append(record)
            legacy.save_ground_truth(records)
            record = legacy._finalize_ground_truth_learning_record(
                project_id,
                record,
                locale=locale,
                trigger="ground_truth_add",
            )
            self.storage.append_domain_event(
                event_type="ActualResultRecorded",
                aggregate_type="project",
                aggregate_id=project_id,
                payload={
                    "project_id": project_id,
                    "ground_truth_id": str(record.get("id") or ""),
                    "source": str(record.get("source") or ""),
                    "final_score": record.get("final_score"),
                },
                idempotency_key=_event_key("ground-truth", record.get("id")),
            )
            return legacy.GroundTruthRecord(
                **legacy._normalize_ground_truth_record(
                    record,
                    project_id_fallback=project_id,
                    default_score_scale_max=legacy.DEFAULT_SCORE_SCALE_MAX,
                )[0]
            )

        return await _run_async_task(
            task_kind="learning",
            task_name="add_ground_truth_from_file",
            project_id=project_id,
            fn=_execute,
        )

    async def add_ground_truth_from_files(
        self,
        *,
        project_id: str,
        files: List[UploadFile],
        judge_scores: str,
        final_score: float,
        source: str,
        locale: str,
    ) -> Any:
        async def _execute() -> Any:
            legacy = self._runtime()
            legacy.ensure_data_dirs()
            staged_uploads: List[tuple[str, Path]] = []
            try:
                for uploaded in files:
                    normalized_filename = (
                        normalize_uploaded_filename(uploaded.filename or "") or "unknown"
                    )
                    staged = await legacy._stage_upload_file_to_temp_path(
                        uploaded,
                        filename=normalized_filename,
                    )
                    staged_uploads.append((normalized_filename, Path(staged["path"])))
                items, success_records = await run_in_threadpool(
                    legacy._build_ground_truth_batch_items_from_uploaded_paths,
                    project_id,
                    uploads=staged_uploads,
                    judge_scores_form=judge_scores,
                    final_score=final_score,
                    source=source,
                    locale=locale,
                )
            finally:
                for _, staged_path in staged_uploads:
                    legacy._remove_temp_file(staged_path)

            if success_records:
                records = legacy.load_ground_truth()
                records.extend(success_records)
                legacy.save_ground_truth(records)
                items = legacy._finalize_ground_truth_batch_learning_records(
                    project_id,
                    items,
                    locale=locale,
                    trigger="ground_truth_batch_add",
                )
                for record in success_records:
                    self.storage.append_domain_event(
                        event_type="ActualResultRecorded",
                        aggregate_type="project",
                        aggregate_id=project_id,
                        payload={
                            "project_id": project_id,
                            "ground_truth_id": str(record.get("id") or ""),
                            "source": str(record.get("source") or ""),
                            "final_score": record.get("final_score"),
                        },
                        idempotency_key=_event_key("ground-truth", record.get("id")),
                    )
            success_count = sum(1 for item in items if item.get("ok"))
            failed_count = len(items) - success_count
            return legacy.GroundTruthBatchResponse(
                project_id=project_id,
                total_files=len(items),
                success_count=success_count,
                failed_count=failed_count,
                items=items,
            )

        return await _run_async_task(
            task_kind="learning",
            task_name="add_ground_truth_from_files",
            project_id=project_id,
            fn=_execute,
        )

    def list_ground_truth(self, *, project_id: str, locale: str) -> List[Any]:
        legacy = self._runtime()
        legacy.ensure_data_dirs()
        projects = legacy.load_projects()
        project = legacy._find_project_optional(project_id, projects)
        if project is None:
            raise HTTPException(
                status_code=404, detail=legacy.t("api.project_not_found", locale=locale)
            )
        project_score_scale_max = legacy._resolve_project_score_scale_max(project)
        records = [
            record
            for record in legacy.load_ground_truth()
            if record.get("project_id") == project_id
        ]
        records = [
            legacy._repair_ground_truth_record_final_score_if_needed(
                project_id,
                record if isinstance(record, dict) else {},
                project=project,
                locale=locale,
            )
            for record in records
        ]
        records = legacy._enrich_ground_truth_submission_metadata(project_id, records)
        return [
            legacy.GroundTruthRecord(
                **legacy._normalize_ground_truth_record(
                    record if isinstance(record, dict) else {},
                    project_id_fallback=project_id,
                    default_score_scale_max=project_score_scale_max,
                )[0]
            )
            for record in records
        ]

    def delete_ground_truth(self, *, project_id: str, record_id: str, locale: str) -> None:
        legacy = self._runtime()
        legacy.ensure_data_dirs()
        projects = legacy.load_projects()
        if not project_exists(project_id, projects):
            raise HTTPException(
                status_code=404, detail=legacy.t("api.project_not_found", locale=locale)
            )
        records = legacy.load_ground_truth()
        if not any(
            record.get("id") == record_id and record.get("project_id") == project_id
            for record in records
        ):
            raise HTTPException(status_code=404, detail="真实评标记录不存在")
        removed = next(
            (
                record
                for record in records
                if record.get("id") == record_id and record.get("project_id") == project_id
            ),
            None,
        )
        records = [
            record
            for record in records
            if not (record.get("id") == record_id and record.get("project_id") == project_id)
        ]
        legacy.save_ground_truth(records)
        if removed is None:
            return None
        gt_id = str(removed.get("id") or "")
        qtrs = legacy.load_qingtian_results()
        linked_submission_ids = {
            str(item.get("submission_id") or "")
            for item in qtrs
            if str((item.get("raw_payload") or {}).get("ground_truth_record_id") or "") == gt_id
        }
        qtrs = [
            item
            for item in qtrs
            if str((item.get("raw_payload") or {}).get("ground_truth_record_id") or "") != gt_id
        ]
        legacy.save_qingtian_results(qtrs)
        submissions = legacy.load_submissions()
        auto_submission_ids = {
            str(item.get("id") or "")
            for item in submissions
            if str(item.get("source_ground_truth_id") or "") == gt_id
            and str(item.get("project_id")) == project_id
        }
        remove_submission_ids = linked_submission_ids.union(auto_submission_ids)
        if remove_submission_ids:
            submissions = [
                item
                for item in submissions
                if str(item.get("id") or "") not in remove_submission_ids
            ]
            legacy.save_submissions(submissions)
            reports = legacy.load_score_reports()
            reports = [
                item
                for item in reports
                if str(item.get("submission_id") or "") not in remove_submission_ids
            ]
            legacy.save_score_reports(reports)
            units = legacy._load_evidence_units_safe()
            units = [
                item
                for item in units
                if str(item.get("submission_id") or "") not in remove_submission_ids
            ]
            legacy.save_evidence_units(units)
        legacy._refresh_project_reflection_objects(project_id)
        return None

    def evolve_project(
        self,
        *,
        project_id: str,
        confirm_extreme_sample: bool,
        locale: str,
    ) -> Any:
        def _execute() -> Any:
            legacy = self._runtime()
            legacy.ensure_data_dirs()
            projects = legacy.load_projects()
            project = legacy._find_project_optional(project_id, projects)
            if project is None:
                raise HTTPException(
                    status_code=404, detail=legacy.t("api.project_not_found", locale=locale)
                )
            evolution_readiness = legacy._build_evolution_readiness(project_id, project)
            if not bool(evolution_readiness.get("ready")):
                issue_texts = [
                    str(item).strip()
                    for item in (evolution_readiness.get("issues") or [])
                    if str(item).strip()
                ]
                raise HTTPException(
                    status_code=422,
                    detail=issue_texts[0]
                    if issue_texts
                    else "请先完成施组评分后再执行学习与校准。",
                )
            legacy._refresh_project_ground_truth_learning_records(project_id)
            blocked_guardrails = legacy._collect_blocked_ground_truth_guardrails(project_id)
            before_manual_confirmation_payload = legacy._build_manual_confirmation_audit_payload(
                project_id,
                project,
                blocked_guardrails_override=blocked_guardrails,
            )
            if blocked_guardrails and not bool(confirm_extreme_sample):
                raise HTTPException(
                    status_code=409,
                    detail=legacy._build_manual_confirmation_detail(
                        project_id,
                        action_label="学习进化",
                    ),
                )
            project_score_scale = legacy._resolve_project_score_scale_max(project)
            records_raw = legacy._list_project_ground_truth_records(
                project_id,
                include_guardrail_blocked=bool(confirm_extreme_sample),
            )
            records = [
                legacy._ground_truth_record_for_learning(
                    record if isinstance(record, dict) else {},
                    default_score_scale_max=project_score_scale,
                )
                for record in records_raw
            ]
            ctx_data = legacy._normalize_project_context_payload(
                legacy._load_project_context_map().get(project_id)
            )
            project_context = str(ctx_data.get("text") or "").strip()
            materials_text = legacy._merge_materials_text(project_id)
            if materials_text:
                project_context = (
                    (project_context + "\n\n" + materials_text)
                    if project_context
                    else materials_text
                )
            report = legacy.build_evolution_report(project_id, records, project_context)
            report["few_shot_examples"] = legacy._build_project_few_shot_prompt_examples(
                project_id, limit=6
            )
            enhanced = legacy.enhance_evolution_report_with_llm(
                project_id, report, records, project_context
            )
            if enhanced is not None:
                report["high_score_logic"] = enhanced.get(
                    "high_score_logic", report["high_score_logic"]
                )
                report["writing_guidance"] = enhanced.get(
                    "writing_guidance", report["writing_guidance"]
                )
                report["sample_count"] = enhanced.get("sample_count", report["sample_count"])
                report["updated_at"] = enhanced.get("updated_at", report["updated_at"])
                report["enhanced_by"] = enhanced.get("enhanced_by")
                report["enhancement_provider_chain"] = list(
                    enhanced.get("enhancement_provider_chain") or []
                )
                report["enhancement_fallback_used"] = bool(
                    enhanced.get("enhancement_fallback_used")
                )
                report["enhancement_attempts"] = int(enhanced.get("enhancement_attempts") or 0)
                report["enhancement_applied"] = bool(enhanced.get("enhancement_applied", True))
                report["enhancement_governed"] = bool(enhanced.get("enhancement_governed", False))
                report["enhancement_governance_notes"] = list(
                    enhanced.get("enhancement_governance_notes") or []
                )
                report["enhancement_review_provider"] = enhanced.get("enhancement_review_provider")
                report["enhancement_review_status"] = str(
                    enhanced.get("enhancement_review_status") or "not_run"
                )
                review_similarity = enhanced.get("enhancement_review_similarity")
                report["enhancement_review_similarity"] = (
                    float(review_similarity) if review_similarity is not None else None
                )
                report["enhancement_review_notes"] = list(
                    enhanced.get("enhancement_review_notes") or []
                )
            reports = legacy._load_evolution_reports_map()
            governance_payload = legacy._build_feedback_governance_report(project_id, project)
            report["manual_confirmation_audit"] = legacy._build_manual_confirmation_audit(
                project_id,
                project,
                action_label="学习进化",
                confirm_extreme_sample_used=bool(confirm_extreme_sample),
                governance_payload=governance_payload,
                before_payload=before_manual_confirmation_payload,
            )
            evolution_reports = reports
            evolution_reports[project_id] = report
            legacy.save_evolution_reports(evolution_reports)
            return legacy.EvolutionReport(**report)

        return _run_sync_task(
            task_kind="learning",
            task_name="evolve_project",
            project_id=project_id,
            fn=_execute,
        )


class OpsApplicationService(_StorageAwareService):
    def system_self_check(self, *, project_id: Optional[str]) -> Any:
        def _execute() -> Any:
            legacy = self._runtime()
            legacy.ensure_data_dirs()
            payload = legacy._run_system_self_check(project_id)
            self.storage.append_domain_event(
                event_type="OpsCheckExecuted",
                aggregate_type="system",
                aggregate_id=str(project_id or "global"),
                payload={"project_id": project_id, "check_type": "self_check"},
            )
            if payload.get("degraded"):
                emit_task_state(
                    logger,
                    task_kind="ops",
                    task_name="system_self_check",
                    state="degraded",
                    project_id=project_id,
                    failed_optional_count=payload.get("failed_optional_count"),
                )
            return legacy.SelfCheckResponse(**payload)

        return _run_sync_task(
            task_kind="ops",
            task_name="system_self_check",
            project_id=project_id,
            fn=_execute,
        )

    def system_data_hygiene(self) -> Any:
        def _execute() -> Any:
            legacy = self._runtime()
            legacy.ensure_data_dirs()
            payload = legacy._build_data_hygiene_report(apply=False)
            self.storage.append_domain_event(
                event_type="OpsCheckExecuted",
                aggregate_type="system",
                aggregate_id="global",
                payload={"check_type": "data_hygiene", "apply": False},
            )
            return legacy.DataHygieneResponse(**payload)

        return _run_sync_task(
            task_kind="ops",
            task_name="system_data_hygiene",
            project_id=None,
            fn=_execute,
        )

    def repair_system_data_hygiene(self) -> Any:
        def _execute() -> Any:
            legacy = self._runtime()
            legacy.ensure_data_dirs()
            payload = legacy._build_data_hygiene_report(apply=True)
            self.storage.append_domain_event(
                event_type="OpsCheckExecuted",
                aggregate_type="system",
                aggregate_id="global",
                payload={"check_type": "data_hygiene", "apply": True},
            )
            return legacy.DataHygieneResponse(**payload)

        return _run_sync_task(
            task_kind="ops",
            task_name="repair_system_data_hygiene",
            project_id=None,
            fn=_execute,
        )

    def system_improvement_overview(self) -> Any:
        def _execute() -> Any:
            legacy = self._runtime()
            legacy.ensure_data_dirs()
            payload = legacy._build_system_improvement_overview()
            self.storage.append_domain_event(
                event_type="OpsCheckExecuted",
                aggregate_type="system",
                aggregate_id="global",
                payload={"check_type": "system_improvement_overview"},
            )
            return legacy.SystemImprovementOverviewResponse(**payload)

        return _run_sync_task(
            task_kind="ops",
            task_name="system_improvement_overview",
            project_id=None,
            fn=_execute,
        )


class CliApplicationService(_StorageAwareService):
    def execute_score(
        self,
        *,
        input_path: str,
        mode: str,
        prompt: str,
        out: Optional[str],
        summary: bool,
        summary_out: Optional[str],
        docx_out: Optional[str],
        locale: str,
    ) -> CliScoreExecution:
        cli_runtime = _cli_runtime()
        path = Path(input_path)
        text = cli_runtime.read_input_file(path)
        config = cli_runtime.load_config()
        rules_report = cli_runtime.score_text(text, config.rubric, config.lexicon)
        normalized_mode = (mode or "").strip().lower()
        if normalized_mode == "spark":
            normalized_mode = "openai"
        if normalized_mode == "rules":
            rules_report.judge_mode = "rules"
            rules_report.judge_source = "rules_engine"
            rules_report.spark_called = False
            report_json = rules_report.model_dump()
        elif normalized_mode == "openai":
            llm_payload = cli_runtime.run_spark_judge(text, config.rubric, prompt, rules_report)
            if llm_payload.get("called_spark_api"):
                llm_payload["judge_mode"] = "openai"
                llm_payload["judge_source"] = "openai_api"
                report_json = llm_payload
            elif llm_payload.get("processing_interrupted"):
                output = cli_runtime._build_llm_interrupted_output(
                    judge_mode="openai_interrupted",
                    llm_payload=llm_payload,
                )
                report_json = json.loads(output)
            else:
                reason = llm_payload.get("reason", "unknown")
                rules_report.judge_mode = "fallback_rules"
                rules_report.judge_source = "rules_engine"
                rules_report.spark_called = False
                rules_report.fallback_reason = f"{reason}; prompt={prompt}"
                report_json = rules_report.model_dump()
        elif normalized_mode == "hybrid":
            llm_payload = cli_runtime.run_spark_judge(text, config.rubric, prompt, rules_report)
            if llm_payload.get("called_spark_api"):
                base_score = rules_report.total_score
                llm_score = llm_payload.get("overall", {}).get("total_score_0_100", base_score)
                max_adjustment = float(
                    config.rubric.get("llm_merge_policy", {}).get("max_adjustment", 10.0)
                )
                adjustment = max(-max_adjustment, min(max_adjustment, llm_score - base_score))
                report_json = {
                    "judge_mode": "hybrid",
                    "judge_source": "openai_api",
                    "spark_called": True,
                    "base_rules_score": base_score,
                    "llm_adjustment": adjustment,
                    "final_total_score": round(base_score + adjustment, 2),
                    "prompt_version": llm_payload.get("prompt_version"),
                }
            elif llm_payload.get("processing_interrupted"):
                output = cli_runtime._build_llm_interrupted_output(
                    judge_mode="hybrid_interrupted",
                    llm_payload=llm_payload,
                )
                report_json = json.loads(output)
            else:
                reason = llm_payload.get("reason", "unknown")
                report_json = {
                    "judge_mode": "hybrid_fallback_rules",
                    "judge_source": "rules_engine",
                    "spark_called": False,
                    "fallback_reason": f"{reason}; prompt={prompt}",
                    "base_rules_score": rules_report.total_score,
                    "llm_adjustment": 0.0,
                    "final_total_score": rules_report.total_score,
                }
        else:
            raise ValueError("mode 仅支持 rules/openai/hybrid（spark 作为兼容别名仍可用）")

        output = json.dumps(report_json, ensure_ascii=False, indent=2)
        if out:
            Path(out).write_text(output, encoding="utf-8")

        summary_text: Optional[str] = None
        written_summary_path: Optional[str] = None
        if summary:
            summary_text = cli_runtime.format_qingtian_word_report(report_json, locale=locale)
            if summary_out:
                Path(summary_out).write_text(summary_text, encoding="utf-8")
                written_summary_path = summary_out

        docx_path: Optional[str] = None
        if docx_out:
            docx_path = str(cli_runtime.export_report_to_docx(report_json, docx_out))

        return CliScoreExecution(
            output=output,
            report_json=report_json,
            summary_text=summary_text,
            summary_path=written_summary_path,
            docx_path=docx_path,
        )

    def process_batch_file(
        self,
        *,
        input_path: Path,
        output_dir: Path,
        mode: str,
        prompt: str,
        docx: bool,
    ) -> Dict[str, Any]:
        cli_runtime = _cli_runtime()
        config = cli_runtime.load_config()
        text = cli_runtime.read_input_file(input_path)
        rules_report = cli_runtime.score_text(text, config.rubric, config.lexicon)
        normalized_mode = (mode or "").strip().lower()
        if normalized_mode == "spark":
            normalized_mode = "openai"
        if normalized_mode == "rules":
            rules_report.judge_mode = "rules"
            rules_report.judge_source = "rules_engine"
            rules_report.spark_called = False
            report_data = rules_report.model_dump()
        elif normalized_mode == "openai":
            llm_payload = cli_runtime.run_spark_judge(text, config.rubric, prompt, rules_report)
            if llm_payload.get("called_spark_api"):
                llm_payload["judge_mode"] = "openai"
                llm_payload["judge_source"] = "openai_api"
                report_data = llm_payload
            else:
                reason = llm_payload.get("reason", "unknown")
                rules_report.judge_mode = "fallback_rules"
                rules_report.judge_source = "rules_engine"
                rules_report.spark_called = False
                rules_report.fallback_reason = f"{reason}; prompt={prompt}"
                report_data = rules_report.model_dump()
        else:
            rules_report.judge_mode = "rules"
            rules_report.judge_source = "rules_engine"
            rules_report.spark_called = False
            report_data = rules_report.model_dump()

        json_out = output_dir / f"{input_path.stem}_report.json"
        json_out.write_text(json.dumps(report_data, ensure_ascii=False, indent=2), encoding="utf-8")
        docx_path: Optional[str] = None
        if docx:
            docx_out = output_dir / f"{input_path.stem}_report.docx"
            docx_path = str(cli_runtime.export_report_to_docx(report_data, str(docx_out)))
        return {
            "input": str(input_path),
            "json_output": str(json_out),
            "docx_output": docx_path,
            "total_score": report_data.get("total_score", 0),
            "status": "success",
        }

    def warmup_cache(
        self,
        *,
        input_path: str,
        workers: int,
        no_skip_existing: bool,
        ttl: Optional[float],
    ) -> Any:
        cli_runtime = _cli_runtime()
        path = Path(input_path)
        config = cli_runtime.load_config()

        def score_fn(text: str):
            report = cli_runtime.score_text(text, config.rubric, config.lexicon)
            return report.model_dump()

        skip_existing = not no_skip_existing
        if workers <= 1:
            return cli_runtime.warmup_cache_from_file(
                str(path),
                score_fn=score_fn,
                skip_existing=skip_existing,
                ttl=ttl,
            )
        items = cli_runtime._load_warmup_items(path)
        return cli_runtime.warmup_cache_parallel(
            items,
            score_fn=score_fn,
            skip_existing=skip_existing,
            ttl=ttl,
            max_workers=min(workers, len(items)),
        )
