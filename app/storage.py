from __future__ import annotations

import contextlib
import functools
import inspect
import json
import os
import stat
import tempfile
import threading
from contextlib import ExitStack
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, TypeVar

BASE_DIR = Path(__file__).resolve().parents[1]
DATA_DIR_ENV_VAR = "QINGTIAN_DATA_DIR"


def _resolve_data_dir() -> Path:
    override = os.environ.get(DATA_DIR_ENV_VAR, "").strip()
    if override:
        return Path(override).expanduser().resolve()
    return BASE_DIR / "data"


DATA_DIR = _resolve_data_dir()
MATERIALS_DIR = DATA_DIR / "materials"
PROJECTS_PATH = DATA_DIR / "projects.json"
SUBMISSIONS_PATH = DATA_DIR / "submissions.json"
MATERIALS_PATH = DATA_DIR / "materials.json"
LEARNING_PATH = DATA_DIR / "learning_profiles.json"
HISTORY_PATH = DATA_DIR / "score_history.json"
PROJECT_CONTEXT_PATH = DATA_DIR / "project_context.json"
GROUND_TRUTH_PATH = DATA_DIR / "ground_truth_scores.json"
EVOLUTION_REPORTS_PATH = DATA_DIR / "evolution_reports.json"
EXPERT_PROFILES_PATH = DATA_DIR / "expert_profiles.json"
SCORE_REPORTS_PATH = DATA_DIR / "score_reports.json"
PROJECT_ANCHORS_PATH = DATA_DIR / "project_anchors.json"
PROJECT_REQUIREMENTS_PATH = DATA_DIR / "project_requirements.json"
EVIDENCE_UNITS_PATH = DATA_DIR / "evidence_units.json"
QINGTIAN_RESULTS_PATH = DATA_DIR / "qingtian_results.json"
CALIBRATION_MODELS_PATH = DATA_DIR / "calibration_models.json"
DELTA_CASES_PATH = DATA_DIR / "delta_cases.json"
CALIBRATION_SAMPLES_PATH = DATA_DIR / "calibration_samples.json"
PATCH_PACKAGES_PATH = DATA_DIR / "patch_packages.json"
PATCH_DEPLOYMENTS_PATH = DATA_DIR / "patch_deployments.json"
HIGH_SCORE_FEATURES_PATH = DATA_DIR / "high_score_features.json"

_PATH_LOCKS: Dict[str, threading.RLock] = {}
_PATH_LOCKS_GUARD = threading.Lock()
_HELD_PATHS = threading.local()
_STORE_PATH_ATTRIBUTES = {
    "projects": "PROJECTS_PATH",
    "submissions": "SUBMISSIONS_PATH",
    "materials": "MATERIALS_PATH",
    "learning_profiles": "LEARNING_PATH",
    "score_history": "HISTORY_PATH",
    "project_context": "PROJECT_CONTEXT_PATH",
    "ground_truth": "GROUND_TRUTH_PATH",
    "evolution_reports": "EVOLUTION_REPORTS_PATH",
    "expert_profiles": "EXPERT_PROFILES_PATH",
    "score_reports": "SCORE_REPORTS_PATH",
    "project_anchors": "PROJECT_ANCHORS_PATH",
    "project_requirements": "PROJECT_REQUIREMENTS_PATH",
    "evidence_units": "EVIDENCE_UNITS_PATH",
    "qingtian_results": "QINGTIAN_RESULTS_PATH",
    "calibration_models": "CALIBRATION_MODELS_PATH",
    "delta_cases": "DELTA_CASES_PATH",
    "calibration_samples": "CALIBRATION_SAMPLES_PATH",
    "patch_packages": "PATCH_PACKAGES_PATH",
    "patch_deployments": "PATCH_DEPLOYMENTS_PATH",
    "high_score_features": "HIGH_SCORE_FEATURES_PATH",
}

T = TypeVar("T")


def ensure_data_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    MATERIALS_DIR.mkdir(parents=True, exist_ok=True)


def _normalize_path(path: Path) -> Path:
    return Path(path).expanduser().resolve()


def _get_path_lock(path: Path) -> threading.RLock:
    key = str(_normalize_path(path))
    with _PATH_LOCKS_GUARD:
        lock = _PATH_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _PATH_LOCKS[key] = lock
        return lock


@contextlib.contextmanager
def _exclusive_file_lock(path: Path):
    lock_file = path.with_suffix(path.suffix + ".lock")
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    fp = lock_file.open("a+", encoding="utf-8")
    if os.name != "posix":
        fp.close()
        raise RuntimeError("cross-process storage locking requires POSIX flock")

    import fcntl

    locked = False
    try:
        fcntl.flock(fp.fileno(), fcntl.LOCK_EX)
        locked = True
        yield
    finally:
        try:
            if locked:
                fcntl.flock(fp.fileno(), fcntl.LOCK_UN)
        finally:
            fp.close()


def _held_path_keys() -> set[str]:
    held = getattr(_HELD_PATHS, "keys", None)
    if held is None:
        held = set()
        _HELD_PATHS.keys = held
    return held


@contextlib.contextmanager
def path_transaction(*paths: Path) -> Iterator[None]:
    normalized = sorted({_normalize_path(path) for path in paths}, key=str)
    held = _held_path_keys()
    new_paths = [path for path in normalized if str(path) not in held]
    if held and new_paths and str(new_paths[0]) < max(held):
        raise RuntimeError("nested storage transaction violates canonical lock order")

    previous = set(held)
    with ExitStack() as stack:
        for path in new_paths:
            stack.enter_context(_get_path_lock(path))
        for path in new_paths:
            stack.enter_context(_exclusive_file_lock(path))
        held.update(str(path) for path in new_paths)
        try:
            yield
        finally:
            held.clear()
            held.update(previous)


def _store_paths(store_names: tuple[str, ...]) -> tuple[Path, ...]:
    try:
        return tuple(Path(globals()[_STORE_PATH_ATTRIBUTES[name]]) for name in store_names)
    except KeyError as exc:
        raise ValueError(f"unknown JSON store: {exc.args[0]}") from exc


def atomic_json_transaction(*store_names: str):
    unknown = set(store_names).difference(_STORE_PATH_ATTRIBUTES)
    if unknown:
        raise ValueError(f"unknown JSON stores: {sorted(unknown)}")

    def decorate(func):
        if inspect.iscoroutinefunction(func):
            raise TypeError("atomic_json_transaction only supports synchronous functions")

        @functools.wraps(func)
        def wrapped(*args, **kwargs):
            with path_transaction(*_store_paths(store_names)):
                return func(*args, **kwargs)

        wrapped.__signature__ = inspect.signature(func, eval_str=True)
        return wrapped

    return decorate


def _fsync_parent_dir(path: Path) -> None:
    if os.name != "posix":
        return
    dir_fd = os.open(str(path.parent), os.O_RDONLY)
    try:
        os.fsync(dir_fd)
    except BaseException as fsync_error:
        try:
            os.close(dir_fd)
        except BaseException as close_error:
            close_note = f"closing parent directory descriptor also failed: {close_error}"
            add_note = getattr(fsync_error, "add_note", None)
            if callable(add_note):
                add_note(close_note)
            else:
                notes = list(getattr(fsync_error, "__notes__", []))
                notes.append(close_note)
                fsync_error.__notes__ = notes
        raise
    else:
        os.close(dir_fd)


def atomic_write_text(path: Path, payload: str) -> None:
    path = _normalize_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        existing_mode = stat.S_IMODE(path.stat().st_mode)
    except FileNotFoundError:
        existing_mode = None
    fd: int | None
    fd, temp_path = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
        text=True,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            fd = None
            handle.write(payload)
            handle.flush()
            if existing_mode is not None:
                os.fchmod(handle.fileno(), existing_mode)
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
        _fsync_parent_dir(path)
    finally:
        if fd is not None:
            os.close(fd)
        Path(temp_path).unlink(missing_ok=True)


def _load_json_unlocked(path: Path, default: T) -> T:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def load_json(path: Path, default: T) -> T:
    normalized = _normalize_path(path)
    held = _held_path_keys()
    if held and str(normalized) not in held:
        # JSON writers always publish with os.replace, so an undeclared read-only
        # dependency can safely observe either complete snapshot without acquiring
        # a path out of canonical order. Any later write still has to acquire or
        # predeclare the path and remains protected by path_transaction.
        return _load_json_unlocked(normalized, default)
    with path_transaction(normalized):
        return _load_json_unlocked(normalized, default)


def save_json(path: Path, data: Any) -> None:
    normalized = _normalize_path(path)
    with path_transaction(normalized):
        payload = json.dumps(data, ensure_ascii=False, indent=2)
        atomic_write_text(normalized, payload)


def update_json(path: Path, default: T, update: Callable[[T], T]) -> T:
    normalized = _normalize_path(path)
    with path_transaction(normalized):
        current = _load_json_unlocked(normalized, default)
        updated = update(current)
        payload = json.dumps(updated, ensure_ascii=False, indent=2)
        atomic_write_text(normalized, payload)
        return updated


def load_projects() -> List[Dict[str, Any]]:
    return load_json(PROJECTS_PATH, [])


def save_projects(data: List[Dict[str, Any]]) -> None:
    save_json(PROJECTS_PATH, data)


def load_submissions() -> List[Dict[str, Any]]:
    return load_json(SUBMISSIONS_PATH, [])


def save_submissions(data: List[Dict[str, Any]]) -> None:
    save_json(SUBMISSIONS_PATH, data)


def load_materials() -> List[Dict[str, Any]]:
    return load_json(MATERIALS_PATH, [])


def save_materials(data: List[Dict[str, Any]]) -> None:
    save_json(MATERIALS_PATH, data)


def load_learning_profiles() -> List[Dict[str, Any]]:
    return load_json(LEARNING_PATH, [])


def save_learning_profiles(data: List[Dict[str, Any]]) -> None:
    save_json(LEARNING_PATH, data)


def load_score_history() -> List[Dict[str, Any]]:
    return load_json(HISTORY_PATH, [])


def save_score_history(data: List[Dict[str, Any]]) -> None:
    save_json(HISTORY_PATH, data)


def append_score_history(entry: Dict[str, Any]) -> None:
    """追加单条评分历史记录"""

    def append(history: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        history.append(entry)
        return history

    update_json(HISTORY_PATH, [], append)


def get_project_score_history(project_id: str) -> List[Dict[str, Any]]:
    """获取指定项目的评分历史（按时间排序）"""
    history = load_score_history()
    project_history = [h for h in history if h.get("project_id") == project_id]
    return sorted(project_history, key=lambda x: x.get("created_at", ""))


def load_project_context() -> Dict[str, Any]:
    """项目ID -> 投喂包/项目背景文本"""
    return load_json(PROJECT_CONTEXT_PATH, {})


def save_project_context(data: Dict[str, Any]) -> None:
    save_json(PROJECT_CONTEXT_PATH, data)


def load_ground_truth() -> List[Dict[str, Any]]:
    """真实评标记录列表（青天大模型等外部评标结果）"""
    return load_json(GROUND_TRUTH_PATH, [])


def save_ground_truth(data: List[Dict[str, Any]]) -> None:
    save_json(GROUND_TRUTH_PATH, data)


@atomic_json_transaction("ground_truth")
def append_ground_truth_records(entries: List[Dict[str, Any]]) -> None:
    records = load_ground_truth()
    records.extend(entries)
    save_ground_truth(records)


def load_evolution_reports() -> Dict[str, Any]:
    """project_id -> 进化报告（高分逻辑、编制指导等）"""
    return load_json(EVOLUTION_REPORTS_PATH, {})


def save_evolution_reports(data: Dict[str, Any]) -> None:
    save_json(EVOLUTION_REPORTS_PATH, data)


def load_expert_profiles() -> List[Dict[str, Any]]:
    """专家关注度配置列表"""
    return load_json(EXPERT_PROFILES_PATH, [])


def save_expert_profiles(data: List[Dict[str, Any]]) -> None:
    save_json(EXPERT_PROFILES_PATH, data)


def load_score_reports() -> List[Dict[str, Any]]:
    """评分报告快照列表（不覆盖历史）"""
    return load_json(SCORE_REPORTS_PATH, [])


def save_score_reports(data: List[Dict[str, Any]]) -> None:
    save_json(SCORE_REPORTS_PATH, data)


def load_project_anchors() -> List[Dict[str, Any]]:
    """项目锚点列表"""
    return load_json(PROJECT_ANCHORS_PATH, [])


def save_project_anchors(data: List[Dict[str, Any]]) -> None:
    save_json(PROJECT_ANCHORS_PATH, data)


def load_project_requirements() -> List[Dict[str, Any]]:
    """项目要求矩阵列表"""
    return load_json(PROJECT_REQUIREMENTS_PATH, [])


def save_project_requirements(data: List[Dict[str, Any]]) -> None:
    save_json(PROJECT_REQUIREMENTS_PATH, data)


def load_evidence_units() -> List[Dict[str, Any]]:
    """证据单元列表"""
    return load_json(EVIDENCE_UNITS_PATH, [])


def save_evidence_units(data: List[Dict[str, Any]]) -> None:
    save_json(EVIDENCE_UNITS_PATH, data)


def load_qingtian_results() -> List[Dict[str, Any]]:
    """真实青天评标结果列表"""
    return load_json(QINGTIAN_RESULTS_PATH, [])


def save_qingtian_results(data: List[Dict[str, Any]]) -> None:
    save_json(QINGTIAN_RESULTS_PATH, data)


def load_calibration_models() -> List[Dict[str, Any]]:
    """校准器版本列表"""
    return load_json(CALIBRATION_MODELS_PATH, [])


def save_calibration_models(data: List[Dict[str, Any]]) -> None:
    save_json(CALIBRATION_MODELS_PATH, data)


def load_delta_cases() -> List[Dict[str, Any]]:
    """误差案例（DELTA_CASE）列表"""
    return load_json(DELTA_CASES_PATH, [])


def save_delta_cases(data: List[Dict[str, Any]]) -> None:
    save_json(DELTA_CASES_PATH, data)


def load_calibration_samples() -> List[Dict[str, Any]]:
    """校准训练样本（FEATURE_ROW）列表"""
    return load_json(CALIBRATION_SAMPLES_PATH, [])


def save_calibration_samples(data: List[Dict[str, Any]]) -> None:
    save_json(CALIBRATION_SAMPLES_PATH, data)


def load_patch_packages() -> List[Dict[str, Any]]:
    """候选补丁包列表"""
    return load_json(PATCH_PACKAGES_PATH, [])


def save_patch_packages(data: List[Dict[str, Any]]) -> None:
    save_json(PATCH_PACKAGES_PATH, data)


def load_patch_deployments() -> List[Dict[str, Any]]:
    """补丁发布记录列表"""
    return load_json(PATCH_DEPLOYMENTS_PATH, [])


def save_patch_deployments(data: List[Dict[str, Any]]) -> None:
    save_json(PATCH_DEPLOYMENTS_PATH, data)


def load_high_score_features() -> List[Dict[str, Any]]:
    """高分逻辑骨架特征库（可更新置信度）"""
    return load_json(HIGH_SCORE_FEATURES_PATH, [])


def save_high_score_features(data: List[Dict[str, Any]]) -> None:
    save_json(HIGH_SCORE_FEATURES_PATH, data)
