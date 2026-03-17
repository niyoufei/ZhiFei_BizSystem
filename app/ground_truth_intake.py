from __future__ import annotations

from typing import Dict, List, Tuple

from fastapi import HTTPException


def _main():
    import app.main as main_mod

    return main_mod


def _resolve_project_score_context(
    project_id: str, *, locale: str
) -> Tuple[Dict[str, object], int]:
    main = _main()
    projects = main.load_projects()
    project = next((p for p in projects if p["id"] == project_id), None)
    if project is None:
        raise HTTPException(status_code=404, detail=main.t("api.project_not_found", locale=locale))
    score_scale_max = main._resolve_project_score_scale_max(project)
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
) -> Dict[str, object]:
    _, score_scale_max = _resolve_project_score_context(project_id, locale=locale)
    if len((shigong_text or "").strip()) < 50:
        raise HTTPException(status_code=422, detail="施组全文过短，至少 50 字以便学习分析。")
    _main()._assert_valid_final_score(final_score, score_scale_max=score_scale_max)
    return _main()._new_ground_truth_record(
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
) -> Dict[str, object]:
    main = _main()
    _, score_scale_max = _resolve_project_score_context(project_id, locale=locale)
    main._assert_valid_final_score(final_score, score_scale_max=score_scale_max)

    submission_id = str(submission_id or "").strip()
    submissions = main.load_submissions()
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

    record = main._new_ground_truth_record(
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
) -> Dict[str, object]:
    main = _main()
    judge_scores_list = main._parse_judge_scores_form(judge_scores_form)
    shigong_text = main._read_uploaded_file_content(content, filename or "")
    return build_ground_truth_record(
        project_id,
        shigong_text=shigong_text,
        judge_scores=judge_scores_list,
        final_score=final_score,
        source=source,
        locale=locale,
    )


def build_ground_truth_batch_items_from_uploaded_files(
    project_id: str,
    *,
    uploads: List[Tuple[str, bytes]],
    judge_scores_form: str,
    final_score: float,
    source: str,
    locale: str,
) -> Tuple[List[Dict[str, object]], List[Dict[str, object]]]:
    main = _main()
    _, score_scale_max = _resolve_project_score_context(project_id, locale=locale)
    judge_scores_list = main._parse_judge_scores_form(judge_scores_form)
    main._assert_valid_final_score(final_score, score_scale_max=score_scale_max)

    items: List[Dict[str, object]] = []
    success_records: List[Dict[str, object]] = []
    for filename, content in uploads:
        clean_filename = filename or "unknown"
        try:
            shigong_text = main._read_uploaded_file_content(content, clean_filename)
            if len(shigong_text.strip()) < 50:
                raise ValueError("施组全文过短，至少 50 字以便学习分析。")
            record = main._new_ground_truth_record(
                project_id=project_id,
                shigong_text=shigong_text,
                judge_scores=judge_scores_list,
                final_score=final_score,
                source=source,
                score_scale_max=score_scale_max,
                judge_weights=None,
                qualitative_tags_by_judge=None,
            )
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
