from __future__ import annotations

from app.engine.tender_preflight import check_tender_hard_lines
from app.engine.tender_profile import load_all_profiles

YUNKANG = "2026BFFGZ50127"  # require_site_plan=False, 有 format_hints
FEIDONG = "2026ADDGZ50033"  # require_site_plan=True

# 一段足够长（>200字）的施组正文，避免误触 NOT_PROVIDED
LONG = (
    "本工程为医院局部改造项目，施工组织设计涵盖工程概况、主要施工方法、拟投入主要物资计划、"
    "施工机械设备配置、劳动力安排计划、确保工程质量的技术组织措施、确保安全生产的技术组织措施、"
    "确保工期的技术组织措施、确保文明施工的技术组织措施等内容，措施力求针对性与可行性。"
) * 2


def _p(tid):
    return load_all_profiles()[tid]


def _find(result, code):
    return next((x for x in result.findings if x.code == code), None)


def test_site_plan_required_missing_then_present():
    p = _p(FEIDONG)
    r_missing = check_tender_hard_lines(LONG, p)  # LONG 不含总平面图
    sp = _find(r_missing, "SITE_PLAN_REQUIRED")
    assert sp is not None and sp.status == "fail" and sp.severity == "warn"

    r_present = check_tender_hard_lines(LONG + "\n施工总平面布置图见附图。", p)
    assert _find(r_present, "SITE_PLAN_REQUIRED").status == "pass"


def test_multiple_shigong_disqualifies():
    p = _p(FEIDONG)
    r = check_tender_hard_lines(LONG, p, shigong_count=2)
    ms = _find(r, "MULTIPLE_SHIGONG")
    assert ms.status == "fail" and ms.severity == "disqualify"
    assert r.disqualified is True
    r1 = check_tender_hard_lines(LONG, p, shigong_count=1)
    assert r1.disqualified is False


def test_not_provided_is_zero_not_disqualify():
    p = _p(FEIDONG)
    r = check_tender_hard_lines("太短了", p)
    npf = _find(r, "NOT_PROVIDED")
    assert npf.status == "fail" and npf.severity == "zero"
    assert r.disqualified is False  # 本项0分，但不等于废标


def test_page_limit_over_pass_unknown():
    p = _p(FEIDONG)
    assert _find(check_tender_hard_lines(LONG, p, page_count=60), "PAGE_LIMIT").status == "fail"
    assert _find(check_tender_hard_lines(LONG, p, page_count=40), "PAGE_LIMIT").status == "pass"
    assert _find(check_tender_hard_lines(LONG, p), "PAGE_LIMIT").status == "unknown"


def test_case1_no_site_plan_rule_but_has_format_advisory():
    p = _p(YUNKANG)
    r = check_tender_hard_lines(LONG, p)
    assert _find(r, "SITE_PLAN_REQUIRED") is None  # 本标不要求总平面图
    fmt = _find(r, "FORMAT")
    assert fmt is not None and fmt.status == "advisory"
