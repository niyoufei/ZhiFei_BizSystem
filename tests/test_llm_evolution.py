"""Tests for app/engine/llm_evolution and evolution LLM backends."""

from __future__ import annotations

import json
import os
from unittest.mock import patch

from app.engine.llm_evolution import (
    EVOLUTION_LLM_BACKEND_ENV,
    enhance_evolution_report_with_llm,
    get_evolution_llm_backend,
)


class TestGetEvolutionLlmBackend:
    def test_default_is_rules(self):
        with patch.dict(os.environ, {}, clear=False):
            if EVOLUTION_LLM_BACKEND_ENV in os.environ:
                del os.environ[EVOLUTION_LLM_BACKEND_ENV]
            assert get_evolution_llm_backend() == "rules"

    def test_env_spark(self):
        with patch.dict(os.environ, {EVOLUTION_LLM_BACKEND_ENV: "spark"}):
            assert get_evolution_llm_backend() == "spark"

    def test_env_openai_gemini(self):
        with patch.dict(os.environ, {EVOLUTION_LLM_BACKEND_ENV: "openai"}):
            assert get_evolution_llm_backend() == "openai"
        with patch.dict(os.environ, {EVOLUTION_LLM_BACKEND_ENV: "gemini"}):
            assert get_evolution_llm_backend() == "gemini"


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


class TestLlmEvolutionOllamaModule:
    def test_enhance_ollama_without_model_returns_none(self):
        from app.engine.llm_evolution_ollama import (
            OLLAMA_MODEL_ENV,
            enhance_evolution_report_ollama,
        )

        report = {
            "project_id": "p1",
            "high_score_logic": ["a"],
            "writing_guidance": ["b"],
            "sample_count": 0,
            "updated_at": "2020-01-01T00:00:00Z",
        }
        with patch.dict(os.environ, {}, clear=False):
            save = os.environ.pop(OLLAMA_MODEL_ENV, None)
            try:
                with patch("app.engine.llm_evolution_ollama._call_ollama_http") as call:
                    out = enhance_evolution_report_ollama("p1", report, [], "")
                assert out is None
                call.assert_not_called()
            finally:
                if save is not None:
                    os.environ[OLLAMA_MODEL_ENV] = save

    def test_call_ollama_http_parses_mock_chat_response(self):
        from app.engine.llm_evolution_ollama import _call_ollama_http

        captured = {}

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self):
                return json.dumps(
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "high_score_logic": ["h1"],
                                    "writing_guidance": ["w1"],
                                },
                                ensure_ascii=False,
                            )
                        }
                    },
                    ensure_ascii=False,
                ).encode("utf-8")

        def fake_urlopen(req, timeout):
            captured["url"] = req.full_url
            captured["timeout"] = timeout
            captured["body"] = json.loads(req.data.decode("utf-8"))
            return FakeResponse()

        with patch("urllib.request.urlopen", fake_urlopen):
            ok, parsed, err = _call_ollama_http(
                "prompt",
                model="qwen2.5",
                max_tokens=128,
                timeout=3,
                base_url="http://127.0.0.1:11434/",
            )

        assert ok is True
        assert err == ""
        assert parsed == {"high_score_logic": ["h1"], "writing_guidance": ["w1"]}
        assert captured["url"] == "http://127.0.0.1:11434/api/chat"
        assert captured["timeout"] == 3
        assert captured["body"]["model"] == "qwen2.5"
        assert captured["body"]["messages"] == [{"role": "user", "content": "prompt"}]
        assert captured["body"]["stream"] is False
        assert captured["body"]["options"]["num_predict"] == 128

    def test_enhance_ollama_returns_existing_report_shape_with_mock_call(self):
        from app.engine.llm_evolution_ollama import (
            OLLAMA_MODEL_ENV,
            enhance_evolution_report_ollama,
        )

        report = {
            "project_id": "p1",
            "high_score_logic": ["rule high"],
            "writing_guidance": ["rule guide"],
            "sample_count": 2,
            "updated_at": "2020-01-01T00:00:00Z",
        }
        parsed = {"high_score_logic": ["h1"], "writing_guidance": ["w1", "w2"]}
        with (
            patch.dict(os.environ, {OLLAMA_MODEL_ENV: "qwen2.5"}, clear=False),
            patch(
                "app.engine.llm_evolution_ollama._call_ollama_http", return_value=(True, parsed, "")
            ),
        ):
            out = enhance_evolution_report_ollama("p1", report, [], "context")

        assert out is not None
        assert out["project_id"] == "p1"
        assert out["high_score_logic"] == ["h1"]
        assert out["writing_guidance"] == ["w1", "w2"]
        assert out["sample_count"] == 2
        assert out["enhanced_by"] == "ollama"
        assert isinstance(out["updated_at"], str)
