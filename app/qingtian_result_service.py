from __future__ import annotations

from copy import deepcopy
from typing import Any, Callable, Dict, List, Optional

Record = Dict[str, object]
Records = List[Record]
TransactionDecorator = Callable[[Callable[[], Record]], Callable[[], Record]]
TransactionFactory = Callable[..., TransactionDecorator]


def _append_rollback_note(error: BaseException, rollback_error: BaseException) -> None:
    note = (
        "qingtian-result rollback also failed: "
        f"{type(rollback_error).__name__}: {rollback_error}"
    )
    add_note = getattr(error, "add_note", None)
    if callable(add_note):
        add_note(note)
        return
    notes = list(getattr(error, "__notes__", []))
    notes.append(note)
    error.__notes__ = notes


def build_qingtian_result_record(
    *,
    submission_id: str,
    qingtian_model_version: Optional[str],
    project_model_version: object,
    default_model_version: str,
    qt_total_score: float,
    qt_dim_scores: Optional[Dict[str, float]],
    qt_reasons: List[Dict[str, Any]],
    raw_payload: Dict[str, Any],
    record_id: str,
    created_at: str,
) -> Dict[str, object]:
    model_version = str(qingtian_model_version or project_model_version or default_model_version)
    return {
        "id": record_id,
        "submission_id": submission_id,
        "qingtian_model_version": model_version,
        "qt_total_score": float(qt_total_score),
        "qt_dim_scores": deepcopy(qt_dim_scores),
        "qt_reasons": deepcopy(qt_reasons),
        "raw_payload": deepcopy(raw_payload),
        "created_at": created_at,
    }


def commit_qingtian_result(
    *,
    submission_id: str,
    qingtian_model_version: Optional[str],
    default_model_version: str,
    qt_total_score: float,
    qt_dim_scores: Optional[Dict[str, float]],
    qt_reasons: List[Dict[str, Any]],
    raw_payload: Dict[str, Any],
    atomic_json_transaction: TransactionFactory,
    load_submissions: Callable[[], Records],
    find_submission: Callable[[str, Records], Record],
    load_projects: Callable[[], Records],
    save_projects: Callable[[Records], None],
    find_project: Callable[[str, Records], Record],
    load_qingtian_results: Callable[[], Records],
    save_qingtian_results: Callable[[Records], None],
    record_id_factory: Callable[[], str],
    now_iso: Callable[[], str],
) -> Record:
    @atomic_json_transaction("projects", "qingtian_results", "submissions")
    def commit() -> Record:
        submission = find_submission(submission_id, load_submissions())
        project_id = str(submission.get("project_id") or "")
        original_projects = deepcopy(load_projects())
        projects = deepcopy(original_projects)
        project = find_project(project_id, projects)
        original_results = deepcopy(load_qingtian_results())

        record = build_qingtian_result_record(
            submission_id=submission_id,
            qingtian_model_version=qingtian_model_version,
            project_model_version=project.get("qingtian_model_version"),
            default_model_version=default_model_version,
            qt_total_score=qt_total_score,
            qt_dim_scores=qt_dim_scores,
            qt_reasons=qt_reasons,
            raw_payload=raw_payload,
            record_id=record_id_factory(),
            created_at=now_iso(),
        )
        attempted: List[str] = []

        try:
            results = deepcopy(original_results)
            results.append(deepcopy(record))
            attempted.append("qingtian_results")
            save_qingtian_results(results)

            if str(project.get("status") or "") == "scoring_preparation":
                project["status"] = "submitted_to_qingtian"
                project["updated_at"] = now_iso()
                attempted.append("projects")
                save_projects(projects)
        except BaseException as error:
            originals = {
                "projects": original_projects,
                "qingtian_results": original_results,
            }
            savers = {
                "projects": save_projects,
                "qingtian_results": save_qingtian_results,
            }
            for name in reversed(attempted):
                try:
                    savers[name](deepcopy(originals[name]))
                except BaseException as rollback_error:
                    _append_rollback_note(error, rollback_error)
            raise

        return record

    return commit()


def select_latest_qingtian_result(
    results: List[Dict[str, object]],
    *,
    submission_id: str,
) -> Optional[Dict[str, object]]:
    scoped_results = [
        result for result in results if str(result.get("submission_id")) == submission_id
    ]
    if not scoped_results:
        return None
    return sorted(
        scoped_results,
        key=lambda result: str(result.get("created_at", "")),
        reverse=True,
    )[0]
