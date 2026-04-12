from __future__ import annotations

import contextlib
import ctypes
import hashlib
import json
import logging
import os
import tempfile
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Sequence, cast

from app.infrastructure.storage.file_store import FileArtifactStore
from app.infrastructure.storage.sqlite_event_store import SQLiteEventStore, SQLiteEventStoreError
from app.infrastructure.storage.sqlite_metadata import (
    SQLiteMetadataError,
    SQLiteMetadataRepository,
)
from app.ports.artifact_store import ArtifactStore, StoredArtifact
from app.ports.event_store import AppendResult, EventEnvelope, EventStore
from app.ports.repositories import CollectionDescriptor, Repository

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
VERSIONED_JSON_DIR = DATA_DIR / "versions"

_PATH_LOCKS: Dict[str, threading.RLock] = {}
_PATH_LOCKS_GUARD = threading.Lock()


class StorageDataError(RuntimeError):
    def __init__(self, path: Path, code: str, detail: str):
        super().__init__(detail)
        self.path = path
        self.code = code
        self.detail = detail


class VersionedJsonSnapshotNotFound(StorageDataError):
    pass


@dataclass(frozen=True)
class StorageRuntimeConfig:
    primary_backend: str
    enable_sqlite_mirror: bool
    enable_event_log: bool
    validate_dual_write: bool
    legacy_json_write: bool
    metadata_db_path: Path
    event_db_path: Path


@dataclass(frozen=True)
class StorageRuntime:
    config: StorageRuntimeConfig
    json_repository: Repository
    sqlite_repository: Repository | None
    event_store: EventStore
    artifact_store: ArtifactStore


def _is_secure_blob(payload: bytes) -> bool:
    return payload.startswith(_SECURE_FILE_MAGIC)


def ensure_data_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    get_artifact_store().ensure_roots()
    VERSIONED_JSON_DIR.mkdir(parents=True, exist_ok=True)


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
    windll = cast(Any, getattr(ctypes, "windll"))
    crypt32 = windll.crypt32
    kernel32 = windll.kernel32
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
        win_error = cast(Any, getattr(ctypes, "WinError", None))
        if callable(win_error):
            raise win_error()
        raise OSError("DPAPI operation failed")
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


def _now_version_token() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


def _version_bucket_for_path(path: Path) -> Path:
    return VERSIONED_JSON_DIR / path.stem


def _snapshot_path_for(path: Path, version_id: str) -> Path:
    bucket = _version_bucket_for_path(path)
    return bucket / f"{path.stem}_v{version_id}{path.suffix}"


def _version_id_from_name(filename: str, stem: str) -> str:
    prefix = f"{stem}_v"
    suffix = ".json"
    if filename.startswith(prefix) and filename.endswith(suffix):
        return filename[len(prefix) : -len(suffix)]
    return ""


def list_json_versions(path: Path) -> List[Dict[str, Any]]:
    bucket = _version_bucket_for_path(path)
    if not bucket.exists():
        return []
    rows: List[Dict[str, Any]] = []
    for item in sorted(bucket.glob(f"{path.stem}_v*{path.suffix}"), reverse=True):
        try:
            stat = item.stat()
        except OSError:
            continue
        version_id = _version_id_from_name(item.name, path.stem)
        rows.append(
            {
                "version_id": version_id or item.name,
                "filename": item.name,
                "path": item,
                "size_bytes": int(stat.st_size),
                "created_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
            }
        )
    return rows


def _write_json_version_snapshot(path: Path, payload: bytes) -> str:
    ensure_data_dirs()
    bucket = _version_bucket_for_path(path)
    bucket.mkdir(parents=True, exist_ok=True)
    version_id = _now_version_token()
    snapshot_path = _snapshot_path_for(path, version_id)
    save_bytes(snapshot_path, payload)
    return version_id


def restore_json_version(path: Path, version_id: str) -> Dict[str, Any]:
    snapshot_path = _snapshot_path_for(path, str(version_id).strip())
    if not snapshot_path.exists():
        raise VersionedJsonSnapshotNotFound(
            path,
            "snapshot_not_found",
            f"未找到历史版本：{path.stem} / {version_id}",
        )
    payload = read_bytes(snapshot_path)
    backup_version_id = None
    if path.exists():
        try:
            current_payload = read_bytes(path)
        except OSError:
            current_payload = b""
        if current_payload and current_payload != payload:
            backup_version_id = _write_json_version_snapshot(path, current_payload)
    save_bytes(path, payload)
    result = {
        "version_id": str(version_id).strip(),
        "restored_at": datetime.now(timezone.utc).isoformat(),
        "snapshot_path": snapshot_path,
        "current_path": path,
        "backup_version_id": backup_version_id,
    }
    collection_name = _find_collection_name_for_path(path)
    if collection_name:
        append_domain_event(
            event_type="RollbackApplied",
            aggregate_type="collection",
            aggregate_id=collection_name,
            payload={
                "collection": collection_name,
                "version_id": str(version_id).strip(),
                "backup_version_id": backup_version_id,
                "path": str(path),
            },
            idempotency_key=f"rollback:{collection_name}:{version_id}",
        )
    return result


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


def _parse_json_payload(path: Path, payload: bytes, default: Any) -> Any:
    try:
        decoded = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise StorageDataError(
            path,
            "json_decode_failed",
            f"数据文件已损坏或编码异常：{path.name}，请检查历史版本后回滚。",
        ) from exc
    try:
        parsed = json.loads(decoded)
    except json.JSONDecodeError as exc:
        raise StorageDataError(
            path,
            "json_parse_failed",
            f"数据文件 JSON 格式损坏：{path.name}（第 {exc.lineno} 行，第 {exc.colno} 列），请使用历史版本回滚。",
        ) from exc
    if isinstance(default, list) and not isinstance(parsed, list):
        raise StorageDataError(
            path,
            "json_shape_mismatch",
            f"数据文件结构异常：{path.name} 应为数组，但实际为 {type(parsed).__name__}。",
        )
    if isinstance(default, dict) and not isinstance(parsed, dict):
        raise StorageDataError(
            path,
            "json_shape_mismatch",
            f"数据文件结构异常：{path.name} 应为对象，但实际为 {type(parsed).__name__}。",
        )
    return parsed


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        payload = read_bytes(path)
    except FileNotFoundError:
        return default
    except OSError as exc:
        raise StorageDataError(
            path, "file_read_failed", f"读取文件失败：{path.name}，{exc}"
        ) from exc
    return _parse_json_payload(path, payload, default)


def load_json_version(path: Path, version_id: str, default: Any) -> Any:
    snapshot_path = _snapshot_path_for(path, str(version_id).strip())
    if not snapshot_path.exists():
        raise VersionedJsonSnapshotNotFound(
            path,
            "snapshot_not_found",
            f"未找到历史版本：{path.stem} / {version_id}",
        )
    try:
        payload = read_bytes(snapshot_path)
    except OSError as exc:
        raise StorageDataError(
            snapshot_path,
            "file_read_failed",
            f"读取历史版本失败：{snapshot_path.name}，{exc}",
        ) from exc
    return _parse_json_payload(snapshot_path, payload, default)


def save_json(path: Path, data: Any, *, keep_history: bool = False) -> None:
    try:
        payload = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise StorageDataError(
            path, "json_serialize_failed", f"写入 JSON 失败：{path.name}，{exc}"
        ) from exc
    if keep_history:
        _write_json_version_snapshot(path, payload)
    try:
        save_bytes(path, payload)
    except OSError as exc:
        raise StorageDataError(
            path, "file_write_failed", f"写入文件失败：{path.name}，{exc}"
        ) from exc


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


class JsonCollectionRepository:
    def load(self, descriptor: CollectionDescriptor) -> Any:
        return load_json(descriptor.path_getter(), descriptor.default_factory())

    def save(
        self, descriptor: CollectionDescriptor, data: Any, *, keep_history: bool = False
    ) -> None:
        save_json(descriptor.path_getter(), data, keep_history=keep_history)

    def exists(self, descriptor: CollectionDescriptor) -> bool:
        return descriptor.path_getter().exists()

    def snapshot_hash(self, descriptor: CollectionDescriptor) -> str | None:
        if not self.exists(descriptor):
            return None
        return _canonical_payload_hash(self.load(descriptor))


class NoOpEventStore:
    def append(self, event: EventEnvelope) -> AppendResult:
        return AppendResult(event=event, inserted=False)

    def list_events(
        self,
        *,
        after_sequence: int = 0,
        event_types: Sequence[str] | None = None,
        aggregate_id: str | None = None,
        aggregate_type: str | None = None,
    ) -> list[EventEnvelope]:
        return []

    def save_projection_snapshot(
        self,
        *,
        name: str,
        last_sequence: int,
        snapshot: dict[str, Any],
    ) -> None:
        return None

    def load_projection_snapshot(self, name: str):
        return None


def _canonical_payload_hash(data: Any) -> str:
    payload = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _metadata_db_path() -> Path:
    override = str(os.environ.get("ZHIFEI_STORAGE_DB_PATH") or "").strip()
    if override:
        return Path(override).expanduser()
    return DATA_DIR / "metadata.sqlite3"


def _event_db_path() -> Path:
    override = str(os.environ.get("ZHIFEI_EVENT_DB_PATH") or "").strip()
    if override:
        return Path(override).expanduser()
    return DATA_DIR / "events.sqlite3"


def get_storage_runtime_config() -> StorageRuntimeConfig:
    primary_backend = str(os.environ.get("ZHIFEI_STORAGE_PRIMARY") or "json").strip().lower()
    if primary_backend not in {"json", "sqlite"}:
        primary_backend = "json"
    return StorageRuntimeConfig(
        primary_backend=primary_backend,
        enable_sqlite_mirror=_env_flag("ZHIFEI_STORAGE_ENABLE_SQLITE_MIRROR", default=False),
        enable_event_log=_env_flag("ZHIFEI_STORAGE_ENABLE_EVENT_LOG", default=False),
        validate_dual_write=_env_flag("ZHIFEI_STORAGE_VALIDATE_DUAL_WRITE", default=False),
        legacy_json_write=_env_flag("ZHIFEI_STORAGE_LEGACY_JSON_WRITE", default=True),
        metadata_db_path=_metadata_db_path(),
        event_db_path=_event_db_path(),
    )


def _build_storage_runtime() -> StorageRuntime:
    config = get_storage_runtime_config()
    json_repository = JsonCollectionRepository()
    sqlite_repository: Repository | None = None
    if config.primary_backend == "sqlite" or config.enable_sqlite_mirror:
        sqlite_repository = SQLiteMetadataRepository(config.metadata_db_path)
    event_store: EventStore
    if config.enable_event_log:
        event_store = SQLiteEventStore(config.event_db_path)
    else:
        event_store = NoOpEventStore()
    artifact_store = FileArtifactStore(MATERIALS_DIR)
    return StorageRuntime(
        config=config,
        json_repository=json_repository,
        sqlite_repository=sqlite_repository,
        event_store=event_store,
        artifact_store=artifact_store,
    )


def get_storage_runtime() -> StorageRuntime:
    return _build_storage_runtime()


def build_storage_backend_status() -> Dict[str, Any]:
    config = get_storage_runtime_config()
    return {
        "primary_backend": config.primary_backend,
        "sqlite_mirror_enabled": config.enable_sqlite_mirror,
        "event_log_enabled": config.enable_event_log,
        "dual_write_validation_enabled": config.validate_dual_write,
        "legacy_json_write": config.legacy_json_write,
        "metadata_db_path": str(config.metadata_db_path),
        "event_db_path": str(config.event_db_path),
        "artifact_root": str(MATERIALS_DIR),
    }


def probe_storage_lock_status() -> Dict[str, Any]:
    ensure_data_dirs()
    probe_path = DATA_DIR / ".storage_lock_probe"
    lock = _get_path_lock(probe_path)
    acquired = False
    try:
        acquired = lock.acquire(timeout=1.0)
        if not acquired:
            return {
                "ok": False,
                "probe_path": str(probe_path),
                "detail": "runtime_lock_timeout",
            }
        with _exclusive_file_lock(probe_path):
            return {
                "ok": True,
                "probe_path": str(probe_path),
                "detail": f"lock_file={probe_path.with_suffix(probe_path.suffix + '.lock')}",
            }
    except Exception as exc:
        return {
            "ok": False,
            "probe_path": str(probe_path),
            "detail": f"{type(exc).__name__}: {exc}",
        }
    finally:
        if acquired:
            lock.release()


def probe_event_log_appendability() -> Dict[str, Any]:
    runtime = _build_storage_runtime()
    if not runtime.config.enable_event_log:
        return {"ok": True, "enabled": False, "detail": "event_log_disabled"}
    if isinstance(runtime.event_store, SQLiteEventStore):
        try:
            with runtime.event_store._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                connection.rollback()
            return {
                "ok": True,
                "enabled": True,
                "backend": "sqlite",
                "db_path": str(runtime.config.event_db_path),
                "detail": "begin_immediate_ok",
            }
        except Exception as exc:
            return {
                "ok": False,
                "enabled": True,
                "backend": "sqlite",
                "db_path": str(runtime.config.event_db_path),
                "detail": f"{type(exc).__name__}: {exc}",
            }
    return {"ok": True, "enabled": True, "backend": type(runtime.event_store).__name__}


def _rebuild_project_activity_projection_full() -> Dict[str, Any]:
    runtime = _build_storage_runtime()
    if not runtime.config.enable_event_log:
        return {
            "projection_name": "project_activity",
            "event_log_enabled": False,
            "projects": [],
            "projects_by_id": {},
            "last_sequence": 0,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
    state: Dict[str, Dict[str, Any]] = {}
    last_sequence = 0
    events = runtime.event_store.list_events(after_sequence=0)
    for event in events:
        _apply_project_audit_projection(state, event)
        last_sequence = int(event.sequence_no or last_sequence)
    projects = sorted(
        state.values(),
        key=lambda item: (str(item.get("created_at") or ""), str(item.get("project_id") or "")),
    )
    return {
        "projection_name": "project_activity",
        "event_log_enabled": True,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "last_sequence": last_sequence,
        "project_count": len(projects),
        "projects": projects,
        "projects_by_id": state,
    }


def probe_projection_consistency() -> Dict[str, Any]:
    runtime = _build_storage_runtime()
    if not runtime.config.enable_event_log:
        return {"ok": True, "enabled": False, "detail": "event_log_disabled"}
    replayed = replay_project_activity_projection(persist=False)
    rebuilt = _rebuild_project_activity_projection_full()
    replay_hash = _canonical_payload_hash(replayed.get("projects_by_id") or {})
    rebuilt_hash = _canonical_payload_hash(rebuilt.get("projects_by_id") or {})
    replay_last_sequence = int(replayed.get("last_sequence") or 0)
    rebuilt_last_sequence = int(rebuilt.get("last_sequence") or 0)
    ok = replay_hash == rebuilt_hash and replay_last_sequence == rebuilt_last_sequence
    return {
        "ok": ok,
        "enabled": True,
        "projection_name": "project_activity",
        "detail": (
            f"snapshot_hash={replay_hash[:12]}, rebuilt_hash={rebuilt_hash[:12]}, "
            f"snapshot_last_sequence={replay_last_sequence}, rebuilt_last_sequence={rebuilt_last_sequence}"
        ),
        "snapshot_hash": replay_hash,
        "rebuilt_hash": rebuilt_hash,
        "snapshot_last_sequence": replay_last_sequence,
        "rebuilt_last_sequence": rebuilt_last_sequence,
    }


def probe_learning_artifact_versions() -> Dict[str, Any]:
    artifacts = [
        ("calibration_models", CALIBRATION_MODELS_PATH, load_calibration_models()),
        ("high_score_features", HIGH_SCORE_FEATURES_PATH, load_high_score_features()),
        ("evolution_reports", EVOLUTION_REPORTS_PATH, _load_collection("evolution_reports")),
    ]
    rows: List[Dict[str, Any]] = []
    overall_ok = True
    for name, path, payload in artifacts:
        record_count = len(payload) if isinstance(payload, list) else len(payload.keys())
        version_count = len(list_json_versions(path))
        row_ok = record_count <= 0 or version_count > 0
        overall_ok = overall_ok and row_ok
        rows.append(
            {
                "name": name,
                "path": str(path),
                "record_count": int(record_count),
                "version_count": int(version_count),
                "ok": row_ok,
            }
        )
    detail = "; ".join(
        f"{row['name']}:records={row['record_count']},versions={row['version_count']}"
        for row in rows
    )
    return {"ok": overall_ok, "rows": rows, "detail": detail}


def _collection_descriptors() -> Dict[str, CollectionDescriptor]:
    return {
        "projects": CollectionDescriptor(
            name="projects",
            path_getter=lambda: PROJECTS_PATH,
            default_factory=list,
            shape="list",
            entity_kind="project",
            storage_tier="database",
        ),
        "submissions": CollectionDescriptor(
            name="submissions",
            path_getter=lambda: SUBMISSIONS_PATH,
            default_factory=list,
            shape="list",
            entity_kind="submission",
            storage_tier="database",
        ),
        "materials": CollectionDescriptor(
            name="materials",
            path_getter=lambda: MATERIALS_PATH,
            default_factory=list,
            shape="list",
            entity_kind="material",
            storage_tier="database",
        ),
        "material_parse_jobs": CollectionDescriptor(
            name="material_parse_jobs",
            path_getter=lambda: MATERIAL_PARSE_JOBS_PATH,
            default_factory=list,
            shape="list",
            entity_kind="task_state",
            storage_tier="database",
        ),
        "learning_profiles": CollectionDescriptor(
            name="learning_profiles",
            path_getter=lambda: LEARNING_PATH,
            default_factory=list,
            shape="list",
            entity_kind="learning_profile",
            storage_tier="database",
        ),
        "score_history": CollectionDescriptor(
            name="score_history",
            path_getter=lambda: HISTORY_PATH,
            default_factory=list,
            shape="list",
            entity_kind="score_history",
            storage_tier="database",
        ),
        "project_context": CollectionDescriptor(
            name="project_context",
            path_getter=lambda: PROJECT_CONTEXT_PATH,
            default_factory=dict,
            shape="dict",
            entity_kind="project_context",
            storage_tier="database",
        ),
        "ground_truth": CollectionDescriptor(
            name="ground_truth",
            path_getter=lambda: GROUND_TRUTH_PATH,
            default_factory=list,
            shape="list",
            entity_kind="ground_truth_record",
            storage_tier="database",
        ),
        "evolution_reports": CollectionDescriptor(
            name="evolution_reports",
            path_getter=lambda: EVOLUTION_REPORTS_PATH,
            default_factory=dict,
            shape="dict",
            entity_kind="learning_artifact_index",
            keep_history=True,
            storage_tier="database",
        ),
        "expert_profiles": CollectionDescriptor(
            name="expert_profiles",
            path_getter=lambda: EXPERT_PROFILES_PATH,
            default_factory=list,
            shape="list",
            entity_kind="expert_profile",
            keep_history=True,
            storage_tier="database",
        ),
        "score_reports": CollectionDescriptor(
            name="score_reports",
            path_getter=lambda: SCORE_REPORTS_PATH,
            default_factory=list,
            shape="list",
            entity_kind="score_report_index",
            storage_tier="database",
        ),
        "project_anchors": CollectionDescriptor(
            name="project_anchors",
            path_getter=lambda: PROJECT_ANCHORS_PATH,
            default_factory=list,
            shape="list",
            entity_kind="project_anchor",
            storage_tier="database",
        ),
        "project_requirements": CollectionDescriptor(
            name="project_requirements",
            path_getter=lambda: PROJECT_REQUIREMENTS_PATH,
            default_factory=list,
            shape="list",
            entity_kind="project_requirement",
            storage_tier="database",
        ),
        "evidence_units": CollectionDescriptor(
            name="evidence_units",
            path_getter=lambda: EVIDENCE_UNITS_PATH,
            default_factory=list,
            shape="list",
            entity_kind="evidence_unit_index",
            storage_tier="database",
        ),
        "qingtian_results": CollectionDescriptor(
            name="qingtian_results",
            path_getter=lambda: QINGTIAN_RESULTS_PATH,
            default_factory=list,
            shape="list",
            entity_kind="actual_result_index",
            storage_tier="database",
        ),
        "calibration_models": CollectionDescriptor(
            name="calibration_models",
            path_getter=lambda: CALIBRATION_MODELS_PATH,
            default_factory=list,
            shape="list",
            entity_kind="calibration_model_index",
            keep_history=True,
            storage_tier="database",
        ),
        "delta_cases": CollectionDescriptor(
            name="delta_cases",
            path_getter=lambda: DELTA_CASES_PATH,
            default_factory=list,
            shape="list",
            entity_kind="delta_case",
            storage_tier="database",
        ),
        "calibration_samples": CollectionDescriptor(
            name="calibration_samples",
            path_getter=lambda: CALIBRATION_SAMPLES_PATH,
            default_factory=list,
            shape="list",
            entity_kind="calibration_sample",
            storage_tier="database",
        ),
        "patch_packages": CollectionDescriptor(
            name="patch_packages",
            path_getter=lambda: PATCH_PACKAGES_PATH,
            default_factory=list,
            shape="list",
            entity_kind="patch_package",
            storage_tier="database",
        ),
        "patch_deployments": CollectionDescriptor(
            name="patch_deployments",
            path_getter=lambda: PATCH_DEPLOYMENTS_PATH,
            default_factory=list,
            shape="list",
            entity_kind="governance_action",
            storage_tier="database",
        ),
        "high_score_features": CollectionDescriptor(
            name="high_score_features",
            path_getter=lambda: HIGH_SCORE_FEATURES_PATH,
            default_factory=list,
            shape="list",
            entity_kind="feature_pack_index",
            keep_history=True,
            storage_tier="database",
        ),
    }


def get_registered_collection_descriptors() -> Dict[str, CollectionDescriptor]:
    return dict(_collection_descriptors())


def _get_collection_descriptor(name: str) -> CollectionDescriptor:
    descriptor = _collection_descriptors().get(str(name))
    if descriptor is None:
        raise KeyError(f"unknown_storage_collection:{name}")
    return descriptor


def _find_collection_name_for_path(path: Path) -> str | None:
    resolved = path.resolve()
    for name, descriptor in _collection_descriptors().items():
        try:
            if descriptor.path_getter().resolve() == resolved:
                return name
        except FileNotFoundError:
            continue
    return None


def _coerce_runtime_storage_error(
    exc: Exception, *, fallback_path: Path, action: str
) -> StorageDataError:
    if isinstance(exc, StorageDataError):
        return exc
    if isinstance(exc, SQLiteMetadataError):
        return StorageDataError(fallback_path, exc.code, exc.detail)
    if isinstance(exc, SQLiteEventStoreError):
        return StorageDataError(fallback_path, exc.code, exc.detail)
    return StorageDataError(fallback_path, action, str(exc))


def _load_collection(name: str) -> Any:
    descriptor = _get_collection_descriptor(name)
    runtime = _build_storage_runtime()
    if runtime.config.primary_backend == "sqlite" and runtime.sqlite_repository is not None:
        try:
            if runtime.sqlite_repository.exists(descriptor):
                return runtime.sqlite_repository.load(descriptor)
        except Exception as exc:
            logger.warning(
                "sqlite_primary_read_failed collection=%s detail=%s; fallback=json",
                name,
                exc,
            )
    return runtime.json_repository.load(descriptor)


def load_collection(descriptor: CollectionDescriptor) -> Any:
    return _load_collection(descriptor.name)


def _validate_dual_write(
    runtime: StorageRuntime, descriptor: CollectionDescriptor
) -> Dict[str, Any]:
    if runtime.sqlite_repository is None:
        return {"collection": descriptor.name, "matched": False, "reason": "sqlite_disabled"}
    json_hash = runtime.json_repository.snapshot_hash(descriptor)
    sqlite_hash = runtime.sqlite_repository.snapshot_hash(descriptor)
    matched = json_hash == sqlite_hash
    return {
        "collection": descriptor.name,
        "matched": matched,
        "json_hash": json_hash,
        "sqlite_hash": sqlite_hash,
    }


def _emit_save_side_effect_events(name: str, previous: Any, current: Any) -> None:
    if not get_storage_runtime_config().enable_event_log:
        return
    if name == "calibration_models":
        previous_versions = {
            str(item.get("version") or item.get("id") or "").strip()
            for item in previous or []
            if isinstance(item, dict)
        }
        for row in current or []:
            if not isinstance(row, dict):
                continue
            version = str(row.get("version") or row.get("id") or "").strip()
            if not version or version in previous_versions:
                continue
            append_domain_event(
                event_type="CalibratorTrained",
                aggregate_type="project",
                aggregate_id=str(row.get("project_id") or "global"),
                payload={
                    "collection": name,
                    "version": version,
                    "project_id": row.get("project_id"),
                    "metrics": dict(row.get("metrics") or {}),
                    "sample_count": row.get("sample_count"),
                    "explanation": row.get("explanation"),
                },
                idempotency_key=f"calibrator-trained:{version}",
            )
    if name == "high_score_features":
        previous_ids = {
            str(item.get("id") or item.get("feature_id") or "").strip()
            for item in previous or []
            if isinstance(item, dict)
        }
        for row in current or []:
            if not isinstance(row, dict):
                continue
            feature_id = str(row.get("id") or row.get("feature_id") or "").strip()
            if not feature_id or feature_id in previous_ids:
                continue
            append_domain_event(
                event_type="FeaturePackUpdated",
                aggregate_type="project",
                aggregate_id=str(row.get("project_id") or "global"),
                payload={
                    "collection": name,
                    "feature_id": feature_id,
                    "project_id": row.get("project_id"),
                    "feature_name": row.get("name"),
                    "confidence": row.get("confidence"),
                    "reason": row.get("reason"),
                },
                idempotency_key=f"feature-pack-updated:{feature_id}",
            )


def _save_collection(name: str, data: Any, *, keep_history: bool | None = None) -> None:
    descriptor = _get_collection_descriptor(name)
    runtime = _build_storage_runtime()
    effective_keep_history = descriptor.keep_history if keep_history is None else keep_history
    should_write_sqlite = runtime.sqlite_repository is not None and (
        runtime.config.primary_backend == "sqlite" or runtime.config.enable_sqlite_mirror
    )
    should_write_json = runtime.config.primary_backend == "json" or runtime.config.legacy_json_write
    previous_payload = _load_collection(name) if runtime.config.enable_event_log else None

    if runtime.config.primary_backend == "sqlite" and runtime.sqlite_repository is not None:
        try:
            runtime.sqlite_repository.save(descriptor, data, keep_history=effective_keep_history)
        except Exception as exc:
            raise _coerce_runtime_storage_error(
                exc,
                fallback_path=descriptor.path_getter(),
                action="sqlite_primary_write_failed",
            ) from exc
        if should_write_json:
            try:
                runtime.json_repository.save(descriptor, data, keep_history=effective_keep_history)
            except Exception as exc:
                logger.warning(
                    "legacy_json_write_failed collection=%s detail=%s",
                    descriptor.name,
                    exc,
                )
    else:
        runtime.json_repository.save(descriptor, data, keep_history=effective_keep_history)
        if should_write_sqlite and runtime.sqlite_repository is not None:
            try:
                runtime.sqlite_repository.save(
                    descriptor, data, keep_history=effective_keep_history
                )
            except Exception as exc:
                logger.warning(
                    "sqlite_mirror_write_failed collection=%s detail=%s",
                    descriptor.name,
                    exc,
                )
    if runtime.config.validate_dual_write and should_write_json and should_write_sqlite:
        validation = _validate_dual_write(runtime, descriptor)
        if not validation.get("matched"):
            raise StorageDataError(
                descriptor.path_getter(),
                "dual_write_validation_failed",
                f"JSON/SQLite 双写校验失败：{descriptor.name}",
            )
    _emit_save_side_effect_events(name, previous_payload, data)


def save_collection(
    descriptor: CollectionDescriptor,
    data: Any,
    *,
    keep_history: bool | None = None,
) -> None:
    _save_collection(descriptor.name, data, keep_history=keep_history)


def collection_snapshot_hash(descriptor: CollectionDescriptor) -> str | None:
    runtime = _build_storage_runtime()
    if runtime.config.primary_backend == "sqlite" and runtime.sqlite_repository is not None:
        try:
            if runtime.sqlite_repository.exists(descriptor):
                return runtime.sqlite_repository.snapshot_hash(descriptor)
        except Exception:
            logger.warning("sqlite_snapshot_hash_failed collection=%s", descriptor.name)
    return runtime.json_repository.snapshot_hash(descriptor)


def ensure_sqlite_seeded(*, collections: Sequence[str] | None = None) -> Dict[str, Any]:
    runtime = _build_storage_runtime()
    sqlite_repository = SQLiteMetadataRepository(runtime.config.metadata_db_path)
    requested = list(collections or _collection_descriptors().keys())
    rows: List[Dict[str, Any]] = []
    for name in requested:
        descriptor = _get_collection_descriptor(name)
        payload = runtime.json_repository.load(descriptor)
        sqlite_repository.save(descriptor, payload, keep_history=descriptor.keep_history)
        rows.append(
            {
                "collection": name,
                "records_hash": _canonical_payload_hash(payload),
                "record_count": len(payload) if isinstance(payload, list) else len(payload.keys()),
            }
        )
    return {
        "metadata_db_path": str(runtime.config.metadata_db_path),
        "seeded_collections": rows,
        "count": len(rows),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def validate_storage_sync(*, collections: Sequence[str] | None = None) -> Dict[str, Any]:
    runtime = _build_storage_runtime()
    if runtime.sqlite_repository is None:
        runtime = StorageRuntime(
            config=runtime.config,
            json_repository=runtime.json_repository,
            sqlite_repository=SQLiteMetadataRepository(runtime.config.metadata_db_path),
            event_store=runtime.event_store,
            artifact_store=runtime.artifact_store,
        )
    requested = list(collections or _collection_descriptors().keys())
    rows = [_validate_dual_write(runtime, _get_collection_descriptor(name)) for name in requested]
    matched = sum(1 for row in rows if row.get("matched"))
    return {
        "matched_collections": matched,
        "total_collections": len(rows),
        "rows": rows,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def get_artifact_store() -> ArtifactStore:
    return _build_storage_runtime().artifact_store


def store_artifact_copy(
    *,
    project_id: str,
    artifact_type: str,
    source_path: Path,
    filename: str,
) -> StoredArtifact:
    return get_artifact_store().store_file(
        project_id=project_id,
        artifact_type=artifact_type,
        source_path=source_path,
        filename=filename,
    )


def remove_project_artifacts(project_id: str) -> None:
    get_artifact_store().remove_project(project_id)


def append_domain_event(
    *,
    event_type: str,
    aggregate_type: str,
    aggregate_id: str,
    payload: Dict[str, Any],
    actor_type: str = "system",
    actor_id: str = "system",
    correlation_id: str | None = None,
    causation_id: str | None = None,
    idempotency_key: str | None = None,
    metadata: Dict[str, Any] | None = None,
    event_version: int = 1,
) -> Dict[str, Any]:
    runtime = _build_storage_runtime()
    if not runtime.config.enable_event_log:
        return {"inserted": False, "disabled": True, "event_type": event_type}
    try:
        from app.application.task_runtime import current_runtime_context

        runtime_context = current_runtime_context()
    except Exception:
        runtime_context = {}
    effective_correlation_id = (
        correlation_id or str(runtime_context.get("correlation_id") or "").strip() or None
    )
    effective_metadata = dict(metadata or {})
    for key in ("run_id", "task_kind", "task_name"):
        value = str(runtime_context.get(key) or "").strip()
        if value and key not in effective_metadata:
            effective_metadata[key] = value
    event = EventEnvelope(
        event_id=str(os.urandom(16).hex()),
        aggregate_type=str(aggregate_type),
        aggregate_id=str(aggregate_id),
        event_type=str(event_type),
        event_version=int(event_version),
        payload=dict(payload or {}),
        occurred_at=datetime.now(timezone.utc).isoformat(),
        actor_type=str(actor_type),
        actor_id=str(actor_id),
        causation_id=causation_id,
        correlation_id=effective_correlation_id,
        idempotency_key=idempotency_key,
        metadata=effective_metadata,
    )
    try:
        result = runtime.event_store.append(event)
    except Exception as exc:
        logger.warning(
            "domain_event_append_failed event_type=%s aggregate_type=%s aggregate_id=%s detail=%s",
            event_type,
            aggregate_type,
            aggregate_id,
            exc,
        )
        return {
            "inserted": False,
            "disabled": False,
            "event_type": event_type,
            "error": str(exc),
        }
    return {
        "inserted": result.inserted,
        "sequence_no": result.event.sequence_no,
        "event_id": result.event.event_id,
        "event_type": result.event.event_type,
    }


def list_domain_events(
    *,
    after_sequence: int = 0,
    event_types: Sequence[str] | None = None,
    aggregate_id: str | None = None,
    aggregate_type: str | None = None,
) -> List[Dict[str, Any]]:
    runtime = _build_storage_runtime()
    rows = runtime.event_store.list_events(
        after_sequence=after_sequence,
        event_types=event_types,
        aggregate_id=aggregate_id,
        aggregate_type=aggregate_type,
    )
    return [
        {
            "sequence_no": row.sequence_no,
            "event_id": row.event_id,
            "aggregate_type": row.aggregate_type,
            "aggregate_id": row.aggregate_id,
            "event_type": row.event_type,
            "event_version": row.event_version,
            "payload": row.payload,
            "occurred_at": row.occurred_at,
            "actor_type": row.actor_type,
            "actor_id": row.actor_id,
            "causation_id": row.causation_id,
            "correlation_id": row.correlation_id,
            "idempotency_key": row.idempotency_key,
            "metadata": row.metadata,
        }
        for row in rows
    ]


def _apply_project_audit_projection(
    state: Dict[str, Dict[str, Any]],
    event: EventEnvelope,
) -> Dict[str, Dict[str, Any]]:
    aggregate_id = str(event.aggregate_id or "").strip()
    project_id = str(event.payload.get("project_id") or aggregate_id).strip() or aggregate_id
    if not project_id:
        return state
    row = state.setdefault(
        project_id,
        {
            "project_id": project_id,
            "project_name": None,
            "created_at": None,
            "artifact_upload_count": 0,
            "score_count": 0,
            "actual_result_count": 0,
            "calibrator_count": 0,
            "feature_pack_update_count": 0,
            "governance_decision_count": 0,
            "rollback_count": 0,
            "ops_check_count": 0,
            "last_event_at": None,
        },
    )
    row["last_event_at"] = event.occurred_at
    if event.event_type == "ProjectCreated":
        row["project_name"] = event.payload.get("name")
        row["created_at"] = event.occurred_at
    elif event.event_type == "ArtifactUploaded":
        row["artifact_upload_count"] += 1
    elif event.event_type == "ScoreComputed":
        row["score_count"] += 1
    elif event.event_type == "ActualResultRecorded":
        row["actual_result_count"] += 1
    elif event.event_type == "CalibratorTrained":
        row["calibrator_count"] += 1
    elif event.event_type == "FeaturePackUpdated":
        row["feature_pack_update_count"] += 1
    elif event.event_type == "GovernanceDecisionApplied":
        row["governance_decision_count"] += 1
    elif event.event_type == "RollbackApplied":
        row["rollback_count"] += 1
    elif event.event_type == "OpsCheckExecuted":
        row["ops_check_count"] += 1
    return state


def replay_project_activity_projection(*, persist: bool = False) -> Dict[str, Any]:
    runtime = _build_storage_runtime()
    if not runtime.config.enable_event_log:
        return {
            "projection_name": "project_activity",
            "event_log_enabled": False,
            "projects": [],
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
    snapshot = runtime.event_store.load_projection_snapshot("project_activity")
    state = (
        dict(snapshot.snapshot.get("projects_by_id") or {})
        if snapshot and isinstance(snapshot.snapshot, dict)
        else {}
    )
    after_sequence = snapshot.last_sequence if snapshot else 0
    events = runtime.event_store.list_events(after_sequence=after_sequence)
    last_sequence = after_sequence
    for event in events:
        _apply_project_audit_projection(state, event)
        last_sequence = int(event.sequence_no or last_sequence)
    projects = sorted(
        state.values(),
        key=lambda item: (str(item.get("created_at") or ""), str(item.get("project_id") or "")),
    )
    payload = {
        "projection_name": "project_activity",
        "event_log_enabled": True,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "last_sequence": last_sequence,
        "project_count": len(projects),
        "projects": projects,
        "projects_by_id": state,
    }
    if persist:
        runtime.event_store.save_projection_snapshot(
            name="project_activity",
            last_sequence=last_sequence,
            snapshot=payload,
        )
    return payload


def load_projects() -> List[Dict[str, Any]]:
    return _load_collection("projects")


def save_projects(data: List[Dict[str, Any]]) -> None:
    _save_collection("projects", data)


def load_submissions() -> List[Dict[str, Any]]:
    return _load_collection("submissions")


def save_submissions(data: List[Dict[str, Any]]) -> None:
    _save_collection("submissions", data)


def load_materials() -> List[Dict[str, Any]]:
    return _load_collection("materials")


def save_materials(data: List[Dict[str, Any]]) -> None:
    _save_collection("materials", data)


def load_material_parse_jobs() -> List[Dict[str, Any]]:
    return _load_collection("material_parse_jobs")


def save_material_parse_jobs(data: List[Dict[str, Any]]) -> None:
    _save_collection("material_parse_jobs", data)


def load_learning_profiles() -> List[Dict[str, Any]]:
    return _load_collection("learning_profiles")


def save_learning_profiles(data: List[Dict[str, Any]]) -> None:
    _save_collection("learning_profiles", data)


def load_score_history() -> List[Dict[str, Any]]:
    return _load_collection("score_history")


def save_score_history(data: List[Dict[str, Any]]) -> None:
    _save_collection("score_history", data)


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
    return _load_collection("project_context")


def save_project_context(data: Dict[str, Any]) -> None:
    _save_collection("project_context", data)


def load_ground_truth() -> List[Dict[str, Any]]:
    """真实评标记录列表（青天大模型等外部评标结果）"""
    return _load_collection("ground_truth")


def save_ground_truth(data: List[Dict[str, Any]]) -> None:
    _save_collection("ground_truth", data)


def load_evolution_reports() -> Dict[str, Any]:
    """project_id -> 进化报告（高分逻辑、编制指导等）"""
    return _load_collection("evolution_reports")


def save_evolution_reports(data: Dict[str, Any]) -> None:
    _save_collection("evolution_reports", data, keep_history=True)


def load_expert_profiles() -> List[Dict[str, Any]]:
    """专家关注度配置列表"""
    return _load_collection("expert_profiles")


def save_expert_profiles(data: List[Dict[str, Any]]) -> None:
    _save_collection("expert_profiles", data, keep_history=True)


def load_score_reports() -> List[Dict[str, Any]]:
    """评分报告快照列表（不覆盖历史）"""
    return _load_collection("score_reports")


def save_score_reports(data: List[Dict[str, Any]]) -> None:
    _save_collection("score_reports", data)


def load_project_anchors() -> List[Dict[str, Any]]:
    """项目锚点列表"""
    return _load_collection("project_anchors")


def save_project_anchors(data: List[Dict[str, Any]]) -> None:
    _save_collection("project_anchors", data)


def load_project_requirements() -> List[Dict[str, Any]]:
    """项目要求矩阵列表"""
    return _load_collection("project_requirements")


def save_project_requirements(data: List[Dict[str, Any]]) -> None:
    _save_collection("project_requirements", data)


def load_evidence_units() -> List[Dict[str, Any]]:
    """证据单元列表"""
    return _load_collection("evidence_units")


def save_evidence_units(data: List[Dict[str, Any]]) -> None:
    _save_collection("evidence_units", data)


def load_qingtian_results() -> List[Dict[str, Any]]:
    """真实青天评标结果列表"""
    return _load_collection("qingtian_results")


def save_qingtian_results(data: List[Dict[str, Any]]) -> None:
    _save_collection("qingtian_results", data)


def load_calibration_models() -> List[Dict[str, Any]]:
    """校准器版本列表"""
    return _load_collection("calibration_models")


def save_calibration_models(data: List[Dict[str, Any]]) -> None:
    _save_collection("calibration_models", data, keep_history=True)


def load_delta_cases() -> List[Dict[str, Any]]:
    """误差案例（DELTA_CASE）列表"""
    return _load_collection("delta_cases")


def save_delta_cases(data: List[Dict[str, Any]]) -> None:
    _save_collection("delta_cases", data)


def load_calibration_samples() -> List[Dict[str, Any]]:
    """校准训练样本（FEATURE_ROW）列表"""
    return _load_collection("calibration_samples")


def save_calibration_samples(data: List[Dict[str, Any]]) -> None:
    _save_collection("calibration_samples", data)


def load_patch_packages() -> List[Dict[str, Any]]:
    """候选补丁包列表"""
    return _load_collection("patch_packages")


def save_patch_packages(data: List[Dict[str, Any]]) -> None:
    _save_collection("patch_packages", data)


def load_patch_deployments() -> List[Dict[str, Any]]:
    """补丁发布记录列表"""
    return _load_collection("patch_deployments")


def save_patch_deployments(data: List[Dict[str, Any]]) -> None:
    _save_collection("patch_deployments", data)


def load_high_score_features() -> List[Dict[str, Any]]:
    """高分逻辑骨架特征库（可更新置信度）"""
    return _load_collection("high_score_features")


def save_high_score_features(data: List[Dict[str, Any]]) -> None:
    _save_collection("high_score_features", data, keep_history=True)
