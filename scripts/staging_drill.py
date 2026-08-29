from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

SCHEMA = "qingtian-r8-backup-restore-drill-v1"
DATA_DESTINATION = "/var/lib/qingtian"
CONFIG_DESTINATION = "/srv/qingtian/app/resources"


class DrillBlocked(RuntimeError):
    pass


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    candidate = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    candidate.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(candidate, path)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _run(args: Sequence[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(args),
        check=check,
        capture_output=True,
        text=True,
    )


def _compose_args(repo_root: Path, project: str) -> list[str]:
    return [
        "docker",
        "compose",
        "--project-directory",
        str(repo_root),
        "--project-name",
        project,
        "-f",
        str(repo_root / "compose.yaml"),
        "-f",
        str(repo_root / "docker-compose.monitoring.yml"),
    ]


def _container_id(compose_args: Sequence[str]) -> str:
    result = _run([*compose_args, "ps", "-q", "app"])
    container_id = result.stdout.strip()
    if not container_id:
        raise DrillBlocked("staging app container is absent")
    return container_id


def _mount_names(container: str) -> dict[str, str]:
    payload = json.loads(
        _run(["docker", "inspect", "--format", "{{json .Mounts}}", container]).stdout
    )
    result = {
        str(item.get("Destination")): str(item.get("Name"))
        for item in payload
        if item.get("Type") == "volume"
    }
    for destination in (DATA_DESTINATION, CONFIG_DESTINATION):
        if not result.get(destination):
            raise DrillBlocked(f"named volume is missing for {destination}")
    return result


def _repository_snapshot(container: str) -> dict[str, Any]:
    code = """
import json
from app import storage
from app.storage_migration import snapshot_fingerprint

backend = storage._SQLITE_BACKEND
if backend is None:
    raise SystemExit('sqlite backend is not active')
snapshot = {name: backend.load(name) for name in backend.store_names}
print(json.dumps({
    'fingerprint': snapshot_fingerprint(snapshot),
    'integrity_check': backend.integrity_check(),
    'journal_mode': backend.journal_mode(),
    'store_count': len(snapshot),
}, sort_keys=True))
"""
    result = _run(["docker", "exec", container, "python", "-c", code])
    return json.loads(result.stdout)


def _offline_repository_state(image: str, data_volume: str) -> dict[str, Any]:
    """Checkpoint and fingerprint the source only after application writes stop."""
    code = """
import json
from app import storage
from app.storage_migration import snapshot_fingerprint

backend = storage._SQLITE_BACKEND
if backend is None:
    raise SystemExit('sqlite backend is not active')
checkpoint = list(backend.checkpoint())
snapshot = {name: backend.load(name) for name in backend.store_names}
print(json.dumps({
    'checkpoint': checkpoint,
    'fingerprint': snapshot_fingerprint(snapshot),
    'integrity_check': backend.integrity_check(),
    'journal_mode': backend.journal_mode(),
    'store_count': len(snapshot),
}, sort_keys=True))
"""
    result = _run(
        [
            "docker",
            "run",
            "--rm",
            "--entrypoint",
            "python",
            "--env",
            "QINGTIAN_ENV=production",
            "--env",
            "QINGTIAN_DATA_DIR=/var/lib/qingtian",
            "--env",
            "QINGTIAN_STORAGE_BACKEND=sqlite",
            "--env",
            "QINGTIAN_SQLITE_PATH=/var/lib/qingtian/qingtian.sqlite3",
            "--volume",
            f"{data_volume}:{DATA_DESTINATION}",
            image,
            "-c",
            code,
        ]
    )
    return json.loads(result.stdout)


def _backup_volume(image: str, volume: str, destination: str, archive: Path) -> None:
    code = """
import sys
import tarfile
from pathlib import Path

root = Path(sys.argv[1])
with tarfile.open(fileobj=sys.stdout.buffer, mode='w|gz', format=tarfile.PAX_FORMAT) as tf:
    for path in sorted(root.rglob('*')):
        tf.add(path, arcname=str(path.relative_to(root)), recursive=False)
"""
    with archive.open("wb") as output:
        result = subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "--entrypoint",
                "python",
                "--volume",
                f"{volume}:{destination}:ro",
                image,
                "-c",
                code,
                destination,
            ],
            check=False,
            stdout=output,
            stderr=subprocess.PIPE,
        )
    if result.returncode != 0:
        raise DrillBlocked(
            f"volume backup failed for {volume}: {result.stderr.decode('utf-8', errors='replace')}"
        )


def _restore_volume(image: str, volume: str, destination: str, archive: Path) -> None:
    code = """
import shutil
import sys
import tarfile
from pathlib import Path

root = Path(sys.argv[1]).resolve()
for child in list(root.iterdir()):
    if child.is_dir() and not child.is_symlink():
        shutil.rmtree(child)
    else:
        child.unlink()
with tarfile.open(fileobj=sys.stdin.buffer, mode='r|gz') as tf:
    for member in tf:
        destination = (root / member.name).resolve()
        if destination != root and root not in destination.parents:
            raise SystemExit(f'unsafe archive member: {member.name}')
        tf.extract(member, root, filter='data')
"""
    with archive.open("rb") as source:
        result = subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "--interactive",
                "--entrypoint",
                "python",
                "--volume",
                f"{volume}:{destination}",
                image,
                "-c",
                code,
                destination,
            ],
            stdin=source,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    if result.returncode != 0:
        raise DrillBlocked(
            f"volume restore failed for {volume}: {result.stderr.decode('utf-8', errors='replace')}"
        )


def _request_json(url: str, *, api_key: str | None = None) -> tuple[int, Any]:
    headers = {"Accept": "application/json"}
    if api_key:
        headers["X-API-Key"] = api_key
    request = Request(url, headers=headers)
    try:
        with urlopen(request, timeout=10.0) as response:
            status = int(response.status)
            body = response.read()
    except HTTPError as exc:
        status = int(exc.code)
        body = exc.read()
    except (OSError, URLError) as exc:
        raise DrillBlocked(f"request failed: {url}: {exc}") from exc
    return status, json.loads(body.decode("utf-8")) if body else None


def _wait_ready(base_url: str, timeout_seconds: int = 180) -> None:
    deadline = time.monotonic() + timeout_seconds
    last_error = "not attempted"
    while time.monotonic() < deadline:
        try:
            status, payload = _request_json(f"{base_url}/ready")
            if status == 200 and (payload or {}).get("status") == "ready":
                return
            last_error = f"status={status} payload={payload}"
        except DrillBlocked as exc:
            last_error = str(exc)
        time.sleep(2)
    raise DrillBlocked(f"readiness timeout: {last_error}")


def _start_restore_container(
    *,
    name: str,
    image: str,
    data_volume: str,
    config_volume: str,
    api_key_file: Path,
    port: int,
) -> None:
    _run(["docker", "rm", "--force", name], check=False)
    result = _run(
        [
            "docker",
            "run",
            "--detach",
            "--name",
            name,
            "--publish",
            f"127.0.0.1:{port}:8000",
            "--env",
            "QINGTIAN_ENV=production",
            "--env",
            "QINGTIAN_API_KEYS_FILE=/run/secrets/qingtian_api_keys",
            "--env",
            "QINGTIAN_DATA_DIR=/var/lib/qingtian",
            "--env",
            "QINGTIAN_STORAGE_BACKEND=sqlite",
            "--env",
            "QINGTIAN_SQLITE_PATH=/var/lib/qingtian/qingtian.sqlite3",
            "--env",
            "WEB_CONCURRENCY=1",
            "--read-only",
            "--tmpfs",
            "/tmp:size=256m,mode=1777",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--mount",
            f"type=bind,source={api_key_file.resolve()},target=/run/secrets/qingtian_api_keys,readonly",
            "--volume",
            f"{data_volume}:{DATA_DESTINATION}",
            "--volume",
            f"{config_volume}:{CONFIG_DESTINATION}",
            image,
        ]
    )
    if not result.stdout.strip():
        raise DrillBlocked(f"failed to start restore container {name}")


def _verify_restored_api(base_url: str, api_key: str) -> None:
    status, payload = _request_json(f"{base_url}/health")
    if status != 200 or (payload or {}).get("status") != "healthy":
        raise DrillBlocked(f"restored health failed: {status} {payload}")
    status, payload = _request_json(f"{base_url}/api/v1/projects", api_key=api_key)
    if status != 200 or not isinstance(payload, list):
        raise DrillBlocked(f"restored project read failed: {status} {payload}")


def run_drill(
    *,
    repo_root: Path,
    project: str,
    api_key_file: Path,
    rc2_image: str,
    rc1_image: str,
    output_dir: Path,
    restore_port: int,
) -> dict[str, Any]:
    report_path = output_dir / "drill-report.json"
    if report_path.exists():
        raise DrillBlocked(f"refusing to overwrite existing report: {report_path}")
    if "@sha256:" not in rc2_image or "@sha256:" not in rc1_image:
        raise DrillBlocked("RC images must use immutable sha256 digests")
    output_dir.mkdir(parents=True, exist_ok=True)
    api_key = api_key_file.read_text(encoding="utf-8").strip()
    if not api_key or "\n" in api_key or "," in api_key:
        raise DrillBlocked("drill requires exactly one API key")

    compose = _compose_args(repo_root, project)
    original_container = _container_id(compose)
    mounts = _mount_names(original_container)
    restore_data = f"{project}-restore-data"
    restore_config = f"{project}-restore-config"
    restore_container = f"{project}-restore-app"
    data_archive = output_dir / "qingtian-data.tgz"
    config_archive = output_dir / "qingtian-config.tgz"
    report: dict[str, Any] = {
        "schema": SCHEMA,
        "status": "RUNNING",
        "started_at": _utc_now(),
        "completed_at": None,
        "rc2_image": rc2_image,
        "rc1_image": rc1_image,
        "source_snapshot": None,
        "restored_rc2_snapshot": None,
        "rollback_rc1_snapshot": None,
        "restored_again_rc2_snapshot": None,
        "checkpoint": None,
        "backup_manifest": {},
        "rpo_zero": False,
        "rto_seconds": None,
        "rto_le_900_seconds": False,
        "failure": None,
    }
    _atomic_json(report_path, report)
    original_stopped = False
    try:
        started_rto = time.monotonic()
        _run([*compose, "stop", "app"])
        original_stopped = True

        offline_state = _offline_repository_state(rc2_image, mounts[DATA_DESTINATION])
        report["source_snapshot"] = {
            key: offline_state[key]
            for key in ("fingerprint", "integrity_check", "journal_mode", "store_count")
        }
        report["checkpoint"] = {
            "checkpoint": offline_state["checkpoint"],
            "integrity_check": offline_state["integrity_check"],
        }
        if report["checkpoint"]["integrity_check"] != "ok":
            raise DrillBlocked("source SQLite integrity check failed")
        if int(report["checkpoint"]["checkpoint"][0]) != 0:
            raise DrillBlocked(f"source WAL checkpoint remained busy: {report['checkpoint']}")

        _backup_volume(rc2_image, mounts[DATA_DESTINATION], DATA_DESTINATION, data_archive)
        _backup_volume(rc2_image, mounts[CONFIG_DESTINATION], CONFIG_DESTINATION, config_archive)
        report["backup_manifest"] = {
            data_archive.name: {
                "sha256": _sha256(data_archive),
                "bytes": data_archive.stat().st_size,
            },
            config_archive.name: {
                "sha256": _sha256(config_archive),
                "bytes": config_archive.stat().st_size,
            },
        }

        _run(["docker", "volume", "create", restore_data])
        _run(["docker", "volume", "create", restore_config])
        _restore_volume(rc2_image, restore_data, DATA_DESTINATION, data_archive)
        _restore_volume(rc2_image, restore_config, CONFIG_DESTINATION, config_archive)

        restore_url = f"http://127.0.0.1:{restore_port}"
        _start_restore_container(
            name=restore_container,
            image=rc2_image,
            data_volume=restore_data,
            config_volume=restore_config,
            api_key_file=api_key_file,
            port=restore_port,
        )
        _wait_ready(restore_url)
        report["rto_seconds"] = round(time.monotonic() - started_rto, 6)
        report["rto_le_900_seconds"] = report["rto_seconds"] <= 900
        _verify_restored_api(restore_url, api_key)
        report["restored_rc2_snapshot"] = _repository_snapshot(restore_container)
        report["rpo_zero"] = (
            report["source_snapshot"]["fingerprint"]
            == report["restored_rc2_snapshot"]["fingerprint"]
        )
        if not report["rpo_zero"]:
            raise DrillBlocked("restored logical fingerprint differs from source")

        _run(["docker", "rm", "--force", restore_container])
        _start_restore_container(
            name=restore_container,
            image=rc1_image,
            data_volume=restore_data,
            config_volume=restore_config,
            api_key_file=api_key_file,
            port=restore_port,
        )
        _wait_ready(restore_url)
        _verify_restored_api(restore_url, api_key)
        report["rollback_rc1_snapshot"] = _repository_snapshot(restore_container)
        if (
            report["rollback_rc1_snapshot"]["fingerprint"]
            != report["source_snapshot"]["fingerprint"]
        ):
            raise DrillBlocked("RC1 rollback changed the logical fingerprint")

        _run(["docker", "rm", "--force", restore_container])
        _start_restore_container(
            name=restore_container,
            image=rc2_image,
            data_volume=restore_data,
            config_volume=restore_config,
            api_key_file=api_key_file,
            port=restore_port,
        )
        _wait_ready(restore_url)
        _verify_restored_api(restore_url, api_key)
        report["restored_again_rc2_snapshot"] = _repository_snapshot(restore_container)
        if (
            report["restored_again_rc2_snapshot"]["fingerprint"]
            != report["source_snapshot"]["fingerprint"]
        ):
            raise DrillBlocked("RC2 restoration after rollback changed the logical fingerprint")
        if not report["rto_le_900_seconds"]:
            raise DrillBlocked(f"RTO exceeded 900 seconds: {report['rto_seconds']}")
        report["status"] = "PASS"
    except BaseException as exc:
        report["status"] = "BLOCKED"
        report["failure"] = f"{type(exc).__name__}: {exc}"
    finally:
        _run(["docker", "rm", "--force", restore_container], check=False)
        if original_stopped:
            restart = _run([*compose, "start", "app"], check=False)
            if restart.returncode != 0 and report["failure"] is None:
                report["status"] = "BLOCKED"
                report["failure"] = f"failed to restart original app: {restart.stderr.strip()}"
        report["completed_at"] = _utc_now()
        _atomic_json(report_path, report)
    return report


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the QingTian R8 backup/restore gate")
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).parents[1])
    parser.add_argument("--project", default="qingtian-r8")
    parser.add_argument("--api-key-file", type=Path, required=True)
    parser.add_argument("--rc2-image", required=True)
    parser.add_argument("--rc1-image", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--restore-port", type=int, default=18081)
    args = parser.parse_args(argv)
    if not 1 <= args.restore_port <= 65535:
        parser.error("--restore-port must be between 1 and 65535")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = _parse_args(argv)
        report = run_drill(
            repo_root=args.repo_root.resolve(),
            project=args.project,
            api_key_file=args.api_key_file.resolve(),
            rc2_image=args.rc2_image,
            rc1_image=args.rc1_image,
            output_dir=args.output_dir.resolve(),
            restore_port=args.restore_port,
        )
    except BaseException as exc:
        print(f"BLOCKED_QINGTIAN_R8_RECOVERY_DRILL: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
