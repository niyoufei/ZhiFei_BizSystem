"""Tests for app/engine/llm_evolution and evolution LLM backends."""

from __future__ import annotations

import os
from unittest.mock import patch

from app.engine.llm_evolution import (
    EVOLUTION_LLM_BACKEND_ENV,
    enhance_evolution_report_with_llm,
    get_evolution_llm_backend,
)
from app.engine.openai_compat import resolve_openai_model


class TestGetEvolutionLlmBackend:
    def test_default_is_rules(self):
        with patch.dict(os.environ, {}, clear=True):
            assert get_evolution_llm_backend() == "rules"

    def test_default_is_openai_when_key_present(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}, clear=True):
            assert get_evolution_llm_backend() == "openai"

    def test_env_spark(self):
        with patch.dict(os.environ, {EVOLUTION_LLM_BACKEND_ENV: "spark"}):
            assert get_evolution_llm_backend() == "spark"

    def test_env_openai_gemini(self):
        with patch.dict(os.environ, {EVOLUTION_LLM_BACKEND_ENV: "openai"}):
            assert get_evolution_llm_backend() == "openai"
        with patch.dict(os.environ, {EVOLUTION_LLM_BACKEND_ENV: "gemini"}):
            assert get_evolution_llm_backend() == "gemini"


class TestOpenAIModelAliases:
    def test_chatgpt_alias_maps_to_gpt_54(self):
        assert resolve_openai_model("ChatGPT5.4") == "gpt-5.4"
        assert resolve_openai_model("chatgpt-5") == "gpt-5.4"
        assert resolve_openai_model("gpt-5") == "gpt-5.4"


class TestEnhanceEvolutionReportWithLlm:
    def test_rules_backend_returns_none(self):
        with patch.dict(os.environ, {EVOLUTION_LLM_BACKEND_ENV: "rules"}):
            report = {
                "project_id": "p1",
                "high_score_logic": ["a"],
                "writing_guidance": ["b"],
                "sample_count": 0,
                "updated_at": "2020-01-01T00:00:00Z",
            }
            out = enhance_evolution_report_with_llm("p1", report, [], "")
            assert out is None

    def test_spark_without_credentials_returns_none(self):
        with patch.dict(os.environ, {EVOLUTION_LLM_BACKEND_ENV: "spark"}):
            save = os.environ.pop("SPARK_APIPASSWORD", None)
            try:
                report = {
                    "project_id": "p1",
                    "high_score_logic": ["a"],
                    "writing_guidance": ["b"],
                    "sample_count": 0,
                    "updated_at": "2020-01-01T00:00:00Z",
                }
                out = enhance_evolution_report_with_llm("p1", report, [], "")
                assert out is None
            finally:
                if save is not None:
                    os.environ["SPARK_APIPASSWORD"] = save

    def test_openai_stub_returns_none(self):
        with patch.dict(os.environ, {EVOLUTION_LLM_BACKEND_ENV: "openai"}):
            save = os.environ.pop("OPENAI_API_KEY", None)
            try:
                report = {
                    "project_id": "p1",
                    "high_score_logic": ["a"],
                    "writing_guidance": ["b"],
                    "sample_count": 0,
                    "updated_at": "2020-01-01T00:00:00Z",
                }
                out = enhance_evolution_report_with_llm("p1", report, [], "")
                assert out is None
            finally:
                if save is not None:
                    os.environ["OPENAI_API_KEY"] = save

    def test_gemini_stub_returns_none(self):
        with patch.dict(os.environ, {EVOLUTION_LLM_BACKEND_ENV: "gemini"}):
            save = os.environ.pop("GEMINI_API_KEY", None)
            try:
                report = {
                    "project_id": "p1",
                    "high_score_logic": ["a"],
                    "writing_guidance": ["b"],
                    "sample_count": 0,
                    "updated_at": "2020-01-01T00:00:00Z",
                }
                out = enhance_evolution_report_with_llm("p1", report, [], "")
                assert out is None
            finally:
                if save is not None:
                    os.environ["GEMINI_API_KEY"] = save


class TestLlmEvolutionSparkModule:
    def test_parse_evolution_response(self):
        from app.engine.llm_evolution_common import parse_evolution_response

        assert parse_evolution_response({}) is None
        assert parse_evolution_response({"high_score_logic": [], "writing_guidance": ["x"]}) is None
        out = parse_evolution_response(
            {"high_score_logic": ["h1"], "writing_guidance": ["w1", "w2"]}
        )
        assert out is not None
        assert out["high_score_logic"] == ["h1"]
        assert out["writing_guidance"] == ["w1", "w2"]

    def test_enhance_evolution_report_spark_no_credentials_returns_none(self):
        from app.engine.llm_evolution_spark import enhance_evolution_report_spark

        report = {
            "project_id": "p1",
            "high_score_logic": ["a"],
            "writing_guidance": ["b"],
            "sample_count": 0,
            "updated_at": "2020-01-01T00:00:00Z",
        }
        with patch("app.engine.llm_evolution_spark._get_spark_bearer_token", return_value=None):
            out = enhance_evolution_report_spark("p1", report, [], "")
        assert out is None
