from __future__ import annotations

from types import SimpleNamespace

from app.domain.learning.loop import LearningLoopService
from app.domain.scoring.core import ScoringCoreService


def test_scoring_core_service_uses_injected_storage() -> None:
    storage = SimpleNamespace(
        load_submissions=lambda: [
            {
                "id": "s1",
                "project_id": "p1",
                "filename": "施组A.docx",
                "created_at": "2026-04-12T00:00:00+00:00",
                "report": {
                    "dimension_scores": {
                        "01": {
                            "id": "01",
                            "name": "工程总体部署",
                            "score": 0,
                            "max_score": 6,
                            "evidence": [],
                            "hits": [],
                        },
                        "02": {
                            "id": "02",
                            "name": "安全管理",
                            "score": 4,
                            "max_score": 6,
                            "evidence": ["第12页 安全交底"],
                            "hits": ["安全交底"],
                        },
                    }
                },
            }
        ]
    )

    service = ScoringCoreService(storage=storage)

    snapshot = service.load_submission_snapshot(project_id="p1", submission_id="s1")
    rows = service.build_dimension_coverage_rows(project_id="p1", submission_id="s1")

    assert snapshot["filename"] == "施组A.docx"
    assert rows[0]["dimension_id"] == "01"
    assert rows[0]["has_evidence"] is False
    assert rows[1]["dimension_id"] == "02"
    assert rows[1]["location_hint"] == "第12页 安全交底"


def test_learning_loop_service_uses_injected_storage() -> None:
    storage = SimpleNamespace(
        load_projects=lambda: [
            {
                "id": "p1",
                "name": "项目一",
                "meta": {"score_scale_max": 100},
            }
        ],
        load_ground_truth=lambda: [
            {
                "id": "gt1",
                "project_id": "p1",
                "source_submission_id": "s1",
                "final_score_100": 88,
                "score_scale_max": 100,
                "created_at": "2026-04-12T01:00:00+00:00",
            }
        ],
        load_submissions=lambda: [
            {
                "id": "s1",
                "project_id": "p1",
                "filename": "施组B.docx",
                "created_at": "2026-04-12T00:00:00+00:00",
                "report": {
                    "pred_total_score": 72,
                    "dimension_scores": {
                        "01": {
                            "id": "01",
                            "name": "总体部署",
                            "score": 4,
                            "max_score": 6,
                            "evidence": ["第3页 总体部署"],
                            "hits": ["总体部署"],
                        },
                        "02": {
                            "id": "02",
                            "name": "进度计划",
                            "score": 3,
                            "max_score": 6,
                            "evidence": ["第5页 进度计划网图"],
                            "hits": ["进度计划网图"],
                        },
                    },
                },
            }
        ],
    )

    service = LearningLoopService(storage=storage)

    snapshot = service.build_score_deviation_snapshot(
        project_id="p1",
        ground_truth_id="gt1",
        submission_id="s1",
    )

    assert snapshot["project_name"] == "项目一"
    assert snapshot["actual_score_100"] == 88.0
    assert snapshot["predicted_score_100"] == 72.0
    assert snapshot["delta_score_100"] == 16.0
    assert snapshot["dimension_names"] == ["总体部署", "进度计划"]
