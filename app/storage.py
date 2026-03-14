from __future__ import annotations

import contextlib
import ctypes
import json
import logging
import os
import tempfile
import threading
from pathlib import Path
from typing import Any, Dict, List

BASE_DIR = Path(__file__).resolve().parents[1]
logger = logging.getLogger(__name__)

_SECURE_FILE_MAGIC = b"ZHIFEI_SECURE_V1\0"
_SECURE_RUNTIME_LOCK = threading.Lock()
_SECURE_RUNTIME_PREPARED = False
_DPAPI_OPTIONAL_ENTROPY = b"ZhifeiBizSystem::SecureDesktop"


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    value = str(raw).strip().lower()
    return value in {"1", "true", "yes", "on"}


def is_secure_desktop_mode_enabled() -> bool:
    return _env_flag("ZHIFEI_SECURE_DESKTOP", default=False)


def _resolve_data_dir() -> Path:
    override = str(os.environ.get("ZHIFEI_DATA_DIR") or "").strip()
    if override:
        return Path(override).expanduser()
    if is_secure_desktop_mode_enabled() and os.name == "nt":
        local_appdata = str(os.environ.get("LOCALAPPDATA") or "").strip()
        if local_appdata:
            return Path(local_appdata) / "QingtianBidSystem" / "data"
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
MATERIAL_PARSE_JOBS_PATH = DATA_DIR / "material_parse_jobs.json"

_PATH_LOCKS: Dict[str, threading.RLock] = {}
_PATH_LOCKS_GUARD = threading.Lock()


def _is_secure_blob(payload: bytes) -> bool:
    return payload.startswith(_SECURE_FILE_MAGIC)


def ensure_data_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    MATERIALS_DIR.mkdir(parents=True, exist_ok=True)


def _get_path_lock(path: Path) -> threading.RLock:
    key = str(path.resolve())
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
    try:
        if os.name == "posix":
            try:
                import fcntl

                fcntl.flock(fp.fileno(), fcntl.LOCK_EX)
            except Exception:
                pass
        yield
    finally:
        if os.name == "posix":
            try:
                import fcntl

                fcntl.flock(fp.fileno(), fcntl.LOCK_UN)
            except Exception:
                pass
        fp.close()


def _fsync_parent_dir(path: Path) -> None:
    if os.name != "posix":
        return
    dir_fd: int | None = None
    try:
        dir_fd = os.open(str(path.parent), os.O_RDONLY)
        os.fsync(dir_fd)
    except Exception:
        return
    finally:
        if dir_fd is not None:
            try:
                os.close(dir_fd)
            except Exception:
                pass


def _atomic_write_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
        text=False,
    )
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
        _fsync_parent_dir(path)
    finally:
        if os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except OSError:
                pass


def _atomic_write_text(path: Path, payload: str) -> None:
    _atomic_write_bytes(path, payload.encode("utf-8"))


def _require_windows_dpapi() -> None:
    if os.name != "nt" or not hasattr(ctypes, "windll"):
        raise RuntimeError("secure_desktop_requires_windows_dpapi")


def _blob_from_bytes(payload: bytes):
    class DATA_BLOB(ctypes.Structure):
        _fields_ = [
            ("cbData", ctypes.c_uint32),
            ("pbData", ctypes.POINTER(ctypes.c_ubyte)),
        ]

    if payload:
        buffer = ctypes.create_string_buffer(payload)
        blob = DATA_BLOB(
            cbData=len(payload),
            pbData=ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)),
        )
        return blob, buffer, DATA_BLOB
    blob = DATA_BLOB(cbData=0, pbData=None)
    return blob, None, DATA_BLOB


def _dpapi_crypt(payload: bytes, *, decrypt: bool) -> bytes:
    _require_windows_dpapi()
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    in_blob, in_buffer, blob_type = _blob_from_bytes(payload)
    entropy_blob, entropy_buffer, _ = _blob_from_bytes(_DPAPI_OPTIONAL_ENTROPY)
    out_blob = blob_type()
    crypt_fn = crypt32.CryptUnprotectData if decrypt else crypt32.CryptProtectData
    entropy_ptr = ctypes.byref(entropy_blob) if _DPAPI_OPTIONAL_ENTROPY else None
    ok = crypt_fn(
        ctypes.byref(in_blob),
        None,
        entropy_ptr,
        None,
        None,
        0x01,
        ctypes.byref(out_blob),
    )
    if not ok:
        raise ctypes.WinError()
    try:
        if not out_blob.cbData:
            return b""
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        if out_blob.pbData:
            kernel32.LocalFree(out_blob.pbData)
        del in_buffer
        del entropy_buffer


def _encrypt_payload(payload: bytes) -> bytes:
    if not is_secure_desktop_mode_enabled():
        return payload
    encrypted = _dpapi_crypt(payload, decrypt=False)
    return _SECURE_FILE_MAGIC + encrypted


def _decrypt_payload(payload: bytes) -> bytes:
    if not _is_secure_blob(payload):
        return payload
    return _dpapi_crypt(payload[len(_SECURE_FILE_MAGIC) :], decrypt=True)


def read_bytes(path: Path) -> bytes:
    if not path.exists():
        raise FileNotFoundError(path)
    lock = _get_path_lock(path)
    with lock:
        with _exclusive_file_lock(path):
            payload = path.read_bytes()
    return _decrypt_payload(payload)


def save_bytes(path: Path, payload: bytes) -> None:
    lock = _get_path_lock(path)
    stored_payload = _encrypt_payload(payload)
    with lock:
        with _exclusive_file_lock(path):
            _atomic_write_bytes(path, stored_payload)


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(read_bytes(path).decode("utf-8"))


def save_json(path: Path, data: Any) -> None:
    payload = json.dumps(data, ensure_ascii=False, indent=2)
    save_bytes(path, payload.encode("utf-8"))


def _iter_secure_candidate_files() -> List[Path]:
    if not DATA_DIR.exists():
        return []
    rows: List[Path] = []
    for path in DATA_DIR.rglob("*"):
        if not path.is_file():
            continue
        if path.name.endswith(".lock") or path.name.endswith(".tmp"):
            continue
        if path.name.startswith(".") and path.suffix == ".tmp":
            continue
        rows.append(path)
    return rows


def prepare_secure_runtime() -> None:
    global _SECURE_RUNTIME_PREPARED
    if not is_secure_desktop_mode_enabled():
        return
    _require_windows_dpapi()
    with _SECURE_RUNTIME_LOCK:
        if _SECURE_RUNTIME_PREPARED:
            return
        ensure_data_dirs()
        migrated = 0
        for path in _iter_secure_candidate_files():
            try:
                payload = path.read_bytes()
            except OSError:
                continue
            if _is_secure_blob(payload):
                continue
            save_bytes(path, payload)
            migrated += 1
        _SECURE_RUNTIME_PREPARED = True
        if migrated:
            logger.info("secure desktop runtime encrypted %s existing data files", migrated)


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


def load_material_parse_jobs() -> List[Dict[str, Any]]:
    return load_json(MATERIAL_PARSE_JOBS_PATH, [])


def save_material_parse_jobs(data: List[Dict[str, Any]]) -> None:
    save_json(MATERIAL_PARSE_JOBS_PATH, data)


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
    history = load_score_history()
    history.append(entry)
    save_score_history(history)


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
