"""
战略判断层（Strategy Advisor）。

升级增量 3。回答两个真实数据逼出来的问题：
1) 施组含金量评估：在本标的评标办法 + 竞争分布下，施组到底是不是胜负手，
   决定要不要在施组上砸资源（decisive 决定性 / coupled 联动 / gate 门槛）。
2) 动态目标设定：目标不是「绝对满分」，而是「本标良好档·全场最前」。
   四标实测无人进优秀档，绝对满分不是合理目标。

纪律：纯新增、确定性、零外部依赖；不修改核心评分链。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Dict, Optional, Sequence, Tuple

from app.engine.tender_profile import (
    TenderScoringProfile,
    field_target_score,
    summarize_field,
)

# 含金量 -> 建议投入强度
_EFFORT_BY_LEVEL = {
    "decisive": "high",
    "coupled": "medium",
    "gate": "low-gate",
}

_DECIDED_BY = {
    "decisive": "价格普遍踩满、商务齐全后，施组分直接决定排名——施组是胜负手，值得重点投入。",
    "coupled": "施组与价格联动，差距常在小数点后，需施组与价格同时到位才稳。",
    "gate": "施组在此办法下是「门槛项」：主要决定能否进入报价评审，最终由价格定胜负——施组达标即可，过度投入边际收益低。",
}

# 施组分「极差/满分」低于该比例，视为高度聚集、几乎无区分度
_LOW_SPREAD_RATIO = 0.02


@dataclass(frozen=True)
class LeverageAssessment:
    tender_id: str
    eval_method: str
    leverage_level: str  # decisive / coupled / gate
    recommended_effort: str  # high / medium / low-gate
    decided_by: str
    rationale: Tuple[str, ...]
    field_signal: Optional[str]  # 由竞争分布 refine 出的信号（给了数据才有）

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class TargetRecommendation:
    tender_id: str
    target_f: Optional[float]  # 动态目标 F 分（默认=全场最高）
    target_band: Optional[str]
    target_quantile: float  # 1.0 = 全场最前
    ceiling_note: str
    current_gap: Optional[float]  # 给了 current_f 时，距目标还差多少分

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


def assess_shigong_leverage(
    profile: TenderScoringProfile,
    *,
    field_shigong_scores: Optional[Sequence[float]] = None,
    field_price_scores: Optional[Sequence[float]] = None,
) -> LeverageAssessment:
    """评估在本标里施组的胜负含金量。配置给出基线，竞争分布做 refine。"""
    level = profile.shigong_weight_type
    effort = _EFFORT_BY_LEVEL.get(level, "medium")
    decided = _DECIDED_BY.get(level, "")

    rationale = [
        f"评标办法：{profile.eval_method}；配置判定施组含金量为「{level}」。",
    ]

    field_signal: Optional[str] = None
    if field_shigong_scores:
        sm = summarize_field(field_shigong_scores)
        if sm:
            spread = sm["max"] - sm["min"]
            ratio = spread / float(profile.shigong_max_score) if profile.shigong_max_score else 0.0
            if ratio < _LOW_SPREAD_RATIO:
                field_signal = (
                    f"施组分高度聚集（极差 {spread:.2f}/{profile.shigong_max_score:g}），"
                    "几乎无区分度——靠施组拉开差距很难。"
                )
            else:
                field_signal = f"施组分有区分度（极差 {spread:.2f}），值得在施组上发力。"
            rationale.append(field_signal)

    if field_price_scores:
        pm = summarize_field(field_price_scores)
        if pm and (pm["max"] - pm["min"]) < 1e-9:
            rationale.append("报价分完全相同——价格已无区分度，施组/商务成为唯一变量。")

    return LeverageAssessment(
        tender_id=profile.tender_id,
        eval_method=profile.eval_method,
        leverage_level=level,
        recommended_effort=effort,
        decided_by=decided,
        rationale=tuple(rationale),
        field_signal=field_signal,
    )


def recommend_target(
    profile: TenderScoringProfile,
    *,
    field_shigong_scores: Optional[Sequence[float]] = None,
    current_f: Optional[float] = None,
    quantile: float = 1.0,
) -> TargetRecommendation:
    """给出本标的动态目标分。默认目标=全场最高（quantile=1.0，即冲到全场最前）。"""
    scores = list(field_shigong_scores or [])
    target_f = field_target_score(scores, quantile=quantile) if scores else None
    target_band = profile.band_of(target_f) if target_f is not None else None

    ceiling_note = "四标实测无人进入优秀档；现实目标为「本标良好档·全场最前」，不必追绝对满分。"
    current_gap: Optional[float] = None
    if current_f is not None and target_f is not None:
        current_gap = round(max(0.0, float(target_f) - float(current_f)), 4)

    return TargetRecommendation(
        tender_id=profile.tender_id,
        target_f=(round(float(target_f), 4) if target_f is not None else None),
        target_band=target_band,
        target_quantile=quantile,
        ceiling_note=ceiling_note,
        current_gap=current_gap,
    )
