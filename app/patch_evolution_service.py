from __future__ import annotations

import copy
from typing import Any, Callable, Dict, List, Optional

Record = Dict[str, object]
Records = List[Record]
Callback = Callable[..., Any]


class PatchNotFoundError(LookupError):
    pass


class PatchDeltaCasesNotFoundError(LookupError):
    pass


class UnsupportedPatchActionError(ValueError):
    pass


class InvalidPatchTransitionError(ValueError):
    pass


def select_deployed_patch(
    project_id: str,
    *,
    load_patch_packages: Callable[[], Records],
) -> Optional[Record]:
    packages = [
        patch
        for patch in load_patch_packages()
        if str(patch.get("project_id")) == project_id and str(patch.get("status")) == "deployed"
    ]
    if not packages:
        return None
    packages.sort(key=lambda item: str(item.get("updated_at", "")), reverse=True)
    return packages[0]


def auto_govern_deployed_patch(
    *,
    project_id: str,
    delta_cases: Records,
    load_patch_packages: Callable[[], Records],
    save_patch_packages: Callable[[Records], None],
    load_patch_deployments: Callable[[], Records],
    save_patch_deployments: Callable[[Records], None],
    evaluate_patch_shadow: Callback,
    to_float_or_none: Callback,
    now_iso: Callable[[], str],
    new_id: Callable[[], str],
) -> Record:
    result: Record = {
        "checked": False,
        "project_id": project_id,
        "patch_id": None,
        "gate_passed": None,
        "sample_count": 0,
        "action": "skip",
        "reason": "no_deployed_patch",
        "rolled_back": False,
        "rollback_to_patch_id": None,
        "metrics_before_after": {},
        "deployment_record_ids": [],
    }
    if not delta_cases:
        result["reason"] = "no_delta_cases"
        return result

    packages = load_patch_packages()
    deployed = [
        patch
        for patch in packages
        if str(patch.get("project_id")) == project_id and str(patch.get("status")) == "deployed"
    ]
    if not deployed:
        return result

    deployed = sorted(
        deployed,
        key=lambda item: str(item.get("updated_at", "")),
        reverse=True,
    )
    patch = deployed[0]
    patch_id = str(patch.get("id") or "")
    result["checked"] = True
    result["patch_id"] = patch_id

    shadow = evaluate_patch_shadow(patch=patch, delta_cases=delta_cases)
    metrics = shadow.get("metrics_before_after") or {}
    sample_count = int(to_float_or_none(metrics.get("sample_count")) or len(delta_cases) or 0)
    gate_passed = bool(shadow.get("gate_passed"))
    result["sample_count"] = sample_count
    result["gate_passed"] = gate_passed
    result["metrics_before_after"] = metrics

    min_rollback_samples = 3
    if gate_passed:
        result["action"] = "keep"
        result["reason"] = "shadow_passed"
        return result
    if sample_count < min_rollback_samples:
        result["action"] = "skip"
        result["reason"] = "insufficient_samples_for_rollback"
        return result

    timestamp = now_iso()
    rollback_pointer = str(patch.get("rollback_pointer") or "").strip()
    rollback_target = None
    if rollback_pointer:
        rollback_target = next(
            (
                candidate
                for candidate in packages
                if str(candidate.get("id") or "") == rollback_pointer
                and str(candidate.get("project_id") or "") == project_id
            ),
            None,
        )

    for row in packages:
        if str(row.get("project_id") or "") != project_id:
            continue
        if str(row.get("status") or "") == "deployed":
            row["status"] = "shadow_pass"
            row["updated_at"] = timestamp

    patch["status"] = "rolled_back"
    patch["updated_at"] = timestamp

    rollback_to_patch_id: Optional[str] = None
    if rollback_target is not None:
        rollback_target["status"] = "deployed"
        rollback_target["updated_at"] = timestamp
        rollback_to_patch_id = str(rollback_target.get("id") or "")

    save_patch_packages(packages)

    deployment_record_ids: List[str] = []
    deployments = load_patch_deployments()
    rollback_record = {
        "id": new_id(),
        "patch_id": patch_id,
        "project_id": project_id,
        "action": "auto_rollback",
        "deployed": False,
        "metrics_before_after": metrics,
        "rollback_to_version": rollback_to_patch_id or rollback_pointer or None,
        "created_at": timestamp,
    }
    deployments.append(rollback_record)
    deployment_record_ids.append(str(rollback_record["id"]))
    if rollback_to_patch_id:
        promote_record = {
            "id": new_id(),
            "patch_id": rollback_to_patch_id,
            "project_id": project_id,
            "action": "auto_promote_rollback_pointer",
            "deployed": True,
            "metrics_before_after": metrics,
            "rollback_to_version": None,
            "created_at": timestamp,
        }
        deployments.append(promote_record)
        deployment_record_ids.append(str(promote_record["id"]))
    save_patch_deployments(deployments)

    result["action"] = "rollback"
    result["reason"] = "shadow_failed"
    result["rolled_back"] = True
    result["rollback_to_patch_id"] = rollback_to_patch_id
    result["deployment_record_ids"] = deployment_record_ids
    return result


def apply_deployed_patch_to_report(
    project_id: str,
    report: Record,
    *,
    load_patch_packages: Callable[[], Records],
    compute_v2_rule_total: Callback,
    deployed_patch: Optional[Record] = None,
    deployed_patch_resolved: bool = False,
) -> None:
    patch = deployed_patch
    if not deployed_patch_resolved:
        patch = select_deployed_patch(
            project_id,
            load_patch_packages=load_patch_packages,
        )
    if not patch:
        return
    payload = patch.get("patch_payload") or {}
    penalties = report.get("penalties")
    if not isinstance(penalties, list) or not penalties:
        report.setdefault("meta", {})
        report["meta"]["patch_id"] = patch.get("id")
        return

    multipliers = payload.get("penalty_multiplier") or {}
    old_penalty_total = sum(
        float(penalty.get("points", penalty.get("deduct", 0.0)))
        for penalty in penalties
        if isinstance(penalty, dict)
    )
    new_penalty_total = 0.0
    for penalty in penalties:
        if not isinstance(penalty, dict):
            continue
        code = str(penalty.get("code") or "")
        multiplier = float(multipliers.get(code, 1.0)) if code else 1.0
        if "points" in penalty and penalty.get("points") is not None:
            penalty["points"] = round(float(penalty.get("points", 0.0)) * multiplier, 2)
            new_penalty_total += float(penalty["points"])
        elif "deduct" in penalty and penalty.get("deduct") is not None:
            penalty["deduct"] = round(float(penalty.get("deduct", 0.0)) * multiplier, 2)
            new_penalty_total += float(penalty["deduct"])
        else:
            new_penalty_total += 0.0

    has_dim_components = ("dim_total_90" in report) or ("dim_total_80" in report)
    if has_dim_components:
        dim_total_80 = float(report.get("dim_total_80", 0.0))
        dim_total_90 = report.get("dim_total_90")
        if dim_total_90 is not None:
            dim_total_80 = max(0.0, min(80.0, float(dim_total_90) * (80.0 / 90.0)))
        consistency_bonus = float(report.get("consistency_bonus", 0.0))
        new_rule_total, normalized_dim_total_90 = compute_v2_rule_total(
            dim_total_80=dim_total_80,
            consistency_bonus=consistency_bonus,
            penalty_points=new_penalty_total,
        )
        report["rule_total_score"] = new_rule_total
        report["total_score"] = new_rule_total
        report["dim_total_80"] = round(dim_total_80, 2)
        report["dim_total_90"] = normalized_dim_total_90
    else:
        old_total = float(report.get("rule_total_score", report.get("total_score", 0.0)))
        delta = new_penalty_total - old_penalty_total
        new_total = max(0.0, min(100.0, round(old_total - delta, 2)))
        report["rule_total_score"] = new_total
        report["total_score"] = new_total

    report.setdefault("meta", {})
    report["meta"]["patch_id"] = patch.get("id")
    report["meta"]["patch_status"] = patch.get("status")


def mine_patch(
    *,
    project_id: str,
    patch_type: str,
    top_k: int,
    load_delta_cases: Callable[[], Records],
    load_patch_packages: Callable[[], Records],
    save_patch_packages: Callable[[Records], None],
    mine_patch_package: Callback,
) -> Record:
    delta_cases = [case for case in load_delta_cases() if str(case.get("project_id")) == project_id]
    if not delta_cases:
        raise PatchDeltaCasesNotFoundError

    packages = load_patch_packages()
    rollback_pointer = None
    deployed = [
        patch
        for patch in packages
        if str(patch.get("project_id")) == project_id and str(patch.get("status")) == "deployed"
    ]
    if deployed:
        deployed = sorted(
            deployed,
            key=lambda item: str(item.get("updated_at", "")),
            reverse=True,
        )
        rollback_pointer = str(deployed[0].get("id") or "")

    package = mine_patch_package(
        project_id=project_id,
        delta_cases=delta_cases,
        patch_type=patch_type,
        top_k=top_k,
        rollback_pointer=rollback_pointer,
    )
    packages.append(package)
    save_patch_packages(packages)
    return package


def list_patches(
    *,
    project_id: str,
    load_patch_packages: Callable[[], Records],
) -> Records:
    rows = [patch for patch in load_patch_packages() if str(patch.get("project_id")) == project_id]
    return sorted(rows, key=lambda item: str(item.get("updated_at", "")), reverse=True)


def shadow_eval_patch(
    *,
    patch_id: str,
    load_patch_packages: Callable[[], Records],
    save_patch_packages: Callable[[Records], None],
    load_delta_cases: Callable[[], Records],
    evaluate_patch_shadow: Callback,
    now_iso: Callable[[], str],
) -> Record:
    packages = load_patch_packages()
    patch = next((item for item in packages if str(item.get("id")) == patch_id), None)
    if patch is None:
        raise PatchNotFoundError
    project_id = str(patch.get("project_id") or "")
    delta_cases = [case for case in load_delta_cases() if str(case.get("project_id")) == project_id]

    result = evaluate_patch_shadow(patch=patch, delta_cases=delta_cases)
    patch["shadow_metrics"] = result.get("metrics_before_after", {})
    patch["status"] = "shadow_pass" if bool(result.get("gate_passed")) else "candidate"
    patch["updated_at"] = now_iso()
    save_patch_packages(packages)
    return result


def deploy_or_rollback_patch(
    *,
    patch_id: str,
    action_raw: object,
    rollback_to_version: Optional[str],
    load_patch_packages: Callable[[], Records],
    save_patch_packages: Callable[[Records], None],
    load_patch_deployments: Callable[[], Records],
    save_patch_deployments: Callable[[Records], None],
    now_iso: Callable[[], str],
    new_id: Callable[[], str],
) -> Record:
    packages = load_patch_packages()
    original_packages = copy.deepcopy(packages)
    patch = next((item for item in packages if str(item.get("id")) == patch_id), None)
    if patch is None:
        raise PatchNotFoundError

    action = str(action_raw or "deploy").lower()
    if action not in {"deploy", "rollback"}:
        raise UnsupportedPatchActionError

    project_id = str(patch.get("project_id") or "")
    timestamp = now_iso()
    deployed = action == "deploy"
    if deployed:
        if str(patch.get("status") or "") != "shadow_pass":
            raise InvalidPatchTransitionError("补丁仅可从 shadow_pass 状态发布")
        if str(rollback_to_version or "").strip():
            raise InvalidPatchTransitionError("deploy 不接受 rollback_to_version")
        rollback_target_id = str(patch.get("rollback_pointer") or "").strip()
        if rollback_target_id:
            rollback_target = next(
                (
                    item
                    for item in packages
                    if str(item.get("id") or "") == rollback_target_id
                    and str(item.get("project_id") or "") == project_id
                    and item is not patch
                ),
                None,
            )
            if rollback_target is None:
                raise InvalidPatchTransitionError("回滚目标不存在或不属于当前项目")
            if str(rollback_target.get("status") or "") not in {
                "deployed",
                "shadow_pass",
                "rolled_back",
            }:
                raise InvalidPatchTransitionError("回滚目标必须是已通过 shadow 验证的历史补丁")
        for item in packages:
            if str(item.get("project_id")) == project_id and str(item.get("status")) == "deployed":
                item["status"] = "shadow_pass"
                item["updated_at"] = timestamp
        patch["status"] = "deployed"
    else:
        if str(patch.get("status") or "") != "deployed":
            raise InvalidPatchTransitionError("仅可回滚当前 deployed 状态的补丁")
        rollback_target_id = str(rollback_to_version or patch.get("rollback_pointer") or "").strip()
        rollback_target = next(
            (
                item
                for item in packages
                if str(item.get("id") or "") == rollback_target_id
                and str(item.get("project_id") or "") == project_id
                and item is not patch
            ),
            None,
        )
        if rollback_target is None:
            raise InvalidPatchTransitionError("回滚目标不存在或不属于当前项目")
        if str(rollback_target.get("status") or "") not in {"shadow_pass", "rolled_back"}:
            raise InvalidPatchTransitionError("回滚目标必须是已通过 shadow 验证的历史补丁")
        for item in packages:
            if (
                str(item.get("project_id") or "") == project_id
                and str(item.get("status") or "") == "deployed"
            ):
                item["status"] = "shadow_pass"
                item["updated_at"] = timestamp
        patch["status"] = "rolled_back"
        rollback_target["status"] = "deployed"
        rollback_target["updated_at"] = timestamp
    patch["updated_at"] = timestamp

    record = {
        "id": new_id(),
        "patch_id": patch_id,
        "project_id": project_id,
        "action": action,
        "deployed": deployed,
        "metrics_before_after": patch.get("shadow_metrics") or {},
        "rollback_to_version": rollback_target_id or None,
        "created_at": timestamp,
    }
    deployments = load_patch_deployments()
    original_deployments = copy.deepcopy(deployments)
    deployments.append(record)
    package_write_attempted = False
    deployment_write_attempted = False
    try:
        package_write_attempted = True
        save_patch_packages(packages)
        deployment_write_attempted = True
        save_patch_deployments(deployments)
    except BaseException as transition_error:
        restore_errors: List[str] = []
        if package_write_attempted:
            try:
                save_patch_packages(original_packages)
            except BaseException as restore_error:
                restore_errors.append(f"patch package restore failed: {restore_error}")
        if deployment_write_attempted:
            try:
                save_patch_deployments(original_deployments)
            except BaseException as restore_error:
                restore_errors.append(f"patch deployment restore failed: {restore_error}")
        if restore_errors:
            note = "; ".join(restore_errors)
            add_note = getattr(transition_error, "add_note", None)
            if callable(add_note):
                add_note(note)
            else:
                notes = list(getattr(transition_error, "__notes__", []))
                notes.append(note)
                transition_error.__notes__ = notes
        raise
    return record


def run_auto_patch_lifecycle(
    *,
    project_id: str,
    delta_cases: Records,
    auto_govern_deployed_patch: Callback,
    load_patch_packages: Callable[[], Records],
    save_patch_packages: Callable[[Records], None],
    load_patch_deployments: Callable[[], Records],
    save_patch_deployments: Callable[[Records], None],
    mine_patch_package: Callback,
    evaluate_patch_shadow: Callback,
    now_iso: Callable[[], str],
    new_id: Callable[[], str],
) -> Record:
    result: Record = {
        "patch_id": None,
        "patch_gate_passed": None,
        "patch_deployed": False,
        "patch_auto_govern": {
            "checked": False,
            "reason": "not_run",
            "action": "skip",
        },
    }
    if not delta_cases:
        return result

    result["patch_auto_govern"] = auto_govern_deployed_patch(
        project_id=project_id,
        delta_cases=delta_cases,
    )
    packages = load_patch_packages()
    deployed = [
        patch
        for patch in packages
        if str(patch.get("project_id")) == project_id and str(patch.get("status")) == "deployed"
    ]
    rollback_pointer = str(deployed[0].get("id")) if deployed else None
    patch = mine_patch_package(
        project_id=project_id,
        delta_cases=delta_cases,
        patch_type="threshold",
        top_k=5,
        rollback_pointer=rollback_pointer,
    )
    patch_id = str(patch.get("id"))
    shadow = evaluate_patch_shadow(patch=patch, delta_cases=delta_cases)
    patch_gate_passed = bool(shadow.get("gate_passed"))
    patch["shadow_metrics"] = shadow.get("metrics_before_after", {})
    patch["status"] = "shadow_pass" if patch_gate_passed else "candidate"
    patch["updated_at"] = now_iso()

    patch_deployed = False
    if patch_gate_passed:
        for item in packages:
            if str(item.get("project_id")) == project_id and str(item.get("status")) == "deployed":
                item["status"] = "shadow_pass"
        patch["status"] = "deployed"
        patch_deployed = True
        deployment = {
            "id": new_id(),
            "patch_id": patch_id,
            "project_id": project_id,
            "action": "deploy",
            "deployed": True,
            "metrics_before_after": patch.get("shadow_metrics") or {},
            "rollback_to_version": patch.get("rollback_pointer"),
            "created_at": now_iso(),
        }
        deployments = load_patch_deployments()
        deployments.append(deployment)
        save_patch_deployments(deployments)

    packages.append(patch)
    save_patch_packages(packages)
    result["patch_id"] = patch_id
    result["patch_gate_passed"] = patch_gate_passed
    result["patch_deployed"] = patch_deployed
    return result
