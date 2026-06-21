from __future__ import annotations

from app.engine.compilation_advisor import (
    build_gap_explanation_prompt,
    build_rewrite_prompt,
    explain_gap,
    get_compilation_llm_backend,
    suggest_rewrite,
)
from app.engine.shigong_diagnostics import diagnose_shigong
from app.engine.tender_profile import load_all_profiles

YUNKANG = "2026BFFGZ50127"
SAMPLE = "本工程为骨科医院局部改造，针对工程项目整体理解清晰，质量目标合格，工期可控。"


def _p():
    return load_all_profiles()[YUNKANG]


def _diag():
    return diagnose_shigong(SAMPLE, _p(), project_terms=["医院", "骨科"])


def test_backend_defaults_to_rules():
    assert get_compilation_llm_backend() == "rules"


def test_explain_gap_underestimate_rules():
    s = explain_gap(_diag(), predicted_f=3.9, real_f=4.44, profile=_p())
    assert "低估" in s.text
    assert s.source == "rules"
    assert s.affects_score is False and s.preview_only is True and s.no_write is True


def test_explain_gap_overestimate_rules():
    s = explain_gap(_diag(), predicted_f=4.2, real_f=3.5, profile=_p())
    assert "高估" in s.text


def test_explain_gap_uses_injected_llm():
    s = explain_gap(
        _diag(), predicted_f=3.9, real_f=4.44, profile=_p(), llm=lambda p: "LLM写的解释"
    )
    assert s.text == "LLM写的解释"
    assert s.source == "llm:injected"
    assert s.affects_score is False  # LLM 路径同样永不影响评分


def test_suggest_rewrite_rules_injects_terms_and_placeholders():
    s = suggest_rewrite(
        "严格按照规范确保工程质量，加强管理。",
        _p(),
        project_terms=["骨科医院"],
    )
    assert "骨科医院" in s.text
    assert "验收" in s.text and ("责任岗位" in s.text or "频次" in s.text)
    assert "严格按照" in s.text and "确保" in s.text  # 指出应删的空泛
    assert s.source == "rules"
    assert s.affects_score is False and s.preview_only is True


def test_suggest_rewrite_uses_injected_llm():
    s = suggest_rewrite("确保安全", _p(), project_terms=["医院"], llm=lambda p: "改写结果")
    assert s.text == "改写结果" and s.source == "llm:injected"
    assert s.affects_score is False


def test_prompts_are_nonempty_and_contextual():
    gp = build_gap_explanation_prompt(_diag(), 3.9, 4.44, _p())
    assert "真实青天F分" in gp and "3.9" in gp
    rp = build_rewrite_prompt("确保质量", _p(), project_terms=["骨科医院"])
    assert "骨科医院" in rp and "确保质量" in rp
