from __future__ import annotations

from typing import Any, Protocol


class ScoringCoreStorage(Protocol):
    def load_submissions(self) -> list[dict[str, Any]]:
        ...


def _safe_float(value: object, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _report_dimension_rows(report: dict[str, Any]) -> list[dict[str, Any]]:
    raw = report.get("dimension_scores")
    if isinstance(raw, dict):
        return [item for item in raw.values() if isinstance(item, dict)]
    raw = report.get("rule_dim_scores")
    if isinstance(raw, dict):
        rows: list[dict[str, Any]] = []
        for dim_id, value in raw.items():
            if not isinstance(value, dict):
                continue
            row = dict(value)
            row.setdefault("id", str(dim_id))
            rows.append(row)
        return rows
    return []


class ScoringCoreService:
    def __init__(self, *, storage: ScoringCoreStorage | None = None):
        if storage is None:
            from app.bootstrap.storage import get_storage_access

            storage = get_storage_access()
        self.storage = storage

    def load_submission_snapshot(
        self,
        *,
        project_id: str,
        submission_id: str | None = None,
    ) -> dict[str, Any]:
        submissions = [
            dict(row)
            for row in self.storage.load_submissions()
            if isinstance(row, dict) and str(row.get("project_id") or "") == str(project_id or "")
        ]
        if not submissions:
            raise ValueError("submission_not_found")
        if submission_id:
            for row in submissions:
                if str(row.get("id") or "") == str(submission_id):
                    return row
            raise ValueError("submission_not_found")
        submissions.sort(
            key=lambda row: (
                str(row.get("created_at") or ""),
                str(row.get("id") or ""),
            ),
            reverse=True,
        )
        return submissions[0]

    def build_dimension_coverage_rows(
        self,
        *,
        project_id: str,
        submission_id: str | None = None,
    ) -> list[dict[str, Any]]:
        submission = self.load_submission_snapshot(
            project_id=project_id, submission_id=submission_id
        )
        report = submission.get("report")
        report = dict(report) if isinstance(report, dict) else {}
        rows: list[dict[str, Any]] = []
        for item in _report_dimension_rows(report):
            evidence = item.get("evidence")
            hits = item.get("hits")
            evidence_list = evidence if isinstance(evidence, list) else []
            hits_list = hits if isinstance(hits, list) else []
            score = _safe_float(item.get("score"))
            max_score = max(_safe_float(item.get("max_score"), 1.0), 1.0)
            ratio = score / max_score if max_score > 0 else 0.0
            location_hint = ""
            if evidence_list:
                location_hint = str(evidence_list[0] or "").strip()
            elif hits_list:
                location_hint = str(hits_list[0] or "").strip()
            if not location_hint:
                location_hint = str(submission.get("filename") or "inline")
            rows.append(
                {
                    "dimension_id": str(item.get("id") or ""),
                    "dimension_name": str(item.get("name") or item.get("id") or ""),
                    "score": round(score, 2),
                    "max_score": round(max_score, 2),
                    "score_ratio": round(ratio, 4),
                    "evidence_count": len(evidence_list),
                    "hit_count": len(hits_list),
                    "location_hint": location_hint,
                    "has_evidence": bool(evidence_list or hits_list),
                }
            )
        rows.sort(
            key=lambda row: (
                int(row["evidence_count"] > 0),
                row["score_ratio"],
                row["dimension_id"],
            )
        )
        return rows
