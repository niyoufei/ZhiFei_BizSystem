from __future__ import annotations

import copy
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app import tender_criteria_service
from app.engine.tender_profile import TenderProfileValidationError
from app.main import app
from app.tender_criteria_service import (
    apply_approved_tender_score,
    approve_tender_profile,
    build_evidence_attention_profile,
    extract_tender_profile_draft,
    project_tender_profile_state,
    score_document_against_profile,
)

TENDER_TEXT = """
第三章 评审标准
一、施工总体部署（20分）
施工部署应结合现场条件，明确施工段划分和总体实施顺序。
二、施工进度与工期保证（30分）
进度计划须明确关键线路、里程碑和纠偏措施。
三、质量安全保障（50分）
质量保证体系应完整，安全生产责任和检查频次须明确。
未提供施工组织设计的不得分。
"""

SPLIT_TABLE_TENDER_TEXT = """
第三章 评标办法
详细评审标准
条款号 评审因素 分值 评审标准
2.2.2 技术文件 施工组织设
计
5 分
依据投标人提供的施工组织设计进行评审，包括但
不限于以下内容：
1.针对工程项目整体理解；
2.拟采用的新技术、新工艺（如有）；
3.确保工期与质量；
4.确保人、材、机配置合理；
5.确保安全文明施工；
6.涉及绿色建筑的应体现绿色施工要求。
文本格式要求为 50 页以内、正文 22 磅、页边距 2.5 厘米。
较差得 0 分≤F＜2，一般得 2≤F＜3.5，优秀得 3.5
≤F≤5
"""


def test_extract_creates_reviewable_draft_without_auto_approval():
    state = extract_tender_profile_draft(
        project_id="p1",
        project_name="测试标段",
        source_text=TENDER_TEXT,
    )

    assert state["status"] == "draft"
    assert state["approved"] is False
    assert state["needs_review"] is True
    assert state["profile"]["score_scale"] == 100.0
    assert [item["name"] for item in state["profile"]["scoring_items"]] == [
        "施工总体部署",
        "施工进度与工期保证",
        "质量安全保障",
    ]
    assert state["sources"][0]["source_locator"] == "第 2 行"
    assert state["profile"]["hard_redlines"][0]["action"] == "manual_review"


def test_extract_recovers_split_pdf_table_technical_criterion():
    state = extract_tender_profile_draft(
        project_id="p1",
        project_name="测试标段",
        source_text=SPLIT_TABLE_TENDER_TEXT,
    )

    assert state["status"] == "draft"
    assert state["approved"] is False
    assert state["profile"]["score_scale"] == 5.0
    assert [item["name"] for item in state["profile"]["scoring_items"]] == [
        "施工组织设计"
    ]
    requirements = state["profile"]["scoring_items"][0]["evidence_requirements"]
    assert len(requirements) == 6
    assert any("工期与质量" in requirement for requirement in requirements)
    attention_items = state["attention_profile"]["items"]
    assert len(attention_items) == 1
    evidence_rows = attention_items[0]["evidence"]
    assert len(evidence_rows) == 6
    point_counts = [len(row["expert_points"]) for row in evidence_rows]
    assert all(2 <= count <= 8 for count in point_counts)
    assert sum(point_counts) != 36
    assert state["attention_profile"]["selection_context"]["version"] == (
        "expert-point-selector-v3"
    )
    for row in evidence_rows:
        attention = row["attention"]
        assert attention["default"] == (attention["min"] + attention["max"]) / 2
        assert attention["current"] == attention["default"]


def test_split_table_requirements_stop_before_notes_and_other_scoring_items():
    state = extract_tender_profile_draft(
        project_id="p-boundary",
        project_name="公共建筑装饰装修工程",
        source_text="""
        第三章 评标办法
        详细评审标准
        条款号 评审因素 分值 评审标准
        2.2.2 技术文件 施工组织设
        计
        5 分
        依据投标人提供的施工组织设计进行评审，包括但不限于以下内容：
        1.针对工程项目整体理解；
        2.拟采用的新技术、新工艺（如有）；
        3.确保工期与质量的保障体系与措施；
        4.确保人、材、机的保障体系与措施；
        5.确保安全文明生产的管理体系与措施；
        6.涉及绿色建筑的应体现绿色建筑等技术措施。
        较差得 0 分≤F＜2，一般得 2≤F＜3.5，优秀得 3.5≤F≤5
        注：（1）施工组织设计编制建议：
        包括封面和目录）；
        投标人业绩 2.5 分
        每提供一个公共建筑装饰装修工程施工业绩得 2.5 分。
        （2）业绩证明资料同第二章投标人须知前附表附录。
        """,
    )

    requirements = state["profile"]["scoring_items"][0]["evidence_requirements"]

    assert len(requirements) == 6
    assert requirements[0].startswith("1.")
    assert requirements[-1].startswith("6.")
    assert not any(
        marker in requirement
        for requirement in requirements
        for marker in ("封面和目录", "投标人业绩", "业绩证明资料", "附录")
    )


@pytest.mark.parametrize(
    ("project_name", "scene_tag", "expected_counts", "expected_total"),
    (
        ("滨湖办公楼新建工程", "new_building", [7, 5, 8, 6, 7, 5], 38),
        ("滨湖办公楼维修改造工程", "building_renovation", [8, 5, 8, 6, 9, 6], 42),
        ("城市市政道路工程", "municipal", [7, 4, 8, 7, 8, 6], 40),
        ("综合机电安装工程", "mep_installation", [6, 5, 8, 7, 6, 4], 36),
        ("城市园林景观工程", "landscape", [6, 4, 7, 6, 5, 6], 34),
    ),
)
def test_standard_six_umbrella_requirements_enable_exact_scene_catalog(
    project_name, scene_tag, expected_counts, expected_total
):
    state = extract_tender_profile_draft(
        project_id=f"p-{scene_tag}",
        project_name=project_name,
        source_text=SPLIT_TABLE_TENDER_TEXT,
    )

    context = state["attention_profile"]["selection_context"]
    evidence_rows = state["attention_profile"]["items"][0]["evidence"]
    point_counts = [len(row["expert_points"]) for row in evidence_rows]
    assert context["version"] == "expert-point-selector-v3"
    assert context["scene_tags"] == [scene_tag]
    assert context["catalog_summary"]["categories"] == [
        {
            "tag": scene_tag,
            "label": tender_criteria_service.PROJECT_TYPE_CATALOGS[scene_tag]["label"],
            "catalog_total": expected_total,
        }
    ]
    assert point_counts == expected_counts
    assert sum(point_counts) == expected_total
    assert context["catalog_summary"]["combined_catalog_total"] == expected_total
    assert context["catalog_summary"]["enabled_unique_count"] == expected_total
    assert context["catalog_summary"]["evidence_link_count"] == expected_total
    assert all(
        point["catalog_id"] and point["catalog_code"]
        for row in evidence_rows
        for point in row["expert_points"]
    )


def test_eight_first_level_requirements_are_not_silently_cut_to_six():
    source_text = """
    第三章 评审标准
    施工组织设计（8分）
    1.总体部署应结合现场条件。
    2.进度计划须明确关键线路。
    3.质量体系应完整可追溯。
    4.安全管理须覆盖危险源。
    5.劳动力计划应满足高峰需求。
    6.材料供应应明确进场检验。
    7.机械设备应明确备用方案。
    8.绿色施工应明确扬尘控制。
    """

    state = extract_tender_profile_draft(
        project_id="p-eight",
        project_name="市政道路工程",
        source_text=source_text,
    )

    requirements = state["profile"]["scoring_items"][0]["evidence_requirements"]
    assert len(requirements) == 8
    assert "绿色施工" in requirements[-1]
    point_total = sum(
        len(row["expert_points"])
        for row in state["attention_profile"]["items"][0]["evidence"]
    )
    assert point_total != 40


def test_four_focused_requirements_are_not_padded_to_scene_catalog_total():
    state = extract_tender_profile_draft(
        project_id="p-four",
        project_name="市政道路工程",
        source_text="""
        第三章 评审标准
        施工组织设计（8分）
        1.现场出入口应明确交通组织。
        2.关键线路须设置周进度纠偏。
        3.深基坑应明确监测频率。
        4.施工污水应说明处置路径。
        """,
    )

    rows = state["attention_profile"]["items"][0]["evidence"]
    allocations = state["attention_profile"]["selection_context"]["allocations"]
    assert len(rows) == 4
    assert sum(len(row["expert_points"]) for row in rows) != 40
    assert all(allocation["scope"] == "focused" for allocation in allocations)


def test_dynamic_expert_point_ids_are_deterministic():
    first = extract_tender_profile_draft(
        project_id="p1",
        project_name="城市道路及园林工程",
        source_text=SPLIT_TABLE_TENDER_TEXT,
    )
    second = extract_tender_profile_draft(
        project_id="p1",
        project_name="城市道路及园林工程",
        source_text=SPLIT_TABLE_TENDER_TEXT,
    )

    def point_ids(state):
        return [
            point["point_id"]
            for item in state["attention_profile"]["items"]
            for evidence in item["evidence"]
            for point in evidence["expert_points"]
        ]

    assert point_ids(first) == point_ids(second)
    assert first["attention_profile"]["selection_context"] == second[
        "attention_profile"
    ]["selection_context"]


def test_catalog_summary_deduplicates_reused_catalog_entries_across_evidence():
    draft = extract_tender_profile_draft(
        project_id="p1",
        project_name="滨湖办公楼新建工程",
        source_text=SPLIT_TABLE_TENDER_TEXT,
    )
    profile = copy.deepcopy(draft["profile"])
    first_requirement = profile["scoring_items"][0]["evidence_requirements"][0]
    profile["scoring_items"][0]["evidence_requirements"] = [
        first_requirement,
        first_requirement,
    ]

    attention = build_evidence_attention_profile(profile)
    rows = attention["items"][0]["evidence"]
    first_catalog_ids = {point["catalog_id"] for point in rows[0]["expert_points"]}
    second_catalog_ids = {point["catalog_id"] for point in rows[1]["expert_points"]}
    first_point_ids = {point["point_id"] for point in rows[0]["expert_points"]}
    second_point_ids = {point["point_id"] for point in rows[1]["expert_points"]}
    summary = attention["selection_context"]["catalog_summary"]

    assert first_catalog_ids == second_catalog_ids
    assert first_point_ids.isdisjoint(second_point_ids)
    assert summary["enabled_unique_count"] == 7
    assert summary["evidence_link_count"] == 14


def test_compound_scene_uses_catalog_id_deduplicated_union():
    state = extract_tender_profile_draft(
        project_id="p-compound",
        project_name="市政园林景观工程",
        source_text=SPLIT_TABLE_TENDER_TEXT,
    )
    context = state["attention_profile"]["selection_context"]
    catalog_ids = [
        point["catalog_id"]
        for item in state["attention_profile"]["items"]
        for evidence in item["evidence"]
        for point in evidence["expert_points"]
    ]

    assert context["scene_tags"] == ["municipal", "landscape"]
    assert len(catalog_ids) == len(set(catalog_ids))
    assert context["catalog_summary"]["enabled_unique_count"] == len(catalog_ids)
    assert context["catalog_summary"]["combined_catalog_total"] == len(catalog_ids)


@pytest.mark.parametrize(
    ("project_name", "source_context", "expected"),
    (
        ("新建市政道路工程", "", ("municipal",)),
        ("城市道路维修改造工程", "", ("municipal",)),
        ("新建机电安装工程", "", ("mep_installation",)),
        ("滨湖办公楼新建工程", "", ("new_building",)),
        ("滨湖办公楼维修改造工程", "", ("building_renovation",)),
        ("市政道路及园林景观工程", "", ("municipal", "landscape")),
        ("办公楼新建及室外市政工程", "", ("new_building", "municipal")),
        ("市政道路工程", "正文提到办公楼新建", ("municipal",)),
        ("", "本项目为办公楼新建工程", ("new_building",)),
    ),
)
def test_scene_inference_requires_engineering_type_anchors(
    project_name, source_context, expected
):
    assert tender_criteria_service._infer_scene_tags(project_name, source_context) == expected


def _v2_attention_fixture(draft):
    profile = draft["profile"]
    v2 = copy.deepcopy(draft["attention_profile"])
    scene_tags = tuple(v2["selection_context"]["scene_tags"])
    v2["selection_context"] = {
        "version": "expert-point-selector-v2",
        "scene_tags": list(scene_tags),
        "scene_labels": [
            tender_criteria_service._PROJECT_SCENE_CANDIDATES[tag]["label"]
            for tag in scene_tags
        ],
    }
    adjusted = False
    for item_index, item in enumerate(profile["scoring_items"]):
        for evidence_index, requirement in enumerate(item["evidence_requirements"]):
            evidence = v2["items"][item_index]["evidence"][evidence_index]
            selected = tender_criteria_service._select_expert_points_v2(
                requirement,
                item_name=item["name"],
                tender_name=profile["tender_name"],
                scene_tags=scene_tags,
            )
            points = []
            for point in selected:
                name = point["name"]
                minimum, default, maximum = point["attention"]
                current = minimum if not adjusted else default
                adjusted = True
                points.append(
                    {
                        "point_id": tender_criteria_service._stable_id(
                            "point",
                            f"{evidence['evidence_id']}:{name}",
                            point["catalog_index"],
                        ),
                        "name": name,
                        "description": point["description"],
                        "source_type": point["source_type"],
                        "attention": {
                            "min": minimum,
                            "default": default,
                            "max": maximum,
                            "current": current,
                        },
                    }
                )
            evidence["expert_points"] = points
    return v2


def _point_signature(attention_profile):
    return [
        (point["point_id"], point["attention"]["current"])
        for item in attention_profile["items"]
        for evidence in item["evidence"]
        for point in evidence["expert_points"]
    ]


def test_existing_v2_profile_round_trips_without_id_or_current_drift():
    draft = extract_tender_profile_draft(
        project_id="p1",
        project_name="滨湖办公楼新建工程",
        source_text=SPLIT_TABLE_TENDER_TEXT,
    )
    v2 = _v2_attention_fixture(draft)
    expected_signature = _point_signature(v2)

    normalized = build_evidence_attention_profile(draft["profile"], v2)
    got = project_tender_profile_state(
        {
            "meta": {
                "tender_profile_state": {
                    "profile": draft["profile"],
                    "attention_profile": v2,
                }
            }
        }
    )
    approved = approve_tender_profile(
        profile_payload=draft["profile"],
        draft_state={"attention_profile": v2},
        attention_profile=v2,
    )

    for attention in (
        normalized,
        got["attention_profile"],
        approved["attention_profile"],
    ):
        assert attention["selection_context"]["version"] == "expert-point-selector-v2"
        assert _point_signature(attention) == expected_signature

    strict_v2 = copy.deepcopy(v2)
    strict_v2["items"][0]["evidence"][0]["expert_points"].pop()
    with pytest.raises(TenderProfileValidationError, match="point_id"):
        build_evidence_attention_profile(draft["profile"], strict_v2)


def test_legacy_profile_without_context_migrates_to_v3():
    draft = extract_tender_profile_draft(
        project_id="p1",
        project_name="滨湖办公楼新建工程",
        source_text=SPLIT_TABLE_TENDER_TEXT,
    )
    legacy = _v2_attention_fixture(draft)
    legacy.pop("selection_context")

    migrated = build_evidence_attention_profile(draft["profile"], legacy)

    assert migrated["selection_context"]["version"] == "expert-point-selector-v3"
    assert all(
        point["catalog_id"] and point["catalog_code"]
        for item in migrated["items"]
        for evidence in item["evidence"]
        for point in evidence["expert_points"]
    )


def test_attention_profile_round_trips_without_changing_statutory_score():
    draft = extract_tender_profile_draft(
        project_id="p1",
        project_name="测试标段",
        source_text=SPLIT_TABLE_TENDER_TEXT,
    )
    before = score_document_against_profile(draft["profile"], "施工组织设计与工期质量措施")
    adjusted = copy.deepcopy(draft["attention_profile"])
    first = adjusted["items"][0]["evidence"][0]["attention"]
    first["current"] = first["min"]

    approved = approve_tender_profile(
        profile_payload=draft["profile"],
        draft_state=draft,
        attention_profile=adjusted,
    )
    after = score_document_against_profile(approved["profile"], "施工组织设计与工期质量措施")

    assert approved["attention_profile"]["items"][0]["evidence"][0]["attention"][
        "current"
    ] == first["min"]
    assert after == before


def test_attention_profile_rejects_value_outside_expert_range():
    draft = extract_tender_profile_draft(
        project_id="p1",
        project_name="测试标段",
        source_text=SPLIT_TABLE_TENDER_TEXT,
    )
    invalid = copy.deepcopy(draft["attention_profile"])
    attention = invalid["items"][0]["evidence"][0]["attention"]
    attention["current"] = attention["max"] + 0.5

    with pytest.raises(TenderProfileValidationError, match="必须位于"):
        approve_tender_profile(
            profile_payload=draft["profile"],
            draft_state=draft,
            attention_profile=invalid,
        )


def test_split_table_fallback_rejects_generic_scoring_note():
    state = extract_tender_profile_draft(
        project_id="p1",
        project_name="测试标段",
        source_text="""
        第三章 评审标准
        施工组织设计评分说明
        项目经理 5分
        """,
    )

    assert state["status"] == "needs_input"
    assert state["profile"] is None


def test_approval_is_explicit_and_preserves_provenance():
    draft = extract_tender_profile_draft(
        project_id="p1",
        project_name="测试标段",
        source_text=TENDER_TEXT,
    )

    approved = approve_tender_profile(
        profile_payload=draft["profile"],
        draft_state=draft,
    )

    assert approved["status"] == "approved"
    assert approved["approved"] is True
    assert approved["needs_review"] is False
    assert approved["sources"] == draft["sources"]
    assert approved["approved_at"]


def test_project_scoring_uses_approved_items_and_keeps_legacy_diagnostics():
    draft = extract_tender_profile_draft(
        project_id="p1",
        project_name="测试标段",
        source_text=TENDER_TEXT,
    )
    approved = approve_tender_profile(profile_payload=draft["profile"], draft_state=draft)
    report = {
        "total_score": 71.0,
        "rule_total_score": 71.0,
        "pred_total_score": 74.0,
        "dimension_scores": {"01": {"score": 7.1}},
        "meta": {},
    }

    applied = apply_approved_tender_score(
        project={"id": "p1", "meta": {"tender_profile_state": approved}},
        report=report,
        document_text=(
            "现场条件已踏勘，施工段划分和总体实施顺序明确。"
            "进度计划包含关键线路、里程碑和纠偏措施。"
            "质量保证体系完整，安全生产责任清晰，每日检查两次。"
        ),
    )

    assert applied is True
    assert report["total_score"] == 100.0
    assert report["tender_score"]["raw_total"] == 100.0
    assert report["tender_score"]["item_count"] == 3
    assert report["legacy_score"]["pred_total_score"] == 74.0
    assert report["meta"]["legacy_16d_role"] == "secondary_diagnostic"


def test_unapproved_draft_cannot_change_project_score():
    draft = extract_tender_profile_draft(
        project_id="p1",
        project_name="测试标段",
        source_text=TENDER_TEXT,
    )
    report = {"total_score": 71.0, "meta": {}}

    applied = apply_approved_tender_score(
        project={"id": "p1", "meta": {"tender_profile_state": draft}},
        report=report,
        document_text="施工部署",
    )

    assert applied is False
    assert report == {"total_score": 71.0, "meta": {}}


def test_scoring_reports_zero_for_missing_requirement_evidence():
    draft = extract_tender_profile_draft(
        project_id="p1",
        project_name="测试标段",
        source_text=TENDER_TEXT,
    )

    result = score_document_against_profile(draft["profile"], "仅有项目名称，无措施内容。")

    assert result["raw_total"] == 0.0
    assert result["normalized_total"] == 0.0


def test_project_api_extracts_then_explicitly_approves_profile():
    project = {
        "id": "p1",
        "name": "测试标段",
        "meta": {},
        "created_at": "2026-01-01T00:00:00+00:00",
    }
    client = TestClient(app, headers={"X-API-Key": "tender-profile-test-key"})

    with (
        patch.dict("os.environ", {"API_KEYS": "tender-profile-test-key"}, clear=False),
        patch("app.main.load_projects", return_value=[project]),
        patch("app.main.save_projects") as save_projects,
        patch("app.main._merge_tender_materials_text", return_value=TENDER_TEXT),
    ):
        extracted = client.post("/api/v1/projects/p1/tender-profile/extract")

    assert extracted.status_code == 200
    draft = extracted.json()
    assert draft["status"] == "draft"
    assert draft["approved"] is False
    assert draft["attention_profile"]["schema_version"] == "evidence-attention-v1"
    save_projects.assert_called_once()

    with (
        patch.dict("os.environ", {"API_KEYS": "tender-profile-test-key"}, clear=False),
        patch("app.main.load_projects", return_value=[project]),
        patch("app.main.save_projects") as save_projects,
    ):
        approved = client.put(
            "/api/v1/projects/p1/tender-profile/approve",
            json={"profile": draft["profile"]},
        )

    assert approved.status_code == 200
    assert approved.json()["approved"] is True
    assert approved.json()["profile"]["score_scale"] == 100.0
    assert approved.json()["attention_profile"]["score_effect"] == "none"
    save_projects.assert_called_once()
