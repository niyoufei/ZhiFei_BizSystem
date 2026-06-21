from __future__ import annotations

import pytest

from app.engine.shigong_analyzer import AnalyzerError, analyze_shigong, analyze_to_markdown

YUNKANG = "2026BFFGZ50127"
SAMPLE = (
    "本工程为骨科医院局部改造，针对工程项目整体理解清晰。重点难点为不停诊改造，措施为分区施工。"
    "新技术采用BIM，新工艺为装配式。确保工期120天，质量目标一次验收合格。人材机配置：项目经理1名，"
    "劳动力80人，塔吊2台，每日旁站验收。安全文明生产：扬尘监测每日3次，安全员驻场。"
) * 2
TERMS = ["医院", "骨科", "改造", "不停诊"]


def test_analyze_returns_full_report():
    r = analyze_shigong(SAMPLE, YUNKANG, project_terms=TERMS)
    assert r.profile.tender_id == YUNKANG
    assert 0.0 <= r.internal_composite <= 100.0
    assert r.prediction.band is not None
    assert r.leverage.leverage_level == "decisive"
    assert r.diagnosis.coverage_rate > 0


def test_markdown_has_key_sections():
    md = analyze_to_markdown(SAMPLE, YUNKANG, project_terms=TERMS)
    for header in ("# 施组分析报告", "## 预测", "## 硬红线预检", "## 三轴诊断", "## ROI 优化清单"):
        assert header in md


def test_field_scores_attach_target_and_percentile():
    field = [4.44, 4.30, 3.51, 3.66, 4.38]
    r = analyze_shigong(SAMPLE, YUNKANG, project_terms=TERMS, field_scores=field)
    assert r.target is not None and r.target.target_f == 4.44
    assert r.prediction.percentile_in_field is not None


def test_disqualify_shows_in_markdown():
    md = analyze_to_markdown(SAMPLE, YUNKANG, project_terms=TERMS, shigong_count=2)
    assert "判废" in md


def test_unknown_tender_raises():
    with pytest.raises(AnalyzerError):
        analyze_shigong(SAMPLE, "NO_SUCH_TENDER")


def test_deterministic():
    a = analyze_to_markdown(SAMPLE, YUNKANG, project_terms=TERMS)
    b = analyze_to_markdown(SAMPLE, YUNKANG, project_terms=TERMS)
    assert a == b
