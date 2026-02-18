"""Tests for app/engine/evolution scoring_evolution and compilation_instructions."""

from __future__ import annotations

from app.engine.evolution import (
    _build_compilation_instructions,
    _build_scoring_evolution,
    _default_compilation_instructions,
    _empty_scoring_evolution,
)


class TestScoringEvolution:
    def test_empty(self):
        out = _empty_scoring_evolution()
        assert "dimension_multipliers" in out
        assert "rationale" in out
        assert "goal" in out
        assert out["dimension_multipliers"] == {}

    def test_build_high_low_delta(self):
        high_group = [
            {"our_dimensions": {"07": 8.0, "09": 7.0, "02": 5.0}},
            {"our_dimensions": {"07": 8.5, "09": 6.5, "02": 5.5}},
        ]
        low_group = [
            {"our_dimensions": {"07": 4.0, "09": 5.0, "02": 6.0}},
        ]
        out = _build_scoring_evolution(high_group, low_group)
        assert "07" in out["dimension_multipliers"]
        assert "09" in out["dimension_multipliers"]
        assert "02" in out["dimension_multipliers"]
        assert out["dimension_multipliers"]["07"] >= 1.0
        assert out["dimension_multipliers"]["02"] <= 1.0


class TestCompilationInstructions:
    def test_default_empty(self):
        out = _default_compilation_instructions([])
        assert "required_sections" in out
        assert "required_charts_images" in out
        assert "mandatory_elements" in out
        assert "forbidden_patterns" in out
        assert "guidance_items" in out

    def test_build_includes_guidance_and_summary(self):
        high = ["高分共性1"]
        guidance = ["编制建议1"]
        out = _build_compilation_instructions(high, guidance)
        assert out["high_score_summary"] == high
        assert "编制建议1" in out["guidance_items"]
