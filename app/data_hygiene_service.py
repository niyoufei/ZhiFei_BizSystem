from __future__ import annotations

from copy import deepcopy
from typing import Any, Callable, Dict, List

Record = Dict[str, object]
Records = List[Record]
LoaderMap = Dict[str, Callable[[], Any]]
SaverMap = Dict[str, Callable[[Any], None]]
TransactionDecorator = Callable[[Callable[[], Record]], Callable[[], Record]]
TransactionFactory = Callable[..., TransactionDecorator]

DATA_HYGIENE_STORES = (
    "calibration_models",
    "calibration_samples",
    "delta_cases",
    "evidence_units",
    "evolution_reports",
    "ground_truth",
    "learning_profiles",
    "materials",
    "patch_deployments",
    "patch_packages",
    "project_anchors",
    "project_context",
    "project_requirements",
    "projects",
    "qingtian_results",
    "score_history",
    "score_reports",
    "submissions",
)


def _append_rollback_note(error: BaseException, rollback_error: BaseException) -> None:
    note = (
        "data-hygiene rollback also failed: " f"{type(rollback_error).__name__}: {rollback_error}"
    )
    add_note = getattr(error, "add_note", None)
    if callable(add_note):
        add_note(note)
        return
    notes = list(getattr(error, "__notes__", []))
    notes.append(note)
    error.__notes__ = notes


def _build_report_locked(
    *,
    apply: bool,
    loaders: LoaderMap,
    savers: SaverMap,
    now_iso: Callable[[], str],
) -> Record:
    projects = loaders["projects"]()
    valid_project_ids = {
        str(project.get("id") or "").strip()
        for project in projects
        if str(project.get("id") or "").strip()
    }
    datasets: Records = []
    orphan_records_total = 0
    cleaned_records_total = 0

    def append_dataset(
        *,
        name: str,
        total: int,
        orphan_count: int,
        cleaned_count: int = 0,
        mode: str = "project_id",
    ) -> None:
        nonlocal orphan_records_total, cleaned_records_total
        orphan_records_total += int(orphan_count)
        cleaned_records_total += int(cleaned_count)
        datasets.append(
            {
                "name": name,
                "total": int(total),
                "orphan_count": int(orphan_count),
                "cleaned_count": int(cleaned_count),
                "mode": mode,
            }
        )

    def scan_project_rows(name: str) -> Records:
        rows = loaders[name]()
        kept: Records = []
        orphan_count = 0
        for row in rows:
            if not isinstance(row, dict):
                kept.append(row)
                continue
            project_id = str(row.get("project_id") or "").strip()
            if project_id and project_id not in valid_project_ids:
                orphan_count += 1
                continue
            kept.append(row)
        if apply and orphan_count > 0:
            savers[name](kept)
        append_dataset(
            name="ground_truth_scores" if name == "ground_truth" else name,
            total=len(rows),
            orphan_count=orphan_count,
            cleaned_count=orphan_count if apply else 0,
        )
        return kept

    submissions_kept = scan_project_rows("submissions")
    valid_submission_ids = {
        str(row.get("id") or "").strip()
        for row in submissions_kept
        if isinstance(row, dict) and str(row.get("id") or "").strip()
    }

    for name in (
        "materials",
        "learning_profiles",
        "score_history",
        "ground_truth",
        "project_anchors",
        "project_requirements",
        "delta_cases",
        "calibration_samples",
    ):
        scan_project_rows(name)

    calibration_models = loaders["calibration_models"]()
    kept_models: Records = []
    orphan_models = 0
    for model in calibration_models:
        if not isinstance(model, dict):
            kept_models.append(model)
            continue
        train_filter = model.get("train_filter")
        scoped_project_id = (
            str(train_filter.get("project_id") or "").strip()
            if isinstance(train_filter, dict)
            else ""
        )
        if scoped_project_id and scoped_project_id not in valid_project_ids:
            orphan_models += 1
            continue
        kept_models.append(model)
    if apply and orphan_models > 0:
        savers["calibration_models"](kept_models)
    append_dataset(
        name="calibration_models",
        total=len(calibration_models),
        orphan_count=orphan_models,
        cleaned_count=orphan_models if apply else 0,
        mode="train_filter.project_id",
    )

    patch_packages_kept = scan_project_rows("patch_packages")
    valid_patch_ids = {
        str(row.get("id") or "").strip()
        for row in patch_packages_kept
        if isinstance(row, dict) and str(row.get("id") or "").strip()
    }
    patch_deployments = loaders["patch_deployments"]()
    kept_deployments: Records = []
    orphan_deployments = 0
    for row in patch_deployments:
        if not isinstance(row, dict):
            kept_deployments.append(row)
            continue
        project_id = str(row.get("project_id") or "").strip()
        patch_id = str(row.get("patch_id") or "").strip()
        if (project_id and project_id not in valid_project_ids) or (
            patch_id and patch_id not in valid_patch_ids
        ):
            orphan_deployments += 1
            continue
        kept_deployments.append(row)
    if apply and orphan_deployments > 0:
        savers["patch_deployments"](kept_deployments)
    append_dataset(
        name="patch_deployments",
        total=len(patch_deployments),
        orphan_count=orphan_deployments,
        cleaned_count=orphan_deployments if apply else 0,
        mode="project_id|patch_id",
    )

    for name in ("score_reports", "evidence_units", "qingtian_results"):
        rows = loaders[name]()
        kept: Records = []
        orphan_count = 0
        for row in rows:
            if not isinstance(row, dict):
                kept.append(row)
                continue
            project_id = str(row.get("project_id") or "").strip()
            submission_id = str(row.get("submission_id") or "").strip()
            if (project_id and project_id not in valid_project_ids) or (
                submission_id and submission_id not in valid_submission_ids
            ):
                orphan_count += 1
                continue
            kept.append(row)
        if apply and orphan_count > 0:
            savers[name](kept)
        append_dataset(
            name=name,
            total=len(rows),
            orphan_count=orphan_count,
            cleaned_count=orphan_count if apply else 0,
            mode="project_id|submission_id",
        )

    for name in ("project_context", "evolution_reports"):
        data = loaders[name]()
        if not isinstance(data, dict):
            append_dataset(
                name=name,
                total=0,
                orphan_count=0,
                mode="project_map",
            )
            continue
        orphan_keys = [key for key in data if str(key) not in valid_project_ids]
        if apply and orphan_keys:
            savers[name]({key: value for key, value in data.items() if key not in orphan_keys})
        append_dataset(
            name=name,
            total=len(data),
            orphan_count=len(orphan_keys),
            cleaned_count=len(orphan_keys) if apply else 0,
            mode="project_map",
        )

    recommendations: List[str] = []
    if orphan_records_total <= 0:
        recommendations.append("数据卫生良好：未发现跨项目孤儿记录。")
    elif apply:
        recommendations.append(
            f"已清理孤儿记录 {cleaned_records_total} 条，建议执行一次 doctor/acceptance 回归。"
        )
    else:
        recommendations.append(
            f"发现孤儿记录 {orphan_records_total} 条，建议调用 /api/v1/system/data_hygiene/repair 进行修复。"
        )
    if orphan_records_total > 0:
        recommendations.append(
            "建议在批量删除项目后执行数据卫生巡检，避免历史孤儿记录影响统计与审计。"
        )

    return {
        "generated_at": now_iso(),
        "apply_mode": bool(apply),
        "valid_project_count": len(valid_project_ids),
        "orphan_records_total": orphan_records_total,
        "cleaned_records_total": cleaned_records_total,
        "datasets": datasets,
        "recommendations": recommendations,
    }


def build_data_hygiene_report(
    *,
    apply: bool,
    atomic_json_transaction: TransactionFactory,
    loaders: LoaderMap,
    savers: SaverMap,
    now_iso: Callable[[], str],
) -> Record:
    @atomic_json_transaction(*DATA_HYGIENE_STORES)
    def build() -> Record:
        if not apply:
            return _build_report_locked(
                apply=False,
                loaders=loaders,
                savers=savers,
                now_iso=now_iso,
            )

        originals = {name: deepcopy(loaders[name]()) for name in savers}
        attempted: List[str] = []
        tracked_savers: SaverMap = {}
        for name, saver in savers.items():

            def tracked_save(data: Any, *, _name=name, _saver=saver) -> None:
                if _name not in attempted:
                    attempted.append(_name)
                _saver(data)

            tracked_savers[name] = tracked_save
        try:
            return _build_report_locked(
                apply=True,
                loaders=loaders,
                savers=tracked_savers,
                now_iso=now_iso,
            )
        except BaseException as error:
            for name in reversed(attempted):
                try:
                    savers[name](deepcopy(originals[name]))
                except BaseException as rollback_error:
                    _append_rollback_note(error, rollback_error)
            raise

    return build()
