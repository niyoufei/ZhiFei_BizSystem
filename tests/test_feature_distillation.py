from __future__ import annotations

import json
from pathlib import Path

from app.engine import feature_distillation as fd
from app.schemas import ExtractedFeature
from app.storage import StorageDataError


def test_extract_logic_skeleton_prompt_has_hard_antiplagiarism_guard() -> None:
    result = fd.extract_logic_skeleton_via_llm("示例文本", dimension_id="P02", llm_invoke=None)
    prompt = result["prompt"]
    assert "绝对禁止摘抄任何原文句式或具体业务数据" in prompt
    assert "[前置条件] + [技术/动作] + [量化指标类型]" in prompt


def test_extract_logic_skeleton_sanitizes_raw_and_numeric_content() -> None:
    chunk_text = "项目采用BIM三维协同，每周2次碰撞检查并闭环。"

    def fake_llm(_: str) -> str:
        payload = {
            "logic_skeleton": [
                "项目采用BIM三维协同，每周2次碰撞检查并闭环。",
                "场景明确 + 模型协同审查 + 问题关闭率",
            ]
        }
        return json.dumps(payload, ensure_ascii=False)

    result = fd.extract_logic_skeleton_via_llm(chunk_text, dimension_id="P02", llm_invoke=fake_llm)
    skeleton = result["logic_skeleton"]
    assert skeleton
    assert all("[前置条件]" in x and "[技术/动作]" in x and "[量化指标类型]" in x for x in skeleton)
    assert all(not any(ch.isdigit() for ch in x) for x in skeleton)


def test_update_feature_confidence_increase_and_persist(monkeypatch) -> None:
    feature = ExtractedFeature(
        feature_id="F-1",
        dimension_id="P02",
        logic_skeleton=["[前置条件] 场景明确 + [技术/动作] 协同审查 + [量化指标类型] 闭环完成率"],
        confidence_score=0.5,
        usage_count=0,
        active=True,
    )
    saved = {"count": 0}

    monkeypatch.setattr(fd, "load_feature_kb", lambda: [feature])

    def _save(_features):
        saved["count"] += 1

    monkeypatch.setattr(fd, "save_feature_kb", _save)

    out = fd.update_feature_confidence(["F-1"], actual_score=88, predicted_score=70)
    assert out["updated"] == 1
    assert feature.confidence_score > 0.5
    assert feature.usage_count == 1
    assert saved["count"] == 1


def test_update_feature_confidence_retire_when_low_and_used(monkeypatch) -> None:
    feature = ExtractedFeature(
        feature_id="F-2",
        dimension_id="P03",
        logic_skeleton=["[前置条件] 风险识别 + [技术/动作] 应急联动 + [量化指标类型] 处置时效"],
        confidence_score=0.19,
        usage_count=2,
        active=True,
    )

    monkeypatch.setattr(fd, "load_feature_kb", lambda: [feature])
    monkeypatch.setattr(fd, "save_feature_kb", lambda _features: None)

    out = fd.update_feature_confidence(["F-2"], actual_score=50, predicted_score=92)
    assert out["updated"] == 1
    assert out["retired"] == 1
    assert feature.active is False
    assert feature.usage_count == 3


def test_distill_feature_from_text_returns_sanitized_feature() -> None:
    feature = fd.distill_feature_from_text(
        dimension_id="09",
        source_text="关键线路明确；每周2次纠偏会；节点验收闭环。",
        confidence_score=0.72,
    )
    assert feature is not None
    assert feature.dimension_id == "09"
    assert feature.logic_skeleton
    assert all("[前置条件]" in item for item in feature.logic_skeleton)
    assert all(not any(ch.isdigit() for ch in item) for item in feature.logic_skeleton)


def test_upsert_distilled_features_adds_and_updates_existing(monkeypatch) -> None:
    existing = ExtractedFeature(
        feature_id="F-existing",
        dimension_id="09",
        logic_skeleton=["[前置条件] 节点明确 + [技术/动作] 计划纠偏 + [量化指标类型] 闭环验收"],
        confidence_score=0.45,
        usage_count=2,
        active=True,
    )
    new_feature = ExtractedFeature(
        feature_id="F-new",
        dimension_id="09",
        logic_skeleton=["[前置条件] 场景明确 + [技术/动作] 资源统筹 + [量化指标类型] 节点达成率"],
        confidence_score=0.67,
        usage_count=0,
        active=True,
    )
    stronger_existing = ExtractedFeature(
        feature_id="F-override",
        dimension_id="09",
        logic_skeleton=list(existing.logic_skeleton),
        confidence_score=0.8,
        usage_count=0,
        active=True,
    )
    saved = {}

    monkeypatch.setattr(fd, "load_feature_kb", lambda: [existing])
    monkeypatch.setattr(
        fd, "save_feature_kb", lambda features: saved.setdefault("count", len(features))
    )

    out = fd.upsert_distilled_features([new_feature, stronger_existing])

    assert out["added"] == 1
    assert out["updated"] == 1
    assert out["total"] == 2
    assert existing.confidence_score == 0.8
    assert saved["count"] == 2


def test_load_feature_kb_falls_back_to_bootstrap_when_storage_is_corrupted(monkeypatch) -> None:
    bootstrap_feature = ExtractedFeature(
        feature_id="F-bootstrap",
        dimension_id="09",
        logic_skeleton=["[前置条件] 场景明确 + [技术/动作] 资源统筹 + [量化指标类型] 达成率"],
        confidence_score=0.67,
        usage_count=0,
        active=True,
    )

    def _raise_storage_error():
        raise StorageDataError(Path("/tmp/high_score_features.json"), "json_parse_failed", "boom")

    monkeypatch.setattr(fd, "load_high_score_features", _raise_storage_error)
    monkeypatch.setattr(fd, "_load_bootstrap_features", lambda: [bootstrap_feature])

    out = fd.load_feature_kb()

    assert len(out) == 1
    assert out[0].feature_id == "F-bootstrap"


def test_generate_tailored_advice_prompt_and_error_handling() -> None:
    base = fd.generate_tailored_advice(
        weak_text="弱项文本",
        project_context="合肥TOD精装项目",
        top_logic_skeletons=[["[前置条件] A + [技术/动作] B + [量化指标类型] C"]],
        llm_invoke=None,
    )
    assert base["advice"] is None
    assert "绝不带有模板套用痕迹" in base["prompt"]

    def _raise(_: str) -> str:
        raise RuntimeError("llm down")

    err = fd.generate_tailored_advice(
        weak_text="弱项文本",
        project_context="合肥TOD精装项目",
        top_logic_skeletons=[["[前置条件] A + [技术/动作] B + [量化指标类型] C"]],
        llm_invoke=_raise,
    )
    assert err["advice"] is None
    assert "llm_invoke_failed" in err["error"]


def test_select_top_logic_skeletons_excludes_pending_and_ignored(monkeypatch) -> None:
    pending = ExtractedFeature(
        feature_id="F-pending",
        dimension_id="09",
        logic_skeleton=["[前置条件] 场景明确 + [技术/动作] 节点策划 + [量化指标类型] 闭环验收"],
        confidence_score=0.95,
        usage_count=5,
        active=True,
        governance_status="pending",
    )
    adopted = ExtractedFeature(
        feature_id="F-adopted",
        dimension_id="09",
        logic_skeleton=["[前置条件] 风险识别 + [技术/动作] 计划纠偏 + [量化指标类型] 节点达成率"],
        confidence_score=0.82,
        usage_count=3,
        active=True,
        governance_status="adopted",
    )
    ignored = ExtractedFeature(
        feature_id="F-ignored",
        dimension_id="09",
        logic_skeleton=["[前置条件] 条件约束 + [技术/动作] 资源联动 + [量化指标类型] 到位时效"],
        confidence_score=0.99,
        usage_count=6,
        active=True,
        governance_status="ignored",
    )
    monkeypatch.setattr(fd, "load_feature_kb", lambda: [pending, adopted, ignored])

    out = fd.select_top_logic_skeletons(dimension_ids=["09"], top_k=3)

    assert [item.feature_id for item in out] == ["F-adopted"]


def test_select_top_few_shot_prompt_examples_only_uses_adopted_features(monkeypatch) -> None:
    adopted = ExtractedFeature(
        feature_id="F-adopted",
        dimension_id="09",
        logic_skeleton=["[前置条件] 风险识别 + [技术/动作] 计划纠偏 + [量化指标类型] 节点达成率"],
        confidence_score=0.82,
        usage_count=3,
        active=True,
        governance_status="auto_adopted",
        source_highlights=["评委表扬关键线路纠偏闭环", "节点验收责任明确"],
    )
    legacy = ExtractedFeature(
        feature_id="F-legacy",
        dimension_id="09",
        logic_skeleton=["[前置条件] 场景明确 + [技术/动作] 资源统筹 + [量化指标类型] 达成率"],
        confidence_score=0.99,
        usage_count=10,
        active=True,
    )
    monkeypatch.setattr(fd, "load_feature_kb", lambda: [adopted, legacy])

    out = fd.select_top_few_shot_prompt_examples(dimension_ids=["09"], top_k=3)

    assert len(out) == 1
    assert out[0]["feature_id"] == "F-adopted"
    assert out[0]["dimension_name"]
    assert out[0]["source_highlights"] == ["评委表扬关键线路纠偏闭环", "节点验收责任明确"]
