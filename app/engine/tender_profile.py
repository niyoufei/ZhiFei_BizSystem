"""
按标评分配置（Tender Scoring Profile）。

升级增量 1 · 地基层。本模块为纯新增、确定性、零外部依赖（仅标准库），
不接入核心评分主链（不修改 scorer.py / v2_scorer.py / storage.py）。

设计依据来自 4 个真实标的横向对比，核心结论：
- 评分口径随标剧变：分制（5 分 / 100 分）、分档阈值、考量项条目、评标办法都不同；
  因此评分配置必须「按标加载」，不能用一套固定维度通吃。
- 预测目标应是「单一 F 分（按本标分制）+ 档位 + 跨标归一化百分比」，而非 16 维各自分。
- 施组的「胜负含金量」由评标办法决定：综合评估法可能决定性、技术评分合理价格法常为门槛。

本模块提供：配置数据模型、加载/校验、F 分→档位、跨标归一化、字段内百分位与动态目标。
后续增量（预测目标改造 / 校准归一化 / 战略判断层）在此之上构建。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional, Sequence, Tuple

# 仓库根目录下的 config/tender_profiles/（与既有 config/qingtian_hefei_chapter_factors_v1.json 同级）
_REPO_ROOT = Path(__file__).resolve().parents[2]
TENDER_PROFILES_DIR = _REPO_ROOT / "config" / "tender_profiles"

# 评标办法（真实标中已出现的两类）
EVAL_METHOD_COMPREHENSIVE = "综合评估法"
EVAL_METHOD_TECH_REASONABLE_PRICE = "技术评分合理价格法"
EVAL_METHODS: Tuple[str, ...] = (
    EVAL_METHOD_COMPREHENSIVE,
    EVAL_METHOD_TECH_REASONABLE_PRICE,
)

# 施组在该评标办法下的「胜负权重」分类（决定是否值得在施组上砸资源）
WEIGHT_DECISIVE = "decisive"  # 决定性：价格普遍踩满，施组定胜负（案例1）
WEIGHT_COUPLED = "coupled"  # 联动：施组与价格共同决定，差距极小（案例2）
WEIGHT_GATE = "gate"  # 门槛：施组只决定能否入围，最终价格定胜负（案例3/4）
WEIGHT_TYPES: Tuple[str, ...] = (WEIGHT_DECISIVE, WEIGHT_COUPLED, WEIGHT_GATE)

# 恒定三轴：四个标都明确写「针对性、可行性、语言精练度」
QUALITY_AXES: Tuple[str, ...] = ("针对性", "可行性", "语言精练度")


class TenderProfileError(ValueError):
    """招标评分配置非法时抛出。"""


@dataclass(frozen=True)
class ScoreBand:
    """一个分档区间，例如「良好 3<F<4.5」。边界包含与否按真实标精确建模。"""

    name: str
    lower: float
    upper: float
    lower_inclusive: bool = False
    upper_inclusive: bool = True

    def contains(self, score: float) -> bool:
        lo_ok = score >= self.lower if self.lower_inclusive else score > self.lower
        hi_ok = score <= self.upper if self.upper_inclusive else score < self.upper
        return bool(lo_ok and hi_ok)


@dataclass(frozen=True)
class HardLines:
    """硬红线：命中即扣分 / 判废 / 不得分，属确定性闸门（供后续 preflight 增量复用）。"""

    max_pages: Optional[int] = None
    require_site_plan: bool = False  # 是否必须含「施工总平面布置图」（案例3/4 为 True）
    multiple_shigong_rejected: bool = True  # 提供两份及以上=备选方案=判废
    zero_if_not_provided: bool = True  # 未提供 / 无任何针对性、可行性=不得分
    format_hints: Tuple[str, ...] = ()  # 排版要求（说明性，不直接判废）


@dataclass(frozen=True)
class TenderScoringProfile:
    """单个招标项目的施组评分口径。"""

    tender_id: str
    tender_name: str
    eval_method: str
    shigong_max_score: float  # 施组满分：5 或 100
    bands: Tuple[ScoreBand, ...]  # 分档，建议从低到高
    considerations: Tuple[str, ...]  # 考量项（按标，可增删）
    hard_lines: HardLines
    shigong_weight_type: str  # decisive / coupled / gate
    quality_axes: Tuple[str, ...] = QUALITY_AXES
    score_composition: Optional[Dict[str, float]] = None  # 技术/商务/报价 分值构成
    judge_count_observed: Optional[int] = None  # 实测评委数（5 或 7，现场定）
    source: str = ""  # 出处（招标编号 / 评标一览表）
    notes: str = ""  # 真实数据观察备注

    # ---- 派生计算（确定性） ----
    def band_of(self, score: float) -> Optional[str]:
        """返回 F 分所属档位名；落在任何档外（如 0 分=未提供）返回 None。"""
        s = float(score)
        for band in self.bands:
            if band.contains(s):
                return band.name
        return None

    def top_band_name(self) -> Optional[str]:
        """最高档名（按区间上界，再按下界取最大）。"""
        if not self.bands:
            return None
        return max(self.bands, key=lambda b: (b.upper, b.lower)).name

    def is_top_band(self, score: float) -> bool:
        """该分是否落在最高档（如「优秀」）。"""
        b = self.band_of(score)
        return b is not None and b == self.top_band_name()

    def normalize(self, score: float) -> float:
        """归一化到 0..1（= F / 满分），用于跨标可比。"""
        if self.shigong_max_score <= 0:
            return 0.0
        return max(0.0, min(1.0, float(score) / float(self.shigong_max_score)))


# ==================== 模块级便捷函数 ====================


def score_to_band(profile: TenderScoringProfile, score: float) -> Optional[str]:
    return profile.band_of(score)


def normalize_score(profile: TenderScoringProfile, score: float) -> float:
    return profile.normalize(score)


def percentile_in_field(
    score: float,
    field_scores: Sequence[float],
    *,
    higher_is_better: bool = True,
) -> float:
    """该分在「同标竞争对手分布」中的百分位（0..1）。1.0 表示全场最前。"""
    vals = [float(s) for s in field_scores if s is not None]
    if not vals:
        return 0.0
    if higher_is_better:
        n = sum(1 for s in vals if s <= float(score))
    else:
        n = sum(1 for s in vals if s >= float(score))
    return n / len(vals)


def field_target_score(
    field_scores: Sequence[float],
    *,
    quantile: float = 1.0,
) -> Optional[float]:
    """动态目标分：默认取全场最高（quantile=1.0），即「冲到全场最前」。"""
    vals = sorted(float(s) for s in field_scores if s is not None)
    if not vals:
        return None
    if quantile >= 1.0:
        return vals[-1]
    if quantile <= 0.0:
        return vals[0]
    idx = int(round(quantile * (len(vals) - 1)))
    return vals[max(0, min(len(vals) - 1, idx))]


def summarize_field(field_scores: Sequence[float]) -> Dict[str, float]:
    """同标竞争分布概览：count / min / max / mean。"""
    vals = [float(s) for s in field_scores if s is not None]
    if not vals:
        return {}
    return {
        "count": float(len(vals)),
        "min": min(vals),
        "max": max(vals),
        "mean": sum(vals) / len(vals),
    }


# ==================== 加载与校验 ====================


def _parse_band(raw: object) -> ScoreBand:
    if not isinstance(raw, dict):
        raise TenderProfileError(f"分档必须是对象: {raw!r}")
    try:
        return ScoreBand(
            name=str(raw["name"]),
            lower=float(raw["lower"]),
            upper=float(raw["upper"]),
            lower_inclusive=bool(raw.get("lower_inclusive", False)),
            upper_inclusive=bool(raw.get("upper_inclusive", True)),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise TenderProfileError(f"分档配置非法: {raw!r} ({exc})") from exc


def profile_from_dict(data: object) -> TenderScoringProfile:
    """从已解析的 JSON 对象构造 profile，并做必填字段与枚举校验。"""
    if not isinstance(data, dict):
        raise TenderProfileError("配置必须是 JSON 对象")

    required = [
        "tender_id",
        "tender_name",
        "eval_method",
        "shigong_max_score",
        "bands",
        "considerations",
        "shigong_weight_type",
    ]
    missing = [k for k in required if k not in data or data.get(k) in (None, "", [])]
    if missing:
        raise TenderProfileError("缺少必填字段: " + ", ".join(missing))

    eval_method = str(data["eval_method"])
    if eval_method not in EVAL_METHODS:
        raise TenderProfileError(f"未知评标办法: {eval_method}")

    weight_type = str(data["shigong_weight_type"])
    if weight_type not in WEIGHT_TYPES:
        raise TenderProfileError(f"未知施组胜负权重类型: {weight_type}")

    try:
        max_score = float(data["shigong_max_score"])
    except (TypeError, ValueError) as exc:
        raise TenderProfileError("shigong_max_score 必须是数字") from exc
    if max_score <= 0:
        raise TenderProfileError("shigong_max_score 必须为正数")

    bands = tuple(_parse_band(b) for b in data["bands"])
    if not bands:
        raise TenderProfileError("bands 不能为空")

    hl_raw = data.get("hard_lines") or {}
    if not isinstance(hl_raw, dict):
        raise TenderProfileError("hard_lines 必须是对象")
    max_pages_raw = hl_raw.get("max_pages")
    hard_lines = HardLines(
        max_pages=(int(max_pages_raw) if max_pages_raw is not None else None),
        require_site_plan=bool(hl_raw.get("require_site_plan", False)),
        multiple_shigong_rejected=bool(hl_raw.get("multiple_shigong_rejected", True)),
        zero_if_not_provided=bool(hl_raw.get("zero_if_not_provided", True)),
        format_hints=tuple(str(x) for x in (hl_raw.get("format_hints") or [])),
    )

    axes = tuple(str(x) for x in (data.get("quality_axes") or QUALITY_AXES))

    comp_raw = data.get("score_composition")
    composition = (
        {str(k): float(v) for k, v in comp_raw.items()} if isinstance(comp_raw, dict) else None
    )

    jc_raw = data.get("judge_count_observed")

    return TenderScoringProfile(
        tender_id=str(data["tender_id"]),
        tender_name=str(data["tender_name"]),
        eval_method=eval_method,
        shigong_max_score=max_score,
        bands=bands,
        considerations=tuple(str(x) for x in data["considerations"]),
        hard_lines=hard_lines,
        shigong_weight_type=weight_type,
        quality_axes=axes,
        score_composition=composition,
        judge_count_observed=(int(jc_raw) if jc_raw is not None else None),
        source=str(data.get("source", "")),
        notes=str(data.get("notes", "")),
    )


def load_profile(path: Path | str) -> TenderScoringProfile:
    p = Path(path)
    if not p.exists():
        raise TenderProfileError(f"配置文件不存在: {p}")
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise TenderProfileError(f"配置文件 JSON 解析失败: {p} ({exc})") from exc
    return profile_from_dict(data)


def load_all_profiles(
    directory: Path | str = TENDER_PROFILES_DIR,
) -> Dict[str, TenderScoringProfile]:
    """加载目录下全部 *.json 标书配置，返回 {tender_id: profile}。目录不存在则返回空。"""
    d = Path(directory)
    out: Dict[str, TenderScoringProfile] = {}
    if not d.exists():
        return out
    for fp in sorted(d.glob("*.json")):
        prof = load_profile(fp)
        out[prof.tender_id] = prof
    return out


def get_profile(
    tender_id: str,
    directory: Path | str = TENDER_PROFILES_DIR,
) -> Optional[TenderScoringProfile]:
    return load_all_profiles(directory).get(str(tender_id))
