"""
评分结果缓存模块。

功能：
- 基于输入文本 hash 的缓存 key
- 内存缓存 + 可选的文件持久化
- TTL 支持（默认 1 小时）
- 线程安全
- 缓存统计（命中率等）
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from app.metrics import (
    record_cache_eviction,
    record_cache_hit,
    record_cache_miss,
    update_cache_size,
)
from app.storage import DATA_DIR, ensure_data_dirs, load_json, save_json

CACHE_PATH = DATA_DIR / "score_cache.json"
DEFAULT_TTL = 3600  # 1 小时


@dataclass
class CacheEntry:
    """缓存条目"""

    key: str
    value: Dict[str, Any]
    created_at: float
    ttl: float
    hits: int = 0

    def is_expired(self) -> bool:
        """检查是否过期"""
        return time.time() > self.created_at + self.ttl

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "key": self.key,
            "value": self.value,
            "created_at": self.created_at,
            "ttl": self.ttl,
            "hits": self.hits,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CacheEntry":
        """从字典创建"""
        return cls(
            key=data["key"],
            value=data["value"],
            created_at=data["created_at"],
            ttl=data["ttl"],
            hits=data.get("hits", 0),
        )


@dataclass
class CacheStats:
    """缓存统计"""

    total_requests: int = 0
    hits: int = 0
    misses: int = 0
    evictions: int = 0
    size: int = 0

    @property
    def hit_rate(self) -> float:
        """命中率"""
        if self.total_requests == 0:
            return 0.0
        return self.hits / self.total_requests

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "total_requests": self.total_requests,
            "hits": self.hits,
            "misses": self.misses,
            "evictions": self.evictions,
            "size": self.size,
            "hit_rate": round(self.hit_rate, 4),
        }


@dataclass
class ScoreCache:
    """评分结果缓存"""

    max_size: int = 1000
    default_ttl: float = DEFAULT_TTL
    persist: bool = True
    _cache: Dict[str, CacheEntry] = field(default_factory=dict)
    _stats: CacheStats = field(default_factory=CacheStats)
    _lock: threading.RLock = field(default_factory=threading.RLock)

    def __post_init__(self) -> None:
        """初始化后加载持久化缓存"""
        if self.persist:
            self._load_from_disk()

    def _compute_key(self, text: str, config_hash: Optional[str] = None) -> str:
        """
        计算缓存 key。

        Args:
            text: 输入文本
            config_hash: 可选的配置 hash（用于在配置变更时失效缓存）

        Returns:
            缓存 key (SHA256 hash)
        """
        content = text
        if config_hash:
            content = f"{text}::{config_hash}"
        return hashlib.sha256(content.encode("utf-8")).hexdigest()

    def get(self, text: str, config_hash: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """
        获取缓存的评分结果。

        Args:
            text: 输入文本
            config_hash: 可选的配置 hash

        Returns:
            缓存的评分结果，未命中或已过期返回 None
        """
        key = self._compute_key(text, config_hash)

        with self._lock:
            self._stats.total_requests += 1

            entry = self._cache.get(key)
            if entry is None:
                self._stats.misses += 1
                record_cache_miss()
                return None

            if entry.is_expired():
                del self._cache[key]
                self._stats.misses += 1
                self._stats.evictions += 1
                self._stats.size = len(self._cache)
                record_cache_miss()
                record_cache_eviction(1)
                update_cache_size(self._stats.size)
                self._save_to_disk()
                return None

            entry.hits += 1
            self._stats.hits += 1
            record_cache_hit()
            return entry.value

    def set(
        self,
        text: str,
        result: Dict[str, Any],
        config_hash: Optional[str] = None,
        ttl: Optional[float] = None,
    ) -> str:
        """
        缓存评分结果。

        Args:
            text: 输入文本
            result: 评分结果
            config_hash: 可选的配置 hash
            ttl: 可选的 TTL（秒），默认使用 default_ttl

        Returns:
            缓存 key
        """
        key = self._compute_key(text, config_hash)
        entry = CacheEntry(
            key=key,
            value=result,
            created_at=time.time(),
            ttl=ttl if ttl is not None else self.default_ttl,
        )

        with self._lock:
            # 如果缓存已满，清理过期条目
            if len(self._cache) >= self.max_size:
                self._evict_expired()

            # 如果仍然满，清理最旧的条目
            if len(self._cache) >= self.max_size:
                self._evict_oldest()

            self._cache[key] = entry
            self._stats.size = len(self._cache)
            update_cache_size(self._stats.size)
            self._save_to_disk()

        return key

    def invalidate(self, text: str, config_hash: Optional[str] = None) -> bool:
        """
        使指定缓存失效。

        Args:
            text: 输入文本
            config_hash: 可选的配置 hash

        Returns:
            是否成功删除
        """
        key = self._compute_key(text, config_hash)
        with self._lock:
            if key in self._cache:
                del self._cache[key]
                self._stats.evictions += 1
                self._stats.size = len(self._cache)
                record_cache_eviction(1)
                update_cache_size(self._stats.size)
                self._save_to_disk()
                return True
            return False

    def clear(self) -> int:
        """
        清空缓存。

        Returns:
            清除的条目数
        """
        with self._lock:
            count = len(self._cache)
            self._cache.clear()
            self._stats.evictions += count
            self._stats.size = 0
            if count > 0:
                record_cache_eviction(count)
            update_cache_size(0)
            self._save_to_disk()
            return count

    def get_stats(self) -> CacheStats:
        """获取缓存统计"""
        with self._lock:
            self._stats.size = len(self._cache)
            return CacheStats(
                total_requests=self._stats.total_requests,
                hits=self._stats.hits,
                misses=self._stats.misses,
                evictions=self._stats.evictions,
                size=self._stats.size,
            )

    def _evict_expired(self) -> int:
        """清理所有过期条目"""
        count = 0
        keys_to_remove = [k for k, v in self._cache.items() if v.is_expired()]
        for key in keys_to_remove:
            del self._cache[key]
            count += 1
        self._stats.evictions += count
        if count > 0:
            record_cache_eviction(count)
            update_cache_size(len(self._cache))
        return count

    def _evict_oldest(self) -> None:
        """清理最旧的条目"""
        if not self._cache:
            return
        oldest_key = min(self._cache.keys(), key=lambda k: self._cache[k].created_at)
        del self._cache[oldest_key]
        self._stats.evictions += 1
        record_cache_eviction(1)
        update_cache_size(len(self._cache))

    def _save_to_disk(self) -> None:
        """持久化缓存到磁盘"""
        if not self.persist:
            return
        try:
            ensure_data_dirs()
            data = {k: v.to_dict() for k, v in self._cache.items()}
            save_json(CACHE_PATH, data)
        except Exception:
            pass  # 静默失败，缓存丢失不影响核心功能

    def _load_from_disk(self) -> None:
        """从磁盘加载缓存"""
        if not self.persist or not CACHE_PATH.exists():
            return
        try:
            data = load_json(CACHE_PATH, {})
            for key, entry_data in data.items():
                entry = CacheEntry.from_dict(entry_data)
                if not entry.is_expired():
                    self._cache[key] = entry
            self._stats.size = len(self._cache)
        except Exception:
            pass  # 静默失败，从空缓存开始


# 全局缓存实例
_score_cache: Optional[ScoreCache] = None
_cache_lock = threading.Lock()


def get_score_cache(
    max_size: int = 1000,
    default_ttl: float = DEFAULT_TTL,
    persist: bool = True,
) -> ScoreCache:
    """
    获取全局缓存实例（单例模式）。

    首次调用时创建实例，后续调用返回同一实例。
    参数仅在首次调用时生效。

    Args:
        max_size: 最大缓存条目数
        default_ttl: 默认 TTL（秒）
        persist: 是否持久化到磁盘

    Returns:
        ScoreCache 实例
    """
    global _score_cache
    with _cache_lock:
        if _score_cache is None:
            _score_cache = ScoreCache(
                max_size=max_size,
                default_ttl=default_ttl,
                persist=persist,
            )
        return _score_cache


def reset_score_cache() -> None:
    """重置全局缓存实例（主要用于测试）"""
    global _score_cache
    with _cache_lock:
        _score_cache = None


def cache_score_result(
    text: str,
    result: Dict[str, Any],
    config_hash: Optional[str] = None,
    ttl: Optional[float] = None,
) -> str:
    """
    缓存评分结果（便捷函数）。

    Args:
        text: 输入文本
        result: 评分结果
        config_hash: 可选的配置 hash
        ttl: 可选的 TTL（秒）

    Returns:
        缓存 key
    """
    return get_score_cache().set(text, result, config_hash, ttl)


def get_cached_score(
    text: str,
    config_hash: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """
    获取缓存的评分结果（便捷函数）。

    Args:
        text: 输入文本
        config_hash: 可选的配置 hash

    Returns:
        缓存的评分结果，未命中返回 None
    """
    return get_score_cache().get(text, config_hash)


def get_cache_stats() -> Dict[str, Any]:
    """获取缓存统计（便捷函数）"""
    return get_score_cache().get_stats().to_dict()


def clear_score_cache() -> int:
    """清空缓存（便捷函数）"""
    return get_score_cache().clear()


@dataclass
class WarmupResult:
    """预热结果"""

    total_items: int = 0
    warmed: int = 0
    skipped: int = 0  # 已在缓存中
    failed: int = 0
    duration_ms: float = 0.0
    errors: list = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "total_items": self.total_items,
            "warmed": self.warmed,
            "skipped": self.skipped,
            "failed": self.failed,
            "duration_ms": round(self.duration_ms, 2),
            "success_rate": round(self.warmed / self.total_items, 4)
            if self.total_items > 0
            else 0.0,
            "errors": self.errors[:10],  # 只返回前 10 个错误
        }


def warmup_cache(
    items: list,
    score_fn: Optional[Any] = None,
    config_hash: Optional[str] = None,
    skip_existing: bool = True,
    ttl: Optional[float] = None,
) -> WarmupResult:
    """
    预热缓存。

    Args:
        items: 要预热的条目列表，每个条目是:
               - str: 文本内容
               - tuple: (text, result) 预计算的结果
               - dict: {"text": ..., "result": ...}
        score_fn: 评分函数，接受文本返回结果字典（当 items 只有文本时需要）
        config_hash: 可选的配置 hash
        skip_existing: 是否跳过已存在的缓存（默认 True）
        ttl: 可选的 TTL

    Returns:
        WarmupResult 预热结果统计
    """
    start_time = time.time()
    result = WarmupResult(total_items=len(items))
    cache = get_score_cache()

    for item in items:
        try:
            # 解析条目
            if isinstance(item, str):
                text = item
                score_result = None
            elif isinstance(item, tuple) and len(item) == 2:
                text, score_result = item
            elif isinstance(item, dict):
                text = item.get("text", "")
                score_result = item.get("result")
            else:
                result.failed += 1
                result.errors.append(f"Invalid item format: {type(item)}")
                continue

            if not text:
                result.failed += 1
                result.errors.append("Empty text")
                continue

            # 检查是否已存在
            if skip_existing:
                existing = cache.get(text, config_hash)
                if existing is not None:
                    result.skipped += 1
                    continue

            # 如果没有预计算结果，使用评分函数
            if score_result is None:
                if score_fn is None:
                    result.failed += 1
                    result.errors.append("No score_fn provided for text-only item")
                    continue
                try:
                    score_result = score_fn(text)
                except Exception as e:
                    result.failed += 1
                    result.errors.append(f"score_fn error: {str(e)[:100]}")
                    continue

            # 写入缓存
            cache.set(text, score_result, config_hash, ttl)
            result.warmed += 1

        except Exception as e:
            result.failed += 1
            result.errors.append(f"Unexpected error: {str(e)[:100]}")

    result.duration_ms = (time.time() - start_time) * 1000
    return result


def warmup_cache_from_file(
    filepath: str,
    score_fn: Optional[Any] = None,
    config_hash: Optional[str] = None,
    skip_existing: bool = True,
    ttl: Optional[float] = None,
) -> WarmupResult:
    """
    从文件预热缓存。

    支持的文件格式:
    - .txt: 每行一个文本
    - .json: 数组，每个元素是 {"text": ..., "result": ...} 或纯文本

    Args:
        filepath: 文件路径
        score_fn: 评分函数
        config_hash: 配置 hash
        skip_existing: 是否跳过已存在
        ttl: TTL

    Returns:
        WarmupResult
    """
    from pathlib import Path

    path = Path(filepath)
    if not path.exists():
        return WarmupResult(
            total_items=0,
            failed=1,
            errors=[f"File not found: {filepath}"],
        )

    items: list = []
    try:
        if path.suffix == ".json":
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, list):
                items = data
            else:
                return WarmupResult(
                    total_items=0,
                    failed=1,
                    errors=["JSON file must contain an array"],
                )
        elif path.suffix == ".txt":
            lines = path.read_text(encoding="utf-8").strip().split("\n")
            items = [line.strip() for line in lines if line.strip()]
        else:
            return WarmupResult(
                total_items=0,
                failed=1,
                errors=[f"Unsupported file format: {path.suffix}"],
            )
    except Exception as e:
        return WarmupResult(
            total_items=0,
            failed=1,
            errors=[f"Failed to read file: {str(e)}"],
        )

    return warmup_cache(
        items=items,
        score_fn=score_fn,
        config_hash=config_hash,
        skip_existing=skip_existing,
        ttl=ttl,
    )


def warmup_cache_parallel(
    items: list,
    score_fn: Optional[Any] = None,
    config_hash: Optional[str] = None,
    skip_existing: bool = True,
    ttl: Optional[float] = None,
    max_workers: int = 4,
) -> WarmupResult:
    """
    并行预热缓存。

    适用于 score_fn 是 I/O 密集型操作的场景。

    Args:
        items: 要预热的条目列表
        score_fn: 评分函数
        config_hash: 配置 hash
        skip_existing: 是否跳过已存在
        ttl: TTL
        max_workers: 并行工作线程数

    Returns:
        WarmupResult
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    start_time = time.time()
    result = WarmupResult(total_items=len(items))
    cache = get_score_cache()
    lock = threading.Lock()

    def process_item(item: Any) -> tuple:
        """处理单个条目，返回 (status, error)"""
        try:
            # 解析条目
            if isinstance(item, str):
                text = item
                score_result = None
            elif isinstance(item, tuple) and len(item) == 2:
                text, score_result = item
            elif isinstance(item, dict):
                text = item.get("text", "")
                score_result = item.get("result")
            else:
                return ("failed", f"Invalid item format: {type(item)}")

            if not text:
                return ("failed", "Empty text")

            # 检查是否已存在
            if skip_existing:
                existing = cache.get(text, config_hash)
                if existing is not None:
                    return ("skipped", None)

            # 如果没有预计算结果，使用评分函数
            if score_result is None:
                if score_fn is None:
                    return ("failed", "No score_fn provided for text-only item")
                try:
                    score_result = score_fn(text)
                except Exception as e:
                    return ("failed", f"score_fn error: {str(e)[:100]}")

            # 写入缓存
            cache.set(text, score_result, config_hash, ttl)
            return ("warmed", None)

        except Exception as e:
            return ("failed", f"Unexpected error: {str(e)[:100]}")

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(process_item, item): item for item in items}

        for future in as_completed(futures):
            status, error = future.result()
            with lock:
                if status == "warmed":
                    result.warmed += 1
                elif status == "skipped":
                    result.skipped += 1
                else:
                    result.failed += 1
                    if error:
                        result.errors.append(error)

    result.duration_ms = (time.time() - start_time) * 1000
    return result
