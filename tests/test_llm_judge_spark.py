"""Tests for app/engine/llm_judge_spark.py module."""

from __future__ import annotations

import os
from typing import Any, Dict
from unittest.mock import patch

from app.engine.llm_judge_spark import (
    _build_from_rules,
    _placeholder_evidence,
    build_spark_payload_from_rules,
    post_process_llm_output,
    run_spark_judge,
    validate_llm_judge_json,
)
from app.schemas import DimensionScore, EvidenceSpan, LogicLockResult, ScoreReport


class TestLoadPrompt:
    """Tests for load_prompt function."""

    def test_load_prompt_reads_existing_file(self):
        """load_prompt should read actual prompt file content (lines 12-14)."""
        from app.engine.llm_judge_spark import load_prompt

        # Test with the actual prompt file that exists
        result = load_prompt("spark_judge_qingtian_v1")
        assert isinstance(result, str)
        assert len(result) > 0
        # The prompt should contain some expected content
        assert "评判" in result or "施工" in result or "评分" in result

    def test_load_prompt_returns_string(self):
        """load_prompt should return a string."""
        from app.engine.llm_judge_spark import load_prompt

        result = load_prompt("spark_judge_qingtian_v1")
        assert isinstance(result, str)

    def test_load_prompt_file_not_found(self):
        """load_prompt should raise error for non-existent file."""
        import pytest

        from app.engine.llm_judge_spark import load_prompt

        with pytest.raises(FileNotFoundError):
            load_prompt("non_existent_prompt")


class TestPlaceholderEvidence:
    """Tests for _placeholder_evidence function."""

    def test_returns_correct_structure(self):
        """_placeholder_evidence should return standard placeholder dict."""
        result = _placeholder_evidence()
        assert result["start_index"] == 0
        assert result["end_index"] == 0
        assert "snippet" in result
        assert "未在输入文本中检索到" in result["snippet"]

    def test_returns_new_instance(self):
        """_placeholder_evidence should return new dict each time."""
        result1 = _placeholder_evidence()
        result2 = _placeholder_evidence()
        assert result1 is not result2


class TestValidateLlmJudgeJson:
    """Tests for validate_llm_judge_json function."""

    def _make_valid_payload(self) -> Dict[str, Any]:
        """Create a valid payload for testing."""
        dim_scores = {}
        for i in range(1, 17):
            dim_id = f"{i:02d}"
            dim_scores[dim_id] = {
                "id": dim_id,
                "name": f"维度{i}",
                "module": "module",
                "score_0_10": 7.0,
                "max_score_0_10": 10,
                "weight_multiplier": 1.0,
                "definition_points": ["point1"],
                "defects": ["defect1"],
                "improvements": ["improvement1"],
                "evidence": [
                    {
                        "snippet": "evidence",
                        "quote": "evidence",
                        "anchor_label": "正文片段",
                    }
                ],
            }
        return {
            "judge_mode": "openai",
            "model": "gpt-5.4",
            "prompt_version": "v1",
            "weights": {
                "high_priority_dims": ["07", "09"],
                "high_priority_multiplier": 1.4,
                "normal_multiplier": 1.0,
            },
            "overall": {"total_score_0_100": 75.0},
            "logic_lock": {"definition_score_0_5": 3.0},
            "dimension_scores": dim_scores,
            "penalties": [],
        }

    def test_valid_payload_passes(self):
        """Valid payload should pass validation."""
        payload = self._make_valid_payload()
        ok, err = validate_llm_judge_json(payload)
        assert ok is True
        assert err == {}

    def test_missing_top_level_field(self):
        """Missing top-level field should fail."""
        payload = self._make_valid_payload()
        del payload["judge_mode"]
        ok, err = validate_llm_judge_json(payload)
        assert ok is False
        assert "missing_field:judge_mode" in err["details"]

    def test_missing_dimension(self):
        """Missing dimension should fail."""
        payload = self._make_valid_payload()
        del payload["dimension_scores"]["05"]
        ok, err = validate_llm_judge_json(payload)
        assert ok is False
        assert "missing_dimension:05" in err["details"]

    def test_missing_weights_field(self):
        """Missing weights field should fail."""
        payload = self._make_valid_payload()
        del payload["weights"]["high_priority_dims"]
        ok, err = validate_llm_judge_json(payload)
        assert ok is False
        assert "missing_weights:high_priority_dims" in err["details"]

    def test_missing_dim_field(self):
        """Missing dimension field should fail."""
        payload = self._make_valid_payload()
        del payload["dimension_scores"]["01"]["name"]
        ok, err = validate_llm_judge_json(payload)
        assert ok is False
        assert "missing_dim_field:01:name" in err["details"]

    def test_invalid_evidence_type(self):
        """Non-list or empty evidence should fail."""
        payload = self._make_valid_payload()
        payload["dimension_scores"]["01"]["evidence"] = []
        ok, err = validate_llm_judge_json(payload)
        assert ok is False
        assert "invalid_evidence_type:01" in err["details"]

    def test_evidence_not_list(self):
        """Evidence as string should fail."""
        payload = self._make_valid_payload()
        payload["dimension_scores"]["01"]["evidence"] = "not a list"
        ok, err = validate_llm_judge_json(payload)
        assert ok is False
        assert "invalid_evidence_type:01" in err["details"]

    def test_rejects_evidence_without_anchor_or_quote(self):
        payload = self._make_valid_payload()
        payload["dimension_scores"]["01"]["evidence"] = [{"snippet": "只有片段"}]

        ok, err = validate_llm_judge_json(payload)

        assert ok is False
        assert "invalid_evidence_item:01" in err["details"]


class TestPostProcessLlmOutput:
    """Tests for post_process_llm_output function."""

    def _make_dim_score(self, score: float = 7.0) -> Dict[str, Any]:
        """Create a dimension score dict."""
        return {
            "score_0_10": score,
            "definition_points": None,
            "defects": None,
            "improvements": None,
            "evidence": None,
        }

    def test_fills_empty_definition_points(self):
        """Empty definition_points should be filled."""
        payload = {"dimension_scores": {"01": self._make_dim_score()}}
        rubric = {}
        result = post_process_llm_output(payload, rubric)
        assert len(result["dimension_scores"]["01"]["definition_points"]) > 0

    def test_fills_empty_defects(self):
        """Empty defects should be filled."""
        payload = {"dimension_scores": {"01": self._make_dim_score()}}
        rubric = {}
        result = post_process_llm_output(payload, rubric)
        assert len(result["dimension_scores"]["01"]["defects"]) > 0

    def test_fills_empty_improvements(self):
        """Empty improvements should be filled."""
        payload = {"dimension_scores": {"01": self._make_dim_score()}}
        rubric = {}
        result = post_process_llm_output(payload, rubric)
        assert len(result["dimension_scores"]["01"]["improvements"]) > 0

    def test_fills_empty_evidence(self):
        """Empty evidence should be filled with placeholder."""
        payload = {"dimension_scores": {"01": self._make_dim_score()}}
        rubric = {}
        result = post_process_llm_output(payload, rubric)
        assert len(result["dimension_scores"]["01"]["evidence"]) > 0
        assert result["dimension_scores"]["01"]["evidence"][0]["start_index"] == 0
        assert result["dimension_scores"]["01"]["evidence"][0]["anchor_label"] == "未定位锚点"
        assert "未在输入文本中检索到" in result["dimension_scores"]["01"]["evidence"][0]["quote"]

    def test_caps_score_when_default_used(self):
        """Score should be capped at 4.0 when defaults are used."""
        payload = {"dimension_scores": {"01": self._make_dim_score(score=8.0)}}
        rubric = {}
        result = post_process_llm_output(payload, rubric)
        assert result["dimension_scores"]["01"]["score_0_10"] <= 4.0

    def test_adds_system_notice_when_default_used(self):
        """System notice should be added when defaults are used."""
        payload = {"dimension_scores": {"01": self._make_dim_score()}}
        rubric = {}
        result = post_process_llm_output(payload, rubric)
        defects = result["dimension_scores"]["01"]["defects"]
        assert any("[系统提示]" in d for d in defects)

    def test_applies_high_priority_multiplier(self):
        """High priority dims should get higher multiplier."""
        payload = {
            "dimension_scores": {
                "07": {
                    "score_0_10": 7.0,
                    "defects": ["d"],
                    "improvements": ["i"],
                    "evidence": [{}],
                },
                "01": {
                    "score_0_10": 7.0,
                    "defects": ["d"],
                    "improvements": ["i"],
                    "evidence": [{}],
                },
            }
        }
        rubric = {
            "llm_weight_profile": {
                "high_priority_dims": ["07"],
                "high_priority_multiplier": 1.4,
                "normal_multiplier": 1.0,
            }
        }
        result = post_process_llm_output(payload, rubric)
        assert result["dimension_scores"]["07"]["weight_multiplier"] == 1.4
        assert result["dimension_scores"]["01"]["weight_multiplier"] == 1.0

    def test_adds_weights_to_payload(self):
        """weights field should be added to payload."""
        payload = {"dimension_scores": {}}
        rubric = {
            "llm_weight_profile": {
                "high_priority_dims": ["07", "09"],
                "high_priority_multiplier": 1.5,
                "normal_multiplier": 1.0,
            }
        }
        result = post_process_llm_output(payload, rubric)
        assert "weights" in result
        assert result["weights"]["high_priority_multiplier"] == 1.5


class TestBuildFromRules:
    """Tests for _build_from_rules function."""

    def _make_score_report(self) -> ScoreReport:
        """Create a mock ScoreReport."""
        from app.engine.dimensions import DIMENSIONS

        dim_scores = {}
        for dim_id, meta in DIMENSIONS.items():
            dim_scores[dim_id] = DimensionScore(
                id=dim_id,
                name=meta["name"],
                module=meta["module"],
                score=7.0,
                max_score=10.0,
                hits=["hit1"],
                evidence=[EvidenceSpan(start_index=0, end_index=10, snippet="test snippet")],
            )

        return ScoreReport(
            total_score=75.0,
            dimension_scores=dim_scores,
            logic_lock=LogicLockResult(
                definition_score=4.0,
                analysis_score=4.0,
                solution_score=4.0,
                breaks=[],
                evidence=[],
            ),
            penalties=[],
            suggestions=[],
            meta={},
            judge_mode="local",
            judge_source="scorer",
            fallback_reason="",
        )

    def test_builds_correct_structure(self):
        """_build_from_rules should build correct payload structure."""
        report = self._make_score_report()
        rubric = {}
        result = _build_from_rules(report, rubric)

        assert "judge_mode" in result
        assert "model" in result
        assert "prompt_version" in result
        assert "weights" in result
        assert "overall" in result
        assert "logic_lock" in result
        assert "dimension_scores" in result
        assert "penalties" in result

    def test_includes_all_dimensions(self):
        """Result should include all 16 dimensions."""
        from app.engine.dimensions import DIMENSIONS

        report = self._make_score_report()
        rubric = {}
        result = _build_from_rules(report, rubric)

        for dim_id in DIMENSIONS:
            assert dim_id in result["dimension_scores"]

    def test_dimension_has_required_fields(self):
        """Each dimension should have required fields."""
        report = self._make_score_report()
        rubric = {}
        result = _build_from_rules(report, rubric)

        dim = result["dimension_scores"]["01"]
        assert "id" in dim
        assert "name" in dim
        assert "module" in dim
        assert "score_0_10" in dim
        assert "evidence" in dim

    def test_handles_logic_lock_breaks(self):
        """Logic lock breaks should be converted correctly."""
        report = self._make_score_report()
        report.logic_lock.breaks = ["definition", "analysis"]
        rubric = {}
        result = _build_from_rules(report, rubric)

        breaks = result["logic_lock"]["breaks"]
        assert len(breaks) == 2
        assert breaks[0]["type"] == "missing_definition"
        assert breaks[1]["type"] == "missing_analysis"

    def test_handles_logic_lock_solution_break(self):
        """Logic lock solution breaks should use else branch (line 177)."""
        report = self._make_score_report()
        # Use "solution" to trigger the else branch at line 177
        report.logic_lock.breaks = ["solution"]
        rubric = {}
        result = _build_from_rules(report, rubric)

        breaks = result["logic_lock"]["breaks"]
        assert len(breaks) == 1
        assert breaks[0]["type"] == "missing_solution"

    def test_handles_all_logic_lock_break_types(self):
        """Test all three break types together."""
        report = self._make_score_report()
        report.logic_lock.breaks = ["definition", "analysis", "solution"]
        rubric = {}
        result = _build_from_rules(report, rubric)

        breaks = result["logic_lock"]["breaks"]
        assert len(breaks) == 3
        assert breaks[0]["type"] == "missing_definition"
        assert breaks[1]["type"] == "missing_analysis"
        assert breaks[2]["type"] == "missing_solution"

    def test_uses_placeholder_for_empty_evidence(self):
        """Empty evidence should use placeholder."""
        from app.engine.dimensions import DIMENSIONS

        dim_scores = {}
        for dim_id, meta in DIMENSIONS.items():
            dim_scores[dim_id] = DimensionScore(
                id=dim_id,
                name=meta["name"],
                module=meta["module"],
                score=7.0,
                max_score=10.0,
                hits=[],
                evidence=[],  # Empty evidence
            )

        report = ScoreReport(
            total_score=75.0,
            dimension_scores=dim_scores,
            logic_lock=LogicLockResult(
                definition_score=4.0,
                analysis_score=4.0,
                solution_score=4.0,
                breaks=[],
                evidence=[],
            ),
            penalties=[],
            suggestions=[],
            meta={},
            judge_mode="local",
            judge_source="scorer",
            fallback_reason="",
        )
        rubric = {}
        result = _build_from_rules(report, rubric)

        dim = result["dimension_scores"]["01"]
        assert len(dim["evidence"]) == 1
        assert dim["evidence"][0]["start_index"] == 0
        assert dim["evidence"][0]["anchor_label"] == "未定位锚点"
        assert dim["score_0_10"] <= 4.0


class TestBuildSparkPayloadFromRules:
    """Tests for build_spark_payload_from_rules function."""

    def _make_score_report(self) -> ScoreReport:
        """Create a mock ScoreReport."""
        from app.engine.dimensions import DIMENSIONS

        dim_scores = {}
        for dim_id, meta in DIMENSIONS.items():
            dim_scores[dim_id] = DimensionScore(
                id=dim_id,
                name=meta["name"],
                module=meta["module"],
                score=7.0,
                max_score=10.0,
                hits=["hit1"],
                evidence=[EvidenceSpan(start_index=0, end_index=10, snippet="test")],
            )

        return ScoreReport(
            total_score=75.0,
            dimension_scores=dim_scores,
            logic_lock=LogicLockResult(
                definition_score=4.0,
                analysis_score=4.0,
                solution_score=4.0,
                breaks=[],
                evidence=[],
            ),
            penalties=[],
            suggestions=[],
            meta={},
            judge_mode="local",
            judge_source="scorer",
            fallback_reason="",
        )

    def test_returns_processed_payload(self):
        """build_spark_payload_from_rules should return post-processed payload."""
        report = self._make_score_report()
        rubric = {"llm_weight_profile": {"high_priority_dims": ["07"]}}
        result = build_spark_payload_from_rules(report, rubric)

        assert "weights" in result
        assert "dimension_scores" in result


class TestRunSparkJudge:
    """Tests for run_spark_judge function."""

    def _make_score_report(self) -> ScoreReport:
        """Create a mock ScoreReport."""
        from app.engine.dimensions import DIMENSIONS

        dim_scores = {}
        for dim_id, meta in DIMENSIONS.items():
            dim_scores[dim_id] = DimensionScore(
                id=dim_id,
                name=meta["name"],
                module=meta["module"],
                score=7.0,
                max_score=10.0,
                hits=["hit1"],
                evidence=[EvidenceSpan(start_index=0, end_index=10, snippet="test")],
            )

        return ScoreReport(
            total_score=75.0,
            dimension_scores=dim_scores,
            logic_lock=LogicLockResult(
                definition_score=4.0,
                analysis_score=4.0,
                solution_score=4.0,
                breaks=[],
                evidence=[],
            ),
            penalties=[],
            suggestions=[],
            meta={},
            judge_mode="local",
            judge_source="scorer",
            fallback_reason="",
        )

    def test_returns_missing_credentials_when_env_not_set(self):
        """run_spark_judge should return error when credentials missing."""
        # Clear any existing env vars
        with patch.dict(os.environ, {}, clear=True):
            report = self._make_score_report()
            result = run_spark_judge("test text", {}, "prompt", report)

            assert result["called_spark_api"] is False
            assert result["called_openai_api"] is False
            assert result["reason"] == "missing_openai_api_key"
            assert result["processing_interrupted"] is True

    def test_returns_missing_openai_credentials_with_legacy_spark_env(self):
        """仅有旧 Spark 环境变量时，应明确提示迁移到 OPENAI_API_KEY。"""
        with patch.dict(os.environ, {"SPARK_APP_ID": "test"}, clear=True):
            report = self._make_score_report()
            result = run_spark_judge("test text", {}, "prompt", report)

            assert result["called_spark_api"] is False
            assert result["called_openai_api"] is False
            assert result["reason"] == "missing_openai_api_key"
            assert result["processing_interrupted"] is True
            assert result["legacy_spark_env_keys"] == ["SPARK_APP_ID"]
            assert "OPENAI_API_KEY" in result["migration_hint"]

    @patch("app.engine.llm_judge_spark.load_prompt")
    @patch("app.engine.llm_judge_spark._call_spark_http")
    def test_loads_prompt_when_openai_credentials_present(self, mock_call_http, mock_load):
        """run_spark_judge should load prompt when OPENAI_API_KEY is set."""
        mock_load.return_value = "test prompt content"
        mock_call_http.return_value = (False, None, "upstream_failed")

        with patch.dict(
            os.environ,
            {
                "OPENAI_API_KEY": "sk-test",
            },
        ):
            report = self._make_score_report()
            result = run_spark_judge("test text", {}, "test_prompt", report)

            mock_load.assert_called_once_with("test_prompt")
            assert result["called_spark_api"] is False
            assert result["reason"] == "request_failed"
            assert result["processing_interrupted"] is True

    @patch("app.engine.llm_judge_spark.load_prompt")
    @patch("app.engine.llm_judge_spark._call_spark_http")
    def test_returns_payload_with_openai_credentials(self, mock_call_http, mock_load):
        """run_spark_judge should return valid payload when OPENAI_API_KEY is present."""
        mock_load.return_value = "test prompt"
        report = self._make_score_report()
        payload = build_spark_payload_from_rules(report, {})
        payload["judge_mode"] = "openai"
        payload["model"] = "gpt-5.4"
        payload["judge_source"] = "openai_api"
        mock_call_http.return_value = (True, payload, "")

        with patch.dict(
            os.environ,
            {
                "OPENAI_API_KEY": "sk-test",
            },
        ):
            result = run_spark_judge("test text", {}, "prompt", report)

            assert result["called_spark_api"] is True
            assert result["called_openai_api"] is True
            assert result["judge_mode"] == "openai"
            assert result["judge_source"] == "openai_api"
            assert "dimension_scores" in result

    @patch("app.engine.llm_judge_spark.select_top_few_shot_prompt_examples")
    @patch("app.engine.llm_judge_spark.load_prompt")
    @patch("app.engine.llm_judge_spark._call_spark_http")
    def test_injects_adopted_few_shot_examples_into_prompt(
        self,
        mock_call_http,
        mock_load,
        mock_select_few_shot,
    ):
        mock_load.return_value = "test prompt"
        report = self._make_score_report()
        payload = build_spark_payload_from_rules(report, {})
        payload["judge_mode"] = "openai"
        payload["model"] = "gpt-5.4"
        payload["judge_source"] = "openai_api"
        mock_select_few_shot.return_value = [
            {
                "dimension_name": "09 工期目标保障与进度控制措施",
                "logic_skeleton": [
                    "[前置条件] 关键线路明确 + [技术/动作] 周纠偏闭环 + [量化指标类型] 节点达成率"
                ],
                "source_highlights": ["评委表扬关键线路控制"],
            }
        ]

        captured = {}

        def _fake_call(message):
            captured["message"] = message
            return True, payload, ""

        mock_call_http.side_effect = _fake_call

        with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test"}):
            result = run_spark_judge("test text", {}, "prompt", report)

        assert result["called_openai_api"] is True
        kwargs = mock_select_few_shot.call_args.kwargs
        assert kwargs["project_id"] is None
        assert "已采纳高分少样本逻辑骨架" in captured["message"]
        assert "09 工期目标保障与进度控制措施" in captured["message"]
        assert "评委表扬关键线路控制" in captured["message"]

    @patch("app.engine.llm_judge_spark.select_top_few_shot_prompt_examples")
    @patch("app.engine.llm_judge_spark.load_prompt")
    @patch("app.engine.llm_judge_spark._call_spark_http")
    def test_run_spark_judge_passes_project_id_to_few_shot_selector(
        self,
        mock_call_http,
        mock_load,
        mock_select_few_shot,
    ):
        mock_load.return_value = "test prompt"
        report = self._make_score_report()
        report.meta["project_id"] = "p-meta"
        payload = build_spark_payload_from_rules(report, {})
        payload["judge_mode"] = "openai"
        payload["model"] = "gpt-5.4"
        payload["judge_source"] = "openai_api"
        mock_call_http.return_value = (True, payload, "")
        mock_select_few_shot.return_value = []

        with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test"}):
            result = run_spark_judge("test text", {}, "prompt", report, project_id="p-explicit")

        assert result["called_openai_api"] is True
        kwargs = mock_select_few_shot.call_args.kwargs
        assert kwargs["project_id"] == "p-explicit"

    @patch("app.engine.llm_judge_spark.time.sleep")
    @patch("app.engine.llm_judge_spark.load_prompt")
    @patch("app.engine.llm_judge_spark._call_spark_http")
    def test_retries_retryable_llm_failures_before_success(
        self,
        mock_call_http,
        mock_load,
        mock_sleep,
    ):
        mock_load.return_value = "test prompt"
        report = self._make_score_report()
        payload = build_spark_payload_from_rules(report, {})
        payload["judge_mode"] = "openai"
        payload["model"] = "gpt-5.4"
        payload["judge_source"] = "openai_api"
        mock_call_http.side_effect = [
            (False, None, "timed out"),
            (False, None, "json_parse_failed"),
            (True, payload, ""),
        ]

        with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test"}):
            result = run_spark_judge("test text", {}, "prompt", report)

        assert result["called_spark_api"] is True
        assert result["retry_attempts"] == 3
        assert mock_call_http.call_count == 3
        assert mock_sleep.call_count == 2

    @patch("app.engine.llm_judge_spark.time.sleep")
    @patch("app.engine.llm_judge_spark.load_prompt")
    @patch("app.engine.llm_judge_spark._call_spark_http")
    def test_returns_interrupted_payload_when_retry_exhausted(
        self,
        mock_call_http,
        mock_load,
        mock_sleep,
    ):
        mock_load.return_value = "test prompt"
        report = self._make_score_report()
        mock_call_http.return_value = (False, None, "timed out")

        with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test"}):
            result = run_spark_judge("test text", {}, "prompt", report)

        assert result["called_spark_api"] is False
        assert result["processing_interrupted"] is True
        assert result["judge_mode"] == "openai_interrupted"
        assert result["error_code"] == "llm_processing_interrupted"
        assert result["retry_attempts"] == 3
        assert mock_call_http.call_count == 3
        assert mock_sleep.call_count == 2


class TestEdgeCases:
    """Tests for edge cases and boundary conditions."""

    def test_validate_empty_payload(self):
        """Empty payload should fail validation."""
        ok, err = validate_llm_judge_json({})
        assert ok is False
        assert len(err["details"]) > 0

    def test_post_process_empty_dim_scores(self):
        """Post-process should handle empty dimension_scores."""
        payload = {"dimension_scores": {}}
        rubric = {}
        result = post_process_llm_output(payload, rubric)
        assert result["dimension_scores"] == {}

    def test_post_process_drops_penalty_without_evidence(self):
        payload = {
            "dimension_scores": {
                "01": {
                    "score_0_10": 7.0,
                    "definition_points": ["已提供定义"],
                    "defects": ["问题存在"],
                    "improvements": ["建议补充参数"],
                    "evidence": [
                        {
                            "snippet": "第3页 施工部署明确。",
                            "quote": "施工部署明确。",
                            "anchor_label": "第3页｜正文片段",
                        }
                    ],
                }
            },
            "penalties": [
                {
                    "code": "P-01",
                    "message": "无证据扣分",
                    "deduct": 2.0,
                    "evidence": {"snippet": "", "quote": "", "anchor_label": ""},
                }
            ],
        }

        result = post_process_llm_output(payload, {})

        assert result["penalties"] == []

    def test_validate_with_extra_dimensions(self):
        """Extra dimensions beyond 16 should still pass if all required present."""
        # Create valid payload
        dim_scores = {}
        for i in range(1, 17):
            dim_id = f"{i:02d}"
            dim_scores[dim_id] = {
                "id": dim_id,
                "name": f"维度{i}",
                "module": "module",
                "score_0_10": 7.0,
                "max_score_0_10": 10,
                "weight_multiplier": 1.0,
                "definition_points": ["p"],
                "defects": ["d"],
                "improvements": ["i"],
                "evidence": [{"snippet": "e", "quote": "e", "anchor_label": "正文片段"}],
            }
        # Add extra dimension
        dim_scores["99"] = dim_scores["01"].copy()

        payload = {
            "judge_mode": "openai",
            "model": "gpt-5.4",
            "prompt_version": "v1",
            "weights": {
                "high_priority_dims": [],
                "high_priority_multiplier": 1.0,
                "normal_multiplier": 1.0,
            },
            "overall": {},
            "logic_lock": {},
            "dimension_scores": dim_scores,
            "penalties": [],
        }

        ok, err = validate_llm_judge_json(payload)
        assert ok is True
