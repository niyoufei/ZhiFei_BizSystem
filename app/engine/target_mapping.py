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

import re
from dataclasses import asdict, dataclass
from typing import Callable, Dict, Optional, Sequence, Tuple

from app.engine.tender_profile import (
    HardRedline,
    ScoringItem,
    TenderProfile,
    TenderProfileValidationError,
    TenderScoringProfile,
    percentile_in_field,
    validate_tender_profile,
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


class TargetMappingError(ValueError):
    """按标 target mapping bridge 构建失败。"""


@dataclass(frozen=True)
class TargetSignal:
    """一个按标评分项映射到内部目标信号后的结构表示。"""

    item_id: str
    name: str
    max_score: float
    legacy_dimension_refs: Tuple[str, ...]
    evidence_requirements: Tuple[str, ...]
    band_count: int
    band_labels: Tuple[str, ...]
    band_triggers: Tuple[str, ...]


@dataclass(frozen=True)
class TargetCoverage:
    """按标 target mapping 覆盖情况。"""

    item_count: int
    total_score: float
    mapped_item_count: int
    unmapped_item_ids: Tuple[str, ...]
    legacy_dimension_refs: Tuple[str, ...]
    hard_redline_count: int


@dataclass(frozen=True)
class TargetMapping:
    """TenderProfile.scoring_items 到内部 target signals 的兼容桥接结果。"""

    tender_id: str
    tender_name: str
    version: str
    score_scale: float
    targets: Tuple[TargetSignal, ...]
    hard_redlines: Tuple[Dict[str, object], ...]
    coverage: TargetCoverage


_LEGACY_DIMENSION_REF_RE = re.compile(r"^(?:dim|dimension)?_?(\d{1,2})$")
_UNSAFE_REF_RE = re.compile(r"[^a-z0-9]+")


def normalize_legacy_dimension_ref(ref: str) -> str:
    """归一化 legacy dimension ref；非 01-16 自定义引用安全保留。"""
    normalized = _UNSAFE_REF_RE.sub("_", str(ref).strip().lower()).strip("_")
    if not normalized:
        return ""

    match = _LEGACY_DIMENSION_REF_RE.match(normalized)
    if match:
        number = int(match.group(1))
        if 1 <= number <= 16:
            return f"dim_{number:02d}"
    return normalized


def _normalize_legacy_dimension_refs(refs: Sequence[str]) -> Tuple[str, ...]:
    normalized = {ref for ref in (normalize_legacy_dimension_ref(item) for item in refs) if ref}
    return tuple(sorted(normalized))


def collect_legacy_dimension_refs(profile: TenderProfile) -> list[str]:
    """收集 profile 级与 item 级 legacy refs，并去重、排序、归一化。"""
    refs = list(profile.legacy_dimension_refs)
    for item in profile.scoring_items:
        refs.extend(item.legacy_dimension_refs)
    return list(_normalize_legacy_dimension_refs(refs))


def _band_label(band: object) -> str:
    return str(
        getattr(band, "label", "") or getattr(band, "name", "") or getattr(band, "band_id", "")
    )


def _target_signal_from_item(item: ScoringItem) -> TargetSignal:
    band_labels = tuple(label for label in (_band_label(band) for band in item.bands) if label)
    band_triggers = tuple(
        sorted(
            {
                str(trigger)
                for band in item.bands
                for trigger in getattr(band, "triggers", ())
                if str(trigger).strip()
            }
        )
    )
    return TargetSignal(
        item_id=item.item_id,
        name=item.name,
        max_score=float(item.max_score),
        legacy_dimension_refs=_normalize_legacy_dimension_refs(item.legacy_dimension_refs),
        evidence_requirements=tuple(str(req) for req in item.evidence_requirements),
        band_count=len(item.bands),
        band_labels=band_labels,
        band_triggers=band_triggers,
    )


def _hard_redline_to_dict(redline: HardRedline) -> Dict[str, object]:
    return {
        "redline_id": redline.redline_id,
        "description": redline.description,
        "action": redline.action,
        "applies_to": list(redline.applies_to),
    }


def build_target_mapping(profile: TenderProfile) -> TargetMapping:
    """把 TenderProfile contract 桥接为内部 target mapping，不执行评分裁决。"""
    try:
        validate_tender_profile(profile)
    except TenderProfileValidationError as exc:
        raise TargetMappingError(f"TenderProfile 无法构建 target mapping: {exc}") from exc

    targets = tuple(_target_signal_from_item(item) for item in profile.scoring_items)
    unmapped_item_ids = tuple(
        target.item_id for target in targets if not target.legacy_dimension_refs
    )
    coverage = TargetCoverage(
        item_count=len(targets),
        total_score=float(profile.score_scale),
        mapped_item_count=len(targets) - len(unmapped_item_ids),
        unmapped_item_ids=unmapped_item_ids,
        legacy_dimension_refs=tuple(collect_legacy_dimension_refs(profile)),
        hard_redline_count=len(profile.hard_redlines),
    )
    return TargetMapping(
        tender_id=profile.tender_id,
        tender_name=profile.tender_name,
        version=profile.version,
        score_scale=float(profile.score_scale),
        targets=targets,
        hard_redlines=tuple(_hard_redline_to_dict(redline) for redline in profile.hard_redlines),
        coverage=coverage,
    )


def target_mapping_to_dict(mapping: TargetMapping) -> Dict[str, object]:
    """返回 JSON 友好的 target mapping dict。"""
    return {
        "tender_id": mapping.tender_id,
        "tender_name": mapping.tender_name,
        "version": mapping.version,
        "score_scale": float(mapping.score_scale),
        "targets": [
            {
                "item_id": target.item_id,
                "name": target.name,
                "max_score": float(target.max_score),
                "legacy_dimension_refs": list(target.legacy_dimension_refs),
                "evidence_requirements": list(target.evidence_requirements),
                "band_count": target.band_count,
                "band_labels": list(target.band_labels),
                "band_triggers": list(target.band_triggers),
            }
            for target in mapping.targets
        ],
        "hard_redlines": [
            {
                "redline_id": redline["redline_id"],
                "description": redline["description"],
                "action": redline["action"],
                "applies_to": list(redline["applies_to"]),
            }
            for redline in mapping.hard_redlines
        ],
        "coverage": {
            "item_count": mapping.coverage.item_count,
            "total_score": float(mapping.coverage.total_score),
            "mapped_item_count": mapping.coverage.mapped_item_count,
            "unmapped_item_ids": list(mapping.coverage.unmapped_item_ids),
            "legacy_dimension_refs": list(mapping.coverage.legacy_dimension_refs),
            "hard_redline_count": mapping.coverage.hard_redline_count,
        },
    }
