from __future__ import annotations

import shutil
from pathlib import Path

import pytest


@pytest.fixture(scope="session", autouse=True)
def isolate_repo_persistent_data(tmp_path_factory: pytest.TempPathFactory):
    """
    保护仓库内的真实 data/ 状态不被测试污染。

    说明：
    - 仅备份 data/*.json，避免复制用户可能很大的原始资料文件。
    - 备份并恢复 data/versions 下的版本快照，避免版本化测试污染仓库。
    - materials/ 目录只清理“测试期间新增”的文件/目录，不改动原有文件。
    """
    repo_root = Path(__file__).resolve().parents[1]
    data_dir = repo_root / "data"
    materials_dir = data_dir / "materials"
    versions_dir = data_dir / "versions"
    backup_dir = tmp_path_factory.mktemp("repo_data_backup")

    original_json_relpaths: set[str] = set()
    if data_dir.exists():
        for src in data_dir.glob("*.json"):
            rel = src.relative_to(data_dir)
            dst = backup_dir / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            original_json_relpaths.add(rel.as_posix())

    original_version_relpaths: set[str] = set()
    if versions_dir.exists():
        for src in versions_dir.rglob("*.json"):
            rel = src.relative_to(data_dir)
            dst = backup_dir / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            original_version_relpaths.add(rel.as_posix())

    original_material_entries: set[str] = set()
    if materials_dir.exists():
        original_material_entries = {
            path.relative_to(materials_dir).as_posix() for path in materials_dir.rglob("*")
        }

    yield

    data_dir.mkdir(parents=True, exist_ok=True)

    for current in list(data_dir.glob("*.json")):
        rel = current.relative_to(data_dir).as_posix()
        if rel not in original_json_relpaths:
            current.unlink(missing_ok=True)

    for rel in sorted(original_json_relpaths):
        src = backup_dir / rel
        dst = data_dir / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)

    if versions_dir.exists():
        current_version_entries = sorted(
            versions_dir.rglob("*"),
            key=lambda path: len(path.relative_to(versions_dir).parts),
            reverse=True,
        )
        for path in current_version_entries:
            rel = path.relative_to(data_dir).as_posix()
            if rel in original_version_relpaths:
                continue
            if path.is_file() or path.is_symlink():
                path.unlink(missing_ok=True)
            elif path.is_dir():
                try:
                    path.rmdir()
                except OSError:
                    pass

    for rel in sorted(original_version_relpaths):
        src = backup_dir / rel
        dst = data_dir / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)

    if materials_dir.exists():
        current_entries = sorted(
            materials_dir.rglob("*"),
            key=lambda path: len(path.relative_to(materials_dir).parts),
            reverse=True,
        )
        for path in current_entries:
            rel = path.relative_to(materials_dir).as_posix()
            if rel in original_material_entries:
                continue
            if path.is_file() or path.is_symlink():
                path.unlink(missing_ok=True)
            elif path.is_dir():
                try:
                    path.rmdir()
                except OSError:
                    pass


@pytest.fixture(autouse=True)
def isolate_auth_env(monkeypatch: pytest.MonkeyPatch):
    """避免本机 .env 中的 API_KEYS/ZHIFEI_REQUIRE_API_KEYS 污染测试默认行为。"""
    monkeypatch.delenv("API_KEYS", raising=False)
    monkeypatch.delenv("ZHIFEI_REQUIRE_API_KEYS", raising=False)
