from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def test_rotate_runtime_file_archives_non_empty_file(tmp_path: Path):
    from app.runtime_logs import rotate_runtime_file

    log_path = tmp_path / "ops_agents.log"
    log_path.write_text("line-1\n", encoding="utf-8")

    result = rotate_runtime_file(log_path, keep=3)

    assert result["rotated"] is True
    archived_to = Path(str(result["archived_to"]))
    assert archived_to.exists()
    assert archived_to.read_text(encoding="utf-8") == "line-1\n"
    assert not log_path.exists()


def test_rotate_runtime_file_ignores_empty_file(tmp_path: Path):
    from app.runtime_logs import rotate_runtime_file

    log_path = tmp_path / "server.log"
    log_path.write_text("", encoding="utf-8")

    result = rotate_runtime_file(log_path, keep=3)

    assert result["rotated"] is False
    assert log_path.exists()


def test_rotate_runtime_file_prunes_old_archives(tmp_path: Path):
    from app.runtime_logs import rotate_runtime_file

    archive_dir = tmp_path / "log_archive"
    archive_dir.mkdir()
    for idx in range(4):
        archived = archive_dir / f"server_20260316_00000{idx}.log"
        archived.write_text(str(idx), encoding="utf-8")

    log_path = tmp_path / "server.log"
    log_path.write_text("latest", encoding="utf-8")

    rotate_runtime_file(log_path, keep=2)

    remaining = sorted(path.name for path in archive_dir.glob("server_*.log"))
    assert len(remaining) == 2


def test_rotate_runtime_logs_cli(tmp_path: Path):
    script = Path(__file__).resolve().parents[1] / "scripts" / "rotate_runtime_logs.py"
    log_path = tmp_path / "ops_agents.log"
    status_path = tmp_path / "ops_agents_status.json"
    log_path.write_text("cycle=1\n", encoding="utf-8")
    status_path.write_text("{}", encoding="utf-8")

    proc = subprocess.run(
        [sys.executable, str(script), str(log_path), str(status_path)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert proc.returncode == 0
    payload = json.loads(proc.stdout)
    assert len(payload) == 2
    assert all(row["rotated"] is True for row in payload)
