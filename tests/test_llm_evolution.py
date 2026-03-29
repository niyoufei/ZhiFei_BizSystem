"""Tests for app/engine/llm_evolution and evolution LLM backends."""

from __future__ import annotations

import os
from unittest.mock import patch

from app.engine.llm_evolution import (
    AUTO_MULTI_PROVIDER_BACKEND,
    EVOLUTION_LLM_BACKEND_ENV,
    enhance_evolution_report_with_llm,
    get_evolution_llm_backend,
    get_evolution_llm_provider_chain,
    get_llm_backend_status,
)
from app.engine.llm_evolution_common import parse_api_key_pool
from app.engine.openai_compat import resolve_openai_model


class TestGetEvolutionLlmBackend:
    def test_default_is_rules(self):
        with patch.dict(os.environ, {}, clear=True):
            assert get_evolution_llm_backend() == "rules"

    def test_default_is_openai_when_key_present(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}, clear=True):
            assert get_evolution_llm_backend() == "openai"

    def test_default_is_gemini_when_only_gemini_key_present(self):
        with patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"}, clear=True):
            assert get_evolution_llm_backend() == "gemini"

    def test_env_spark_alias_maps_to_openai(self):
        with patch.dict(os.environ, {EVOLUTION_LLM_BACKEND_ENV: "spark"}):
            assert get_evolution_llm_backend() == "openai"

    def test_env_openai_gemini(self):
        with patch.dict(os.environ, {EVOLUTION_LLM_BACKEND_ENV: "openai"}):
            assert get_evolution_llm_backend() == "openai"
        with patch.dict(os.environ, {EVOLUTION_LLM_BACKEND_ENV: "gemini"}):
            assert get_evolution_llm_backend() == "gemini"

    def test_auto_provider_chain_prefers_openai_then_gemini(self):
        with patch.dict(
            os.environ,
            {
                EVOLUTION_LLM_BACKEND_ENV: AUTO_MULTI_PROVIDER_BACKEND,
                "OPENAI_API_KEY": "openai-key",
                "GEMINI_API_KEY": "gemini-key",
            },
            clear=True,
        ):
            assert get_evolution_llm_backend() == "openai"
            assert get_evolution_llm_provider_chain() == ["openai", "gemini"]

    def test_explicit_openai_uses_gemini_fallback_when_openai_unavailable(self):
        with patch.dict(
            os.environ,
            {
                EVOLUTION_LLM_BACKEND_ENV: "openai",
                "GEMINI_API_KEY": "gemini-key",
            },
            clear=True,
        ):
            assert get_evolution_llm_backend() == "gemini"
            assert get_evolution_llm_provider_chain() == ["gemini"]

    def test_explicit_openai_without_any_config_falls_back_to_rules(self):
        with patch.dict(os.environ, {EVOLUTION_LLM_BACKEND_ENV: "openai"}, clear=True):
            assert get_evolution_llm_backend() == "rules"
            assert get_evolution_llm_provider_chain() == []


class TestOpenAIModelAliases:
    def test_chatgpt_alias_maps_to_gpt_54(self):
        assert resolve_openai_model("ChatGPT5.4") == "gpt-5.4"
        assert resolve_openai_model("chatgpt-5") == "gpt-5.4"
        assert resolve_openai_model("gpt-5") == "gpt-5.4"


class TestApiKeyPoolParsing:
    def test_parse_api_key_pool_dedupes_and_keeps_order(self):
        keys = parse_api_key_pool("key-b", "key-a, key-b , key-c")
        assert keys == ["key-a", "key-b", "key-c"]


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

    def test_spark_alias_without_openai_credentials_returns_none(self):
        with patch.dict(os.environ, {EVOLUTION_LLM_BACKEND_ENV: "spark"}):
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

    def test_llm_backend_status_reports_legacy_spark_alias(self):
        with patch.dict(
            os.environ,
            {
                EVOLUTION_LLM_BACKEND_ENV: "spark",
                "SPARK_MODEL": "gpt-5.4",
                "OPENAI_API_KEY": "test-key",
            },
            clear=True,
        ):
            status = get_llm_backend_status()

        assert status["evolution_backend"] == "openai"
        assert status["requested_backend"] == "spark"
        assert status["backend_alias_applied"] is True
        assert status["auto_mode"] is False
        assert status["spark_configured"] is True
        assert status["legacy_spark_env_keys"] == ["SPARK_MODEL"]
        assert status["openai_configured"] is True
        assert status["openai_account_count"] == 1
        assert status["gemini_account_count"] == 0
        assert status["provider_chain"] == ["openai"]
        assert status["fallback_providers"] == []

    def test_llm_backend_status_reports_auto_chain_and_fallbacks(self):
        with patch.dict(
            os.environ,
            {
                EVOLUTION_LLM_BACKEND_ENV: AUTO_MULTI_PROVIDER_BACKEND,
                "OPENAI_API_KEYS": "openai-key,openai-key-2",
                "GEMINI_API_KEYS": "gemini-key,gemini-key-2",
            },
            clear=True,
        ):
            status = get_llm_backend_status()

        assert status["evolution_backend"] == "openai"
        assert status["requested_backend"] == AUTO_MULTI_PROVIDER_BACKEND
        assert status["auto_mode"] is True
        assert status["openai_account_count"] == 2
        assert status["gemini_account_count"] == 2
        assert status["provider_chain"] == ["openai", "gemini"]
        assert status["fallback_providers"] == ["gemini"]

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

    @patch("app.engine.llm_evolution._enhance_with_gemini")
    @patch("app.engine.llm_evolution._enhance_with_openai")
    def test_openai_falls_back_to_gemini_when_openai_returns_none(self, mock_openai, mock_gemini):
        mock_openai.side_effect = [None, None]
        mock_gemini.return_value = {
            "project_id": "p1",
            "high_score_logic": ["g1"],
            "writing_guidance": ["w1"],
            "sample_count": 1,
            "updated_at": "2020-01-01T00:00:00Z",
            "enhanced_by": "gemini",
        }
        with patch.dict(
            os.environ,
            {
                EVOLUTION_LLM_BACKEND_ENV: "openai",
                "OPENAI_API_KEY": "openai-key",
                "GEMINI_API_KEY": "gemini-key",
            },
            clear=True,
        ):
            report = {
                "project_id": "p1",
                "high_score_logic": ["a"],
                "writing_guidance": ["b"],
                "sample_count": 1,
                "updated_at": "2020-01-01T00:00:00Z",
            }
            out = enhance_evolution_report_with_llm("p1", report, [], "")

        assert out is not None
        assert out["enhanced_by"] == "gemini"
        assert out["enhancement_provider_chain"] == ["openai", "gemini"]
        assert out["enhancement_fallback_used"] is True
        assert out["enhancement_attempts"] == 3

    @patch("app.engine.llm_evolution._enhance_with_gemini")
    @patch("app.engine.llm_evolution._enhance_with_openai")
    def test_auto_mode_prefers_openai_when_primary_succeeds(self, mock_openai, mock_gemini):
        mock_openai.return_value = {
            "project_id": "p1",
            "high_score_logic": ["o1"],
            "writing_guidance": ["w1"],
            "sample_count": 1,
            "updated_at": "2020-01-01T00:00:00Z",
            "enhanced_by": "openai",
        }
        with patch.dict(
            os.environ,
            {
                EVOLUTION_LLM_BACKEND_ENV: AUTO_MULTI_PROVIDER_BACKEND,
                "OPENAI_API_KEY": "openai-key",
                "GEMINI_API_KEY": "gemini-key",
            },
            clear=True,
        ):
            report = {
                "project_id": "p1",
                "high_score_logic": ["a"],
                "writing_guidance": ["b"],
                "sample_count": 1,
                "updated_at": "2020-01-01T00:00:00Z",
            }
            out = enhance_evolution_report_with_llm("p1", report, [], "")

        assert out is not None
        assert out["enhanced_by"] == "openai"
        assert out["enhancement_provider_chain"] == ["openai", "gemini"]
        assert out["enhancement_fallback_used"] is False
        assert out["enhancement_attempts"] == 1
        mock_gemini.assert_not_called()


class TestProviderAccountPooling:
    def test_openai_provider_switches_to_second_account_when_first_fails(self):
        from app.engine import llm_evolution_openai as openai_module

        openai_module._OPENAI_KEY_FAILURES.clear()
        openai_module._OPENAI_KEY_CURSOR = 0
        report = {
            "project_id": "p1",
            "high_score_logic": ["a"],
            "writing_guidance": ["b"],
            "sample_count": 1,
            "updated_at": "2020-01-01T00:00:00Z",
        }
        with patch.dict(
            os.environ,
            {
                "OPENAI_API_KEYS": "key-1,key-2",
            },
            clear=True,
        ):
            with patch.object(
                openai_module,
                "_call_openai_http",
                side_effect=[
                    (False, None, "rate_limit"),
                    (
                        True,
                        {"high_score_logic": ["o1"], "writing_guidance": ["w1"]},
                        "",
                    ),
                ],
            ) as mock_call:
                out = openai_module.enhance_evolution_report_openai("p1", report, [], "")

        assert out is not None
        assert out["enhanced_by"] == "openai"
        assert mock_call.call_args_list[0].kwargs["api_key"] == "key-1"
        assert mock_call.call_args_list[1].kwargs["api_key"] == "key-2"

    def test_gemini_provider_switches_to_second_account_when_first_fails(self):
        from app.engine import llm_evolution_gemini as gemini_module

        gemini_module._GEMINI_KEY_FAILURES.clear()
        gemini_module._GEMINI_KEY_CURSOR = 0
        report = {
            "project_id": "p1",
            "high_score_logic": ["a"],
            "writing_guidance": ["b"],
            "sample_count": 1,
            "updated_at": "2020-01-01T00:00:00Z",
        }
        with patch.dict(
            os.environ,
            {
                "GEMINI_API_KEYS": "g-1,g-2",
            },
            clear=True,
        ):
            with patch.object(
                gemini_module,
                "_call_gemini_http",
                side_effect=[
                    (False, None, "quota"),
                    (
                        True,
                        {"high_score_logic": ["g1"], "writing_guidance": ["w1"]},
                        "",
                    ),
                ],
            ) as mock_call:
                out = gemini_module.enhance_evolution_report_gemini("p1", report, [], "")

        assert out is not None
        assert out["enhanced_by"] == "gemini"
        assert mock_call.call_args_list[0].kwargs["api_key"] == "g-1"
        assert mock_call.call_args_list[1].kwargs["api_key"] == "g-2"


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
