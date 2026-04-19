from __future__ import annotations

from typing import Any, Protocol


class LearningLoopStorage(Protocol):
    def load_projects(self) -> list[dict[str, Any]]:
        ...

    def load_ground_truth(self) -> list[dict[str, Any]]:
        ...

    def load_submissions(self) -> list[dict[str, Any]]:
        ...


def _safe_float(value: object) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalize_score_scale_max(value: object, default: int = 100) -> int:
    try:
        raw = int(float(value))
    except (TypeError, ValueError):
        return default
    return 5 if raw == 5 else 100


def _convert_score_to_100(value: object, score_scale_max: int) -> float | None:
    score = _safe_float(value)
    if score is None:
        return None
    scale = float(_normalize_score_scale_max(score_scale_max))
    if scale <= 0:
        return None
    normalized = max(0.0, min(100.0, score / scale * 100.0))
    return round(normalized, 2)


def _project_score_scale_max(project: dict[str, Any] | None) -> int:
    meta = project.get("meta") if isinstance(project, dict) else {}
    meta = meta if isinstance(meta, dict) else {}
    return _normalize_score_scale_max(meta.get("score_scale_max"), default=100)


def _report_dimension_names(report: dict[str, Any]) -> list[str]:
    rows = report.get("dimension_scores")
    if isinstance(rows, dict):
        ordered = []
        for value in rows.values():
            if not isinstance(value, dict):
                continue
            name = str(value.get("name") or value.get("id") or "").strip()
            if name:
                ordered.append(name)
        return ordered
    rows = report.get("rule_dim_scores")
    if isinstance(rows, dict):
        ordered = []
        for key, value in rows.items():
            if isinstance(value, dict):
                name = str(value.get("name") or value.get("id") or key or "").strip()
            else:
                name = str(key or "").strip()
            if name:
                ordered.append(name)
        return ordered
    return []


class LearningLoopService:
    def __init__(self, *, storage: LearningLoopStorage | None = None):
        if storage is None:
            from app.bootstrap.storage import get_storage_access

            storage = get_storage_access()
        self.storage = storage

    def load_project_snapshot(self, *, project_id: str) -> dict[str, Any]:
        for row in self.storage.load_projects():
            if isinstance(row, dict) and str(row.get("id") or "") == str(project_id or ""):
                return dict(row)
        raise ValueError("project_not_found")

    def load_ground_truth_snapshot(
        self,
        *,
        project_id: str,
        ground_truth_id: str | None = None,
    ) -> dict[str, Any]:
        rows = [
            dict(row)
            for row in self.storage.load_ground_truth()
            if isinstance(row, dict) and str(row.get("project_id") or "") == str(project_id or "")
        ]
        if not rows:
            raise ValueError("ground_truth_not_found")
        if ground_truth_id:
            for row in rows:
                if str(row.get("id") or "") == str(ground_truth_id):
                    return row
            raise ValueError("ground_truth_not_found")
        rows.sort(
            key=lambda row: (
                str(row.get("updated_at") or row.get("created_at") or ""),
                str(row.get("id") or ""),
            ),
            reverse=True,
        )
        return rows[0]

    def load_submission_snapshot(
        self,
        *,
        project_id: str,
        submission_id: str | None = None,
        linked_ground_truth: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        rows = [
            dict(row)
            for row in self.storage.load_submissions()
            if isinstance(row, dict) and str(row.get("project_id") or "") == str(project_id or "")
        ]
        if not rows:
            raise ValueError("submission_not_found")
        linked_submission_id = str(
            (linked_ground_truth or {}).get("source_submission_id") or ""
        ).strip()
        target_id = str(submission_id or "").strip() or linked_submission_id
        if target_id:
            for row in rows:
                if str(row.get("id") or "") == target_id:
                    return row
            raise ValueError("submission_not_found")
        rows.sort(
            key=lambda row: (
                str(row.get("created_at") or ""),
                str(row.get("id") or ""),
            ),
            reverse=True,
        )
        return rows[0]

    def build_score_deviation_snapshot(
        self,
        *,
        project_id: str,
        ground_truth_id: str | None = None,
        submission_id: str | None = None,
    ) -> dict[str, Any]:
        project = self.load_project_snapshot(project_id=project_id)
        gt_row = self.load_ground_truth_snapshot(
            project_id=project_id, ground_truth_id=ground_truth_id
        )
        submission = self.load_submission_snapshot(
            project_id=project_id,
            submission_id=submission_id,
            linked_ground_truth=gt_row,
        )
        project_scale = _project_score_scale_max(project)
        gt_scale = _normalize_score_scale_max(gt_row.get("score_scale_max"), default=project_scale)
        actual_score_100 = _safe_float(gt_row.get("final_score_100"))
        if actual_score_100 is None:
            raw_actual = _safe_float(gt_row.get("final_score_raw"))
            if raw_actual is None:
                raw_actual = _safe_float(gt_row.get("final_score"))
            actual_score_100 = _convert_score_to_100(raw_actual, gt_scale)
        report = submission.get("report")
        report = dict(report) if isinstance(report, dict) else {}
        predicted_score_100 = _safe_float(report.get("pred_total_score"))
        if predicted_score_100 is None:
            predicted_score_100 = _safe_float(report.get("total_score"))
        if predicted_score_100 is None:
            predicted_score_100 = _safe_float(report.get("rule_total_score"))
        if predicted_score_100 is None:
            predicted_score_100 = _safe_float(submission.get("total_score"))
        if predicted_score_100 is None:
            raise ValueError("predicted_score_not_found")
        if project_scale == 5 and predicted_score_100 <= 5.0:
            predicted_score_100 = _convert_score_to_100(predicted_score_100, 5)
        if actual_score_100 is None:
            raise ValueError("actual_score_not_found")
        delta_score_100 = round(float(actual_score_100) - float(predicted_score_100), 2)
        delta_ratio = round(abs(delta_score_100) / 100.0, 4)
        penalty_count = (
            len(report.get("penalties") or []) if isinstance(report.get("penalties"), list) else 0
        )
        dimension_names = _report_dimension_names(report)
        return {
            "project_id": project_id,
            "project_name": str(project.get("name") or project_id),
            "ground_truth_id": str(gt_row.get("id") or ""),
            "submission_id": str(submission.get("id") or ""),
            "submission_filename": str(submission.get("filename") or ""),
            "actual_score_100": round(float(actual_score_100), 2),
            "predicted_score_100": round(float(predicted_score_100), 2),
            "delta_score_100": delta_score_100,
            "delta_ratio": delta_ratio,
            "penalty_count": int(penalty_count),
            "dimension_names": dimension_names[:6],
            "project_score_scale_max": int(project_scale),
            "ground_truth_score_scale_max": int(gt_scale),
        }
