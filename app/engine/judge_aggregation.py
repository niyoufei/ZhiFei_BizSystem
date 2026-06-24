"""
评委均分与关注度建模（Judge Aggregation & Attention Modeling）。

升级增量 5。把"多评委打分 -> 一个最终分"的真实机制做成确定性工具：

真实数据结论（4 标实测）：
- 聚合 = N 位评委（5 或 7，现场定）简单平均，按"小数点后第三位四舍五入"保留两位。
  已用真实分验证：长春[4.33,4.36,4.35,4.36,4.8]→4.44；庐金 7 位→82.10。
- 关注度机理：青天大模型按文本给"基准分"；评委可调"关注度"产生微扰，多数评委不调
  → 分数几乎相同。因此基准分稳定可学，个别评委的偏移即关注度调整。

本模块据此提供：精确聚合 + 离散度分析（估计基准分、共识评委数、最离群评委）。
纯新增、确定性、零外部依赖；不接核心评分主链。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from math import isfinite
from statistics import median, pstdev
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

from app.engine.target_mapping import build_target_mapping, target_mapping_to_dict
from app.engine.tender_profile import (
    TenderProfile,
    TenderProfileValidationError,
    TenderScoringProfile,
    validate_tender_profile,
)


def _round_half_up(x: float, ndigits: int = 2) -> float:
    """四舍五入到 ndigits 位（匹配招标文件"第三位四舍五入"，非 Python 默认银行家舍入）。"""
    quant = Decimal(1).scaleb(-ndigits)
    return float(Decimal(str(x)).quantize(quant, rounding=ROUND_HALF_UP))


def aggregate_judges(judge_scores: Sequence[float], *, ndigits: int = 2) -> float:
    """真实聚合：N 位评委简单平均，四舍五入两位。"""
    vals = [float(s) for s in judge_scores if s is not None]
    if not vals:
        raise ValueError("judge_scores 不能为空")
    return _round_half_up(sum(vals) / len(vals), ndigits)


def aggregate_judges_trimmed(
    judge_scores: Sequence[float],
    *,
    trim: int = 1,
    ndigits: int = 2,
) -> float:
    """去掉 trim 个最高与最低后的平均（用于与真实简单平均做对照；本批标实测用简单平均）。"""
    vals = sorted(float(s) for s in judge_scores if s is not None)
    if not vals:
        raise ValueError("judge_scores 不能为空")
    if len(vals) > 2 * trim:
        vals = vals[trim : len(vals) - trim]
    return _round_half_up(sum(vals) / len(vals), ndigits)


@dataclass(frozen=True)
class JudgeDispersion:
    n: int
    mean: float  # 真实聚合分（简单平均）
    median: float
    minimum: float
    maximum: float
    spread: float  # max - min
    std: float  # 总体标准差
    base_estimate: float  # 估计的大模型基准分（用中位数，抗个别宽松评委）
    n_consensus: int  # 与基准在容差内一致的评委数（≈未调关注度）
    n_adjusted: int  # 偏离基准的评委数（≈调了关注度）
    most_deviant_index: int  # 偏离均值最大的评委（0-based）
    most_deviant_value: float

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


def analyze_dispersion(judge_scores: Sequence[float], *, tol: float = 0.1) -> JudgeDispersion:
    """分析评委分布，分离"大模型基准分"与"关注度微扰"。

    tol：判定"与基准一致（≈未调关注度）"的容差（绝对值）。
    """
    vals = [float(s) for s in judge_scores if s is not None]
    if not vals:
        raise ValueError("judge_scores 不能为空")
    n = len(vals)
    mu = sum(vals) / n
    med = float(median(vals))
    base = med
    n_consensus = sum(1 for v in vals if abs(v - base) <= tol)
    devs = [abs(v - mu) for v in vals]
    mdi = max(range(n), key=lambda i: devs[i])
    return JudgeDispersion(
        n=n,
        mean=_round_half_up(mu, 2),
        median=_round_half_up(med, 2),
        minimum=min(vals),
        maximum=max(vals),
        spread=_round_half_up(max(vals) - min(vals), 2),
        std=_round_half_up(pstdev(vals) if n > 1 else 0.0, 4),
        base_estimate=_round_half_up(base, 2),
        n_consensus=n_consensus,
        n_adjusted=n - n_consensus,
        most_deviant_index=mdi,
        most_deviant_value=vals[mdi],
    )


def predicted_mean_from_base(
    base_score: float,
    *,
    profile: Optional[TenderScoringProfile] = None,
    adjustment_offsets: Optional[Sequence[float]] = None,
    ndigits: int = 2,
) -> float:
    """由"大模型基准分 + 各评委关注度偏移"重建评委均分（用于建模/反演）。

    adjustment_offsets 为各评委相对基准的偏移；缺省（多数评委不调）即返回基准分本身。
    """
    base = float(base_score)
    offsets = [float(o) for o in (adjustment_offsets or [])]
    if not offsets:
        return _round_half_up(base, ndigits)
    scores = [base + o for o in offsets]
    return _round_half_up(sum(scores) / len(scores), ndigits)


class JudgeAggregationError(ValueError):
    """按标评委评分聚合失败。"""


@dataclass(frozen=True)
class JudgeScoreInput:
    judge_id: str
    item_scores: Dict[str, float]
    comment: str = ""
    metadata: Dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class JudgeItemAggregation:
    item_id: str
    name: str
    max_score: float
    score_count: int
    missing_judge_ids: Tuple[str, ...]
    average_score: float
    median_score: float
    min_score: float
    max_observed_score: float
    score_spread: float
    normalized_score: float
    legacy_dimension_refs: Tuple[str, ...]


@dataclass(frozen=True)
class JudgeAggregationReport:
    tender_id: str
    tender_name: str
    version: str
    score_scale: float
    status: str
    judge_count: int
    item_aggregations: Tuple[JudgeItemAggregation, ...]
    total_average_score: float
    total_normalized_score: float
    missing_item_scores: Tuple[str, ...]
    high_dispersion_item_ids: Tuple[str, ...]
    unknown_item_ids: Tuple[str, ...]
    coverage: Dict[str, object]
    summary: Dict[str, object]


def _round_metric(value: float, ndigits: int = 4) -> float:
    return _round_half_up(float(value), ndigits)


def _coerce_judge_score(raw: JudgeScoreInput | Mapping[str, object]) -> JudgeScoreInput:
    if isinstance(raw, JudgeScoreInput):
        return raw
    if not isinstance(raw, Mapping):
        raise JudgeAggregationError(f"评委评分必须是 JudgeScoreInput 或 dict: {raw!r}")

    judge_id = str(raw.get("judge_id", "")).strip()
    if not judge_id:
        raise JudgeAggregationError("judge_id 不得为空")

    raw_item_scores = raw.get("item_scores", {})
    if not isinstance(raw_item_scores, Mapping):
        raise JudgeAggregationError(f"{judge_id}.item_scores 必须是对象")

    metadata = raw.get("metadata") or {}
    if not isinstance(metadata, Mapping):
        raise JudgeAggregationError(f"{judge_id}.metadata 必须是对象")

    return JudgeScoreInput(
        judge_id=judge_id,
        item_scores={str(k): v for k, v in raw_item_scores.items()},
        comment=str(raw.get("comment", "")),
        metadata=dict(metadata),
    )


def _coerce_score(value: object, *, judge_id: str, item_id: str, max_score: float) -> float:
    try:
        score = float(value)
    except (TypeError, ValueError) as exc:
        raise JudgeAggregationError(f"{judge_id}.{item_id} 分数必须是数字") from exc

    if not isfinite(score):
        raise JudgeAggregationError(f"{judge_id}.{item_id} 分数必须是有限数字")
    if score < 0:
        raise JudgeAggregationError(f"{judge_id}.{item_id} 分数不得小于 0")
    if score > max_score:
        raise JudgeAggregationError(f"{judge_id}.{item_id} 分数 {score} 超过评分项满分 {max_score}")
    return score


def _json_ready(value: Any) -> Any:
    if isinstance(value, tuple):
        return [_json_ready(item) for item in value]
    if isinstance(value, list):
        return [_json_ready(item) for item in value]
    if isinstance(value, dict):
        return {str(k): _json_ready(v) for k, v in value.items()}
    return value


def aggregate_judge_scores(
    profile: TenderProfile,
    judge_scores: list[JudgeScoreInput | dict],
) -> JudgeAggregationReport:
    """按 TenderProfile.scoring_items 聚合多评委评分，不执行判废或人工裁决。"""
    try:
        validate_tender_profile(profile)
        target_mapping = build_target_mapping(profile)
    except TenderProfileValidationError as exc:
        raise JudgeAggregationError(f"TenderProfile 校验失败: {exc}") from exc
    except Exception as exc:
        raise JudgeAggregationError(f"target mapping 构建失败: {exc}") from exc

    if not isinstance(judge_scores, list) or not judge_scores:
        raise JudgeAggregationError("judge_scores 必须是非空列表")

    normalized_inputs = [_coerce_judge_score(raw) for raw in judge_scores]
    judge_ids = [item.judge_id for item in normalized_inputs]
    if len(set(judge_ids)) != len(judge_ids):
        raise JudgeAggregationError("judge_id 不得重复")

    items_by_id = {item.item_id: item for item in profile.scoring_items}
    scores_by_item: Dict[str, list[tuple[str, float]]] = {
        item.item_id: [] for item in profile.scoring_items
    }
    unknown_item_ids: list[str] = []
    unknown_seen: set[str] = set()

    for judge_score in normalized_inputs:
        for raw_item_id, raw_score in judge_score.item_scores.items():
            item_id = str(raw_item_id)
            item = items_by_id.get(item_id)
            if item is None:
                if item_id not in unknown_seen:
                    unknown_item_ids.append(item_id)
                    unknown_seen.add(item_id)
                continue
            score = _coerce_score(
                raw_score,
                judge_id=judge_score.judge_id,
                item_id=item_id,
                max_score=float(item.max_score),
            )
            scores_by_item[item_id].append((judge_score.judge_id, score))

    item_aggregations: list[JudgeItemAggregation] = []
    missing_item_scores: list[str] = []
    high_dispersion_item_ids: list[str] = []
    has_missing_judge_score = False

    for item in profile.scoring_items:
        item_scores = scores_by_item[item.item_id]
        scoring_judge_ids = {judge_id for judge_id, _ in item_scores}
        missing_judge_ids = tuple(
            judge_id for judge_id in judge_ids if judge_id not in scoring_judge_ids
        )
        has_missing_judge_score = has_missing_judge_score or bool(missing_judge_ids)

        observed_scores = [score for _, score in item_scores]
        if observed_scores:
            average_score = sum(observed_scores) / len(observed_scores)
            median_score = float(median(observed_scores))
            min_score = min(observed_scores)
            max_observed_score = max(observed_scores)
            score_spread = max_observed_score - min_score
            normalized_score = average_score / float(item.max_score)
            if score_spread / float(item.max_score) >= 0.4:
                high_dispersion_item_ids.append(item.item_id)
        else:
            missing_item_scores.append(item.item_id)
            average_score = 0.0
            median_score = 0.0
            min_score = 0.0
            max_observed_score = 0.0
            score_spread = 0.0
            normalized_score = 0.0

        item_aggregations.append(
            JudgeItemAggregation(
                item_id=item.item_id,
                name=item.name,
                max_score=float(item.max_score),
                score_count=len(observed_scores),
                missing_judge_ids=missing_judge_ids,
                average_score=_round_metric(average_score),
                median_score=_round_metric(median_score),
                min_score=_round_metric(min_score),
                max_observed_score=_round_metric(max_observed_score),
                score_spread=_round_metric(score_spread),
                normalized_score=_round_metric(normalized_score),
                legacy_dimension_refs=tuple(str(ref) for ref in item.legacy_dimension_refs),
            )
        )

    total_average_score = sum(item.average_score for item in item_aggregations)
    total_normalized_score = (
        total_average_score / float(profile.score_scale) if float(profile.score_scale) > 0 else 0.0
    )
    status = (
        "warning"
        if missing_item_scores
        or has_missing_judge_score
        or high_dispersion_item_ids
        or unknown_item_ids
        else "pass"
    )

    expected_score_count = len(profile.scoring_items) * len(judge_ids)
    provided_score_count = sum(item.score_count for item in item_aggregations)
    target_mapping_dict = target_mapping_to_dict(target_mapping)
    coverage = {
        "item_count": len(profile.scoring_items),
        "judge_count": len(judge_ids),
        "expected_score_count": expected_score_count,
        "provided_score_count": provided_score_count,
        "missing_score_count": expected_score_count - provided_score_count,
        "coverage_ratio": _round_metric(
            provided_score_count / expected_score_count if expected_score_count else 0.0
        ),
        "target_mapping": target_mapping_dict["coverage"],
    }
    summary = {
        "status": status,
        "message": "评委评分聚合完成，未执行判废、否决或人工裁决。",
        "missing_item_score_count": len(missing_item_scores),
        "unknown_item_count": len(unknown_item_ids),
        "high_dispersion_item_count": len(high_dispersion_item_ids),
    }

    return JudgeAggregationReport(
        tender_id=profile.tender_id,
        tender_name=profile.tender_name,
        version=profile.version,
        score_scale=float(profile.score_scale),
        status=status,
        judge_count=len(judge_ids),
        item_aggregations=tuple(item_aggregations),
        total_average_score=_round_metric(total_average_score),
        total_normalized_score=_round_metric(total_normalized_score),
        missing_item_scores=tuple(missing_item_scores),
        high_dispersion_item_ids=tuple(high_dispersion_item_ids),
        unknown_item_ids=tuple(unknown_item_ids),
        coverage=coverage,
        summary=summary,
    )


def aggregate_judge_scores_from_dict(
    profile: TenderProfile,
    payload: dict,
) -> JudgeAggregationReport:
    if not isinstance(payload, dict):
        raise JudgeAggregationError("payload 必须是 dict")
    raw_scores = payload.get("judge_scores", payload.get("scores"))
    if raw_scores is None:
        raise JudgeAggregationError("payload 缺少 judge_scores")
    if not isinstance(raw_scores, list):
        raise JudgeAggregationError("payload.judge_scores 必须是列表")
    return aggregate_judge_scores(profile, raw_scores)


def judge_aggregation_to_dict(report: JudgeAggregationReport) -> dict:
    if not isinstance(report, JudgeAggregationReport):
        raise JudgeAggregationError("report 必须是 JudgeAggregationReport")
    return _json_ready(asdict(report))
