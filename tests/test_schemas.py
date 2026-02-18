"""Unit tests for app/schemas.py - Pydantic models."""

import pytest
from pydantic import ValidationError

from app.schemas import (
    AdaptiveApplyResult,
    AdaptivePatch,
    AdaptiveSuggestions,
    AdaptiveValidation,
    CompareNarrative,
    CompareReport,
    DimensionScore,
    EvidenceSpan,
    InsightsReport,
    LearningProfile,
    LogicLockResult,
    MaterialRecord,
    Penalty,
    ProjectCreate,
    ProjectRecord,
    ScoreReport,
    ScoreRequest,
    SubmissionRecord,
    SubScore,
    Suggestion,
)


class TestScoreRequest:
    """Tests for ScoreRequest model."""

    def test_create_with_text_only(self):
        req = ScoreRequest(text="施工方案内容")
        assert req.text == "施工方案内容"
        assert req.project_type is None

    def test_create_with_project_type(self):
        req = ScoreRequest(text="内容", project_type="建筑工程")
        assert req.text == "内容"
        assert req.project_type == "建筑工程"

    def test_text_required(self):
        with pytest.raises(ValidationError):
            ScoreRequest()

    def test_serialization(self):
        req = ScoreRequest(text="测试", project_type="类型")
        data = req.model_dump()
        assert data == {"text": "测试", "project_type": "类型"}


class TestProjectCreate:
    """Tests for ProjectCreate model."""

    def test_create_with_name_only(self):
        proj = ProjectCreate(name="项目名")
        assert proj.name == "项目名"
        assert proj.meta is None

    def test_create_with_meta(self):
        proj = ProjectCreate(name="项目", meta={"key": "value"})
        assert proj.meta == {"key": "value"}

    def test_name_required(self):
        with pytest.raises(ValidationError):
            ProjectCreate()


class TestProjectRecord:
    """Tests for ProjectRecord model."""

    def test_create_full(self):
        rec = ProjectRecord(
            id="proj-001",
            name="测试项目",
            meta={"location": "北京"},
            created_at="2026-01-01T00:00:00",
        )
        assert rec.id == "proj-001"
        assert rec.name == "测试项目"
        assert rec.meta == {"location": "北京"}
        assert rec.created_at == "2026-01-01T00:00:00"

    def test_meta_optional(self):
        rec = ProjectRecord(id="id", name="name", created_at="2026-01-01")
        assert rec.meta is None

    def test_required_fields(self):
        with pytest.raises(ValidationError):
            ProjectRecord(id="id", name="name")  # missing created_at


class TestMaterialRecord:
    """Tests for MaterialRecord model."""

    def test_create(self):
        mat = MaterialRecord(
            id="mat-001",
            project_id="proj-001",
            filename="招标文件.pdf",
            path="/uploads/招标文件.pdf",
            created_at="2026-01-01T00:00:00",
        )
        assert mat.id == "mat-001"
        assert mat.project_id == "proj-001"
        assert mat.filename == "招标文件.pdf"
        assert mat.path == "/uploads/招标文件.pdf"

    def test_all_fields_required(self):
        with pytest.raises(ValidationError):
            MaterialRecord(id="id", project_id="proj")


class TestSubmissionRecord:
    """Tests for SubmissionRecord model."""

    def test_create_full(self):
        sub = SubmissionRecord(
            id="sub-001",
            project_id="proj-001",
            filename="方案A.txt",
            total_score=85.5,
            report={"scores": [1, 2, 3]},
            created_at="2026-01-01",
            text="方案内容",
        )
        assert sub.id == "sub-001"
        assert sub.total_score == 85.5
        assert sub.text == "方案内容"

    def test_text_optional(self):
        sub = SubmissionRecord(
            id="sub-001",
            project_id="proj-001",
            filename="方案.txt",
            total_score=80.0,
            report={},
            created_at="2026-01-01",
        )
        assert sub.text is None


class TestCompareReport:
    """Tests for CompareReport model."""

    def test_create(self):
        report = CompareReport(
            project_id="proj-001",
            rankings=[{"id": "sub-001", "rank": 1}],
            dimension_avg={"技术方案": 8.5, "安全措施": 9.0},
            penalty_stats={"EMPTY_PROMISE": 3, "ACTION_MISSING": 2},
        )
        assert report.project_id == "proj-001"
        assert len(report.rankings) == 1
        assert report.dimension_avg["技术方案"] == 8.5


class TestInsightsReport:
    """Tests for InsightsReport model."""

    def test_create(self):
        report = InsightsReport(
            project_id="proj-001",
            dimension_avg={"安全": 8.0},
            weakest_dims=[{"name": "安全", "avg": 8.0}],
            frequent_penalties=[{"code": "EP", "count": 5}],
            recommendations=[{"action": "加强安全描述"}],
        )
        assert report.project_id == "proj-001"
        assert len(report.weakest_dims) == 1
        assert len(report.recommendations) == 1


class TestLearningProfile:
    """Tests for LearningProfile model."""

    def test_create(self):
        profile = LearningProfile(
            project_id="proj-001",
            dimension_multipliers={"安全": 1.2, "质量": 1.1},
            rationale={"安全": "此项目安全要求高"},
            updated_at="2026-01-01T00:00:00",
        )
        assert profile.dimension_multipliers["安全"] == 1.2
        assert "安全" in profile.rationale


class TestCompareNarrative:
    """Tests for CompareNarrative model."""

    def test_create(self):
        narrative = CompareNarrative(
            project_id="proj-001",
            summary="方案A表现最佳",
            top_submission={"id": "sub-001", "score": 95},
            bottom_submission={"id": "sub-003", "score": 70},
            key_diffs=[{"dim": "安全", "diff": 15}],
        )
        assert narrative.summary == "方案A表现最佳"
        assert narrative.top_submission["score"] == 95


class TestAdaptiveSuggestions:
    """Tests for AdaptiveSuggestions model."""

    def test_create(self):
        sug = AdaptiveSuggestions(
            project_id="proj-001",
            penalty_stats={"EP": 5},
            suggestions=[{"action": "调整词库"}],
        )
        assert sug.penalty_stats["EP"] == 5


class TestAdaptivePatch:
    """Tests for AdaptivePatch model."""

    def test_create(self):
        patch = AdaptivePatch(
            project_id="proj-001",
            lexicon_additions={"新术语": ["A", "B"]},
            rubric_adjustments={"安全": {"weight": 1.1}},
        )
        assert "新术语" in patch.lexicon_additions


class TestAdaptiveApplyResult:
    """Tests for AdaptiveApplyResult model."""

    def test_create(self):
        result = AdaptiveApplyResult(
            project_id="proj-001",
            applied=True,
            changes=["添加词库项", "调整权重"],
            backup_path="/backups/001",
        )
        assert result.applied is True
        assert len(result.changes) == 2


class TestAdaptiveValidation:
    """Tests for AdaptiveValidation model."""

    def test_create(self):
        val = AdaptiveValidation(
            project_id="proj-001",
            avg_delta=2.5,
            comparisons=[{"before": 80, "after": 82.5}],
        )
        assert val.avg_delta == 2.5


class TestEvidenceSpan:
    """Tests for EvidenceSpan model."""

    def test_create(self):
        span = EvidenceSpan(start_index=100, end_index=150, snippet="关键内容片段")
        assert span.start_index == 100
        assert span.end_index == 150
        assert span.snippet == "关键内容片段"

    def test_all_fields_required(self):
        with pytest.raises(ValidationError):
            EvidenceSpan(start_index=0, end_index=10)  # missing snippet


class TestSubScore:
    """Tests for SubScore model."""

    def test_create(self):
        sub = SubScore(
            name="子项1",
            score=8.5,
            hits=["命中词1", "命中词2"],
            evidence=[EvidenceSpan(start_index=0, end_index=10, snippet="证据")],
        )
        assert sub.name == "子项1"
        assert sub.score == 8.5
        assert len(sub.hits) == 2
        assert len(sub.evidence) == 1


class TestDimensionScore:
    """Tests for DimensionScore model."""

    def test_create_minimal(self):
        dim = DimensionScore(
            id="dim-001",
            name="安全措施",
            module="safety",
            score=8.0,
            max_score=10.0,
            hits=["安全帽"],
            evidence=[],
        )
        assert dim.id == "dim-001"
        assert dim.sub_scores is None

    def test_create_with_subscores(self):
        sub = SubScore(name="子项", score=4.0, hits=[], evidence=[])
        dim = DimensionScore(
            id="dim-001",
            name="技术方案",
            module="tech",
            score=8.0,
            max_score=10.0,
            hits=[],
            evidence=[],
            sub_scores=[sub],
        )
        assert len(dim.sub_scores) == 1


class TestLogicLockResult:
    """Tests for LogicLockResult model."""

    def test_create(self):
        result = LogicLockResult(
            definition_score=3.0,
            analysis_score=2.5,
            solution_score=3.5,
            breaks=["逻辑断点1"],
            evidence=[],
        )
        assert result.definition_score == 3.0
        assert result.analysis_score == 2.5
        assert result.solution_score == 3.5
        assert len(result.breaks) == 1


class TestPenalty:
    """Tests for Penalty model."""

    def test_create_minimal(self):
        p = Penalty(code="EMPTY_PROMISE", message="空洞承诺", evidence_span=None)
        assert p.code == "EMPTY_PROMISE"
        assert p.deduct is None
        assert p.tags is None

    def test_create_full(self):
        span = EvidenceSpan(start_index=0, end_index=20, snippet="空话内容")
        p = Penalty(
            code="EMPTY_PROMISE",
            message="发现空洞承诺",
            evidence_span=span,
            deduct=2.0,
            tags=["quality", "content"],
        )
        assert p.deduct == 2.0
        assert len(p.tags) == 2


class TestSuggestion:
    """Tests for Suggestion model."""

    def test_create(self):
        sug = Suggestion(dimension="安全措施", action="补充安全交底内容", expected_gain=2.5)
        assert sug.dimension == "安全措施"
        assert sug.action == "补充安全交底内容"
        assert sug.expected_gain == 2.5


class TestScoreReport:
    """Tests for ScoreReport model - the main report model."""

    def test_create_minimal(self):
        report = ScoreReport(
            total_score=85.0,
            dimension_scores={},
            logic_lock=LogicLockResult(
                definition_score=3.0,
                analysis_score=3.0,
                solution_score=3.0,
                breaks=[],
                evidence=[],
            ),
            penalties=[],
            suggestions=[],
            meta={"version": "1.0"},
        )
        assert report.total_score == 85.0
        assert report.judge_mode is None
        assert report.spark_called is None

    def test_create_full(self):
        dim = DimensionScore(
            id="dim-001",
            name="安全",
            module="safety",
            score=9.0,
            max_score=10.0,
            hits=["安全帽"],
            evidence=[],
        )
        penalty = Penalty(code="EP", message="空洞", evidence_span=None)
        suggestion = Suggestion(dimension="安全", action="改进", expected_gain=1.0)

        report = ScoreReport(
            total_score=88.5,
            dimension_scores={"safety": dim},
            logic_lock=LogicLockResult(
                definition_score=3.0,
                analysis_score=3.0,
                solution_score=4.0,
                breaks=[],
                evidence=[],
            ),
            penalties=[penalty],
            penalties_logic_lock=[],
            penalties_empty_promises=[penalty],
            penalties_action_missing=[],
            suggestions=[suggestion],
            meta={"version": "1.0"},
            judge_mode="llm",
            judge_source="spark",
            spark_called=True,
            fallback_reason=None,
        )
        assert report.total_score == 88.5
        assert "safety" in report.dimension_scores
        assert report.judge_mode == "llm"
        assert report.spark_called is True

    def test_serialization_roundtrip(self):
        """Test that model can be serialized and deserialized."""
        report = ScoreReport(
            total_score=80.0,
            dimension_scores={},
            logic_lock=LogicLockResult(
                definition_score=2.0,
                analysis_score=2.0,
                solution_score=2.0,
                breaks=["断点"],
                evidence=[],
            ),
            penalties=[],
            suggestions=[],
            meta={},
        )
        data = report.model_dump()
        restored = ScoreReport(**data)
        assert restored.total_score == 80.0
        assert restored.logic_lock.breaks == ["断点"]


class TestModelValidation:
    """Tests for Pydantic validation features."""

    def test_type_coercion_float(self):
        """Test that integer is coerced to float."""
        sub = SubmissionRecord(
            id="id",
            project_id="proj",
            filename="file.txt",
            total_score=80,  # int, should become float
            report={},
            created_at="2026-01-01",
        )
        assert isinstance(sub.total_score, float)

    def test_invalid_type_raises(self):
        """Test that invalid type raises ValidationError."""
        with pytest.raises(ValidationError):
            EvidenceSpan(start_index="not an int", end_index=10, snippet="text")

    def test_nested_validation(self):
        """Test that nested models are validated."""
        with pytest.raises(ValidationError):
            SubScore(
                name="test",
                score=5.0,
                hits=[],
                evidence=[{"invalid": "data"}],  # Should be EvidenceSpan
            )


class TestModelEquality:
    """Tests for model equality."""

    def test_same_data_equals(self):
        span1 = EvidenceSpan(start_index=0, end_index=10, snippet="text")
        span2 = EvidenceSpan(start_index=0, end_index=10, snippet="text")
        assert span1 == span2

    def test_different_data_not_equals(self):
        span1 = EvidenceSpan(start_index=0, end_index=10, snippet="text")
        span2 = EvidenceSpan(start_index=0, end_index=20, snippet="text")
        assert span1 != span2


class TestModelCopy:
    """Tests for model copying."""

    def test_model_copy(self):
        span = EvidenceSpan(start_index=0, end_index=10, snippet="original")
        copied = span.model_copy(update={"snippet": "modified"})
        assert span.snippet == "original"
        assert copied.snippet == "modified"
