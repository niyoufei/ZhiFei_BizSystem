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

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from app.engine.judge_aggregation import (
    JudgeAggregationError,
    JudgeAggregationReport,
    judge_aggregation_to_dict,
)
from app.engine.shigong_diagnostics import ShigongDiagnosis, diagnose_shigong
from app.engine.target_mapping import build_target_mapping, target_mapping_to_dict
from app.engine.tender_profile import (
    TenderProfile,
    TenderProfileValidationError,
    TenderScoringProfile,
    load_tender_profile,
    validate_tender_profile,
)

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


class TextCalibrationError(ValueError):
    """按标文本校准失败。"""


@dataclass(frozen=True)
class TextCalibrationSample:
    sample_id: str
    item_id: str
    text: str
    expected_score: float
    observed_score: float
    metadata: Dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class TextCalibrationItem:
    item_id: str
    name: str
    max_score: float
    text_present: bool
    evidence_hits: Tuple[str, ...]
    sample_count: int
    expected_average: Optional[float]
    observed_average: Optional[float]
    score_delta: Optional[float]
    calibration_status: str
    legacy_dimension_refs: Tuple[str, ...]
    notes: Tuple[str, ...]


@dataclass(frozen=True)
class TextCalibrationReport:
    tender_id: str
    tender_name: str
    version: str
    status: str
    items: Tuple[TextCalibrationItem, ...]
    sample_count: int
    text_empty: bool
    judge_summary: Dict[str, object]
    coverage: Dict[str, object]
    calibration_status_counts: Dict[str, int]
    high_delta_item_ids: Tuple[str, ...]
    missing_text_item_ids: Tuple[str, ...]
    summary: Dict[str, object]


def _round_text_metric(value: float, ndigits: int = 4) -> float:
    return round(float(value), ndigits)


def _coerce_text_calibration_sample(
    raw: TextCalibrationSample | Mapping[str, object],
) -> TextCalibrationSample:
    if isinstance(raw, TextCalibrationSample):
        return raw
    if not isinstance(raw, Mapping):
        raise TextCalibrationError(f"sample 必须是 TextCalibrationSample 或 dict: {raw!r}")

    sample_id = str(raw.get("sample_id", "")).strip()
    item_id = str(raw.get("item_id", "")).strip()
    text = str(raw.get("text", ""))
    if not sample_id:
        raise TextCalibrationError("sample_id 不得为空")
    if not item_id:
        raise TextCalibrationError("sample.item_id 不得为空")

    try:
        expected_score = float(raw["expected_score"])
        observed_score = float(raw["observed_score"])
    except KeyError as exc:
        raise TextCalibrationError(f"sample 缺少字段: {exc.args[0]}") from exc
    except (TypeError, ValueError) as exc:
        raise TextCalibrationError("sample expected_score/observed_score 必须是数字") from exc

    metadata_raw = raw.get("metadata") or {}
    if not isinstance(metadata_raw, Mapping):
        raise TextCalibrationError("sample.metadata 必须是 dict")

    return TextCalibrationSample(
        sample_id=sample_id,
        item_id=item_id,
        text=text,
        expected_score=expected_score,
        observed_score=observed_score,
        metadata=dict(metadata_raw),
    )


def _samples_by_item(
    profile: TenderProfile,
    samples: Sequence[TextCalibrationSample | Mapping[str, object]] | None,
) -> Dict[str, List[TextCalibrationSample]]:
    item_ids = {item.item_id for item in profile.scoring_items}
    by_item: Dict[str, List[TextCalibrationSample]] = {item_id: [] for item_id in item_ids}
    for raw in samples or ():
        sample = _coerce_text_calibration_sample(raw)
        if sample.item_id not in item_ids:
            raise TextCalibrationError(f"未知 sample.item_id: {sample.item_id}")
        item = next(
            profile_item
            for profile_item in profile.scoring_items
            if profile_item.item_id == sample.item_id
        )
        max_score = float(item.max_score)
        for field_name, score in (
            ("expected_score", sample.expected_score),
            ("observed_score", sample.observed_score),
        ):
            if score < 0.0 or score > max_score + 0.000001:
                raise TextCalibrationError(
                    f"{sample.sample_id}.{field_name} 超出评分项满分: {sample.item_id}"
                )
        by_item[sample.item_id].append(sample)
    return by_item


def _evidence_hits(document_text: str, requirements: Sequence[str]) -> Tuple[str, ...]:
    text = document_text.casefold()
    hits = []
    for requirement in requirements:
        req = str(requirement).strip()
        if req and req.casefold() in text:
            hits.append(req)
    return tuple(hits)


def _judge_summary(judge_report: JudgeAggregationReport | None) -> Dict[str, object]:
    if judge_report is None:
        return {}
    try:
        report = judge_aggregation_to_dict(judge_report)
    except JudgeAggregationError as exc:
        raise TextCalibrationError(f"judge_report 非法: {exc}") from exc
    return {
        "status": report.get("status"),
        "judge_count": report.get("judge_count"),
        "total_average_score": report.get("total_average_score"),
        "total_normalized_score": report.get("total_normalized_score"),
        "high_dispersion_item_ids": report.get("high_dispersion_item_ids", []),
        "unknown_item_ids": report.get("unknown_item_ids", []),
        "summary": report.get("summary", {}),
    }


def _status_counts(items: Sequence[TextCalibrationItem]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for item in items:
        counts[item.calibration_status] = counts.get(item.calibration_status, 0) + 1
    return counts


def calibrate_text_against_profile(
    profile: TenderProfile,
    document_text: str = "",
    judge_report: JudgeAggregationReport | None = None,
    samples: list[TextCalibrationSample | dict] | None = None,
) -> TextCalibrationReport:
    """按 TenderProfile 生成结构化文本校准报告，不执行裁决或模型推理。"""
    try:
        validate_tender_profile(profile)
    except TenderProfileValidationError as exc:
        raise TextCalibrationError(f"TenderProfile 校验失败: {exc}") from exc

    if not isinstance(document_text, str):
        raise TextCalibrationError("document_text 必须是 str")

    mapping = build_target_mapping(profile)
    mapping_dict = target_mapping_to_dict(mapping)
    text_empty = not document_text.strip()
    samples_for_item = _samples_by_item(profile, samples)

    items: List[TextCalibrationItem] = []
    high_delta_item_ids: List[str] = []
    missing_text_item_ids: List[str] = []

    for scoring_item in profile.scoring_items:
        item_samples = samples_for_item[scoring_item.item_id]
        hits = (
            () if text_empty else _evidence_hits(document_text, scoring_item.evidence_requirements)
        )
        notes: List[str] = []

        if text_empty:
            text_present = False
            notes.append("document_text empty; text presence recorded only")
        elif scoring_item.evidence_requirements:
            text_present = bool(hits)
            if not text_present:
                notes.append("no evidence requirement matched")
        else:
            text_present = True
            notes.append("no evidence requirements; non-empty document treated as present")

        expected_average: Optional[float] = None
        observed_average: Optional[float] = None
        score_delta: Optional[float] = None
        if item_samples:
            expected_average = _round_text_metric(
                sum(sample.expected_score for sample in item_samples) / len(item_samples)
            )
            observed_average = _round_text_metric(
                sum(sample.observed_score for sample in item_samples) / len(item_samples)
            )
            score_delta = _round_text_metric(observed_average - expected_average)

        if not text_present:
            calibration_status = "text_missing"
            missing_text_item_ids.append(scoring_item.item_id)
        elif not item_samples:
            calibration_status = "no_sample"
        elif abs(float(score_delta or 0.0)) >= float(scoring_item.max_score) * 0.2:
            high_delta_item_ids.append(scoring_item.item_id)
            calibration_status = (
                "under_supported" if float(score_delta or 0.0) > 0 else "over_claimed"
            )
        else:
            calibration_status = "aligned"

        items.append(
            TextCalibrationItem(
                item_id=scoring_item.item_id,
                name=scoring_item.name,
                max_score=float(scoring_item.max_score),
                text_present=text_present,
                evidence_hits=hits,
                sample_count=len(item_samples),
                expected_average=expected_average,
                observed_average=observed_average,
                score_delta=score_delta,
                calibration_status=calibration_status,
                legacy_dimension_refs=tuple(scoring_item.legacy_dimension_refs),
                notes=tuple(notes),
            )
        )

    counts = _status_counts(items)
    no_sample_item_ids = tuple(
        item.item_id for item in items if item.calibration_status == "no_sample"
    )
    status = (
        "warning" if missing_text_item_ids or high_delta_item_ids or no_sample_item_ids else "pass"
    )
    coverage = dict(mapping_dict["coverage"])
    coverage.update(
        {
            "text_present_item_count": sum(1 for item in items if item.text_present),
            "text_missing_item_count": len(missing_text_item_ids),
            "sampled_item_count": sum(1 for item in items if item.sample_count > 0),
            "sample_count": sum(item.sample_count for item in items),
        }
    )
    summary: Dict[str, object] = {
        "status": status,
        "item_count": len(items),
        "sample_count": sum(item.sample_count for item in items),
        "text_empty": text_empty,
        "no_sample_item_ids": list(no_sample_item_ids),
        "high_delta_item_ids": list(high_delta_item_ids),
        "missing_text_item_ids": list(missing_text_item_ids),
    }

    return TextCalibrationReport(
        tender_id=profile.tender_id,
        tender_name=profile.tender_name,
        version=profile.version,
        status=status,
        items=tuple(items),
        sample_count=sum(item.sample_count for item in items),
        text_empty=text_empty,
        judge_summary=_judge_summary(judge_report),
        coverage=coverage,
        calibration_status_counts=counts,
        high_delta_item_ids=tuple(high_delta_item_ids),
        missing_text_item_ids=tuple(missing_text_item_ids),
        summary=summary,
    )


def calibrate_text_against_profile_from_file(
    profile_path: str | Path,
    document_text: str = "",
    samples: list[TextCalibrationSample | dict] | None = None,
) -> TextCalibrationReport:
    try:
        profile = load_tender_profile(profile_path)
    except TenderProfileValidationError as exc:
        raise TextCalibrationError(f"TenderProfile 加载失败: {exc}") from exc
    return calibrate_text_against_profile(profile, document_text=document_text, samples=samples)


def text_calibration_to_dict(report: TextCalibrationReport) -> dict:
    if not isinstance(report, TextCalibrationReport):
        raise TextCalibrationError("report 必须是 TextCalibrationReport")
    return {
        "tender_id": report.tender_id,
        "tender_name": report.tender_name,
        "version": report.version,
        "status": report.status,
        "items": [
            {
                "item_id": item.item_id,
                "name": item.name,
                "max_score": float(item.max_score),
                "text_present": item.text_present,
                "evidence_hits": list(item.evidence_hits),
                "sample_count": item.sample_count,
                "expected_average": item.expected_average,
                "observed_average": item.observed_average,
                "score_delta": item.score_delta,
                "calibration_status": item.calibration_status,
                "legacy_dimension_refs": list(item.legacy_dimension_refs),
                "notes": list(item.notes),
            }
            for item in report.items
        ],
        "sample_count": report.sample_count,
        "text_empty": report.text_empty,
        "judge_summary": dict(report.judge_summary),
        "coverage": dict(report.coverage),
        "calibration_status_counts": dict(report.calibration_status_counts),
        "high_delta_item_ids": list(report.high_delta_item_ids),
        "missing_text_item_ids": list(report.missing_text_item_ids),
        "summary": dict(report.summary),
    }
