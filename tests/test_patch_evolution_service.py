from __future__ import annotations

from app import patch_evolution_service


def test_resolved_absence_of_patch_is_not_queried_again() -> None:
    calls = 0

    def load_patch_packages():
        nonlocal calls
        calls += 1
        return [
            {
                "id": "late-patch",
                "project_id": "project-1",
                "status": "deployed",
                "patch_payload": {"penalty_multiplier": {"P1": 2.0}},
            }
        ]

    report = {"penalties": [{"code": "P1", "points": 1.0}]}

    patch_evolution_service.apply_deployed_patch_to_report(
        "project-1",
        report,
        load_patch_packages=load_patch_packages,
        compute_v2_rule_total=lambda _report: 0.0,
        deployed_patch=None,
        deployed_patch_resolved=True,
    )

    assert calls == 0
    assert report["penalties"][0]["points"] == 1.0
