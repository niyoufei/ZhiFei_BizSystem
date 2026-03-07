from __future__ import annotations

from app.engine.anchors import build_project_requirements_from_anchors
from app.engine.v2_scorer import score_text_v2


def _minimal_lexicon() -> dict:
    return {
        "dimension_keywords": {
            "01": ["项目理解", "实施路径", "信息化管理"],
            "02": ["安全", "劳保用品"],
            "03": ["文明施工", "绿色工地"],
            "06": ["关键工序", "工序内容"],
        },
        "empty_promises": {"keywords": []},
        "action_triggers": [],
        "definition": {"keywords": [], "regexes": []},
        "analysis": {"keywords": ["风险", "难点"], "regexes": []},
        "solution": {"keywords": ["措施", "管控", "执行"], "regexes": []},
    }


def _pack_requirements(project_id: str = "p1") -> list[dict]:
    return build_project_requirements_from_anchors(
        project_id,
        anchors=[],
        region="合肥",
        scoring_engine_version="v2.0.0",
    )


def _find_req_hit(report: dict, requirement_id: str) -> dict:
    for item in report.get("requirement_hits") or []:
        if str(item.get("requirement_id")) == requirement_id:
            return item
    return {}


def test_pack_injected_for_hefei_v2() -> None:
    requirements = _pack_requirements("p-hefei")
    req_ids = {str(r.get("id")) for r in requirements}
    assert "REQ-01-INFOMGMT-001" in req_ids
    assert "REQ-03-GREEN-001" in req_ids
    assert "REQ-02-LAOBAO-001" in req_ids
    assert "REQ-06-KEYPROC-001" in req_ids
    assert "REQ-ALL-RISK-001-01" in req_ids
    assert "REQ-ALL-RISK-001-16" in req_ids


def test_pack_not_injected_when_region_or_engine_not_match() -> None:
    reqs_other_region = build_project_requirements_from_anchors(
        "p-other",
        anchors=[],
        region="上海",
        scoring_engine_version="v2.0.0",
    )
    reqs_v1 = build_project_requirements_from_anchors(
        "p-v1",
        anchors=[],
        region="合肥",
        scoring_engine_version="v1.0.0",
    )
    ids_other = {str(r.get("id")) for r in reqs_other_region}
    ids_v1 = {str(r.get("id")) for r in reqs_v1}
    assert "REQ-01-INFOMGMT-001" not in ids_other
    assert "REQ-01-INFOMGMT-001" not in ids_v1


def test_missing_infomgmt_heading_triggers_requirement_and_coverage_cap() -> None:
    text = "项目理解与实施路径已描述，但未设置数字化章节。"
    report = score_text_v2(
        submission_id="s-infomgmt",
        text=text,
        lexicon=_minimal_lexicon(),
        anchors=[],
        requirements=_pack_requirements(),
    )
    req_hit = _find_req_hit(report, "REQ-01-INFOMGMT-001")
    assert req_hit and req_hit.get("hit") is False
    lint_texts = [str(x.get("why_it_matters") or "") for x in report.get("lint_findings") or []]
    assert any("信息化管理" in t for t in lint_texts)
    assert float(report["rule_dim_scores"]["01"]["subscores"]["Coverage"]) <= 1.0


def test_missing_green_heading_triggers_requirement() -> None:
    text = (
        "信息化管理\n模块：实名制与视频巡检，每日巡检，超限整改闭环。\n"
        "文明施工章节提到扬尘和噪声，但未出现完整绿色工地标题。"
    )
    report = score_text_v2(
        submission_id="s-green",
        text=text,
        lexicon=_minimal_lexicon(),
        anchors=[],
        requirements=_pack_requirements(),
    )
    req_hit = _find_req_hit(report, "REQ-03-GREEN-001")
    assert req_hit and req_hit.get("hit") is False
    lint_texts = [str(x.get("why_it_matters") or "") for x in report.get("lint_findings") or []]
    assert any("绿色工地" in t for t in lint_texts)


def test_dim02_ppe_without_laobao_is_missing_requirement() -> None:
    text = "安全管理体系：配置 PPE 并发放到岗。" "设置隐患排查流程和巡检记录。"
    report = score_text_v2(
        submission_id="s-ppe",
        text=text,
        lexicon=_minimal_lexicon(),
        anchors=[],
        requirements=_pack_requirements(),
    )
    req_hit = _find_req_hit(report, "REQ-02-LAOBAO-001")
    assert req_hit and req_hit.get("hit") is False
    assert "must_not_have_terms" in str(req_hit.get("reason") or "")


def test_dim06_missing_key_process_headers_triggers_requirement() -> None:
    text = "关键工序控制点表：工序、难点、做法、检查。" "表头未按标准四列定义。"
    report = score_text_v2(
        submission_id="s-keyproc",
        text=text,
        lexicon=_minimal_lexicon(),
        anchors=[],
        requirements=_pack_requirements(),
    )
    req_hit = _find_req_hit(report, "REQ-06-KEYPROC-001")
    assert req_hit and req_hit.get("hit") is False


def test_any_dimension_missing_risk_or_measure_triggers_req_all_risk() -> None:
    text = "材料与部品采购章节：主要风险为供应波动，但未给出对应措施。"
    report = score_text_v2(
        submission_id="s-risk",
        text=text,
        lexicon=_minimal_lexicon(),
        anchors=[],
        requirements=_pack_requirements(),
    )
    req_hit = _find_req_hit(report, "REQ-ALL-RISK-001-04")
    assert req_hit and req_hit.get("hit") is False
    lint_texts = [str(x.get("why_it_matters") or "") for x in report.get("lint_findings") or []]
    assert any("重点难点/风险点" in t for t in lint_texts)


def test_global_banned_words_requirement_triggers_on_forbidden_terms() -> None:
    text = "本章按照相关规范开展，严格落实现场实际情况下的一般安排。"
    report = score_text_v2(
        submission_id="s-banned-words",
        text=text,
        lexicon=_minimal_lexicon(),
        anchors=[],
        requirements=_pack_requirements(),
    )
    req_hit = _find_req_hit(report, "REQ-ALL-BANNED-001-01")
    assert req_hit and req_hit.get("hit") is False
    assert "must_not_have_terms" in str(req_hit.get("reason") or "")
