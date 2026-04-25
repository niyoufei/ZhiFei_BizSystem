from __future__ import annotations

import hashlib
import json

from app.engine.v2_scorer import score_text_v2


def _minimal_lexicon() -> dict:
    return {
        "dimension_keywords": {},
        "empty_promises": {"keywords": []},
        "action_triggers": [],
        "definition": {"keywords": [], "regexes": []},
        "analysis": {"keywords": [], "regexes": []},
        "solution": {"keywords": [], "regexes": []},
    }


def _replay_case() -> (
    tuple[str, list[dict[str, object]], list[dict[str, object]], list[dict[str, object]]]
):
    evidence_units = []
    for dim_id in [f"{i:02d}" for i in range(1, 17)]:
        unit_text = f"REQ{dim_id} 由项目经理牵头，每日检查，报验签认闭环。"
        if dim_id == "07":
            unit_text += (
                " 危大工程重难点专项方案已论证；存在风险隐患并可能影响工期；"
                "控制在5%以内，每周复核；监测旁站并销项；设置应急预案与复工条件。"
            )
        if dim_id == "09":
            unit_text += (
                " 工期120天，总控计划、月计划、周计划、日计划齐全；"
                "关键线路、节点、里程碑倒排；劳动力机械材料保障冗余调配；"
                "偏差纠偏赶工调整；每日例会与周报。"
            )
        evidence_units.append(
            {
                "dimension_primary": dim_id,
                "text": unit_text,
                "tag_definition": True,
                "tag_analysis": True,
                "tag_solution": True,
                "landing_param": True,
                "landing_freq": True,
                "landing_accept": True,
                "landing_role": True,
                "specificity_score": 1.0,
                "anchor_links": ["project_scope"],
            }
        )
    text = (
        " ".join([f"REQ{i:02d}" for i in range(1, 17)])
        + " 危大工程、重难点、专项方案、论证；存在风险隐患并可能影响工期；"
        + "控制在5%以内，每周2次复核，项目经理负责并执行报验验收；"
        + "设置监测旁站并销项；应急预案与复工条件明确。"
        + "总控计划、月计划、周计划、关键线路、里程碑；劳动力机械材料保障冗余调配；"
        + "发生偏差立即纠偏赶工调整；每日例会与周报。工期120天。"
    )
    requirements = [
        {
            "id": f"r{i:02d}",
            "dimension_id": f"{i:02d}",
            "req_type": "keyword",
            "mandatory": True,
            "req_label": f"REQ{i:02d}",
            "patterns": {"keywords": [f"REQ{i:02d}"]},
        }
        for i in range(1, 17)
    ]
    anchors = [
        {"anchor_key": "contract_duration_days", "value_num": 120},
        {"anchor_key": "quality_standard", "anchor_value": "合格"},
        {"anchor_key": "dangerous_works_list", "anchor_value": ["危大工程"]},
        {"anchor_key": "key_milestones", "anchor_value": ["里程碑"]},
    ]
    return text, anchors, requirements, evidence_units


def _stable_signature(report: dict[str, object]) -> dict[str, object]:
    dims = {
        dim_id: round(float((dim or {}).get("dim_score") or 0.0), 4)
        for dim_id, dim in sorted((report.get("rule_dim_scores") or {}).items())
    }
    evidence_hash = hashlib.sha256(
        json.dumps(
            report.get("rule_dim_scores") or {},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return {
        "total_score": round(float(report.get("rule_total_score") or 0.0), 4),
        "dimension_scores": dims,
        "evidence_hash": evidence_hash,
    }


def test_v2_scoring_replay_is_stable_for_30_runs() -> None:
    text, anchors, requirements, evidence_units = _replay_case()
    signatures = []
    for _ in range(30):
        report = score_text_v2(
            submission_id="replay-30x",
            text=text,
            lexicon=_minimal_lexicon(),
            anchors=anchors,
            requirements=requirements,
            evidence_units=evidence_units,
        )
        signatures.append(_stable_signature(report))

    baseline = signatures[0]
    assert all(signature == baseline for signature in signatures)
