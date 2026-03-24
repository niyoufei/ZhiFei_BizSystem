from __future__ import annotations

from app.engine import ops_agents as oa


def test_run_ops_agents_cycle_short_circuit_on_sre_fail(monkeypatch):
    monkeypatch.setattr(
        oa,
        "_run_sre_watchdog",
        lambda **kwargs: {
            "name": "sre_watchdog",
            "status": "fail",
            "duration_ms": 1,
            "checks": {},
            "actions": {},
            "recommendations": ["sre failed"],
        },
    )

    result = oa.run_ops_agents_cycle(base_url="http://127.0.0.1:8000", max_workers=1)
    assert result["overall"]["status"] == "fail"
    assert result["overall"]["fail_count"] >= 1
    assert result["agents"]["sre_watchdog"]["status"] == "fail"
    assert result["agents"]["data_hygiene"]["status"] == "fail"
    assert result["agents"]["project_flow"]["status"] == "fail"
    assert result["agents"]["tender_project_flow"]["status"] == "fail"
    assert result["agents"]["upload_flow"]["status"] == "fail"
    assert result["agents"]["scoring_quality"]["status"] == "fail"
    assert result["agents"]["evolution"]["status"] == "fail"


def test_run_ops_agents_cycle_warn_from_sub_agents(monkeypatch):
    monkeypatch.setattr(
        oa,
        "_run_sre_watchdog",
        lambda **kwargs: {
            "name": "sre_watchdog",
            "status": "pass",
            "duration_ms": 1,
            "checks": {},
            "actions": {},
            "recommendations": [],
        },
    )
    monkeypatch.setattr(
        oa,
        "_run_data_hygiene_agent",
        lambda **kwargs: {
            "name": "data_hygiene",
            "status": "pass",
            "duration_ms": 1,
            "checks": {},
            "actions": {},
            "metrics": {},
            "recommendations": [],
        },
    )
    monkeypatch.setattr(
        oa,
        "_run_project_flow_agent",
        lambda **kwargs: {
            "name": "project_flow",
            "status": "pass",
            "duration_ms": 1,
            "checks": {},
            "actions": {},
            "metrics": {},
            "recommendations": [],
        },
    )
    monkeypatch.setattr(
        oa,
        "_run_scoring_quality_agent",
        lambda **kwargs: {
            "name": "scoring_quality",
            "status": "warn",
            "duration_ms": 1,
            "checks": {},
            "actions": {},
            "metrics": {},
            "recommendations": ["quality watch"],
        },
    )
    monkeypatch.setattr(
        oa,
        "_run_tender_project_flow_agent",
        lambda **kwargs: {
            "name": "tender_project_flow",
            "status": "pass",
            "duration_ms": 1,
            "checks": {},
            "actions": {},
            "metrics": {},
            "recommendations": [],
        },
    )
    monkeypatch.setattr(
        oa,
        "_run_upload_flow_agent",
        lambda **kwargs: {
            "name": "upload_flow",
            "status": "pass",
            "duration_ms": 1,
            "checks": {},
            "actions": {},
            "metrics": {},
            "recommendations": [],
        },
    )
    monkeypatch.setattr(
        oa,
        "_run_evolution_agent",
        lambda **kwargs: {
            "name": "evolution",
            "status": "pass",
            "duration_ms": 1,
            "checks": {},
            "actions": {},
            "metrics": {},
            "recommendations": [],
        },
    )

    result = oa.run_ops_agents_cycle(base_url="http://127.0.0.1:8000", max_workers=3)
    assert result["overall"]["status"] == "warn"
    assert result["overall"]["warn_count"] == 1
    assert result["overall"]["fail_count"] == 0
    assert "quality watch" in result["recommendations"]
    assert result["agent_count"] == 7


def test_scoring_quality_treats_preparation_critical_as_non_failure():
    def fake_requester(**kwargs):
        url = str(kwargs.get("url") or "")
        if url.endswith("/api/v1/projects"):
            return {
                "ok": True,
                "status_code": 200,
                "elapsed_ms": 1,
                "json": [
                    {
                        "id": "p1",
                        "name": "项目1",
                        "status": "scoring_preparation",
                    }
                ],
                "error": None,
            }
        if url.endswith("/api/v1/projects/p1/mece_audit"):
            return {
                "ok": True,
                "status_code": 200,
                "elapsed_ms": 1,
                "json": {
                    "overall": {"level": "critical"},
                    "summary": {"submission_total": 0, "submission_scored": 0},
                },
                "error": None,
            }
        raise AssertionError(f"unexpected url: {url}")

    result = oa._run_scoring_quality_agent(
        base_url="http://127.0.0.1:8000",
        api_key=None,
        timeout=5.0,
        requester=fake_requester,
    )
    assert result["status"] == "pass"
    assert result["metrics"]["critical_count"] == 0
    assert result["metrics"]["preparation_critical_count"] == 1


def test_project_flow_agent_smoke_success():
    state = {"created": False, "deleted": False}

    deleted_ids: list[str] = []

    def fake_requester(**kwargs):
        method = str(kwargs.get("method") or "")
        url = str(kwargs.get("url") or "")
        payload = kwargs.get("payload") or {}
        if method == "GET" and url.endswith("/api/v1/projects"):
            rows = [{"id": "p1", "name": "项目1"}]
            if state["created"] and not state["deleted"]:
                rows.append({"id": "ops-p1", "name": "OPS_SMOKE_TEST"})
            return {
                "ok": True,
                "status_code": 200,
                "elapsed_ms": 1,
                "json": rows,
                "error": None,
            }
        if method == "POST" and url.endswith("/api/v1/projects"):
            state["created"] = True
            return {
                "ok": True,
                "status_code": 200,
                "elapsed_ms": 1,
                "json": {"id": "ops-p1", "name": payload.get("name")},
                "error": None,
            }
        if method == "DELETE" and url.endswith("/api/v1/projects/ops-p1"):
            deleted_ids.append("ops-p1")
            state["deleted"] = True
            return {
                "ok": True,
                "status_code": 204,
                "elapsed_ms": 1,
                "json": {},
                "error": None,
            }
        raise AssertionError(f"unexpected request: {method} {url}")

    result = oa._run_project_flow_agent(
        base_url="http://127.0.0.1:8000",
        api_key=None,
        timeout=5.0,
        requester=fake_requester,
    )
    assert result["status"] == "pass"
    assert result["metrics"]["created_ok"] == 1
    assert result["metrics"]["listed_after_create"] == 1
    assert result["metrics"]["delete_ok"] == 1
    assert result["metrics"]["removed_after_delete"] == 1


def test_tender_project_flow_agent_smoke_success():
    state = {"created": False, "deleted": False}

    def fake_requester(**kwargs):
        method = str(kwargs.get("method") or "")
        url = str(kwargs.get("url") or "")
        files = kwargs.get("files") or []
        if method == "POST" and url.endswith("/api/v1/projects/create_from_tender"):
            assert files and files[0]["filename"] == "ops_tender_smoke.txt"
            state["created"] = True
            return {
                "ok": True,
                "status_code": 200,
                "elapsed_ms": 1200,
                "json": {
                    "project": {"id": "ops-tender-p1", "name": "OPS招标项目_1"},
                    "inferred_name": "OPS招标项目_1",
                },
                "error": None,
            }
        if method == "GET" and url.endswith("/api/v1/projects"):
            rows = [{"id": "p1", "name": "项目1"}]
            if state["created"] and not state["deleted"]:
                rows.append({"id": "ops-tender-p1", "name": "OPS招标项目_1"})
            return {
                "ok": True,
                "status_code": 200,
                "elapsed_ms": 1,
                "json": rows,
                "error": None,
            }
        if method == "DELETE" and url.endswith("/api/v1/projects/ops-tender-p1"):
            state["deleted"] = True
            return {
                "ok": True,
                "status_code": 204,
                "elapsed_ms": 1,
                "json": {},
                "error": None,
            }
        raise AssertionError(f"unexpected request: {method} {url}")

    original_time = oa.time.time
    oa.time.time = lambda: 0.001
    try:
        result = oa._run_tender_project_flow_agent(
            base_url="http://127.0.0.1:8000",
            api_key=None,
            timeout=5.0,
            requester=fake_requester,
        )
    finally:
        oa.time.time = original_time
    assert result["status"] == "pass"
    assert result["metrics"]["created_ok"] == 1
    assert result["metrics"]["inferred_ok"] == 1
    assert result["metrics"]["elapsed_ok"] == 1
    assert result["metrics"]["listed_after_create"] == 1
    assert result["metrics"]["delete_ok"] == 1
    assert result["metrics"]["removed_after_delete"] == 1


def test_upload_flow_agent_smoke_success():
    state = {"created": False, "deleted": False, "material": False, "submission": False}

    def fake_requester(**kwargs):
        method = str(kwargs.get("method") or "")
        url = str(kwargs.get("url") or "")
        if method == "POST" and url.endswith("/api/v1/projects"):
            state["created"] = True
            return {
                "ok": True,
                "status_code": 200,
                "elapsed_ms": 1,
                "json": {"id": "ops-upload-p1", "name": "OPS上传项目_1"},
                "error": None,
            }
        if method == "POST" and url.endswith("/api/v1/projects/ops-upload-p1/materials"):
            state["material"] = True
            return {
                "ok": True,
                "status_code": 200,
                "elapsed_ms": 1,
                "json": {"material": {"id": "m1", "filename": "ops_material.txt"}},
                "error": None,
            }
        if method == "GET" and url.endswith("/api/v1/projects/ops-upload-p1/materials"):
            return {
                "ok": True,
                "status_code": 200,
                "elapsed_ms": 1,
                "json": [{"id": "m1", "filename": "ops_material.txt"}] if state["material"] else [],
                "error": None,
            }
        if method == "POST" and url.endswith("/api/v1/projects/ops-upload-p1/shigong"):
            state["submission"] = True
            return {
                "ok": True,
                "status_code": 200,
                "elapsed_ms": 1,
                "json": {"id": "s1", "filename": "ops_shigong.txt"},
                "error": None,
            }
        if method == "GET" and url.endswith("/api/v1/projects/ops-upload-p1/submissions"):
            return {
                "ok": True,
                "status_code": 200,
                "elapsed_ms": 1,
                "json": [{"id": "s1", "filename": "ops_shigong.txt"}]
                if state["submission"]
                else [],
                "error": None,
            }
        if method == "DELETE" and url.endswith("/api/v1/projects/ops-upload-p1"):
            state["deleted"] = True
            return {
                "ok": True,
                "status_code": 204,
                "elapsed_ms": 1,
                "json": {},
                "error": None,
            }
        if method == "GET" and url.endswith("/api/v1/projects"):
            rows = [{"id": "p1", "name": "项目1"}]
            if state["created"] and not state["deleted"]:
                rows.append({"id": "ops-upload-p1", "name": "OPS上传项目_1"})
            return {
                "ok": True,
                "status_code": 200,
                "elapsed_ms": 1,
                "json": rows,
                "error": None,
            }
        raise AssertionError(f"unexpected request: {method} {url}")

    original_time = oa.time.time
    oa.time.time = lambda: 0.001
    try:
        result = oa._run_upload_flow_agent(
            base_url="http://127.0.0.1:8000",
            api_key=None,
            timeout=5.0,
            requester=fake_requester,
        )
    finally:
        oa.time.time = original_time
    assert result["status"] == "pass"
    assert result["metrics"]["created_ok"] == 1
    assert result["metrics"]["material_upload_ok"] == 1
    assert result["metrics"]["material_listed"] == 1
    assert result["metrics"]["shigong_upload_ok"] == 1
    assert result["metrics"]["submission_listed"] == 1
    assert result["metrics"]["delete_ok"] == 1
    assert result["metrics"]["removed_after_delete"] == 1
