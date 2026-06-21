"""
预测目标映射层（Target Mapping）。

升级增量 2。把系统内部规则分（0-100）映射成「本标语言」：本标分制下的 F 分、
所属档位、跨标归一化分、同标竞争百分位、距下一档还差多少分。

设计依据（4 个真实标）：真实评标只产出一个 F 分 + 档位（5 分制或 100 分制），
而非 16 维各自分。因此对外预测目标必须统一到本标口径。16 维退居为内部特征。

纪律：纯新增、确定性、零外部依赖；不修改 scorer.py / v2_scorer.py。
当前用「线性基线」映射（internal/100 × 满分）；预留 calibrator 钩子，
后续「校准增量」训练出回归器后可无缝替换基线，不动本层接口。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Callable, Dict, Optional, Sequence

from app.engine.tender_profile import (
    TenderScoringProfile,
    percentile_in_field,
)

# 校准器签名：输入(内部0-100分, profile) -> 本标F分
CalibratorFn = Callable[[float, TenderScoringProfile], float]


@dataclass(frozen=True)
class TargetPrediction:
    """系统对某份施组在「本标口径」下的预测目标表示。"""

    tender_id: str
    internal_score_0_100: float  # 系统内部规则分（输入）
    f_score: float  # 映射到本标分制后的预测 F 分
    band: Optional[str]  # 所属档位（落档外=未提供时为 None）
    normalized: float  # 0..1（= F / 满分），跨标可比
    is_top_band: bool  # 是否已达最高档（如优秀）
    next_band: Optional[str]  # 上一档名（已在最高档则 None）
    gap_to_next_band: Optional[float]  # 距进入上一档还差多少 F 分
    percentile_in_field: Optional[float]  # 同标竞争百分位（给了 field 才有）
    method: str  # "baseline_linear" | "calibrated"

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


def band_gap(
    profile: TenderScoringProfile, f_score: float
) -> tuple[Optional[str], Optional[float]]:
    """返回（上一档名, 距其下界还差的 F 分）。已在最高档返回 (None, None)。"""
    f = float(f_score)
    bands_sorted = sorted(profile.bands, key=lambda b: (b.lower, b.upper))
    cur = profile.band_of(f)
    if cur is None:
        if bands_sorted:
            b0 = bands_sorted[0]
            return b0.name, max(0.0, b0.lower - f)
        return None, None
    idx = next((i for i, b in enumerate(bands_sorted) if b.name == cur), None)
    if idx is None or idx + 1 >= len(bands_sorted):
        return None, None
    nb = bands_sorted[idx + 1]
    return nb.name, max(0.0, nb.lower - f)


def map_internal_to_target(
    internal_score_0_100: float,
    profile: TenderScoringProfile,
    *,
    field_scores: Optional[Sequence[float]] = None,
    calibrator: Optional[CalibratorFn] = None,
) -> TargetPrediction:
    """把内部规则分映射为本标口径预测。

    Args:
        internal_score_0_100: 系统内部规则总分（V1/V2 的 0-100 刻度）。
        profile: 本标评分配置。
        field_scores: 可选，同标其他投标人的 F 分，用于算竞争百分位。
        calibrator: 可选，已训练的校准器；给了就用它替代线性基线。
    """
    internal = float(internal_score_0_100)
    if calibrator is not None:
        f = float(calibrator(internal, profile))
        method = "calibrated"
    else:
        f = (internal / 100.0) * float(profile.shigong_max_score)
        method = "baseline_linear"
    # 钳制到 [0, 满分]
    f = max(0.0, min(float(profile.shigong_max_score), f))

    band = profile.band_of(f)
    next_band, gap = band_gap(profile, f)
    pct = percentile_in_field(f, field_scores) if field_scores else None

    return TargetPrediction(
        tender_id=profile.tender_id,
        internal_score_0_100=round(internal, 4),
        f_score=round(f, 4),
        band=band,
        normalized=round(profile.normalize(f), 4),
        is_top_band=profile.is_top_band(f),
        next_band=next_band,
        gap_to_next_band=(round(gap, 4) if gap is not None else None),
        percentile_in_field=(round(pct, 4) if pct is not None else None),
        method=method,
    )
