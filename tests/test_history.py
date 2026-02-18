"""评分历史记录和趋势分析测试"""

import pytest

from app.engine.history import (
    analyze_trend,
    calculate_trend,
    generate_recommendations,
    get_history,
    record_score,
)
from app.storage import HISTORY_PATH, load_score_history, save_score_history


@pytest.fixture(autouse=True)
def clean_history():
    """每个测试前后清理历史记录"""
    # 备份原有数据
    backup = None
    if HISTORY_PATH.exists():
        backup = HISTORY_PATH.read_text()

    # 清空历史
    save_score_history([])

    yield

    # 恢复原有数据
    if backup:
        HISTORY_PATH.write_text(backup)
    elif HISTORY_PATH.exists():
        HISTORY_PATH.unlink()


class TestRecordScore:
    """测试记录评分功能"""

    def test_record_score_creates_entry(self):
        """记录评分应创建条目"""
        entry = record_score(
            project_id="proj-001",
            submission_id="sub-001",
            filename="test.txt",
            total_score=75.5,
            dimension_scores={"D01": 15.0, "D02": 12.5},
            penalty_count=3,
        )

        assert entry["project_id"] == "proj-001"
        assert entry["submission_id"] == "sub-001"
        assert entry["filename"] == "test.txt"
        assert entry["total_score"] == 75.5
        assert entry["dimension_scores"] == {"D01": 15.0, "D02": 12.5}
        assert entry["penalty_count"] == 3
        assert "id" in entry
        assert "created_at" in entry

    def test_record_score_persists_to_storage(self):
        """记录的评分应持久化到存储"""
        record_score(
            project_id="proj-001",
            submission_id="sub-001",
            filename="test.txt",
            total_score=75.5,
            dimension_scores={"D01": 15.0},
            penalty_count=2,
        )

        history = load_score_history()
        assert len(history) == 1
        assert history[0]["project_id"] == "proj-001"

    def test_record_multiple_scores(self):
        """记录多次评分"""
        for i in range(3):
            record_score(
                project_id="proj-001",
                submission_id=f"sub-00{i+1}",
                filename=f"test_{i+1}.txt",
                total_score=70.0 + i * 5,
                dimension_scores={"D01": 15.0 + i},
                penalty_count=3 - i,
            )

        history = load_score_history()
        assert len(history) == 3


class TestGetHistory:
    """测试获取历史记录功能"""

    def test_get_history_empty(self):
        """无历史记录时返回空"""
        result = get_history("proj-001")
        assert result["project_id"] == "proj-001"
        assert result["entries"] == []
        assert result["total_count"] == 0

    def test_get_history_with_entries(self):
        """有历史记录时返回条目"""
        record_score(
            project_id="proj-001",
            submission_id="sub-001",
            filename="test.txt",
            total_score=75.5,
            dimension_scores={"D01": 15.0},
            penalty_count=2,
        )

        result = get_history("proj-001")
        assert result["total_count"] == 1
        assert len(result["entries"]) == 1

    def test_get_history_filters_by_project(self):
        """历史记录按项目过滤"""
        record_score(
            project_id="proj-001",
            submission_id="sub-001",
            filename="test1.txt",
            total_score=75.5,
            dimension_scores={},
            penalty_count=0,
        )
        record_score(
            project_id="proj-002",
            submission_id="sub-002",
            filename="test2.txt",
            total_score=80.0,
            dimension_scores={},
            penalty_count=0,
        )

        result1 = get_history("proj-001")
        result2 = get_history("proj-002")

        assert result1["total_count"] == 1
        assert result2["total_count"] == 1
        assert result1["entries"][0]["filename"] == "test1.txt"
        assert result2["entries"][0]["filename"] == "test2.txt"


class TestCalculateTrend:
    """测试趋势计算功能"""

    def test_trend_stable_single_score(self):
        """单次评分趋势为稳定"""
        assert calculate_trend([75.0]) == "stable"

    def test_trend_stable_similar_scores(self):
        """相近分数趋势为稳定"""
        assert calculate_trend([75.0, 75.5, 74.5, 75.0]) == "stable"

    def test_trend_improving(self):
        """分数上升趋势"""
        assert calculate_trend([70.0, 72.0, 75.0, 80.0, 85.0]) == "improving"

    def test_trend_declining(self):
        """分数下降趋势"""
        assert calculate_trend([85.0, 80.0, 75.0, 72.0, 70.0]) == "declining"

    def test_trend_two_scores_improving(self):
        """两次评分上升"""
        assert calculate_trend([70.0, 80.0]) == "improving"

    def test_trend_two_scores_declining(self):
        """两次评分下降"""
        assert calculate_trend([80.0, 70.0]) == "declining"


class TestAnalyzeTrend:
    """测试趋势分析功能"""

    def test_analyze_trend_empty(self):
        """无历史数据时的趋势分析"""
        result = analyze_trend("proj-001")
        assert result["project_id"] == "proj-001"
        assert result["total_submissions"] == 0
        assert result["overall_trend"] == "stable"
        assert result["avg_score"] == 0.0
        assert len(result["recommendations"]) > 0

    def test_analyze_trend_with_data(self):
        """有历史数据时的趋势分析"""
        # 记录多次评分，模拟上升趋势
        for i in range(5):
            record_score(
                project_id="proj-001",
                submission_id=f"sub-00{i+1}",
                filename=f"v{i+1}.txt",
                total_score=70.0 + i * 5,  # 70, 75, 80, 85, 90
                dimension_scores={"D01": 15.0 + i, "D02": 10.0 + i},
                penalty_count=5 - i,
            )

        result = analyze_trend("proj-001")

        assert result["total_submissions"] == 5
        assert result["overall_trend"] == "improving"
        assert result["avg_score"] == 80.0
        assert result["best_score"] == 90.0
        assert result["worst_score"] == 70.0
        assert result["latest_score"] == 90.0
        assert result["score_improvement"] == 20.0
        assert len(result["dimension_trends"]) == 2
        assert len(result["penalty_trend"]) == 5
        assert result["penalty_trend"] == [5, 4, 3, 2, 1]  # 下降趋势

    def test_analyze_trend_with_dimension_names(self):
        """趋势分析包含维度名称"""
        record_score(
            project_id="proj-001",
            submission_id="sub-001",
            filename="test.txt",
            total_score=75.0,
            dimension_scores={"D01": 15.0, "D02": 10.0},
            penalty_count=2,
        )

        dimension_names = {"D01": "工程概况", "D02": "施工部署"}
        result = analyze_trend("proj-001", dimension_names)

        dim_trend_names = {d["dimension_name"] for d in result["dimension_trends"]}
        assert "工程概况" in dim_trend_names
        assert "施工部署" in dim_trend_names


class TestGenerateRecommendations:
    """测试建议生成功能"""

    def test_recommendations_improving(self):
        """上升趋势的建议"""
        recs = generate_recommendations(
            overall_trend="improving",
            score_improvement=10.0,
            dimension_trends=[],
            penalty_trend=[5, 3],
            latest_score=85.0,
        )
        assert any("上升" in r or "保持" in r for r in recs)

    def test_recommendations_declining(self):
        """下降趋势的建议"""
        recs = generate_recommendations(
            overall_trend="declining",
            score_improvement=-10.0,
            dimension_trends=[],
            penalty_trend=[3, 5],
            latest_score=65.0,
        )
        assert any("下降" in r for r in recs)

    def test_recommendations_declining_dimensions(self):
        """维度下降的建议"""
        recs = generate_recommendations(
            overall_trend="stable",
            score_improvement=0.0,
            dimension_trends=[
                {
                    "dimension_id": "D01",
                    "dimension_name": "工程概况",
                    "trend": "declining",
                    "latest_score": 10.0,
                    "avg_score": 15.0,
                }
            ],
            penalty_trend=[3, 3],
            latest_score=75.0,
        )
        assert any("工程概况" in r for r in recs)

    def test_recommendations_penalty_increase(self):
        """扣分项增加的建议"""
        recs = generate_recommendations(
            overall_trend="stable",
            score_improvement=0.0,
            dimension_trends=[],
            penalty_trend=[3, 5],
            latest_score=75.0,
        )
        assert any("扣分项" in r and "增加" in r for r in recs)

    def test_recommendations_penalty_decrease(self):
        """扣分项减少的建议"""
        recs = generate_recommendations(
            overall_trend="stable",
            score_improvement=0.0,
            dimension_trends=[],
            penalty_trend=[5, 3],
            latest_score=75.0,
        )
        assert any("扣分项" in r and "减少" in r for r in recs)


class TestSchemas:
    """测试 Schema 模型"""

    def test_score_history_entry_schema(self):
        """测试 ScoreHistoryEntry schema"""
        from app.schemas import ScoreHistoryEntry

        entry = ScoreHistoryEntry(
            id="hist-001",
            project_id="proj-001",
            submission_id="sub-001",
            filename="test.txt",
            total_score=75.5,
            dimension_scores={"D01": 15.0},
            penalty_count=2,
            created_at="2026-02-04T10:00:00+00:00",
        )
        assert entry.total_score == 75.5

    def test_trend_analysis_schema(self):
        """测试 TrendAnalysis schema"""
        from app.schemas import TrendAnalysis, TrendPoint

        trend = TrendAnalysis(
            project_id="proj-001",
            total_submissions=3,
            score_history=[
                TrendPoint(
                    submission_id="sub-001",
                    filename="v1.txt",
                    total_score=70.0,
                    created_at="2026-02-01T10:00:00",
                )
            ],
            overall_trend="improving",
            avg_score=75.0,
            best_score=80.0,
            worst_score=70.0,
            latest_score=80.0,
            score_improvement=10.0,
            dimension_trends=[],
            penalty_trend=[3, 2, 1],
            recommendations=["继续保持"],
        )
        assert trend.overall_trend == "improving"

    def test_project_score_history_schema(self):
        """测试 ProjectScoreHistory schema"""
        from app.schemas import ProjectScoreHistory, ScoreHistoryEntry

        history = ProjectScoreHistory(
            project_id="proj-001",
            entries=[
                ScoreHistoryEntry(
                    id="hist-001",
                    project_id="proj-001",
                    submission_id="sub-001",
                    filename="test.txt",
                    total_score=75.5,
                    dimension_scores={},
                    penalty_count=0,
                    created_at="2026-02-04T10:00:00",
                )
            ],
            total_count=1,
        )
        assert history.total_count == 1
