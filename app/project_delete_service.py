from __future__ import annotations

import shutil
import tempfile
from copy import deepcopy
from pathlib import Path
from typing import Any, Callable, Dict, List

Record = Dict[str, object]
LoadRecords = Callable[[], List[Record]]
SaveRecords = Callable[[List[Record]], None]
LoaderMap = Dict[str, Callable[[], Any]]
SaverMap = Dict[str, Callable[[Any], None]]
TransactionDecorator = Callable[[Callable[[], Record]], Callable[[], Record]]
TransactionFactory = Callable[..., TransactionDecorator]

PROJECT_DELETE_STORES = (
    "calibration_models",
    "calibration_samples",
    "delta_cases",
    "evidence_units",
    "evolution_reports",
    "expert_profiles",
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
        "project-delete rollback also failed: " f"{type(rollback_error).__name__}: {rollback_error}"
    )
    add_note = getattr(error, "add_note", None)
    if callable(add_note):
        add_note(note)
        return
    notes = list(getattr(error, "__notes__", []))
    notes.append(note)
    error.__notes__ = notes


def _restore_staged_paths(
    staged_paths: List[tuple[Path, Path]],
    *,
    error: BaseException,
) -> None:
    for staged, original in reversed(staged_paths):
        if not staged.exists():
            continue
        try:
            original.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(staged), str(original))
        except BaseException as rollback_error:
            _append_rollback_note(error, rollback_error)


def _stage_project_files(
    *,
    project_id: str,
    materials_dir: Path,
    materials: List[Record],
) -> tuple[Path | None, List[tuple[Path, Path]], List[str]]:
    project_dir = (materials_dir / project_id).resolve()
    materials_root = materials_dir.resolve()
    candidates: List[Path] = []
    warnings: List[str] = []
    project_dir_is_managed = project_dir.is_relative_to(materials_root)
    if not project_dir_is_managed:
        warnings.append(f"skipped project directory outside managed root: {project_dir}")
    elif project_dir.exists():
        candidates.append(project_dir)
    for material in materials:
        if str(material.get("project_id") or "") != project_id:
            continue
        raw_path = str(material.get("path") or "").strip()
        if not raw_path:
            continue
        path = Path(raw_path).expanduser().resolve()
        if project_dir_is_managed and (path == project_dir or path.is_relative_to(project_dir)):
            continue
        if not path.is_relative_to(materials_root):
            warnings.append(f"skipped material outside managed root: {path}")
            continue
        if path.exists() and path.is_file() and path not in candidates:
            candidates.append(path)
    if not candidates:
        return None, [], warnings

    materials_dir.parent.mkdir(parents=True, exist_ok=True)
    quarantine = Path(
        tempfile.mkdtemp(
            prefix=f".project-delete-{project_id}-",
            dir=str(materials_dir.parent),
        )
    )
    staged_paths: List[tuple[Path, Path]] = []
    try:
        for index, original in enumerate(candidates):
            staged = quarantine / f"{index:04d}-{original.name}"
            shutil.move(str(original), str(staged))
            staged_paths.append((staged, original))
    except BaseException as error:
        _restore_staged_paths(staged_paths, error=error)
        try:
            shutil.rmtree(quarantine)
        except BaseException as cleanup_error:
            _append_rollback_note(error, cleanup_error)
        raise
    return quarantine, staged_paths, warnings


def _delete_project_records(
    *,
    project_id: str,
    materials_dir: Path,
    ensure_data_dirs: Callable[[], None],
    load_projects: LoadRecords,
    save_projects: SaveRecords,
    load_materials: LoadRecords,
    save_materials: SaveRecords,
    invalidate_material_index_cache: Callable[[str], None],
    load_submissions: LoadRecords,
    save_submissions: SaveRecords,
    load_score_reports: LoadRecords,
    save_score_reports: SaveRecords,
    load_evidence_units: LoadRecords,
    save_evidence_units: SaveRecords,
    load_qingtian_results: LoadRecords,
    save_qingtian_results: SaveRecords,
    load_delta_cases: LoadRecords,
    save_delta_cases: SaveRecords,
    load_calibration_samples: LoadRecords,
    save_calibration_samples: SaveRecords,
    load_patch_packages: LoadRecords,
    save_patch_packages: SaveRecords,
    load_patch_deployments: LoadRecords,
    save_patch_deployments: SaveRecords,
    load_project_anchors: LoadRecords,
    save_project_anchors: SaveRecords,
    load_project_requirements: LoadRecords,
    save_project_requirements: SaveRecords,
    load_learning_profiles: LoadRecords,
    save_learning_profiles: SaveRecords,
    load_score_history: LoadRecords,
    save_score_history: SaveRecords,
    load_project_context: Callable[[], Dict[str, object]],
    save_project_context: Callable[[Dict[str, object]], None],
    load_ground_truth: LoadRecords,
    save_ground_truth: SaveRecords,
    load_calibration_models: LoadRecords,
    save_calibration_models: SaveRecords,
    load_evolution_reports: Callable[[], Dict[str, object]],
    save_evolution_reports: Callable[[Dict[str, object]], None],
    load_expert_profiles: LoadRecords,
    save_expert_profiles: SaveRecords,
    project_not_found_error: Callable[[], Exception],
) -> Dict[str, object]:
    ensure_data_dirs()
    projects = load_projects()
    target = next((p for p in projects if str(p.get("id")) == project_id), None)
    if target is None:
        raise project_not_found_error()
    target_name = str(target.get("name") or project_id)
    target_profile_id = str(target.get("expert_profile_id") or "")

    removed_counts = {
        "materials": 0,
        "submissions": 0,
        "score_reports": 0,
        "ground_truth": 0,
        "delta_cases": 0,
        "calibration_samples": 0,
        "calibration_models": 0,
        "patch_packages": 0,
    }

    save_projects([p for p in projects if p.get("id") != project_id])

    materials = load_materials()
    project_materials = [m for m in materials if m.get("project_id") == project_id]
    removed_counts["materials"] = len(project_materials)
    save_materials([m for m in materials if m.get("project_id") != project_id])

    submissions = load_submissions()
    project_submission_ids = {
        str(s.get("id")) for s in submissions if s.get("project_id") == project_id
    }
    removed_counts["submissions"] = len(project_submission_ids)
    save_submissions([s for s in submissions if s.get("project_id") != project_id])

    score_reports = load_score_reports()
    removed_counts["score_reports"] = sum(
        1 for r in score_reports if r.get("project_id") == project_id
    )
    save_score_reports([r for r in score_reports if r.get("project_id") != project_id])
    evidence_units = load_evidence_units()
    save_evidence_units(
        [u for u in evidence_units if str(u.get("submission_id")) not in project_submission_ids]
    )
    qingtian_results = load_qingtian_results()
    save_qingtian_results(
        [q for q in qingtian_results if str(q.get("submission_id")) not in project_submission_ids]
    )
    delta_cases = load_delta_cases()
    removed_counts["delta_cases"] = sum(
        1
        for d in delta_cases
        if str(d.get("project_id")) == project_id
        or str(d.get("submission_id")) in project_submission_ids
    )
    save_delta_cases(
        [
            d
            for d in delta_cases
            if str(d.get("project_id")) != project_id
            and str(d.get("submission_id")) not in project_submission_ids
        ]
    )
    calibration_samples = load_calibration_samples()
    removed_counts["calibration_samples"] = sum(
        1
        for s in calibration_samples
        if str(s.get("project_id")) == project_id
        or str(s.get("submission_id")) in project_submission_ids
    )
    save_calibration_samples(
        [
            s
            for s in calibration_samples
            if str(s.get("project_id")) != project_id
            and str(s.get("submission_id")) not in project_submission_ids
        ]
    )
    patch_packages = load_patch_packages()
    removed_patch_ids = {
        str(p.get("id")) for p in patch_packages if str(p.get("project_id")) == project_id
    }
    removed_counts["patch_packages"] = len(removed_patch_ids)
    save_patch_packages([p for p in patch_packages if str(p.get("project_id")) != project_id])
    patch_deployments = load_patch_deployments()
    save_patch_deployments(
        [
            d
            for d in patch_deployments
            if str(d.get("project_id")) != project_id
            and str(d.get("patch_id")) not in removed_patch_ids
        ]
    )

    anchors = load_project_anchors()
    save_project_anchors([a for a in anchors if a.get("project_id") != project_id])
    requirements = load_project_requirements()
    save_project_requirements([r for r in requirements if r.get("project_id") != project_id])

    learning_profiles = load_learning_profiles()
    save_learning_profiles([p for p in learning_profiles if p.get("project_id") != project_id])

    score_history = load_score_history()
    save_score_history([h for h in score_history if h.get("project_id") != project_id])

    context = load_project_context()
    if project_id in context:
        context.pop(project_id, None)
        save_project_context(context)

    ground_truth = load_ground_truth()
    removed_counts["ground_truth"] = sum(
        1 for r in ground_truth if r.get("project_id") == project_id
    )
    save_ground_truth([r for r in ground_truth if r.get("project_id") != project_id])

    calibration_models = load_calibration_models()
    kept_models: List[Record] = []
    for model in calibration_models:
        train_filter = model.get("train_filter")
        scoped_project_id = (
            str(train_filter.get("project_id") or "") if isinstance(train_filter, dict) else ""
        )
        if scoped_project_id == project_id:
            removed_counts["calibration_models"] += 1
            continue
        kept_models.append(model)
    save_calibration_models(kept_models)

    reports = load_evolution_reports()
    if project_id in reports:
        reports.pop(project_id, None)
        save_evolution_reports(reports)

    if target_profile_id:
        remaining_projects = load_projects()
        in_use = any(
            str(p.get("expert_profile_id") or "") == target_profile_id for p in remaining_projects
        )
        if not in_use:
            profiles = load_expert_profiles()
            profiles = [ep for ep in profiles if str(ep.get("id") or "") != target_profile_id]
            save_expert_profiles(profiles)

    return {
        "project_id": project_id,
        "project_name": target_name,
        "removed_counts": removed_counts,
    }


def delete_project_cascade(
    *,
    project_id: str,
    atomic_json_transaction: TransactionFactory,
    **kwargs: Any,
) -> Dict[str, object]:
    kwargs["ensure_data_dirs"]()

    @atomic_json_transaction(*PROJECT_DELETE_STORES)
    def commit() -> Record:
        originals = {name: deepcopy(kwargs[f"load_{name}"]()) for name in PROJECT_DELETE_STORES}
        if not any(str(project.get("id") or "") == project_id for project in originals["projects"]):
            raise kwargs["project_not_found_error"]()

        quarantine, staged_paths, cleanup_warnings = _stage_project_files(
            project_id=project_id,
            materials_dir=kwargs["materials_dir"],
            materials=originals["materials"],
        )
        savers = {name: kwargs[f"save_{name}"] for name in PROJECT_DELETE_STORES}
        attempted: List[str] = []
        operation_kwargs = dict(kwargs)
        operation_kwargs["ensure_data_dirs"] = lambda: None
        operation_kwargs["invalidate_material_index_cache"] = lambda _project_id: None
        for name, saver in savers.items():

            def tracked_save(data: Any, *, _name=name, _saver=saver) -> None:
                if _name not in attempted:
                    attempted.append(_name)
                _saver(data)

            operation_kwargs[f"save_{name}"] = tracked_save

        try:
            result = _delete_project_records(project_id=project_id, **operation_kwargs)
        except BaseException as error:
            for name in reversed(attempted):
                try:
                    savers[name](deepcopy(originals[name]))
                except BaseException as rollback_error:
                    _append_rollback_note(error, rollback_error)
            _restore_staged_paths(staged_paths, error=error)
            if quarantine is not None and quarantine.exists():
                try:
                    shutil.rmtree(quarantine)
                except BaseException as cleanup_error:
                    _append_rollback_note(error, cleanup_error)
            raise

        result["cleanup_warnings"] = cleanup_warnings
        result["_quarantine"] = quarantine
        return result

    result = commit()
    cleanup_warnings = list(result.pop("cleanup_warnings", []))
    quarantine = result.pop("_quarantine", None)
    try:
        kwargs["invalidate_material_index_cache"](project_id)
    except BaseException as error:
        cleanup_warnings.append(
            f"material index cache invalidation failed: {type(error).__name__}: {error}"
        )
    if isinstance(quarantine, Path) and quarantine.exists():
        try:
            shutil.rmtree(quarantine)
        except BaseException as error:
            cleanup_warnings.append(f"quarantine cleanup failed: {type(error).__name__}: {error}")
    if cleanup_warnings:
        result["cleanup_warnings"] = cleanup_warnings
    return result
