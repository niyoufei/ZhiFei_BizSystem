"""
Prometheus 指标模块

提供系统运行时指标的收集和导出功能。
"""

from prometheus_client import Counter, Gauge, Histogram, generate_latest
from prometheus_client.core import CollectorRegistry

# 创建自定义注册表（避免与默认注册表冲突）
REGISTRY = CollectorRegistry()

# ==================== 请求指标 ====================

# 请求计数器（按端点和状态码）
REQUEST_COUNT = Counter(
    "qingtian_http_requests_total",
    "HTTP 请求总数",
    ["method", "endpoint", "status_code"],
    registry=REGISTRY,
)

# 请求延迟直方图（秒）
REQUEST_LATENCY = Histogram(
    "qingtian_http_request_duration_seconds",
    "HTTP 请求延迟（秒）",
    ["method", "endpoint"],
    buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0],
    registry=REGISTRY,
)

# ==================== 评分指标 ====================

# 评分请求计数
SCORE_REQUESTS = Counter(
    "qingtian_score_requests_total",
    "评分请求总数",
    registry=REGISTRY,
)

# 评分分数分布直方图
SCORE_DISTRIBUTION = Histogram(
    "qingtian_score_distribution",
    "评分分数分布",
    buckets=[0, 10, 20, 30, 40, 50, 60, 70, 80, 90, 100],
    registry=REGISTRY,
)

# 当前活跃评分任务数
ACTIVE_SCORE_TASKS = Gauge(
    "qingtian_active_score_tasks",
    "当前活跃评分任务数",
    registry=REGISTRY,
)

# ==================== 项目指标 ====================

# 项目总数
PROJECTS_TOTAL = Gauge(
    "qingtian_projects_total",
    "项目总数",
    registry=REGISTRY,
)

# 提交总数
SUBMISSIONS_TOTAL = Gauge(
    "qingtian_submissions_total",
    "施组提交总数",
    registry=REGISTRY,
)

# ==================== 系统指标 ====================

# 配置重载计数
CONFIG_RELOADS = Counter(
    "qingtian_config_reloads_total",
    "配置重载次数",
    registry=REGISTRY,
)

# 配置缓存状态
CONFIG_CACHED = Gauge(
    "qingtian_config_cached",
    "配置是否已缓存（1=是，0=否）",
    registry=REGISTRY,
)

# ==================== 评分缓存指标 ====================

# 缓存命中计数
CACHE_HITS = Counter(
    "qingtian_cache_hits_total",
    "评分缓存命中总数",
    registry=REGISTRY,
)

# 缓存未命中计数
CACHE_MISSES = Counter(
    "qingtian_cache_misses_total",
    "评分缓存未命中总数",
    registry=REGISTRY,
)

# 缓存淘汰计数
CACHE_EVICTIONS = Counter(
    "qingtian_cache_evictions_total",
    "评分缓存淘汰总数",
    registry=REGISTRY,
)

# 当前缓存大小
CACHE_SIZE = Gauge(
    "qingtian_cache_size",
    "当前评分缓存条目数",
    registry=REGISTRY,
)


def get_metrics() -> bytes:
    """获取 Prometheus 格式的指标数据。

    Returns:
        bytes: Prometheus 文本格式的指标数据
    """
    return generate_latest(REGISTRY)


def record_request(method: str, endpoint: str, status_code: int, duration: float) -> None:
    """记录 HTTP 请求指标。

    Args:
        method: HTTP 方法
        endpoint: 端点路径
        status_code: 响应状态码
        duration: 请求耗时（秒）
    """
    REQUEST_COUNT.labels(method=method, endpoint=endpoint, status_code=str(status_code)).inc()
    REQUEST_LATENCY.labels(method=method, endpoint=endpoint).observe(duration)


def record_score(score: float) -> None:
    """记录评分指标。

    Args:
        score: 评分分数
    """
    SCORE_REQUESTS.inc()
    SCORE_DISTRIBUTION.observe(score)


def update_project_stats(projects_count: int, submissions_count: int) -> None:
    """更新项目统计指标。

    Args:
        projects_count: 项目总数
        submissions_count: 提交总数
    """
    PROJECTS_TOTAL.set(projects_count)
    SUBMISSIONS_TOTAL.set(submissions_count)


def record_config_reload() -> None:
    """记录配置重载事件。"""
    CONFIG_RELOADS.inc()


def update_config_cached(cached: bool) -> None:
    """更新配置缓存状态。

    Args:
        cached: 是否已缓存
    """
    CONFIG_CACHED.set(1 if cached else 0)


def increment_active_tasks() -> None:
    """增加活跃评分任务计数。"""
    ACTIVE_SCORE_TASKS.inc()


def decrement_active_tasks() -> None:
    """减少活跃评分任务计数。"""
    ACTIVE_SCORE_TASKS.dec()


def record_cache_hit() -> None:
    """记录缓存命中。"""
    CACHE_HITS.inc()


def record_cache_miss() -> None:
    """记录缓存未命中。"""
    CACHE_MISSES.inc()


def record_cache_eviction(count: int = 1) -> None:
    """记录缓存淘汰。

    Args:
        count: 淘汰条目数
    """
    CACHE_EVICTIONS.inc(count)


def update_cache_size(size: int) -> None:
    """更新缓存大小指标。

    Args:
        size: 当前缓存条目数
    """
    CACHE_SIZE.set(size)
