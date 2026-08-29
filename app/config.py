from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

import yaml

BASE_DIR = Path(__file__).resolve().parent
RESOURCES_DIR = BASE_DIR / "resources"


@dataclass(frozen=True)
class AppConfig:
    rubric: Dict[str, Any]
    lexicon: Dict[str, Any]


class ConfigLoader:
    """
    配置热加载器。

    支持：
    - 缓存配置数据，避免重复读取
    - 基于文件修改时间的自动热加载
    - 线程安全的配置访问
    - 手动刷新接口
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._config: Optional[AppConfig] = None
        self._rubric_mtime: float = 0.0
        self._lexicon_mtime: float = 0.0
        self._source = "none"
        self._active_identity: Optional[tuple[int, int, int]] = None

    def _load_yaml(self, path: Path) -> Dict[str, Any]:
        """加载 YAML 文件"""
        with path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return data

    def _get_mtime(self, path: Path) -> float:
        """获取文件修改时间，文件不存在返回 0"""
        try:
            return path.stat().st_mtime
        except (OSError, FileNotFoundError):
            return 0.0

    def _get_identity(self, path: Path) -> Optional[tuple[int, int, int]]:
        try:
            stat = path.stat()
        except (OSError, FileNotFoundError):
            return None
        return stat.st_ino, stat.st_mtime_ns, stat.st_size

    def _load_active_snapshot(
        self,
        path: Path,
    ) -> tuple[Dict[str, Any], Dict[str, Any], tuple[int, int, int]]:
        for _ in range(3):
            identity_before = self._get_identity(path)
            if identity_before is None:
                raise FileNotFoundError(path)
            data = self._load_yaml(path)
            identity_after = self._get_identity(path)
            if identity_before != identity_after:
                continue
            if not isinstance(data, dict):
                raise ValueError("active configuration snapshot must be a mapping")
            rubric = data.get("rubric")
            lexicon = data.get("lexicon")
            if not isinstance(rubric, dict) or not isinstance(lexicon, dict):
                raise ValueError("active configuration snapshot must contain rubric and lexicon")
            return rubric, lexicon, identity_after
        raise RuntimeError("active configuration snapshot changed repeatedly while loading")

    def _needs_reload(self) -> bool:
        """检查是否需要重新加载配置"""
        active_path = RESOURCES_DIR / "active_config.yaml"
        active_identity = self._get_identity(active_path)
        if active_identity is not None:
            return (
                self._config is None
                or self._source != "active"
                or active_identity != self._active_identity
            )

        rubric_path = RESOURCES_DIR / "rubric.yaml"
        lexicon_path = RESOURCES_DIR / "lexicon.yaml"

        current_rubric_mtime = self._get_mtime(rubric_path)
        current_lexicon_mtime = self._get_mtime(lexicon_path)

        return (
            self._config is None
            or self._source != "legacy"
            or current_rubric_mtime != self._rubric_mtime
            or current_lexicon_mtime != self._lexicon_mtime
        )

    def load(self, force_reload: bool = False) -> AppConfig:
        """
        加载配置（支持热加载）。

        Args:
            force_reload: 是否强制重新加载，忽略缓存

        Returns:
            AppConfig 实例
        """
        with self._lock:
            if force_reload or self._needs_reload():
                active_path = RESOURCES_DIR / "active_config.yaml"
                rubric_path = RESOURCES_DIR / "rubric.yaml"
                lexicon_path = RESOURCES_DIR / "lexicon.yaml"
                try:
                    rubric, lexicon, active_identity = self._load_active_snapshot(active_path)
                except FileNotFoundError:
                    rubric = self._load_yaml(rubric_path)
                    lexicon = self._load_yaml(lexicon_path)
                    self._rubric_mtime = self._get_mtime(rubric_path)
                    self._lexicon_mtime = self._get_mtime(lexicon_path)
                    self._source = "legacy"
                    self._active_identity = None
                else:
                    active_mtime = active_identity[1] / 1_000_000_000
                    self._rubric_mtime = active_mtime
                    self._lexicon_mtime = active_mtime
                    self._source = "active"
                    self._active_identity = active_identity
                self._config = AppConfig(rubric=rubric, lexicon=lexicon)

            return self._config  # type: ignore[return-value]

    def reload(self) -> AppConfig:
        """强制重新加载配置"""
        return self.load(force_reload=True)

    def get_status(self) -> Dict[str, Any]:
        """获取配置加载状态"""
        with self._lock:
            rubric_path = RESOURCES_DIR / "rubric.yaml"
            lexicon_path = RESOURCES_DIR / "lexicon.yaml"
            active_path = RESOURCES_DIR / "active_config.yaml"
            active_identity = self._get_identity(active_path)
            active_mtime = active_identity[1] / 1_000_000_000 if active_identity else 0.0
            rubric_current_mtime = active_mtime if active_identity else self._get_mtime(rubric_path)
            lexicon_current_mtime = (
                active_mtime if active_identity else self._get_mtime(lexicon_path)
            )
            effective_rubric_path = active_path if active_identity else rubric_path
            effective_lexicon_path = active_path if active_identity else lexicon_path

            return {
                "cached": self._config is not None,
                "source": self._source,
                "active_config_path": str(active_path),
                "rubric_path": str(effective_rubric_path),
                "lexicon_path": str(effective_lexicon_path),
                "rubric_mtime": self._rubric_mtime,
                "lexicon_mtime": self._lexicon_mtime,
                "rubric_current_mtime": rubric_current_mtime,
                "lexicon_current_mtime": lexicon_current_mtime,
                "needs_reload": self._needs_reload(),
            }


# 全局配置加载器实例
_config_loader = ConfigLoader()


def load_config(force_reload: bool = False) -> AppConfig:
    """
    加载配置（支持热加载）。

    默认行为：检查文件修改时间，仅在配置文件变更时重新加载。
    设置 force_reload=True 可强制重新加载。

    Args:
        force_reload: 是否强制重新加载

    Returns:
        AppConfig 实例
    """
    return _config_loader.load(force_reload=force_reload)


def reload_config() -> AppConfig:
    """强制重新加载配置"""
    return _config_loader.reload()


def get_config_status() -> Dict[str, Any]:
    """获取配置加载状态（用于调试和监控）"""
    return _config_loader.get_status()


def _load_yaml(path: Path) -> Dict[str, Any]:
    """加载 YAML 文件（保留兼容性）"""
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return data
