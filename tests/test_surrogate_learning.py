from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.engine.preflight import PreFlightFatalError, pre_flight_check
from app.engine.surrogate_learning import calibrate_weights, compute_time_decay_weight
from app.engine.template_rag import build_probe_template_suggestions, compute_probe_dimensions


def _uniform_weights() -> dict:
    return {f"{i:02d}": 1.0 / 16.0 for i in range(1, 17)}


def test_time_decay_reaches_quarter_by_60_days() -> None:
    now = datetime(2026, 2, 21, tzinfo=timezone.utc)
    record_time = (now - timedelta(days=60)).isoformat()
    weight = compute_time_decay_weight(record_time=record_time, now=now, half_life_days=30.0)
    assert abs(weight - 0.25) < 1e-6


def test_time_decay_reaches_12p5_percent_by_90_days() -> None:
    now = datetime(2026, 2, 21, tzinfo=timezone.utc)
    record_time = (now - timedelta(days=90)).isoformat()
    weight = compute_time_decay_weight(record_time=record_time, now=now, half_life_days=30.0)
    assert weight <= 0.125 + 1e-6


def test_calibrate_weights_tag_guided_focuses_bim_dimensions() -> None:
    current = _uniform_weights()
    feedback_records = [
        {
            "predicted_total_score": 70.0,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "judge_feedbacks": [
                {"score": 88.0, "qualitative_tags": ["重点表扬了BIM"]},
                {"score": 86.0, "qualitative_tags": ["智能建造"]},
            ],
        }
    ]
    out = calibrate_weights(current, feedback_records)
    weights = out["weights_norm"]
    assert weights["05"] > current["05"]
    assert weights["14"] > current["14"]
    assert out["stats"]["tag_guided_updates"] >= 1


def test_calibrate_weights_global_mode_keeps_distribution_stable() -> None:
    current = _uniform_weights()
    feedback_records = [
        {
            "predicted_total_score": 80.0,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "judge_feedbacks": [
                {"score": 79.5, "qualitative_tags": []},
                {"score": 80.5, "qualitative_tags": []},
            ],
        }
    ]
    out = calibrate_weights(current, feedback_records)
    assert out["stats"]["global_updates"] >= 1
    assert out["stats"]["drift_l1"] < 0.2


def test_pre_flight_check_blocks_missing_core_sections() -> None:
    text = "仅有工程概况和一般描述，未形成完整章节体系。"
    try:
        pre_flight_check(text, raise_on_fatal=True)
    except PreFlightFatalError as exc:
        assert "缺失骨架章节" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("pre_flight_check 应抛出 PreFlightFatalError")


def test_pre_flight_check_blocks_outdated_norm_reference() -> None:
    text = (
        "编制依据\n工程概况\n施工部署\n施工进度计划\n施工准备与资源配置计划\n"
        "主要施工方法\n质量管理\n安全管理\n"
        "本方案引用 GB 50194-1993。"
    )
    result = pre_flight_check(text, raise_on_fatal=False)
    assert result["fatal"] is True
    assert result["outdated_norm_refs"]


def test_probe_template_suggestions_trigger_under_80_percent() -> None:
    dim_scores = {
        "03": {"dim_score": 4.0, "max_score": 10.0},
        "08": {"dim_score": 5.0, "max_score": 10.0},
        "05": {"dim_score": 4.5, "max_score": 10.0},
        "14": {"dim_score": 4.5, "max_score": 10.0},
        "02": {"dim_score": 5.0, "max_score": 10.0},
        "09": {"dim_score": 5.0, "max_score": 10.0},
        "07": {"dim_score": 4.0, "max_score": 10.0},
    }
    probes = compute_probe_dimensions(text="BIM 应用较弱，未体现智慧工地。", dim_scores=dim_scores)
    suggestions = build_probe_template_suggestions(probes, threshold=0.8)
    assert suggestions
    assert any(s.get("dimension_id") == "P02" for s in suggestions)
    p02 = next((s for s in suggestions if s.get("dimension_id") == "P02"), None)
    assert p02 is not None
    assert isinstance(p02.get("logic_skeletons"), list)
    assert p02.get("references")
    assert isinstance(p02.get("applied_feature_ids"), list)
