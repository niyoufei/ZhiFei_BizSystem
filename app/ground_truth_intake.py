from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Tuple

from fastapi import HTTPException

from app.application.storage_access import StorageAccess
from app.bootstrap.storage import get_storage_access
from app.domain.documents.parse_errors import coerce_document_parse_error
from app.domain.learning.ground_truth_records import (
    assert_valid_final_score,
    new_ground_truth_record,
    parse_judge_scores_form,
    resolve_project_score_scale_max,
)
from app.domain.learning.ground_truth_rule_resolution import (
    resolve_project_ground_truth_score_rule as resolve_domain_ground_truth_score_rule,
)
from app.domain.learning.ground_truth_scoring import (
    auto_compute_ground_truth_final_score_if_needed,
)
from app.i18n import t
from app.infrastructure.documents.runtime_adapters import (
    get_default_uploaded_content_reader_dependencies,
)
from app.infrastructure.documents.uploaded_content import (
    read_uploaded_file_content_with_dependencies,
)


def _storage(storage: StorageAccess | None = None) -> StorageAccess:
    return storage or get_storage_access()


def _main():
    from app.application import runtime as main_mod

    return main_mod


def _resolve_project_ground_truth_score_rule(
    project_id: str,
    *,
    project: Dict[str, object] | None = None,
    storage: StorageAccess | None = None,
) -> Dict[str, object]:
    runtime_attr = getattr(_main(), "_resolve_project_ground_truth_score_rule", None)
    runtime_module_name = getattr(runtime_attr, "__module__", None)
    if runtime_attr is not None and runtime_module_name not in {"app.application.runtime", None}:
        return runtime_attr(project_id, project=project)

    project_row = project
    if project_row is None:
        project_row = next(
            (
                item
                for item in _load_projects(storage)
                if str(item.get("id") or "") == str(project_id)
            ),
            None,
        )
    if not isinstance(project_row, dict):
        raise HTTPException(status_code=404, detail="项目不存在")
    return resolve_domain_ground_truth_score_rule(
        project_id,
        project=project_row,
        materials=_storage(storage).load_materials(),
        extract_rule_from_text=lambda text, filename: (
            _main()._extract_ground_truth_score_rule_from_text(
                text,
                filename=filename,
            )
        ),
        extract_rule_from_material=_main()._extract_ground_truth_score_rule_from_material,
        extract_scale_from_material=_main()._extract_ground_truth_score_scale_from_material,
    )


def _auto_compute_ground_truth_final_score_if_needed(
    project_id: str,
    *,
    judge_scores: List[float],
    final_score: float,
    project: Dict[str, object],
) -> float:
    return auto_compute_ground_truth_final_score_if_needed(
        project_id,
        judge_scores=judge_scores,
        final_score=final_score,
        project=project,
        resolve_scoring_rule=lambda pid, project_row: _resolve_project_ground_truth_score_rule(
            pid,
            project=project_row,
        ),
    )


def _read_uploaded_file_content(
    content: bytes | None,
    filename: str,
    *,
    file_path: Path | None = None,
) -> str:
    runtime_attr = getattr(_main(), "_read_uploaded_file_content", None)
    runtime_module_name = getattr(runtime_attr, "__module__", None)
    if runtime_attr is not None and runtime_module_name not in {"app.application.runtime", None}:
        return runtime_attr(content, filename, file_path=file_path)
    return read_uploaded_file_content_with_dependencies(
        content,
        filename,
        file_path=file_path,
        dependencies=get_default_uploaded_content_reader_dependencies(),
    )


def _load_projects(storage: StorageAccess | None = None) -> List[Dict[str, object]]:
    runtime_attr = getattr(_main(), "load_projects", None)
    runtime_module_name = getattr(runtime_attr, "__module__", None)
    if runtime_attr is not None and runtime_module_name not in {"app.storage", None}:
        return runtime_attr()
    return _storage(storage).load_projects()


def _load_submissions(storage: StorageAccess | None = None) -> List[Dict[str, object]]:
    runtime_attr = getattr(_main(), "load_submissions", None)
    runtime_module_name = getattr(runtime_attr, "__module__", None)
    if runtime_attr is not None and runtime_module_name not in {"app.storage", None}:
        return runtime_attr()
    return _storage(storage).load_submissions()


def _resolve_project_score_context(
    project_id: str, *, locale: str, storage: StorageAccess | None = None
) -> Tuple[Dict[str, object], int]:
    projects = _load_projects(storage)
    project = next((p for p in projects if str(p.get("id") or "") == project_id), None)
    if project is None:
        raise HTTPException(status_code=404, detail=t("api.project_not_found", locale=locale))
    score_scale_max = resolve_project_score_scale_max(project)
    return project, score_scale_max


def build_ground_truth_record(
    project_id: str,
    *,
    shigong_text: str,
    judge_scores: List[float],
    final_score: float,
    source: str,
    locale: str,
    judge_weights: List[float] | None = None,
    qualitative_tags_by_judge: List[List[str]] | None = None,
    storage: StorageAccess | None = None,
) -> Dict[str, object]:
    project, score_scale_max = _resolve_project_score_context(
        project_id,
        locale=locale,
        storage=storage,
    )
    if len((shigong_text or "").strip()) < 50:
        raise HTTPException(status_code=422, detail="施组全文过短，至少 50 字以便学习分析。")
    final_score = _auto_compute_ground_truth_final_score_if_needed(
        project_id,
        judge_scores=judge_scores,
        final_score=final_score,
        project=project,
    )
    assert_valid_final_score(final_score, score_scale_max=score_scale_max)
    return new_ground_truth_record(
        project_id=project_id,
        shigong_text=shigong_text,
        judge_scores=judge_scores,
        final_score=final_score,
        source=source,
        score_scale_max=score_scale_max,
        judge_weights=judge_weights,
        qualitative_tags_by_judge=qualitative_tags_by_judge,
    )


def build_ground_truth_record_from_submission(
    project_id: str,
    *,
    submission_id: str,
    judge_scores: List[float],
    final_score: float,
    source: str,
    qualitative_tags_by_judge: List[List[str]] | None,
    locale: str,
    storage: StorageAccess | None = None,
) -> Dict[str, object]:
    project, score_scale_max = _resolve_project_score_context(
        project_id,
        locale=locale,
        storage=storage,
    )

    submission_id = str(submission_id or "").strip()
    submissions = _load_submissions(storage)
    submission = next(
        (
            s
            for s in submissions
            if str(s.get("id")) == submission_id and str(s.get("project_id")) == project_id
        ),
        None,
    )
    if not submission:
        raise HTTPException(status_code=404, detail="未找到对应施组，请先在步骤4上传施组。")

    shigong_text = str(submission.get("text") or "").strip()
    if len(shigong_text) < 50:
        raise HTTPException(status_code=422, detail="该施组文本过短，暂不支持录入真实评标。")
    final_score = _auto_compute_ground_truth_final_score_if_needed(
        project_id,
        judge_scores=judge_scores,
        final_score=final_score,
        project=project,
    )
    assert_valid_final_score(final_score, score_scale_max=score_scale_max)

    record = new_ground_truth_record(
        project_id=project_id,
        shigong_text=shigong_text,
        judge_scores=[float(x) for x in judge_scores],
        final_score=float(final_score),
        source=source,
        score_scale_max=score_scale_max,
        judge_weights=None,
        qualitative_tags_by_judge=qualitative_tags_by_judge,
    )
    record["source_submission_id"] = submission_id
    record["source_submission_filename"] = submission.get("filename")
    record["source_submission_created_at"] = submission.get("created_at")
    return record


def build_ground_truth_record_from_uploaded_file(
    project_id: str,
    *,
    filename: str,
    content: bytes,
    judge_scores_form: str,
    final_score: float,
    source: str,
    locale: str,
    storage: StorageAccess | None = None,
) -> Dict[str, object]:
    judge_scores_list = parse_judge_scores_form(judge_scores_form)
    shigong_text = _read_uploaded_file_content(content, filename or "")
    record = build_ground_truth_record(
        project_id,
        shigong_text=shigong_text,
        judge_scores=judge_scores_list,
        final_score=final_score,
        source=source,
        locale=locale,
        storage=storage,
    )
    record["source_submission_filename"] = filename or None
    return record


def build_ground_truth_record_from_uploaded_path(
    project_id: str,
    *,
    filename: str,
    file_path: str | Path,
    judge_scores_form: str,
    final_score: float,
    source: str,
    locale: str,
    storage: StorageAccess | None = None,
) -> Dict[str, object]:
    judge_scores_list = parse_judge_scores_form(judge_scores_form)
    try:
        shigong_text = _read_uploaded_file_content(
            None,
            filename or "",
            file_path=Path(file_path),
        )
    except Exception as exc:
        raise coerce_document_parse_error(exc, filename=filename or "") from exc
    record = build_ground_truth_record(
        project_id,
        shigong_text=shigong_text,
        judge_scores=judge_scores_list,
        final_score=final_score,
        source=source,
        locale=locale,
        storage=storage,
    )
    record["source_submission_filename"] = filename or None
    return record


def build_ground_truth_batch_items_from_uploaded_files(
    project_id: str,
    *,
    uploads: List[Tuple[str, bytes]],
    judge_scores_form: str,
    final_score: float,
    source: str,
    locale: str,
    storage: StorageAccess | None = None,
) -> Tuple[List[Dict[str, object]], List[Dict[str, object]]]:
    project, score_scale_max = _resolve_project_score_context(
        project_id,
        locale=locale,
        storage=storage,
    )
    judge_scores_list = parse_judge_scores_form(judge_scores_form)
    final_score = _auto_compute_ground_truth_final_score_if_needed(
        project_id,
        judge_scores=judge_scores_list,
        final_score=final_score,
        project=project,
    )
    assert_valid_final_score(final_score, score_scale_max=score_scale_max)

    items: List[Dict[str, object]] = []
    success_records: List[Dict[str, object]] = []
    for filename, content in uploads:
        clean_filename = filename or "unknown"
        try:
            shigong_text = _read_uploaded_file_content(content, clean_filename)
            if len(shigong_text.strip()) < 50:
                raise ValueError("施组全文过短，至少 50 字以便学习分析。")
            record = new_ground_truth_record(
                project_id=project_id,
                shigong_text=shigong_text,
                judge_scores=judge_scores_list,
                final_score=final_score,
                source=source,
                score_scale_max=score_scale_max,
                judge_weights=None,
                qualitative_tags_by_judge=None,
            )
            record["source_submission_filename"] = clean_filename or None
            success_records.append(record)
            items.append(
                {
                    "filename": clean_filename,
                    "ok": True,
                    "record": record,
                    "detail": None,
                }
            )
        except Exception as exc:
            items.append(
                {
                    "filename": clean_filename,
                    "ok": False,
                    "record": None,
                    "detail": str(exc),
                }
            )
    return items, success_records


def build_ground_truth_batch_items_from_uploaded_paths(
    project_id: str,
    *,
    uploads: List[Tuple[str, str | Path]],
    judge_scores_form: str,
    final_score: float,
    source: str,
    locale: str,
    storage: StorageAccess | None = None,
) -> Tuple[List[Dict[str, object]], List[Dict[str, object]]]:
    project, score_scale_max = _resolve_project_score_context(
        project_id,
        locale=locale,
        storage=storage,
    )
    judge_scores_list = parse_judge_scores_form(judge_scores_form)
    final_score = _auto_compute_ground_truth_final_score_if_needed(
        project_id,
        judge_scores=judge_scores_list,
        final_score=final_score,
        project=project,
    )
    assert_valid_final_score(final_score, score_scale_max=score_scale_max)

    items: List[Dict[str, object]] = []
    success_records: List[Dict[str, object]] = []
    for filename, file_path in uploads:
        clean_filename = filename or "unknown"
        try:
            shigong_text = _read_uploaded_file_content(
                None,
                clean_filename,
                file_path=Path(file_path),
            )
            if len(shigong_text.strip()) < 50:
                raise ValueError("施组全文过短，至少 50 字以便学习分析。")
            record = new_ground_truth_record(
                project_id=project_id,
                shigong_text=shigong_text,
                judge_scores=judge_scores_list,
                final_score=final_score,
                source=source,
                score_scale_max=score_scale_max,
                judge_weights=None,
                qualitative_tags_by_judge=None,
            )
            record["source_submission_filename"] = clean_filename or None
            success_records.append(record)
            items.append(
                {
                    "filename": clean_filename,
                    "ok": True,
                    "record": record,
                    "detail": None,
                }
            )
        except Exception as exc:
            normalized_exc = coerce_document_parse_error(exc, filename=clean_filename)
            items.append(
                {
                    "filename": clean_filename,
                    "ok": False,
                    "record": None,
                    "detail": normalized_exc.detail,
                }
            )
    return items, success_records
