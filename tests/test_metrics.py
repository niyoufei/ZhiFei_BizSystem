"""
Prometheus 指标模块测试
"""


from app.metrics import (
    ACTIVE_SCORE_TASKS,
    CACHE_EVICTIONS,
    CACHE_HITS,
    CACHE_MISSES,
    CACHE_SIZE,
    CONFIG_CACHED,
    CONFIG_RELOADS,
    PROJECTS_TOTAL,
    REGISTRY,
    REQUEST_COUNT,
    SCORE_REQUESTS,
    SUBMISSIONS_TOTAL,
    decrement_active_tasks,
    get_metrics,
    increment_active_tasks,
    record_cache_eviction,
    record_cache_hit,
    record_cache_miss,
    record_config_reload,
    record_request,
    record_score,
    update_cache_size,
    update_config_cached,
    update_project_stats,
)


class TestGetMetrics:
    """get_metrics 函数测试"""

    def test_returns_bytes(self):
        """返回 bytes 类型"""
        result = get_metrics()
        assert isinstance(result, bytes)

    def test_contains_prometheus_format(self):
        """包含 Prometheus 格式的指标"""
        result = get_metrics().decode("utf-8")
        # 应该包含指标名称
        assert "qingtian_" in result

    def test_contains_help_text(self):
        """包含 HELP 注释"""
        result = get_metrics().decode("utf-8")
        assert "# HELP" in result

    def test_contains_type_annotation(self):
        """包含 TYPE 注释"""
        result = get_metrics().decode("utf-8")
        assert "# TYPE" in result


class TestRecordRequest:
    """record_request 函数测试"""

    def test_increments_counter(self):
        """增加请求计数器"""
        # 记录前的值（可能已有值）
        before = REQUEST_COUNT.labels(
            method="GET", endpoint="/test", status_code="200"
        )._value.get()

        record_request("GET", "/test", 200, 0.1)

        after = REQUEST_COUNT.labels(method="GET", endpoint="/test", status_code="200")._value.get()
        assert after == before + 1

    def test_records_latency(self):
        """记录请求延迟"""
        record_request("POST", "/score", 200, 0.5)
        # 验证直方图有数据（通过 get_metrics 间接验证）
        metrics = get_metrics().decode("utf-8")
        assert "qingtian_http_request_duration_seconds" in metrics

    def test_handles_different_status_codes(self):
        """处理不同状态码"""
        record_request("GET", "/error", 404, 0.01)
        record_request("POST", "/fail", 500, 0.02)

        metrics = get_metrics().decode("utf-8")
        assert "404" in metrics
        assert "500" in metrics


class TestRecordScore:
    """record_score 函数测试"""

    def test_increments_score_requests(self):
        """增加评分请求计数"""
        before = SCORE_REQUESTS._value.get()
        record_score(85.5)
        after = SCORE_REQUESTS._value.get()
        assert after == before + 1

    def test_records_score_distribution(self):
        """记录分数分布"""
        record_score(75.0)
        record_score(85.0)
        record_score(95.0)

        metrics = get_metrics().decode("utf-8")
        assert "qingtian_score_distribution" in metrics

    def test_handles_edge_scores(self):
        """处理边界分数"""
        record_score(0.0)
        record_score(100.0)
        # 不应抛出异常


class TestUpdateProjectStats:
    """update_project_stats 函数测试"""

    def test_sets_projects_total(self):
        """设置项目总数"""
        update_project_stats(10, 50)
        assert PROJECTS_TOTAL._value.get() == 10

    def test_sets_submissions_total(self):
        """设置提交总数"""
        update_project_stats(10, 50)
        assert SUBMISSIONS_TOTAL._value.get() == 50

    def test_updates_values(self):
        """更新数值"""
        update_project_stats(5, 20)
        assert PROJECTS_TOTAL._value.get() == 5
        assert SUBMISSIONS_TOTAL._value.get() == 20

        update_project_stats(15, 100)
        assert PROJECTS_TOTAL._value.get() == 15
        assert SUBMISSIONS_TOTAL._value.get() == 100


class TestConfigMetrics:
    """配置相关指标测试"""

    def test_record_config_reload(self):
        """记录配置重载"""
        before = CONFIG_RELOADS._value.get()
        record_config_reload()
        after = CONFIG_RELOADS._value.get()
        assert after == before + 1

    def test_update_config_cached_true(self):
        """更新配置缓存状态为 True"""
        update_config_cached(True)
        assert CONFIG_CACHED._value.get() == 1

    def test_update_config_cached_false(self):
        """更新配置缓存状态为 False"""
        update_config_cached(False)
        assert CONFIG_CACHED._value.get() == 0


class TestActiveScoreTasks:
    """活跃评分任务指标测试"""

    def test_increment_active_tasks(self):
        """增加活跃任务数"""
        # 先重置
        ACTIVE_SCORE_TASKS.set(0)

        increment_active_tasks()
        assert ACTIVE_SCORE_TASKS._value.get() == 1

        increment_active_tasks()
        assert ACTIVE_SCORE_TASKS._value.get() == 2

    def test_decrement_active_tasks(self):
        """减少活跃任务数"""
        ACTIVE_SCORE_TASKS.set(5)

        decrement_active_tasks()
        assert ACTIVE_SCORE_TASKS._value.get() == 4

    def test_increment_decrement_cycle(self):
        """增减循环测试"""
        ACTIVE_SCORE_TASKS.set(0)

        increment_active_tasks()
        increment_active_tasks()
        decrement_active_tasks()

        assert ACTIVE_SCORE_TASKS._value.get() == 1


class TestCacheMetrics:
    """缓存指标测试"""

    def test_record_cache_hit(self):
        """记录缓存命中"""
        before = CACHE_HITS._value.get()
        record_cache_hit()
        after = CACHE_HITS._value.get()
        assert after == before + 1

    def test_record_cache_miss(self):
        """记录缓存未命中"""
        before = CACHE_MISSES._value.get()
        record_cache_miss()
        after = CACHE_MISSES._value.get()
        assert after == before + 1

    def test_record_cache_eviction_single(self):
        """记录单次缓存淘汰"""
        before = CACHE_EVICTIONS._value.get()
        record_cache_eviction()
        after = CACHE_EVICTIONS._value.get()
        assert after == before + 1

    def test_record_cache_eviction_multiple(self):
        """记录多次缓存淘汰"""
        before = CACHE_EVICTIONS._value.get()
        record_cache_eviction(5)
        after = CACHE_EVICTIONS._value.get()
        assert after == before + 5

    def test_update_cache_size(self):
        """更新缓存大小"""
        update_cache_size(42)
        assert CACHE_SIZE._value.get() == 42

        update_cache_size(0)
        assert CACHE_SIZE._value.get() == 0

    def test_cache_metrics_in_output(self):
        """缓存指标出现在输出中"""
        metrics = get_metrics().decode("utf-8")

        cache_metrics = [
            "qingtian_cache_hits_total",
            "qingtian_cache_misses_total",
            "qingtian_cache_evictions_total",
            "qingtian_cache_size",
        ]

        for metric_name in cache_metrics:
            assert metric_name in metrics, f"缺少缓存指标: {metric_name}"


class TestMetricsIntegration:
    """指标集成测试"""

    def test_all_metrics_in_output(self):
        """所有指标都出现在输出中"""
        metrics = get_metrics().decode("utf-8")

        expected_metrics = [
            "qingtian_http_requests_total",
            "qingtian_http_request_duration_seconds",
            "qingtian_score_requests_total",
            "qingtian_score_distribution",
            "qingtian_active_score_tasks",
            "qingtian_projects_total",
            "qingtian_submissions_total",
            "qingtian_config_reloads_total",
            "qingtian_config_cached",
            "qingtian_cache_hits_total",
            "qingtian_cache_misses_total",
            "qingtian_cache_evictions_total",
            "qingtian_cache_size",
        ]

        for metric_name in expected_metrics:
            assert metric_name in metrics, f"缺少指标: {metric_name}"

    def test_registry_isolation(self):
        """注册表隔离测试"""
        # 自定义注册表不应包含默认指标
        from prometheus_client import REGISTRY as DEFAULT_REGISTRY

        assert REGISTRY is not DEFAULT_REGISTRY
