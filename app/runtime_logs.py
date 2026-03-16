from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Dict


def _build_archive_path(path: Path, archive_dir: Path) -> Path:
    suffix = "".join(path.suffixes)
    base_name = path.name[: -len(suffix)] if suffix else path.name
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    candidate = archive_dir / f"{base_name}_{timestamp}{suffix}"
    counter = 1
    while candidate.exists():
        candidate = archive_dir / f"{base_name}_{timestamp}_{counter}{suffix}"
        counter += 1
    return candidate


def rotate_runtime_file(path: Path | str, *, keep: int = 12) -> Dict[str, object]:
    """将运行时日志/状态文件按启动批次归档。

    仅在文件存在且非空时执行归档。旧归档会按同名前缀清理，只保留最近 keep 份。
    """
    target = Path(path)
    result: Dict[str, object] = {
        "path": str(target),
        "rotated": False,
        "archived_to": None,
        "archive_dir": None,
    }
    if not target.exists() or not target.is_file():
        return result

    try:
        size = target.stat().st_size
    except OSError:
        return result
    if size <= 0:
        return result

    archive_dir = target.parent / "log_archive"
    archive_dir.mkdir(parents=True, exist_ok=True)
    archived_to = _build_archive_path(target, archive_dir)
    target.replace(archived_to)

    result["rotated"] = True
    result["archived_to"] = str(archived_to)
    result["archive_dir"] = str(archive_dir)

    keep_count = max(1, int(keep))
    prefix = f"{target.stem}_"
    suffix = "".join(target.suffixes)
    archives = sorted(
        [
            path
            for path in archive_dir.iterdir()
            if path.is_file() and path.name.startswith(prefix) and path.name.endswith(suffix)
        ],
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    )
    for stale in archives[keep_count:]:
        stale.unlink(missing_ok=True)

    return result
