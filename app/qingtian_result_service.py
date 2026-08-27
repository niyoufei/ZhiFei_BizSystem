from __future__ import annotations

from typing import Any, Dict, List, Optional


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
        "qt_dim_scores": qt_dim_scores,
        "qt_reasons": qt_reasons,
        "raw_payload": raw_payload,
        "created_at": created_at,
    }


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
