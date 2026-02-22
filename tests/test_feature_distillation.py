from __future__ import annotations

import json

from app.engine import feature_distillation as fd
from app.schemas import ExtractedFeature


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
