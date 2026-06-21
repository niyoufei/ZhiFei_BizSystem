"""
离线文本校准器（Text Calibration）。

升级增量 8 · 数据驱动。把"施组诊断特征 → 真实青天分"学成一个可插拔的校准器，
用来替换 target_mapping 的线性基线（其 calibrator 钩子已留）。

为什么是这层：主干（增量1-7）就位后，预测精度的天花板由"施组文本↔真实分"
配对数据决定。本层把这条数据→模型的链路做成确定性、可留一验证的组件。

与既有 app/engine/calibrator.py 的关系：
- calibrator.py 在「V2 评分 49 维特征」上做 ridge/isotonic（需运行 v2_scorer）；
- 本模块在「增量6 文本诊断特征」上做轻量 1D 校准，喂给 target_mapping 钩子；
  二者互补。等 V2 特征可用时，同一"拟合+留一+闸门"范式可平移。

纪律：纯新增、确定性、零外部依赖；不接核心评分主链；不写库。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from app.engine.shigong_diagnostics import ShigongDiagnosis, diagnose_shigong
from app.engine.tender_profile import TenderScoringProfile

# composite 质量分（0-100）各项权重：针对性/可行性/覆盖各 0.3，精练度 0.1
_W_SPECIFICITY = 0.30
_W_LANDING = 0.30
_W_COVERAGE = 0.30
_W_CONCISENESS = 0.10


def feature_vector(diagnosis: ShigongDiagnosis) -> Dict[str, float]:
    """从诊断抽取校准特征。"""
    per_k = max(1e-9, diagnosis.char_count / 1000.0)
    return {
        "specificity": diagnosis.axes.specificity,
        "landing": diagnosis.axes.landing,
        "conciseness": diagnosis.axes.conciseness,
        "coverage_rate": diagnosis.coverage_rate,
        "hard_per_k": round(diagnosis.hard_element_count / per_k, 3),
        "generic_per_k": round(diagnosis.generic_phrase_count / per_k, 3),
        "project_term_hits": float(diagnosis.project_term_hits),
    }


def composite_from_diagnosis(diagnosis: ShigongDiagnosis) -> float:
    """把三轴 + 覆盖融成 0-100 的内部质量分（可作为 target_mapping 的 internal 输入）。"""
    a = diagnosis.axes
    q = (
        _W_SPECIFICITY * a.specificity
        + _W_LANDING * a.landing
        + _W_COVERAGE * diagnosis.coverage_rate
        + _W_CONCISENESS * a.conciseness
    )
    return round(100.0 * max(0.0, min(1.0, q)), 3)


def composite_quality(
    text: str,
    profile: TenderScoringProfile,
    *,
    project_terms: Optional[Sequence[str]] = None,
) -> float:
    d = diagnose_shigong(text, profile, project_terms=project_terms)
    return composite_from_diagnosis(d)


@dataclass(frozen=True)
class Calibrator1D:
    """一维线性校准：归一化真实分 ≈ slope * composite + intercept。"""

    slope: float
    intercept: float
    n_samples: int

    def predict_normalized(self, composite_0_100: float) -> float:
        v = self.slope * float(composite_0_100) + self.intercept
        return max(0.0, min(1.0, v))

    def as_target_calibrator(self) -> Callable[[float, TenderScoringProfile], float]:
        """返回符合 target_mapping 钩子签名的函数：(internal_0_100, profile) -> F 分。"""

        def _cal(internal_0_100: float, profile: TenderScoringProfile) -> float:
            norm = self.predict_normalized(internal_0_100)
            return max(0.0, min(float(profile.shigong_max_score), norm * profile.shigong_max_score))

        return _cal


def fit_calibrator_1d(samples: Sequence[Tuple[float, float]]) -> Calibrator1D:
    """最小二乘拟合。samples=[(composite_0_100, normalized_real_f), ...]。

    退化情形（样本<2 或 composite 无方差）：slope=0，intercept=真实分均值。
    """
    pts = [(float(x), float(y)) for x, y in samples]
    n = len(pts)
    if n == 0:
        return Calibrator1D(slope=0.0, intercept=0.0, n_samples=0)
    xbar = sum(x for x, _ in pts) / n
    ybar = sum(y for _, y in pts) / n
    var_x = sum((x - xbar) ** 2 for x, _ in pts)
    if n < 2 or var_x <= 1e-12:
        return Calibrator1D(slope=0.0, intercept=round(ybar, 6), n_samples=n)
    cov_xy = sum((x - xbar) * (y - ybar) for x, y in pts)
    slope = cov_xy / var_x
    intercept = ybar - slope * xbar
    return Calibrator1D(slope=round(slope, 8), intercept=round(intercept, 8), n_samples=n)


def pearson_correlation(samples: Sequence[Tuple[float, float]]) -> float:
    """composite 与 归一化真实分 的皮尔逊相关。≈0 说明特征对真实分无预测力。"""
    pts = [(float(x), float(y)) for x, y in samples]
    n = len(pts)
    if n < 2:
        return 0.0
    xbar = sum(x for x, _ in pts) / n
    ybar = sum(y for _, y in pts) / n
    sx = sum((x - xbar) ** 2 for x, _ in pts)
    sy = sum((y - ybar) ** 2 for _, y in pts)
    if sx <= 1e-12 or sy <= 1e-12:
        return 0.0
    sxy = sum((x - xbar) * (y - ybar) for x, y in pts)
    return round(sxy / ((sx**0.5) * (sy**0.5)), 4)


def _baseline_normalized(composite_0_100: float) -> float:
    """当前线性基线：composite/100。"""
    return max(0.0, min(1.0, float(composite_0_100) / 100.0))


def leave_one_out_mae(samples: Sequence[Tuple[float, float]]) -> Dict[str, float]:
    """留一法对比"校准器 vs 线性基线"的归一化 MAE，并给出部署闸门。"""
    pts = [(float(x), float(y)) for x, y in samples]
    n = len(pts)
    if n < 3:
        return {"n": float(n), "insufficient": 1.0}
    model_errs: List[float] = []
    base_errs: List[float] = []
    for i in range(n):
        train = pts[:i] + pts[i + 1 :]
        cal = fit_calibrator_1d(train)
        x_i, y_i = pts[i]
        model_errs.append(abs(cal.predict_normalized(x_i) - y_i))
        base_errs.append(abs(_baseline_normalized(x_i) - y_i))
    model_mae = sum(model_errs) / n
    base_mae = sum(base_errs) / n
    return {
        "n": float(n),
        "insufficient": 0.0,
        "model_mae": round(model_mae, 5),
        "baseline_mae": round(base_mae, 5),
        "gate_pass": 1.0 if model_mae <= base_mae + 1e-9 else 0.0,
    }


@dataclass(frozen=True)
class CalibrationSample:
    """一条校准样本：施组文本 + 本标 profile + 真实 F 分。"""

    text: str
    profile: TenderScoringProfile
    real_f: float
    project_terms: Tuple[str, ...] = ()
    label: str = ""


def build_samples(records: Sequence[CalibrationSample]) -> List[Tuple[float, float]]:
    """把（文本, profile, 真实分）记录转成 (composite, normalized_real) 训练点。"""
    out: List[Tuple[float, float]] = []
    for r in records:
        comp = composite_quality(r.text, r.profile, project_terms=list(r.project_terms))
        norm_real = r.profile.normalize(r.real_f)
        out.append((comp, norm_real))
    return out
