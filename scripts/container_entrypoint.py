from __future__ import annotations

import json
import os
import sys
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import MutableMapping

API_KEYS_FILE_ENV = "QINGTIAN_API_KEYS_FILE"
PLACEHOLDER_KEY_FRAGMENTS = ("change-me", "your-secret", "example", "placeholder")


@dataclass(frozen=True)
class DeploymentConfig:
    environment: str
    port: int
    workers: int
    storage_backend: str
    data_directory: Path
    sqlite_path: Path | None
    log_level: str
    forwarded_allow_ips: str


def _normalized_api_keys(raw: str) -> list[str]:
    return [part.strip() for line in raw.splitlines() for part in line.split(",") if part.strip()]


def configure_api_keys(
    environment: MutableMapping[str, str],
    *,
    read_text=None,
) -> list[str]:
    read_text = read_text or (lambda path: path.read_text(encoding="utf-8"))
    secret_path = environment.get(API_KEYS_FILE_ENV, "").strip()
    raw = environment.get("API_KEYS", "")
    if secret_path:
        path = Path(secret_path).expanduser()
        try:
            raw = read_text(path)
        except OSError as exc:
            raise RuntimeError(f"cannot read API key secret file: {path}") from exc
    keys = _normalized_api_keys(raw)
    if keys:
        environment["API_KEYS"] = ",".join(keys)
    return keys


def load_deployment_config(environment: MutableMapping[str, str]) -> DeploymentConfig:
    deployment_environment = environment.get("QINGTIAN_ENV", "production").strip().lower()
    if deployment_environment not in {"development", "test", "production"}:
        raise RuntimeError("QINGTIAN_ENV must be development, test, or production")

    keys = configure_api_keys(environment)
    if deployment_environment == "production":
        if not keys:
            raise RuntimeError("production requires API_KEYS or QINGTIAN_API_KEYS_FILE")
        if any(fragment in key.lower() for key in keys for fragment in PLACEHOLDER_KEY_FRAGMENTS):
            raise RuntimeError("production API key contains a placeholder value")

    try:
        port = int(environment.get("PORT", "8000"))
    except ValueError as exc:
        raise RuntimeError("PORT must be an integer") from exc
    if not 1 <= port <= 65535:
        raise RuntimeError("PORT must be between 1 and 65535")

    try:
        workers = int(environment.get("WEB_CONCURRENCY", "1"))
    except ValueError as exc:
        raise RuntimeError("WEB_CONCURRENCY must be an integer") from exc
    if workers != 1:
        raise RuntimeError("WEB_CONCURRENCY must remain 1 until shared cache coordination exists")

    storage_backend = environment.get("QINGTIAN_STORAGE_BACKEND", "sqlite").strip().lower()
    if storage_backend not in {"json", "sqlite"}:
        raise RuntimeError("QINGTIAN_STORAGE_BACKEND must be json or sqlite")

    data_directory = Path(environment.get("QINGTIAN_DATA_DIR", "/var/lib/qingtian")).expanduser()
    if not data_directory.is_absolute():
        raise RuntimeError("QINGTIAN_DATA_DIR must be an absolute path")
    sqlite_path: Path | None = None
    if storage_backend == "sqlite":
        sqlite_path = Path(
            environment.get(
                "QINGTIAN_SQLITE_PATH",
                str(data_directory / "qingtian.sqlite3"),
            )
        ).expanduser()
        if not sqlite_path.is_absolute():
            raise RuntimeError("QINGTIAN_SQLITE_PATH must be an absolute path")
        environment["QINGTIAN_SQLITE_PATH"] = str(sqlite_path)

    log_level = environment.get("QINGTIAN_LOG_LEVEL", "info").strip().lower()
    if log_level not in {"critical", "error", "warning", "info", "debug", "trace"}:
        raise RuntimeError("QINGTIAN_LOG_LEVEL is invalid")
    forwarded_allow_ips = environment.get("FORWARDED_ALLOW_IPS", "127.0.0.1").strip()
    if not forwarded_allow_ips:
        raise RuntimeError("FORWARDED_ALLOW_IPS must not be empty")

    environment["QINGTIAN_DATA_DIR"] = str(data_directory)
    environment["QINGTIAN_STORAGE_BACKEND"] = storage_backend
    environment["PORT"] = str(port)
    return DeploymentConfig(
        environment=deployment_environment,
        port=port,
        workers=workers,
        storage_backend=storage_backend,
        data_directory=data_directory,
        sqlite_path=sqlite_path,
        log_level=log_level,
        forwarded_allow_ips=forwarded_allow_ips,
    )


def prepare_runtime(config: DeploymentConfig) -> dict[str, object]:
    config.data_directory.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        prefix=".startup-",
        suffix=".tmp",
        dir=config.data_directory,
    ) as handle:
        handle.write(b"qingtian-startup-check")
        handle.flush()

    from app import storage
    from app.config import load_config

    load_config()
    storage.ensure_data_dirs()
    if storage.active_storage_backend() != config.storage_backend:
        raise RuntimeError("active storage backend differs from deployment configuration")
    integrity = None
    if config.storage_backend == "sqlite":
        backend = storage._SQLITE_BACKEND
        if backend is None:
            raise RuntimeError("SQLite backend was not initialized")
        integrity = backend.integrity_check()
        if integrity != "ok":
            raise RuntimeError(f"SQLite integrity check failed: {integrity}")
    return {
        "environment": config.environment,
        "port": config.port,
        "workers": config.workers,
        "storage_backend": config.storage_backend,
        "data_directory": str(config.data_directory),
        "sqlite_integrity": integrity,
    }


def build_uvicorn_command(config: DeploymentConfig) -> list[str]:
    return [
        sys.executable,
        "-m",
        "uvicorn",
        "app.main:app",
        "--host",
        "0.0.0.0",
        "--port",
        str(config.port),
        "--workers",
        str(config.workers),
        "--log-level",
        config.log_level,
        "--proxy-headers",
        "--forwarded-allow-ips",
        config.forwarded_allow_ips,
        "--timeout-graceful-shutdown",
        "30",
    ]


def public_config(config: DeploymentConfig) -> dict[str, object]:
    result = asdict(config)
    result["data_directory"] = str(config.data_directory)
    result["sqlite_path"] = str(config.sqlite_path) if config.sqlite_path else None
    return result


def main() -> int:
    try:
        config = load_deployment_config(os.environ)
        runtime = prepare_runtime(config)
    except BaseException as exc:
        print(
            json.dumps(
                {"status": "blocked", "error": f"{type(exc).__name__}: {exc}"},
                ensure_ascii=False,
            ),
            file=sys.stderr,
            flush=True,
        )
        return 78
    print(
        json.dumps(
            {"status": "starting", "configuration": public_config(config), "runtime": runtime},
            ensure_ascii=False,
        ),
        flush=True,
    )
    command = build_uvicorn_command(config)
    os.execv(command[0], command)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
