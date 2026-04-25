from __future__ import annotations

import json
from pathlib import Path

from app.application.services.agents import AgentApplicationService


def _patch_storage_root(monkeypatch, tmp_path: Path):
    from app import storage

    monkeypatch.setattr(storage, "DATA_DIR", tmp_path)
    monkeypatch.setattr(storage, "MATERIALS_DIR", tmp_path / "materials")
    monkeypatch.setattr(storage, "VERSIONED_JSON_DIR", tmp_path / "versions")
    monkeypatch.setattr(storage, "PROJECTS_PATH", tmp_path / "projects.json")
    monkeypatch.setattr(storage, "SUBMISSIONS_PATH", tmp_path / "submissions.json")
    monkeypatch.setattr(storage, "GROUND_TRUTH_PATH", tmp_path / "ground_truth_scores.json")
    return storage


def test_evidence_completeness_agent_reports_candidate_gaps(monkeypatch, tmp_path: Path):
    storage = _patch_storage_root(monkeypatch, tmp_path)
    storage.save_submissions(
        [
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

    service = AgentApplicationService()
    first = service.dry_run(
        agent_name="evidence-completeness",
        payload={
            "project_id": "p1",
            "submission_id": "s1",
            "top_n": 3,
        },
        reuse_cached=False,
    )
    second = service.dry_run(
        agent_name="evidence-completeness",
        payload={
            "project_id": "p1",
            "submission_id": "s1",
            "top_n": 3,
        },
    )

    assert first.audit.status == "success"
    assert second.cached is True
    assert first.output is not None
    assert first.output["submission_id"] == "s1"
    assert first.output["filename"] == "施组A.docx"
    gaps = first.output["candidate_evidence_gaps"]
    assert gaps[0]["dimension_id"] == "01"
    assert gaps[0]["gap_type"] == "missing_evidence"
    assert gaps[0]["severity"] == "high"


def test_score_deviation_analysis_agent_outputs_pending_review_changes(monkeypatch, tmp_path: Path):
    storage = _patch_storage_root(monkeypatch, tmp_path)
    storage.save_projects(
        [
            {
                "id": "p1",
                "name": "项目一",
                "meta": {"score_scale_max": 100},
            }
        ]
    )
    storage.save_submissions(
        [
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
        ]
    )
    storage.save_ground_truth(
        [
            {
                "id": "gt1",
                "project_id": "p1",
                "source_submission_id": "s1",
                "final_score_100": 88,
                "score_scale_max": 100,
                "created_at": "2026-04-12T01:00:00+00:00",
            }
        ]
    )

    service = AgentApplicationService()
    result = service.dry_run(
        agent_name="score-deviation-analysis",
        payload={
            "project_id": "p1",
            "submission_id": "s1",
            "ground_truth_id": "gt1",
        },
        reuse_cached=False,
    )

    assert result.audit.status == "success"
    assert result.output is not None
    assert result.output["delta_score_100"] == 16.0
    assert result.output["supporting_dimensions"] == ["总体部署", "进度计划"]
    assert len(result.output["candidate_changes"]) >= 2
    assert all(item["requires_human_review"] for item in result.output["candidate_changes"])
    assert all(item["apply_allowed"] is False for item in result.output["candidate_changes"])


def test_ops_triage_agent_summarizes_guard_snapshots(tmp_path: Path):
    ops_agents_path = tmp_path / "ops_agents.json"
    doctor_path = tmp_path / "doctor.json"
    soak_path = tmp_path / "soak.json"
    preflight_path = tmp_path / "preflight.json"
    acceptance_path = tmp_path / "acceptance.json"
    ops_agents_path.write_text(
        json.dumps(
            {
                "overall": {"status": "warn"},
                "agents": {"runtime_repair": {"status": "warn"}},
                "recommendations": ["检查运行时告警"],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    doctor_path.write_text(json.dumps({"status": "pass"}, ensure_ascii=False), encoding="utf-8")
    soak_path.write_text(json.dumps({"status": "PASS"}, ensure_ascii=False), encoding="utf-8")
    preflight_path.write_text(
        json.dumps({"status": "watch", "message": "有待确认样本"}, ensure_ascii=False),
        encoding="utf-8",
    )
    acceptance_path.write_text(
        json.dumps({"status": "PASS"}, ensure_ascii=False),
        encoding="utf-8",
    )

    service = AgentApplicationService()
    result = service.dry_run(
        agent_name="ops-triage",
        payload={
            "ops_agents_json_path": str(ops_agents_path),
            "doctor_json_path": str(doctor_path),
            "soak_json_path": str(soak_path),
            "preflight_json_path": str(preflight_path),
            "acceptance_json_path": str(acceptance_path),
        },
        reuse_cached=False,
    )

    assert result.audit.status == "success"
    assert result.output is not None
    assert result.output["overall_status"] == "warn"
    assert len(result.output["diagnostics"]) == 5
    assert any(item["source"] == "trial_preflight" for item in result.output["diagnostics"])
    assert result.output["recommended_actions"]
