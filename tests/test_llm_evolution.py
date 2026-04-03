"""Tests for app/engine/llm_evolution and evolution LLM backends."""

from __future__ import annotations

import os
import time
from unittest.mock import patch

import pytest

import app.engine.llm_runtime_state as llm_runtime_state
from app.engine.llm_evolution import (
    _PROVIDER_FAILURES,
    AUTO_MULTI_PROVIDER_BACKEND,
    EVOLUTION_LLM_BACKEND_ENV,
    enhance_evolution_report_with_llm,
    get_evolution_llm_backend,
    get_evolution_llm_provider_chain,
    get_llm_backend_status,
)
from app.engine.llm_evolution_common import build_evolution_prompt, parse_api_key_pool
from app.engine.llm_evolution_gemini import (
    _GEMINI_KEY_FAILURES,
    get_gemini_evolution_pool_quality,
)
from app.engine.llm_evolution_gemini import (
    _build_key_attempt_order as _build_gemini_key_attempt_order,
)
from app.engine.llm_evolution_gemini import (
    _mark_key_failure as _mark_gemini_key_failure,
)
from app.engine.llm_evolution_openai import (
    _OPENAI_KEY_FAILURES,
    get_openai_evolution_pool_quality,
)
from app.engine.llm_evolution_openai import (
    _build_key_attempt_order as _build_openai_key_attempt_order,
)
from app.engine.llm_evolution_openai import (
    _mark_key_failure as _mark_openai_key_failure,
)
from app.engine.openai_compat import resolve_openai_model


@pytest.fixture(autouse=True)
def _reset_provider_failures(tmp_path, monkeypatch):
    monkeypatch.setattr(
        llm_runtime_state,
        "LLM_RUNTIME_STATE_PATH",
        tmp_path / "llm_runtime_state.json",
    )
    _PROVIDER_FAILURES.clear()
    _OPENAI_KEY_FAILURES.clear()
    _GEMINI_KEY_FAILURES.clear()
    try:
        yield
    finally:
        _PROVIDER_FAILURES.clear()
        _OPENAI_KEY_FAILURES.clear()
        _GEMINI_KEY_FAILURES.clear()


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
        with patch.dict(
            os.environ,
            {EVOLUTION_LLM_BACKEND_ENV: "spark", "OPENAI_API_KEY": "test-key"},
            clear=True,
        ):
            assert get_evolution_llm_backend() == "openai"

    def test_env_openai_gemini(self):
        with patch.dict(
            os.environ,
            {EVOLUTION_LLM_BACKEND_ENV: "openai", "OPENAI_API_KEY": "test-key"},
            clear=True,
        ):
            assert get_evolution_llm_backend() == "openai"
        with patch.dict(
            os.environ,
            {EVOLUTION_LLM_BACKEND_ENV: "gemini", "GEMINI_API_KEY": "test-key"},
            clear=True,
        ):
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
                EVOLUTION_LLM_BACKEND_ENV: AUTO_MULTI_PROVIDER_BACKEND,
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

    def test_build_evolution_prompt_includes_adopted_few_shot_examples(self):
        report = {
            "high_score_logic": ["规则逻辑1"],
            "writing_guidance": ["规则指导1"],
            "sample_count": 2,
            "few_shot_examples": [
                {
                    "dimension_name": "09 工期目标保障与进度控制措施",
                    "logic_skeleton": [
                        "[前置条件] 关键线路明确 + [技术/动作] 周纠偏闭环 + [量化指标类型] 节点达成率"
                    ],
                    "source_highlights": ["评委表扬关键线路控制", "周纠偏责任到岗"],
                }
            ],
        }

        prompt = build_evolution_prompt(
            report,
            [{"final_score": 92.0}, {"final_score": 95.0}],
            "项目背景摘要",
        )

        assert "已采纳高分少样本示例" in prompt
        assert "09 工期目标保障与进度控制措施" in prompt
        assert "评委表扬关键线路控制" in prompt


class TestLlmRuntimeStateCompaction:
    def test_runtime_state_uses_storage_helpers(self, monkeypatch, tmp_path):
        runtime_state_path = tmp_path / "llm_runtime_state.json"
        load_calls: dict[str, object] = {}
        save_calls: dict[str, object] = {}

        def fake_load_json(path, default):
            load_calls["path"] = path
            load_calls["default"] = default
            return {
                "provider_failures": {"openai": 1712102400.0},
                "provider_quality_degraded": {},
                "provider_review_stats": {},
                "account_failures": {},
                "account_request_stats": {},
            }

        def fake_save_json(path, payload):
            save_calls["path"] = path
            save_calls["payload"] = payload

        monkeypatch.setattr(llm_runtime_state, "LLM_RUNTIME_STATE_PATH", runtime_state_path)
        monkeypatch.setattr(llm_runtime_state, "load_json", fake_load_json)
        monkeypatch.setattr(llm_runtime_state, "save_json", fake_save_json)

        state = llm_runtime_state._load_state_unlocked()
        llm_runtime_state._save_state_unlocked(state)

        assert load_calls["path"] == runtime_state_path
        assert isinstance(load_calls["default"], dict)
        assert state["provider_failures"]["openai"] == pytest.approx(1712102400.0, abs=1e-6)
        assert save_calls["path"] == runtime_state_path
        assert save_calls["payload"]["provider_failures"]["openai"] == pytest.approx(
            1712102400.0, abs=1e-6
        )

    def test_provider_review_stats_use_bounded_history_window(self):
        for _ in range(llm_runtime_state.LLM_RUNTIME_STATE_MAX_HISTORY + 7):
            llm_runtime_state.record_provider_review_outcome("openai", "confirmed", time.time())

        stats = llm_runtime_state.get_provider_review_stats()["openai"]

        assert stats["confirmed_count"] <= llm_runtime_state.LLM_RUNTIME_STATE_MAX_HISTORY
        assert stats["last_status"] == "confirmed"

    def test_account_request_stats_use_bounded_history_window(self):
        for _ in range(llm_runtime_state.LLM_RUNTIME_STATE_MAX_HISTORY + 9):
            llm_runtime_state.record_account_request_outcome(
                "openai", "openai-key-1", "success", time.time()
            )

        stats = llm_runtime_state.get_account_request_stats("openai", ["openai-key-1"])[
            "openai-key-1"
        ]

        assert (
            stats["success_count"] + stats["failure_count"]
            <= llm_runtime_state.LLM_RUNTIME_STATE_MAX_HISTORY
        )
        assert stats["last_status"] == "success"


class TestEnhanceEvolutionReportWithLlm:
    def test_openai_key_attempt_order_prefers_higher_quality_ready_key(self):
        llm_runtime_state.record_account_request_outcome(
            "openai", "openai-key-1", "success", time.time()
        )
        llm_runtime_state.record_account_request_outcome(
            "openai", "openai-key-1", "success", time.time()
        )
        llm_runtime_state.record_account_request_outcome(
            "openai", "openai-key-2", "failure", time.time()
        )
        with patch.dict(
            os.environ,
            {"OPENAI_API_KEYS": "openai-key-1,openai-key-2,openai-key-3"},
            clear=True,
        ):
            assert _build_openai_key_attempt_order(
                ["openai-key-1", "openai-key-2", "openai-key-3"]
            ) == ["openai-key-1", "openai-key-3", "openai-key-2"]
            quality = get_openai_evolution_pool_quality()

        assert quality["rated_accounts"] == 2.0
        assert quality["sufficiently_rated_accounts"] == 0.0
        assert quality["best_quality_score"] == 50.0
        assert quality["worst_quality_score"] == 50.0

    def test_openai_key_attempt_order_deprioritizes_low_quality_key_when_better_ready_key_exists(
        self,
    ):
        for _ in range(3):
            llm_runtime_state.record_account_request_outcome(
                "openai", "openai-key-1", "failure", time.time()
            )
        for _ in range(3):
            llm_runtime_state.record_account_request_outcome(
                "openai", "openai-key-2", "success", time.time()
            )
        with patch.dict(
            os.environ,
            {"OPENAI_API_KEYS": "openai-key-1,openai-key-2"},
            clear=True,
        ):
            assert _build_openai_key_attempt_order(["openai-key-1", "openai-key-2"]) == [
                "openai-key-2",
                "openai-key-1",
            ]
            quality = get_openai_evolution_pool_quality()

        assert quality["low_quality_accounts"] == 1.0

    def test_gemini_key_attempt_order_prefers_higher_quality_ready_key(self):
        llm_runtime_state.record_account_request_outcome(
            "gemini", "gemini-key-1", "failure", time.time()
        )
        llm_runtime_state.record_account_request_outcome(
            "gemini", "gemini-key-2", "success", time.time()
        )
        llm_runtime_state.record_account_request_outcome(
            "gemini", "gemini-key-2", "success", time.time()
        )
        with patch.dict(
            os.environ,
            {"GEMINI_API_KEYS": "gemini-key-1,gemini-key-2"},
            clear=True,
        ):
            assert _build_gemini_key_attempt_order(["gemini-key-1", "gemini-key-2"]) == [
                "gemini-key-2",
                "gemini-key-1",
            ]
            quality = get_gemini_evolution_pool_quality()

        assert quality["rated_accounts"] == 2.0
        assert quality["sufficiently_rated_accounts"] == 0.0
        assert quality["average_quality_score"] == 50.0

    def test_openai_pool_quality_stays_neutral_until_accounts_have_enough_history(self):
        llm_runtime_state.record_account_request_outcome(
            "openai", "openai-key-1", "failure", time.time()
        )
        llm_runtime_state.record_account_request_outcome(
            "openai", "openai-key-2", "failure", time.time()
        )

        with patch.dict(
            os.environ,
            {"OPENAI_API_KEYS": "openai-key-1,openai-key-2"},
            clear=True,
        ):
            quality = get_openai_evolution_pool_quality()

        assert quality["rated_accounts"] == 2.0
        assert quality["sufficiently_rated_accounts"] == 0.0
        assert quality["average_quality_score"] == 50.0
        assert quality["low_quality_accounts"] == 0.0

    def test_gemini_key_attempt_order_deprioritizes_low_quality_key_when_better_ready_key_exists(
        self,
    ):
        for _ in range(3):
            llm_runtime_state.record_account_request_outcome(
                "gemini", "gemini-key-1", "failure", time.time()
            )
        for _ in range(3):
            llm_runtime_state.record_account_request_outcome(
                "gemini", "gemini-key-2", "success", time.time()
            )
        with patch.dict(
            os.environ,
            {"GEMINI_API_KEYS": "gemini-key-1,gemini-key-2"},
            clear=True,
        ):
            assert _build_gemini_key_attempt_order(["gemini-key-1", "gemini-key-2"]) == [
                "gemini-key-2",
                "gemini-key-1",
            ]
            quality = get_gemini_evolution_pool_quality()

        assert quality["low_quality_accounts"] == 1.0

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
        assert status["openai_pool_health"] == {
            "total_accounts": 1,
            "healthy_accounts": 1,
            "cooling_accounts": 0,
        }
        assert status["gemini_account_count"] == 0
        assert status["gemini_pool_health"] == {}
        assert status["provider_health"] == {"openai": "healthy"}
        assert status["primary_provider_reason"] == "requested_openai_healthy"
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
        assert status["openai_pool_health"] == {
            "total_accounts": 2,
            "healthy_accounts": 2,
            "cooling_accounts": 0,
        }
        assert status["openai_pool_quality"] == {
            "total_accounts": 2.0,
            "rated_accounts": 0.0,
            "sufficiently_rated_accounts": 0.0,
            "average_quality_score": 50.0,
            "best_quality_score": 50.0,
            "worst_quality_score": 50.0,
            "low_quality_accounts": 0.0,
        }
        assert status["gemini_pool_health"] == {
            "total_accounts": 2,
            "healthy_accounts": 2,
            "cooling_accounts": 0,
        }
        assert status["gemini_pool_quality"] == {
            "total_accounts": 2.0,
            "rated_accounts": 0.0,
            "sufficiently_rated_accounts": 0.0,
            "average_quality_score": 50.0,
            "best_quality_score": 50.0,
            "worst_quality_score": 50.0,
            "low_quality_accounts": 0.0,
        }
        assert status["provider_health"] == {"openai": "healthy", "gemini": "healthy"}
        assert status["primary_provider_reason"] == "default_openai_primary"
        assert status["provider_chain"] == ["openai", "gemini"]
        assert status["fallback_providers"] == ["gemini"]

    def test_auto_provider_chain_promotes_gemini_when_openai_is_cooling_down(self):
        _PROVIDER_FAILURES.clear()
        _PROVIDER_FAILURES["openai"] = time.time()
        llm_runtime_state.set_provider_failure("openai", _PROVIDER_FAILURES["openai"])
        try:
            with patch.dict(
                os.environ,
                {
                    EVOLUTION_LLM_BACKEND_ENV: AUTO_MULTI_PROVIDER_BACKEND,
                    "OPENAI_API_KEY": "openai-key",
                    "GEMINI_API_KEY": "gemini-key",
                },
                clear=True,
            ):
                assert get_evolution_llm_provider_chain() == ["gemini", "openai"]
                status = get_llm_backend_status()
        finally:
            _PROVIDER_FAILURES.clear()

        assert status["evolution_backend"] == "gemini"
        assert status["openai_pool_health"] == {
            "total_accounts": 1,
            "healthy_accounts": 1,
            "cooling_accounts": 0,
        }
        assert status["gemini_pool_health"] == {
            "total_accounts": 1,
            "healthy_accounts": 1,
            "cooling_accounts": 0,
        }
        assert status["provider_health"]["openai"] == "cooldown"
        assert status["provider_health"]["gemini"] == "healthy"
        assert status["primary_provider_reason"] == "openai_cooldown_promoted_gemini"

    def test_auto_provider_chain_promotes_gemini_when_openai_pool_is_thin(self):
        with patch.dict(
            os.environ,
            {
                EVOLUTION_LLM_BACKEND_ENV: AUTO_MULTI_PROVIDER_BACKEND,
                "OPENAI_API_KEYS": "openai-key-1,openai-key-2,openai-key-3,openai-key-4",
                "GEMINI_API_KEYS": "gemini-key-1,gemini-key-2",
            },
            clear=True,
        ):
            _mark_openai_key_failure("openai-key-1")
            _mark_openai_key_failure("openai-key-2")
            _mark_openai_key_failure("openai-key-3")
            _OPENAI_KEY_FAILURES.clear()
            assert get_evolution_llm_provider_chain() == ["gemini", "openai"]
            status = get_llm_backend_status()

        assert status["evolution_backend"] == "gemini"
        assert status["openai_pool_health"] == {
            "total_accounts": 4,
            "healthy_accounts": 1,
            "cooling_accounts": 3,
        }
        assert status["gemini_pool_health"] == {
            "total_accounts": 2,
            "healthy_accounts": 2,
            "cooling_accounts": 0,
        }
        assert status["provider_health"] == {"openai": "healthy", "gemini": "healthy"}
        assert status["primary_provider_reason"] == "openai_thin_pool_promoted_gemini"

    def test_auto_provider_chain_promotes_gemini_when_openai_quality_is_degraded(self):
        with patch.dict(
            os.environ,
            {
                EVOLUTION_LLM_BACKEND_ENV: AUTO_MULTI_PROVIDER_BACKEND,
                "OPENAI_API_KEY": "openai-key",
                "GEMINI_API_KEY": "gemini-key",
            },
            clear=True,
        ):
            llm_runtime_state.set_provider_quality_degraded("openai", time.time())
            assert get_evolution_llm_provider_chain() == ["gemini", "openai"]
            status = get_llm_backend_status()

        assert status["provider_quality"] == {"openai": "degraded", "gemini": "stable"}
        assert status["primary_provider_reason"] == "openai_quality_degraded_promoted_gemini"

    def test_auto_provider_chain_promotes_gemini_when_openai_review_history_regresses(self):
        llm_runtime_state.record_provider_review_outcome("openai", "diverged", time.time())
        llm_runtime_state.record_provider_review_outcome("openai", "diverged", time.time())
        llm_runtime_state.record_provider_review_outcome("gemini", "confirmed", time.time())
        with patch.dict(
            os.environ,
            {
                EVOLUTION_LLM_BACKEND_ENV: AUTO_MULTI_PROVIDER_BACKEND,
                "OPENAI_API_KEY": "openai-key",
                "GEMINI_API_KEY": "gemini-key",
            },
            clear=True,
        ):
            assert get_evolution_llm_provider_chain() == ["gemini", "openai"]
            status = get_llm_backend_status()

        assert status["provider_quality"] == {"openai": "degraded", "gemini": "stable"}
        assert status["provider_review_stats"]["openai"]["diverged_count"] == 2
        assert status["primary_provider_reason"] == "openai_quality_degraded_promoted_gemini"

    def test_auto_provider_chain_promotes_gemini_when_openai_quality_score_is_lower(self):
        llm_runtime_state.record_provider_review_outcome("openai", "confirmed", time.time())
        llm_runtime_state.record_provider_review_outcome("openai", "unavailable", time.time())
        llm_runtime_state.record_provider_review_outcome("openai", "unavailable", time.time())
        llm_runtime_state.record_provider_review_outcome("openai", "unavailable", time.time())
        llm_runtime_state.record_provider_review_outcome("gemini", "confirmed", time.time())
        llm_runtime_state.record_provider_review_outcome("gemini", "confirmed", time.time())
        llm_runtime_state.record_provider_review_outcome("gemini", "confirmed", time.time())
        with patch.dict(
            os.environ,
            {
                EVOLUTION_LLM_BACKEND_ENV: AUTO_MULTI_PROVIDER_BACKEND,
                "OPENAI_API_KEY": "openai-key",
                "GEMINI_API_KEY": "gemini-key",
            },
            clear=True,
        ):
            assert get_evolution_llm_provider_chain() == ["gemini", "openai"]
            status = get_llm_backend_status()

        assert (
            status["provider_quality_score"]["gemini"] > status["provider_quality_score"]["openai"]
        )
        assert status["primary_provider_reason"] == "openai_low_quality_score_promoted_gemini"

    def test_provider_cooldown_survives_runtime_state_reload(self):
        failed_at = time.time()
        llm_runtime_state.set_provider_failure("openai", failed_at)
        _PROVIDER_FAILURES.clear()
        with patch.dict(
            os.environ,
            {
                EVOLUTION_LLM_BACKEND_ENV: AUTO_MULTI_PROVIDER_BACKEND,
                "OPENAI_API_KEY": "openai-key",
                "GEMINI_API_KEY": "gemini-key",
            },
            clear=True,
        ):
            assert get_evolution_llm_provider_chain() == ["gemini", "openai"]
            status = get_llm_backend_status()
        assert status["provider_health"]["openai"] == "cooldown"

    def test_account_cooldown_survives_runtime_state_reload_without_storing_raw_key(self):
        with patch.dict(
            os.environ,
            {
                "OPENAI_API_KEYS": "openai-key-1,openai-key-2",
            },
            clear=True,
        ):
            _mark_openai_key_failure("openai-key-1")
            _OPENAI_KEY_FAILURES.clear()
            status = get_llm_backend_status()
        assert status["openai_pool_health"] == {
            "total_accounts": 2,
            "healthy_accounts": 1,
            "cooling_accounts": 1,
        }
        payload = llm_runtime_state.LLM_RUNTIME_STATE_PATH.read_text(encoding="utf-8")
        assert "openai-key-1" not in payload

    def test_gemini_account_cooldown_survives_runtime_state_reload(self):
        with patch.dict(
            os.environ,
            {
                "GEMINI_API_KEYS": "gemini-key-1,gemini-key-2",
            },
            clear=True,
        ):
            _mark_gemini_key_failure("gemini-key-1")
            _GEMINI_KEY_FAILURES.clear()
            status = get_llm_backend_status()
        assert status["gemini_pool_health"] == {
            "total_accounts": 2,
            "healthy_accounts": 1,
            "cooling_accounts": 1,
        }

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
        assert out["enhancement_review_status"] == "fallback_only"
        assert out["enhancement_review_provider"] is None

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
        mock_gemini.return_value = {
            "project_id": "p1",
            "high_score_logic": ["o1"],
            "writing_guidance": ["w1"],
            "sample_count": 1,
            "updated_at": "2020-01-01T00:00:00Z",
            "enhanced_by": "gemini",
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
        assert out["enhancement_applied"] is True
        assert out["enhancement_governed"] is False
        assert out["enhancement_review_provider"] == "gemini"
        assert out["enhancement_review_status"] == "confirmed"
        assert out["enhancement_review_similarity"] == 1.0
        assert out["enhancement_review_notes"]
        mock_gemini.assert_called_once()

    @patch("app.engine.llm_evolution._enhance_with_gemini")
    @patch("app.engine.llm_evolution._enhance_with_openai")
    def test_review_marks_diverged_when_secondary_output_differs(self, mock_openai, mock_gemini):
        mock_openai.return_value = {
            "project_id": "p1",
            "high_score_logic": ["安全文明施工闭环", "危险工程专项方案"],
            "writing_guidance": ["补强验收节点和责任分工"],
            "sample_count": 1,
            "updated_at": "2020-01-01T00:00:00Z",
        }
        mock_gemini.return_value = {
            "project_id": "p1",
            "high_score_logic": ["质量通病治理", "信息化管理平台"],
            "writing_guidance": ["补强 BIM 协同和智慧工地内容"],
            "sample_count": 1,
            "updated_at": "2020-01-01T00:00:00Z",
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
            out = enhance_evolution_report_with_llm(
                "p1",
                {
                    "project_id": "p1",
                    "high_score_logic": ["a"],
                    "writing_guidance": ["b"],
                    "sample_count": 1,
                    "updated_at": "2020-01-01T00:00:00Z",
                },
                [],
                "",
            )

        assert out is not None
        assert out["enhanced_by"] == "openai"
        assert out["enhancement_review_provider"] == "gemini"
        assert out["enhancement_review_status"] == "diverged"
        assert (out["enhancement_review_similarity"] or 0) < 0.35
        assert out["enhancement_applied"] is False
        assert out["enhancement_governed"] is True
        assert out["high_score_logic"] == ["a"]
        assert out["writing_guidance"] == ["b"]
        assert out["enhancement_governance_notes"]
        status = get_llm_backend_status()
        assert status["provider_quality"]["openai"] == "degraded"
        assert status["provider_review_stats"]["openai"]["diverged_count"] >= 1
        assert status["provider_review_stats"]["openai"]["last_status"] == "diverged"

    @patch("app.engine.llm_evolution._enhance_with_gemini")
    @patch("app.engine.llm_evolution._enhance_with_openai")
    def test_confirmed_review_clears_provider_quality_degradation(self, mock_openai, mock_gemini):
        llm_runtime_state.set_provider_quality_degraded("openai", time.time())
        mock_openai.return_value = {
            "project_id": "p1",
            "high_score_logic": ["质量节点闭环"],
            "writing_guidance": ["补强验收责任分工"],
            "sample_count": 1,
            "updated_at": "2020-01-01T00:00:00Z",
        }
        mock_gemini.return_value = {
            "project_id": "p1",
            "high_score_logic": ["质量节点闭环"],
            "writing_guidance": ["补强验收责任分工"],
            "sample_count": 1,
            "updated_at": "2020-01-01T00:00:00Z",
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
            out = enhance_evolution_report_with_llm(
                "p1",
                {
                    "project_id": "p1",
                    "high_score_logic": ["a"],
                    "writing_guidance": ["b"],
                    "sample_count": 1,
                    "updated_at": "2020-01-01T00:00:00Z",
                },
                [],
                "",
            )
            status = get_llm_backend_status()

        assert out is not None
        assert out["enhancement_review_status"] == "confirmed"
        assert status["provider_quality"]["openai"] == "stable"
        assert status["provider_review_stats"]["openai"]["confirmed_count"] >= 1
        assert status["provider_review_stats"]["openai"]["last_status"] == "confirmed"


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
