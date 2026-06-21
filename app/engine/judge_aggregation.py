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

from dataclasses import asdict, dataclass
from decimal import ROUND_HALF_UP, Decimal
from statistics import median, pstdev
from typing import Dict, Optional, Sequence

from app.engine.tender_profile import TenderScoringProfile


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
