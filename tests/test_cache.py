"""测试评分结果缓存模块"""
from __future__ import annotations

import json
import tempfile
import time
from pathlib import Path
from typing import Any, Dict

import pytest

from app.cache import (
    CacheEntry,
    CacheStats,
    ScoreCache,
    WarmupResult,
    cache_score_result,
    clear_score_cache,
    get_cache_stats,
    get_cached_score,
    get_score_cache,
    reset_score_cache,
    warmup_cache,
    warmup_cache_from_file,
    warmup_cache_parallel,
)


@pytest.fixture(autouse=True)
def reset_cache():
    """每个测试前后重置缓存"""
    reset_score_cache()
    yield
    reset_score_cache()


class TestCacheEntry:
    """测试 CacheEntry 类"""

    def test_cache_entry_creation(self):
        """测试创建缓存条目"""
        entry = CacheEntry(
            key="test_key",
            value={"score": 85},
            created_at=time.time(),
            ttl=3600,
        )
        assert entry.key == "test_key"
        assert entry.value == {"score": 85}
        assert entry.hits == 0

    def test_cache_entry_not_expired(self):
        """测试未过期的条目"""
        entry = CacheEntry(
            key="test",
            value={},
            created_at=time.time(),
            ttl=3600,
        )
        assert not entry.is_expired()

    def test_cache_entry_expired(self):
        """测试已过期的条目"""
        entry = CacheEntry(
            key="test",
            value={},
            created_at=time.time() - 7200,  # 2 小时前
            ttl=3600,  # 1 小时 TTL
        )
        assert entry.is_expired()

    def test_cache_entry_to_dict(self):
        """测试转换为字典"""
        now = time.time()
        entry = CacheEntry(
            key="test",
            value={"data": "value"},
            created_at=now,
            ttl=3600,
            hits=5,
        )
        d = entry.to_dict()
        assert d["key"] == "test"
        assert d["value"] == {"data": "value"}
        assert d["created_at"] == now
        assert d["ttl"] == 3600
        assert d["hits"] == 5

    def test_cache_entry_from_dict(self):
        """测试从字典创建"""
        data = {
            "key": "test",
            "value": {"score": 90},
            "created_at": 1234567890,
            "ttl": 7200,
            "hits": 10,
        }
        entry = CacheEntry.from_dict(data)
        assert entry.key == "test"
        assert entry.value == {"score": 90}
        assert entry.created_at == 1234567890
        assert entry.ttl == 7200
        assert entry.hits == 10


class TestCacheStats:
    """测试 CacheStats 类"""

    def test_cache_stats_default(self):
        """测试默认统计"""
        stats = CacheStats()
        assert stats.total_requests == 0
        assert stats.hits == 0
        assert stats.misses == 0
        assert stats.evictions == 0
        assert stats.size == 0

    def test_hit_rate_zero_requests(self):
        """测试零请求时的命中率"""
        stats = CacheStats()
        assert stats.hit_rate == 0.0

    def test_hit_rate_calculation(self):
        """测试命中率计算"""
        stats = CacheStats(total_requests=100, hits=75, misses=25)
        assert stats.hit_rate == 0.75

    def test_cache_stats_to_dict(self):
        """测试转换为字典"""
        stats = CacheStats(
            total_requests=100,
            hits=80,
            misses=20,
            evictions=5,
            size=50,
        )
        d = stats.to_dict()
        assert d["total_requests"] == 100
        assert d["hits"] == 80
        assert d["misses"] == 20
        assert d["evictions"] == 5
        assert d["size"] == 50
        assert d["hit_rate"] == 0.8


class TestScoreCache:
    """测试 ScoreCache 类"""

    def test_cache_creation(self):
        """测试创建缓存实例"""
        cache = ScoreCache(max_size=100, default_ttl=1800, persist=False)
        assert cache.max_size == 100
        assert cache.default_ttl == 1800

    def test_compute_key_same_text(self):
        """测试相同文本产生相同 key"""
        cache = ScoreCache(persist=False)
        key1 = cache._compute_key("测试文本")
        key2 = cache._compute_key("测试文本")
        assert key1 == key2

    def test_compute_key_different_text(self):
        """测试不同文本产生不同 key"""
        cache = ScoreCache(persist=False)
        key1 = cache._compute_key("文本A")
        key2 = cache._compute_key("文本B")
        assert key1 != key2

    def test_compute_key_with_config_hash(self):
        """测试带配置 hash 的 key"""
        cache = ScoreCache(persist=False)
        key1 = cache._compute_key("测试", config_hash="v1")
        key2 = cache._compute_key("测试", config_hash="v2")
        assert key1 != key2

    def test_set_and_get(self):
        """测试设置和获取缓存"""
        cache = ScoreCache(persist=False)
        result = {"score": 85, "dimensions": {"安全": 90}}

        key = cache.set("测试输入", result)
        assert key is not None

        cached = cache.get("测试输入")
        assert cached == result

    def test_get_miss(self):
        """测试缓存未命中"""
        cache = ScoreCache(persist=False)
        cached = cache.get("不存在的文本")
        assert cached is None

    def test_get_expired(self):
        """测试过期缓存"""
        cache = ScoreCache(default_ttl=0.01, persist=False)  # 10ms TTL
        cache.set("测试", {"score": 80})

        time.sleep(0.02)  # 等待过期
        cached = cache.get("测试")
        assert cached is None

    def test_invalidate(self):
        """测试使缓存失效"""
        cache = ScoreCache(persist=False)
        cache.set("测试", {"score": 80})

        success = cache.invalidate("测试")
        assert success is True
        assert cache.get("测试") is None

    def test_invalidate_nonexistent(self):
        """测试使不存在的缓存失效"""
        cache = ScoreCache(persist=False)
        success = cache.invalidate("不存在")
        assert success is False

    def test_clear(self):
        """测试清空缓存"""
        cache = ScoreCache(persist=False)
        cache.set("文本1", {"score": 80})
        cache.set("文本2", {"score": 85})
        cache.set("文本3", {"score": 90})

        count = cache.clear()
        assert count == 3
        assert cache.get("文本1") is None
        assert cache.get("文本2") is None
        assert cache.get("文本3") is None

    def test_stats_tracking(self):
        """测试统计跟踪"""
        cache = ScoreCache(persist=False)
        cache.set("测试", {"score": 80})

        # 命中
        cache.get("测试")
        cache.get("测试")

        # 未命中
        cache.get("不存在")

        stats = cache.get_stats()
        assert stats.total_requests == 3
        assert stats.hits == 2
        assert stats.misses == 1
        assert stats.size == 1

    def test_max_size_eviction(self):
        """测试超过最大容量时的驱逐"""
        cache = ScoreCache(max_size=3, persist=False)

        for i in range(5):
            cache.set(f"文本{i}", {"score": i})

        stats = cache.get_stats()
        assert stats.size <= 3

    def test_hits_increment(self):
        """测试命中次数增加"""
        cache = ScoreCache(persist=False)
        cache.set("测试", {"score": 80})

        cache.get("测试")
        cache.get("测试")
        cache.get("测试")

        # 直接检查内部状态
        key = cache._compute_key("测试")
        entry = cache._cache[key]
        assert entry.hits == 3

    def test_custom_ttl(self):
        """测试自定义 TTL"""
        cache = ScoreCache(default_ttl=3600, persist=False)
        cache.set("测试", {"score": 80}, ttl=0.01)  # 10ms

        time.sleep(0.02)
        cached = cache.get("测试")
        assert cached is None

    def test_config_hash_isolation(self):
        """测试配置 hash 隔离"""
        cache = ScoreCache(persist=False)
        cache.set("测试", {"score": 80}, config_hash="v1")
        cache.set("测试", {"score": 90}, config_hash="v2")

        result_v1 = cache.get("测试", config_hash="v1")
        result_v2 = cache.get("测试", config_hash="v2")

        assert result_v1["score"] == 80
        assert result_v2["score"] == 90


class TestGlobalCacheFunctions:
    """测试全局缓存便捷函数"""

    def test_get_score_cache_singleton(self):
        """测试单例模式"""
        cache1 = get_score_cache()
        cache2 = get_score_cache()
        assert cache1 is cache2

    def test_cache_score_result(self):
        """测试 cache_score_result 函数"""
        key = cache_score_result("测试输入", {"score": 85})
        assert key is not None
        assert len(key) == 64  # SHA256 hex

    def test_get_cached_score(self):
        """测试 get_cached_score 函数"""
        cache_score_result("测试", {"score": 80})
        result = get_cached_score("测试")
        assert result == {"score": 80}

    def test_get_cached_score_miss(self):
        """测试 get_cached_score 未命中"""
        result = get_cached_score("不存在的文本")
        assert result is None

    def test_get_cache_stats(self):
        """测试 get_cache_stats 函数"""
        cache_score_result("测试", {"score": 80})
        get_cached_score("测试")  # 命中
        get_cached_score("不存在")  # 未命中

        stats = get_cache_stats()
        assert "total_requests" in stats
        assert "hits" in stats
        assert "misses" in stats
        assert "hit_rate" in stats

    def test_clear_score_cache(self):
        """测试 clear_score_cache 函数"""
        # 先清空可能存在的缓存
        clear_score_cache()

        cache_score_result("测试清空1", {"score": 80})
        cache_score_result("测试清空2", {"score": 85})

        count = clear_score_cache()
        assert count == 2
        assert get_cached_score("测试清空1") is None

    def test_reset_score_cache(self):
        """测试 reset_score_cache 函数"""
        cache1 = get_score_cache()
        cache1.set("测试", {"score": 80})

        reset_score_cache()
        cache2 = get_score_cache()

        assert cache1 is not cache2


class TestCachePersistence:
    """测试缓存持久化"""

    def test_persist_disabled(self):
        """测试禁用持久化"""
        cache = ScoreCache(persist=False)
        cache.set("测试", {"score": 80})
        # 应该不会抛出异常
        assert cache.get("测试") == {"score": 80}


class TestCacheEdgeCases:
    """测试边界情况"""

    def test_empty_text(self):
        """测试空文本"""
        cache = ScoreCache(persist=False)
        cache.set("", {"score": 50})
        result = cache.get("")
        assert result == {"score": 50}

    def test_unicode_text(self):
        """测试 Unicode 文本"""
        cache = ScoreCache(persist=False)
        text = "施工组织设计方案 🏗️ 建筑工程"
        cache.set(text, {"score": 85})
        result = cache.get(text)
        assert result == {"score": 85}

    def test_large_result(self):
        """测试大型结果"""
        cache = ScoreCache(persist=False)
        large_result: Dict[str, Any] = {
            "score": 85,
            "dimensions": {f"dim_{i}": i for i in range(100)},
            "details": "x" * 10000,
        }
        cache.set("测试", large_result)
        result = cache.get("测试")
        assert result == large_result

    def test_concurrent_access(self):
        """测试并发访问"""
        import threading

        cache = ScoreCache(persist=False)
        errors: list = []

        def worker(thread_id: int) -> None:
            try:
                for i in range(100):
                    cache.set(f"文本_{thread_id}_{i}", {"score": i})
                    cache.get(f"文本_{thread_id}_{i}")
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0


class TestCacheIntegration:
    """集成测试"""

    def test_full_workflow(self):
        """测试完整工作流"""
        # 1. 首次评分，缓存未命中
        result = get_cached_score("测试施工文档内容")
        assert result is None

        # 2. 模拟评分并缓存
        score_result = {
            "score": 85,
            "dimensions": {
                "安全措施": {"score": 90, "weight": 0.3},
                "技术方案": {"score": 85, "weight": 0.4},
                "质量保证": {"score": 80, "weight": 0.3},
            },
            "suggestions": ["建议增加安全培训计划"],
        }
        cache_score_result("测试施工文档内容", score_result)

        # 3. 再次获取，缓存命中
        cached = get_cached_score("测试施工文档内容")
        assert cached == score_result

        # 4. 检查统计
        stats = get_cache_stats()
        assert stats["hits"] >= 1

        # 5. 清空缓存
        clear_score_cache()
        assert get_cached_score("测试施工文档内容") is None


class TestWarmupResult:
    """测试 WarmupResult 类"""

    def test_warmup_result_default(self):
        """测试默认值"""
        result = WarmupResult()
        assert result.total_items == 0
        assert result.warmed == 0
        assert result.skipped == 0
        assert result.failed == 0
        assert result.duration_ms == 0.0
        assert result.errors == []

    def test_warmup_result_to_dict(self):
        """测试转换为字典"""
        result = WarmupResult(
            total_items=10,
            warmed=8,
            skipped=1,
            failed=1,
            duration_ms=123.456,
            errors=["error1"],
        )
        d = result.to_dict()
        assert d["total_items"] == 10
        assert d["warmed"] == 8
        assert d["skipped"] == 1
        assert d["failed"] == 1
        assert d["duration_ms"] == 123.46
        assert d["success_rate"] == 0.8
        assert d["errors"] == ["error1"]

    def test_warmup_result_success_rate_zero(self):
        """测试零条目时的成功率"""
        result = WarmupResult(total_items=0)
        d = result.to_dict()
        assert d["success_rate"] == 0.0


class TestWarmupCache:
    """测试缓存预热函数"""

    def test_warmup_with_precomputed_results(self):
        """测试使用预计算结果预热"""
        items = [
            ("文本1", {"score": 80}),
            ("文本2", {"score": 85}),
            ("文本3", {"score": 90}),
        ]
        result = warmup_cache(items)

        assert result.total_items == 3
        assert result.warmed == 3
        assert result.skipped == 0
        assert result.failed == 0

        # 验证缓存已填充
        assert get_cached_score("文本1") == {"score": 80}
        assert get_cached_score("文本2") == {"score": 85}
        assert get_cached_score("文本3") == {"score": 90}

    def test_warmup_with_dict_items(self):
        """测试使用字典格式的条目"""
        items = [
            {"text": "文档A", "result": {"score": 75}},
            {"text": "文档B", "result": {"score": 82}},
        ]
        result = warmup_cache(items)

        assert result.warmed == 2
        assert get_cached_score("文档A") == {"score": 75}

    def test_warmup_with_score_fn(self):
        """测试使用评分函数预热"""

        def mock_score_fn(text: str) -> Dict[str, Any]:
            return {"score": len(text) * 10}

        items = ["短文", "中等长度文本", "这是一个比较长的文本内容"]
        result = warmup_cache(items, score_fn=mock_score_fn)

        assert result.warmed == 3
        assert get_cached_score("短文")["score"] == 20
        assert get_cached_score("中等长度文本")["score"] == 60

    def test_warmup_skip_existing(self):
        """测试跳过已存在的缓存"""
        # 预先缓存一个
        cache_score_result("已存在", {"score": 100})

        items = [
            ("已存在", {"score": 50}),  # 应该被跳过
            ("新文本", {"score": 60}),
        ]
        result = warmup_cache(items, skip_existing=True)

        assert result.warmed == 1
        assert result.skipped == 1
        # 原缓存值应该保持不变
        assert get_cached_score("已存在")["score"] == 100

    def test_warmup_no_skip_existing(self):
        """测试不跳过已存在的缓存"""
        cache_score_result("已存在", {"score": 100})

        items = [("已存在", {"score": 50})]
        result = warmup_cache(items, skip_existing=False)

        assert result.warmed == 1
        assert result.skipped == 0
        # 缓存值应该被更新
        assert get_cached_score("已存在")["score"] == 50

    def test_warmup_with_config_hash(self):
        """测试带配置 hash 的预热"""
        items = [("测试文本", {"score": 80})]
        warmup_cache(items, config_hash="v1")

        # 使用相同 config_hash 应该能获取
        assert get_cached_score("测试文本", config_hash="v1")["score"] == 80
        # 不同 config_hash 获取不到
        assert get_cached_score("测试文本", config_hash="v2") is None

    def test_warmup_with_custom_ttl(self):
        """测试自定义 TTL"""
        items = [("快过期", {"score": 70})]
        warmup_cache(items, ttl=0.01)  # 10ms

        time.sleep(0.02)
        assert get_cached_score("快过期") is None

    def test_warmup_invalid_item_format(self):
        """测试无效的条目格式"""
        items = [123, None, [1, 2, 3]]  # type: ignore
        result = warmup_cache(items)

        assert result.failed == 3
        assert result.warmed == 0
        assert len(result.errors) == 3

    def test_warmup_empty_text(self):
        """测试空文本"""
        items = [("", {"score": 50}), {"text": "", "result": {"score": 60}}]
        result = warmup_cache(items)

        assert result.failed == 2

    def test_warmup_missing_score_fn(self):
        """测试缺少评分函数时的纯文本条目"""
        items = ["需要评分的文本"]
        result = warmup_cache(items)  # 没有提供 score_fn

        assert result.failed == 1
        assert "No score_fn provided" in result.errors[0]

    def test_warmup_score_fn_error(self):
        """测试评分函数抛出异常"""

        def failing_score_fn(text: str) -> Dict[str, Any]:
            raise ValueError("评分失败")

        items = ["测试文本"]
        result = warmup_cache(items, score_fn=failing_score_fn)

        assert result.failed == 1
        assert "score_fn error" in result.errors[0]

    def test_warmup_duration_tracking(self):
        """测试耗时跟踪"""
        items = [("测试", {"score": 80})]
        result = warmup_cache(items)

        assert result.duration_ms >= 0


class TestWarmupCacheFromFile:
    """测试从文件预热缓存"""

    def test_warmup_from_json_file(self):
        """测试从 JSON 文件预热"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            data = [
                {"text": "json预热文本1", "result": {"score": 80}},
                {"text": "json预热文本2", "result": {"score": 85}},
            ]
            json.dump(data, f, ensure_ascii=False)
            filepath = f.name

        try:
            result = warmup_cache_from_file(filepath)
            assert result.warmed == 2
            assert get_cached_score("json预热文本1")["score"] == 80
        finally:
            Path(filepath).unlink()

    def test_warmup_from_txt_file(self):
        """测试从文本文件预热"""

        def mock_score_fn(text: str) -> Dict[str, Any]:
            return {"score": len(text)}

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False, encoding="utf-8"
        ) as f:
            f.write("行1内容\n行2内容\n行3内容\n")
            filepath = f.name

        try:
            result = warmup_cache_from_file(filepath, score_fn=mock_score_fn)
            assert result.warmed == 3
        finally:
            Path(filepath).unlink()

    def test_warmup_from_nonexistent_file(self):
        """测试不存在的文件"""
        result = warmup_cache_from_file("/nonexistent/path/file.json")
        assert result.failed == 1
        assert "File not found" in result.errors[0]

    def test_warmup_from_unsupported_format(self):
        """测试不支持的文件格式"""
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as f:
            filepath = f.name

        try:
            result = warmup_cache_from_file(filepath)
            assert result.failed == 1
            assert "Unsupported file format" in result.errors[0]
        finally:
            Path(filepath).unlink()

    def test_warmup_from_invalid_json(self):
        """测试无效的 JSON 内容"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write("not valid json")
            filepath = f.name

        try:
            result = warmup_cache_from_file(filepath)
            assert result.failed == 1
            assert "Failed to read file" in result.errors[0]
        finally:
            Path(filepath).unlink()

    def test_warmup_from_json_non_array(self):
        """测试 JSON 文件不是数组"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({"not": "array"}, f)
            filepath = f.name

        try:
            result = warmup_cache_from_file(filepath)
            assert result.failed == 1
            assert "must contain an array" in result.errors[0]
        finally:
            Path(filepath).unlink()


class TestWarmupCacheParallel:
    """测试并行缓存预热"""

    def test_parallel_warmup_basic(self):
        """测试基本并行预热"""
        items = [(f"并行文本{i}", {"score": i * 10}) for i in range(10)]
        result = warmup_cache_parallel(items, max_workers=4)

        assert result.total_items == 10
        assert result.warmed == 10
        assert result.failed == 0

        # 验证所有缓存都已填充
        for i in range(10):
            assert get_cached_score(f"并行文本{i}")["score"] == i * 10

    def test_parallel_warmup_with_score_fn(self):
        """测试带评分函数的并行预热"""
        call_count = {"value": 0}
        lock = __import__("threading").Lock()

        def counting_score_fn(text: str) -> Dict[str, Any]:
            with lock:
                call_count["value"] += 1
            time.sleep(0.01)  # 模拟 I/O
            return {"score": len(text)}

        items = [f"文本_{i}" for i in range(8)]
        result = warmup_cache_parallel(items, score_fn=counting_score_fn, max_workers=4)

        assert result.warmed == 8
        assert call_count["value"] == 8

    def test_parallel_warmup_skip_existing(self):
        """测试并行预热跳过已存在"""
        # 预先缓存一些
        for i in range(3):
            cache_score_result(f"已缓存{i}", {"score": i})

        items = [(f"已缓存{i}", {"score": i + 100}) for i in range(3)]
        items += [(f"新文本{i}", {"score": i}) for i in range(5)]

        result = warmup_cache_parallel(items, skip_existing=True, max_workers=2)

        assert result.skipped == 3
        assert result.warmed == 5
        # 确认原缓存值未被覆盖
        assert get_cached_score("已缓存0")["score"] == 0

    def test_parallel_warmup_error_handling(self):
        """测试并行预热的错误处理"""

        def flaky_score_fn(text: str) -> Dict[str, Any]:
            if "fail" in text:
                raise ValueError("模拟失败")
            return {"score": 80}

        items = ["正常1", "fail_文本", "正常2", "fail_另一个"]
        result = warmup_cache_parallel(items, score_fn=flaky_score_fn, max_workers=2)

        assert result.warmed == 2
        assert result.failed == 2

    def test_parallel_warmup_faster_than_sequential(self):
        """测试并行预热比串行快"""

        def slow_score_fn(text: str) -> Dict[str, Any]:
            time.sleep(0.05)  # 50ms 延迟
            return {"score": 80}

        items = [f"文本{i}" for i in range(8)]

        # 并行执行（4 workers）
        result_parallel = warmup_cache_parallel(
            items, score_fn=slow_score_fn, max_workers=4, skip_existing=False
        )

        # 清空缓存再串行执行
        clear_score_cache()
        result_sequential = warmup_cache(items, score_fn=slow_score_fn, skip_existing=False)

        # 并行应该明显更快（理论上 ~4x）
        # 允许一些误差，但并行至少应该快 2 倍
        assert result_parallel.duration_ms < result_sequential.duration_ms * 0.7
