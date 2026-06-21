from __future__ import annotations

from app.engine.shigong_diagnostics import (
    build_optimization_checklist,
    decompose_high_score_sample,
    diagnose_shigong,
)
from app.engine.tender_profile import load_all_profiles

YUNKANG = "2026BFFGZ50127"  # 6 项考量
PROJECT_TERMS = ["医院", "骨科", "改造", "不停诊"]

# 强施组：针对性 + 硬要素 + 覆盖全部6项考量
GOOD = (
    "本工程为骨科医院局部改造，针对工程项目整体理解清晰。重点难点为不停诊改造，"
    "措施为分区施工、动线隔离。新技术采用BIM建模，新工艺为装配式隔墙。"
    "工期总日历天数120天，质量目标为一次验收合格。人材机配置：项目经理1名，"
    "劳动力高峰80人，塔吊2台，每日巡检并旁站验收。安全文明生产：扬尘监测每日3次，"
    "PM2.5阈值控制，安全员驻场，隐蔽验收闭环。"
)
# 弱施组：全是空泛套话、无硬要素、覆盖少
WEAK = (
    "我公司将严格按照规范确保工程质量，加强管理，认真组织，精心施工，"
    "全面落实各项要求，高度重视，确保工期，积极努力完成本工程。"
)


def _p():
    return load_all_profiles()[YUNKANG]


def test_good_beats_weak_on_three_axes_and_coverage():
    p = _p()
    g = diagnose_shigong(GOOD, p, project_terms=PROJECT_TERMS)
    w = diagnose_shigong(WEAK, p, project_terms=PROJECT_TERMS)
    assert g.axes.landing > w.axes.landing
    assert g.axes.specificity > w.axes.specificity
    assert g.axes.conciseness > w.axes.conciseness
    assert g.coverage_rate > w.coverage_rate


def test_good_covers_all_considerations():
    g = diagnose_shigong(GOOD, _p(), project_terms=PROJECT_TERMS)
    assert g.coverage_rate == 1.0
    assert g.hard_element_count >= 5
    assert g.generic_phrase_count == 0


def test_weak_checklist_prioritises_missing_then_specificity():
    p = _p()
    w = diagnose_shigong(WEAK, p, project_terms=PROJECT_TERMS)
    checklist = build_optimization_checklist(w, p)
    assert checklist, "弱施组应产出优化项"
    assert checklist[0].issue == "MISSING"
    assert checklist[0].priority == 1
    issues = [i.issue for i in checklist]
    assert "LOW_SPECIFICITY" in issues
    # 按预期提分降序
    gains = [i.expected_gain for i in checklist]
    assert gains == sorted(gains, reverse=True)


def test_good_checklist_is_shorter_than_weak():
    p = _p()
    g = diagnose_shigong(GOOD, p, project_terms=PROJECT_TERMS)
    w = diagnose_shigong(WEAK, p, project_terms=PROJECT_TERMS)
    assert len(build_optimization_checklist(g, p)) < len(build_optimization_checklist(w, p))


def test_decompose_high_score_sample_lists_strengths():
    dec = decompose_high_score_sample(GOOD, _p(), project_terms=PROJECT_TERMS)
    assert dec.strengths
    assert "拆解" in dec.summary
    assert any("针对性" in s or "落地" in s or "覆盖" in s for s in dec.strengths)


def test_diagnose_is_deterministic():
    p = _p()
    a = diagnose_shigong(GOOD, p, project_terms=PROJECT_TERMS)
    b = diagnose_shigong(GOOD, p, project_terms=PROJECT_TERMS)
    assert a.to_dict() == b.to_dict()
