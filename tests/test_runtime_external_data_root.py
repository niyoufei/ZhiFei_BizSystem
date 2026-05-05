"""Runtime coverage for QINGTIAN_DATA_DIR external data root."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def test_runtime_latest_routes_read_external_data_root(tmp_path: Path) -> None:
    data_dir = tmp_path / "external-data"
    data_dir.mkdir()
    (data_dir / "projects.json").write_text(
        json.dumps([{"id": "p1", "name": "Synthetic Runtime Project"}]),
        encoding="utf-8",
    )
    (data_dir / "submissions.json").write_text(
        json.dumps(
            [
                {
                    "id": "tmp-runtime-s1",
                    "project_id": "p1",
                    "filename": "tmp-runtime-submission.md",
                    "text": "Synthetic runtime submission with measurable item 100.",
                    "created_at": "2026-01-01T00:00:00Z",
                    "report": {
                        "scoring_status": "scored",
                        "rule_total_score": 88,
                        "meta": {
                            "evidence_trace": {
                                "total_requirements": 1,
                                "total_hits": 1,
                            },
                            "input_injection": {
                                "mece_inputs": {
                                    "materials_quality_gate_passed": True,
                                },
                            },
                            "material_quality": {"status": "ok"},
                            "material_retrieval": {"used": False},
                            "material_utilization": {"status": "not_applicable"},
                            "material_utilization_gate": {
                                "passed": True,
                                "reasons": [],
                            },
                        },
                        "requirement_hits": [],
                    },
                }
            ],
        ),
        encoding="utf-8",
    )

    repo_root = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env["QINGTIAN_DATA_DIR"] = str(data_dir)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONPATH"] = (
        str(repo_root)
        if not env.get("PYTHONPATH")
        else f"{repo_root}{os.pathsep}{env['PYTHONPATH']}"
    )

    script = r"""
from pathlib import Path
import os

from fastapi.testclient import TestClient

from app import storage
from app.main import app

expected_data_dir = Path(os.environ["QINGTIAN_DATA_DIR"]).expanduser().resolve()
print(f"STORAGE_DATA_DIR={storage.DATA_DIR}")
print(f"DATA_DIR_MATCH={storage.DATA_DIR == expected_data_dir}")
print(f"SUBMISSIONS_PATH_MATCH={storage.SUBMISSIONS_PATH == expected_data_dir / 'submissions.json'}")

client = TestClient(app)
evidence = client.get("/api/v1/projects/p1/evidence_trace/latest")
scoring_basis = client.get("/api/v1/projects/p1/scoring_basis/latest")
print(f"EVIDENCE_STATUS={evidence.status_code}")
print(f"SCORING_BASIS_STATUS={scoring_basis.status_code}")
print(f"EVIDENCE_BODY={evidence.text[:300]}")
print(f"SCORING_BASIS_BODY={scoring_basis.text[:300]}")

assert evidence.status_code == 200
assert scoring_basis.status_code == 200

evidence_payload = evidence.json()
scoring_basis_payload = scoring_basis.json()
print(f"EVIDENCE_SUBMISSION_ID={evidence_payload.get('submission_id')}")
print(f"SCORING_BASIS_SUBMISSION_ID={scoring_basis_payload.get('submission_id')}")
print(f"SCORING_STATUS={scoring_basis_payload.get('scoring_status')}")

assert evidence_payload["submission_id"] == "tmp-runtime-s1"
assert scoring_basis_payload["submission_id"] == "tmp-runtime-s1"
assert scoring_basis_payload["scoring_status"] == "scored"
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=repo_root,
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "DATA_DIR_MATCH=True" in result.stdout
    assert "SUBMISSIONS_PATH_MATCH=True" in result.stdout
    assert "EVIDENCE_STATUS=200" in result.stdout
    assert "SCORING_BASIS_STATUS=200" in result.stdout
    assert "EVIDENCE_SUBMISSION_ID=tmp-runtime-s1" in result.stdout
    assert "SCORING_BASIS_SUBMISSION_ID=tmp-runtime-s1" in result.stdout
    assert "SCORING_STATUS=scored" in result.stdout
