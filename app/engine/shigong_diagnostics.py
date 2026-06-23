"""
编制提分引擎（Shigong Diagnostics）。

升级增量 6 · 方向 B（指导编制）的正出口。确定性引擎，不含 LLM（改写交给增量 7）。

依据 4 标实测：真实评分轴是「针对性、可行性、语言精练度」+ 6/10 项内容考量。
本模块对一份施组做确定性诊断，产出两样东西：
1) 高分样板拆解：把高分施组（如长春 4.44）按三轴 + 考量项拆出"凭什么拿高分"；
2) ROI 优化清单：把弱项转成可执行项，按"预期提分"排序、绑定到考量项。

三轴信号（确定性代理，非青天分本身；青天分由后续校准给）：
- 针对性 specificity：项目专属术语密度 vs 空泛套话密度。
- 可行性 landing：硬要素密度（量化参数 / 频次 / 责任岗位 / 验收动作）。
- 语言精练度 conciseness：空泛套话占比的反向。

纯新增、确定性、零外部依赖；不接核心评分主链。
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from app.engine.target_mapping import build_target_mapping, target_mapping_to_dict
from app.engine.tender_preflight import (
    TenderPreflightError,
    run_tender_preflight,
    tender_preflight_to_dict,
)
from app.engine.tender_profile import (
    TenderProfile,
    TenderProfileValidationError,
    TenderScoringProfile,
    load_tender_profile,
    validate_tender_profile,
)

# 量化参数（数字+单位）
_PARAM_RE = re.compile(
    r"\d+(?:\.\d+)?\s*"
    r"(?:%|‰|mm|cm|m³|m²|m2|m3|m|米|km|kg|公斤|t|吨|h|小时|min|分钟|s|秒|"
    r"天|日|次|根|台|套|组|个|处|名|人|MPa|kN|kV|kW|℃|度|元)"
)
# 频次
_FREQ_RE = re.compile(
    r"(?:每[日天周月班次]|每\s*\d+\s*[日天周月小时]|定期|实时|逐[日层道]|不少于\s*\d+\s*次)"
)
# 责任岗位
_ROLE_TERMS = (
    "项目经理",
    "项目副经理",
    "技术负责人",
    "生产经理",
    "施工员",
    "质检员",
    "安全员",
    "材料员",
    "资料员",
    "测量员",
    "试验员",
    "机管员",
    "专职安全员",
    "班组长",
    "专职",
)
# 验收 / 检查动作
_ACCEPT_TERMS = (
    "验收",
    "旁站",
    "报验",
    "隐蔽验收",
    "三检",
    "复检",
    "实测实量",
    "检验批",
    "签认",
    "交接检",
    "检查记录",
)
# 空泛套话
_GENERIC_TERMS = (
    "严格按照",
    "严格执行",
    "确保",
    "加强",
    "认真",
    "高度重视",
    "精心组织",
    "精心施工",
    "全面落实",
    "积极",
    "努力",
    "坚决",
    "务必",
    "切实",
    "科学组织",
    "合理安排",
    "确保万无一失",
)

# 考量项锚词解析用的前后缀
_PREFIX_STRIPS = (
    "针对工程项目",
    "拟投入的主要",
    "拟采用的",
    "拟投入的",
    "针对",
    "涉及",
    "确保",
    "主要",
)
_SUFFIX_STRIPS = (
    "的保障体系与措施",
    "的技术组织措施",
    "的管理体系与措施",
    "的保障体系",
    "的技术措施",
    "保障体系与措施",
    "技术组织措施",
    "管理体系与措施",
    "安排计划",
    "的措施",
    "计划",
    "措施",
)


def _consideration_anchors(consideration: str) -> List[str]:
    """从考量项文本解析出用于覆盖检测的锚词。"""
    core = str(consideration or "").strip()
    for pre in sorted(_PREFIX_STRIPS, key=len, reverse=True):
        if core.startswith(pre):
            core = core[len(pre) :]
            break
    for suf in sorted(_SUFFIX_STRIPS, key=len, reverse=True):
        if core.endswith(suf):
            core = core[: len(core) - len(suf)]
            break
    parts = [p for chunk in core.split("、") for p in chunk.split("与")]
    anchors = [p for p in parts if len(p) >= 2]
    if not anchors and core:
        anchors = [core]
    extra: List[str] = []
    for a in anchors:
        if "、" in a:
            extra.append(a.replace("、", ""))
        if a.startswith("工程") and len(a) > 3:
            extra.append(a[2:])
    return list(dict.fromkeys(anchors + extra))


def _count_terms(text: str, terms: Sequence[str]) -> int:
    return sum(text.count(t) for t in terms)


@dataclass(frozen=True)
class AxisScores:
    specificity: float  # 针对性 0..1
    landing: float  # 可行性/落地 0..1
    conciseness: float  # 语言精练度 0..1

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class ConsiderationFinding:
    name: str
    addressed: bool
    match_count: int

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class ShigongDiagnosis:
    tender_id: str
    char_count: int
    axes: AxisScores
    considerations: Tuple[ConsiderationFinding, ...]
    coverage_rate: float
    hard_element_count: int
    generic_phrase_count: int
    project_term_hits: int

    def to_dict(self) -> Dict[str, object]:
        d = asdict(self)
        d["axes"] = self.axes.to_dict()
        d["considerations"] = [c.to_dict() for c in self.considerations]
        return d


def diagnose_shigong(
    text: str,
    profile: TenderScoringProfile,
    *,
    project_terms: Optional[Sequence[str]] = None,
) -> ShigongDiagnosis:
    """对一份施组按三轴 + 考量项做确定性诊断。"""
    src = str(text or "")
    chars = max(1, len(src))

    n_param = len(_PARAM_RE.findall(src))
    n_freq = len(_FREQ_RE.findall(src))
    n_role = _count_terms(src, _ROLE_TERMS)
    n_accept = _count_terms(src, _ACCEPT_TERMS)
    hard = n_param + n_freq + n_role + n_accept
    generic = _count_terms(src, _GENERIC_TERMS)
    proj_terms = list(project_terms or [])
    proj_hits = _count_terms(src, proj_terms) if proj_terms else 0

    # 密度（每 1000 字）
    per_k = chars / 1000.0
    hard_density = hard / per_k if per_k else 0.0
    generic_density = generic / per_k if per_k else 0.0

    # 三轴（带文档化常量的有界代理）
    landing = min(1.0, hard_density / 8.0)  # ~8 处硬要素/千字 视为满
    conciseness = min(1.0, max(0.0, 1.0 - generic_density / 6.0))  # 空泛≥6/千字 拉满扣分
    if proj_terms:
        proj_density = proj_hits / per_k if per_k else 0.0
        specificity = min(1.0, proj_density / 6.0)
    else:
        # 无显式术语时的回退代理：落地高 + 空泛低 ≈ 针对性
        specificity = min(1.0, max(0.0, 0.6 * landing + 0.4 * conciseness))

    findings: List[ConsiderationFinding] = []
    addressed_n = 0
    for c in profile.considerations:
        anchors = _consideration_anchors(c)
        cnt = sum(src.count(a) for a in anchors)
        addressed = cnt > 0
        addressed_n += 1 if addressed else 0
        findings.append(ConsiderationFinding(name=c, addressed=addressed, match_count=cnt))

    total = len(profile.considerations) or 1
    coverage = addressed_n / total

    return ShigongDiagnosis(
        tender_id=profile.tender_id,
        char_count=len(src),
        axes=AxisScores(
            specificity=round(specificity, 3),
            landing=round(landing, 3),
            conciseness=round(conciseness, 3),
        ),
        considerations=tuple(findings),
        coverage_rate=round(coverage, 3),
        hard_element_count=hard,
        generic_phrase_count=generic,
        project_term_hits=proj_hits,
    )


@dataclass(frozen=True)
class OptimizationItem:
    consideration: Optional[str]
    issue: str  # MISSING / LOW_SPECIFICITY / WEAK_LANDING / VERBOSE
    severity: str
    action: str
    expected_gain: float  # 启发式 ROI 0..1
    priority: int  # 1 = 最高

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


def build_optimization_checklist(
    diagnosis: ShigongDiagnosis,
    profile: TenderScoringProfile,
) -> List[OptimizationItem]:
    """由诊断生成按预期提分排序的优化清单（绑定考量项）。"""
    raw: List[Tuple[float, str, Optional[str], str, str]] = []

    # 缺失考量项（每个一项）
    for f in diagnosis.considerations:
        if not f.addressed:
            raw.append(
                (
                    0.9,
                    "MISSING",
                    f.name,
                    "high",
                    f"未覆盖考量项「{f.name}」：新增针对本工程的实质内容（含做法+硬要素）。",
                )
            )
    # 针对性（最大杠杆）
    if diagnosis.axes.specificity < 0.5:
        raw.append(
            (
                0.8,
                "LOW_SPECIFICITY",
                None,
                "high",
                "针对性不足：把通用措施改写为针对本工程的具体措施（结合本项目特点、约束、场景）。",
            )
        )
    # 可行性 / 落地
    if diagnosis.axes.landing < 0.5:
        raw.append(
            (
                0.6,
                "WEAK_LANDING",
                None,
                "medium",
                "措施缺硬要素：每条关键措施补足量化参数/频次/责任岗位/验收动作中至少2项。",
            )
        )
    # 语言精练度
    if diagnosis.axes.conciseness < 0.5:
        raw.append(
            (
                0.3,
                "VERBOSE",
                None,
                "low",
                "空泛表述偏多：删减「确保/严格按照/加强」等套话，替换为可量化、可验证的动作。",
            )
        )

    raw.sort(key=lambda x: x[0], reverse=True)
    return [
        OptimizationItem(
            consideration=cons,
            issue=issue,
            severity=sev,
            action=action,
            expected_gain=gain,
            priority=i + 1,
        )
        for i, (gain, issue, cons, sev, action) in enumerate(raw)
    ]


@dataclass(frozen=True)
class SampleDecomposition:
    tender_id: str
    diagnosis: ShigongDiagnosis
    strengths: Tuple[str, ...]  # 高分逻辑
    summary: str

    def to_dict(self) -> Dict[str, object]:
        return {
            "tender_id": self.tender_id,
            "diagnosis": self.diagnosis.to_dict(),
            "strengths": list(self.strengths),
            "summary": self.summary,
        }


def decompose_high_score_sample(
    text: str,
    profile: TenderScoringProfile,
    *,
    project_terms: Optional[Sequence[str]] = None,
    label: str = "高分样板",
) -> SampleDecomposition:
    """把一份高分施组拆成可复用的「高分逻辑」。"""
    d = diagnose_shigong(text, profile, project_terms=project_terms)
    strengths: List[str] = []
    if d.axes.specificity >= 0.6:
        strengths.append(f"针对性强（项目术语命中 {d.project_term_hits} 次）")
    if d.axes.landing >= 0.6:
        strengths.append(f"措施落地（硬要素 {d.hard_element_count} 处）")
    addressed = sum(1 for c in d.considerations if c.addressed)
    if d.coverage_rate >= 0.8:
        strengths.append(f"考量项覆盖全面（{addressed}/{len(d.considerations)}）")
    if d.axes.conciseness >= 0.6:
        strengths.append(f"表述精炼（空泛 {d.generic_phrase_count} 处）")
    if not strengths:
        strengths.append("无突出强项：需按优化清单补强")
    summary = (
        f"{label}拆解：针对性{d.axes.specificity:.2f} / 可行性{d.axes.landing:.2f} / "
        f"精练度{d.axes.conciseness:.2f}；覆盖 {addressed}/{len(d.considerations)} 项考量。"
    )
    return SampleDecomposition(
        tender_id=profile.tender_id,
        diagnosis=d,
        strengths=tuple(strengths),
        summary=summary,
    )


class ShigongDiagnosticsError(ValueError):
    """施组合同诊断失败。"""


@dataclass(frozen=True)
class ShigongDiagnosticIssue:
    code: str
    severity: str
    message: str
    item_id: str = ""
    details: Dict[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.severity not in ("info", "warning", "error"):
            raise ValueError(f"非法 shigong diagnostic severity: {self.severity}")

    def to_dict(self) -> Dict[str, object]:
        return {
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
            "item_id": self.item_id,
            "details": dict(self.details),
        }


@dataclass(frozen=True)
class ShigongDiagnosticReport:
    tender_id: str
    tender_name: str
    version: str
    status: str
    issues: Tuple[ShigongDiagnosticIssue, ...]
    preflight_status: str
    coverage: Dict[str, object]
    unmapped_item_ids: Tuple[str, ...]
    missing_evidence_item_ids: Tuple[str, ...]
    hard_redline_count: int
    legacy_dimension_refs: Tuple[str, ...]
    summary: Dict[str, object]


def _diagnostic_issue_from_preflight(issue: object) -> ShigongDiagnosticIssue:
    details = getattr(issue, "details", {})
    return ShigongDiagnosticIssue(
        code=str(getattr(issue, "code", "")),
        severity=str(getattr(issue, "severity", "warning")),
        message=str(getattr(issue, "message", "")),
        item_id=str(getattr(issue, "item_id", "")),
        details=dict(details) if isinstance(details, dict) else {"raw_details": str(details)},
    )


def _diagnostic_issue_counts(issues: Sequence[ShigongDiagnosticIssue]) -> Dict[str, int]:
    return {
        "info": sum(1 for issue in issues if issue.severity == "info"),
        "warning": sum(1 for issue in issues if issue.severity == "warning"),
        "error": sum(1 for issue in issues if issue.severity == "error"),
    }


def _diagnostic_status(issues: Sequence[ShigongDiagnosticIssue]) -> str:
    if any(issue.severity == "error" for issue in issues):
        return "error"
    if any(issue.severity == "warning" for issue in issues):
        return "warning"
    return "pass"


def _evidence_value_covers_requirement(value: object, requirement: str) -> bool:
    req = str(requirement or "").strip()
    if not req:
        return True
    if value is True:
        return True
    if value is None or value is False:
        return False
    if isinstance(value, str) and not value.strip():
        return False

    req_folded = req.casefold()
    if isinstance(value, dict):
        for key, nested_value in value.items():
            key_text = str(key).casefold()
            if req_folded == key_text and bool(nested_value):
                return True
            if req_folded in key_text and bool(nested_value):
                return True
            if _evidence_value_covers_requirement(nested_value, req):
                return True
        return False
    if isinstance(value, (list, tuple, set, frozenset)):
        return any(_evidence_value_covers_requirement(item, req) for item in value)
    return req_folded in str(value).casefold()


def _missing_provided_evidence_requirements(
    item: object,
    provided_evidence: dict | None,
) -> Tuple[str, ...]:
    requirements = tuple(str(req) for req in getattr(item, "evidence_requirements", ()))
    if not requirements:
        return ()

    item_id = str(getattr(item, "item_id", ""))
    item_evidence = provided_evidence.get(item_id) if provided_evidence else None
    missing = []
    for requirement in requirements:
        if _evidence_value_covers_requirement(item_evidence, requirement):
            continue
        if provided_evidence and _evidence_value_covers_requirement(provided_evidence, requirement):
            continue
        missing.append(requirement)
    return tuple(missing)


def _document_missing_requirements(item: object, document_text: str) -> Tuple[str, ...]:
    src = str(document_text or "").casefold()
    return tuple(
        str(req)
        for req in getattr(item, "evidence_requirements", ())
        if str(req).strip() and str(req).casefold() not in src
    )


def run_shigong_diagnostics(
    profile: TenderProfile,
    document_text: str = "",
    provided_evidence: dict | None = None,
) -> ShigongDiagnosticReport:
    """生成按标 profile / preflight / mapping 兼容的施组上游诊断报告。"""
    try:
        validate_tender_profile(profile)
    except TenderProfileValidationError as exc:
        raise ShigongDiagnosticsError(f"TenderProfile 校验失败: {exc}") from exc

    try:
        preflight = run_tender_preflight(profile)
    except TenderPreflightError as exc:
        raise ShigongDiagnosticsError(f"TenderProfile preflight 失败: {exc}") from exc

    try:
        mapping = build_target_mapping(profile)
    except ValueError as exc:
        raise ShigongDiagnosticsError(f"TenderProfile target mapping 失败: {exc}") from exc

    preflight_dict = tender_preflight_to_dict(preflight)
    mapping_dict = target_mapping_to_dict(mapping)
    issues: List[ShigongDiagnosticIssue] = [
        _diagnostic_issue_from_preflight(issue) for issue in preflight.issues
    ]

    for item in profile.scoring_items:
        missing = _missing_provided_evidence_requirements(item, provided_evidence)
        if missing:
            issues.append(
                ShigongDiagnosticIssue(
                    code="EVIDENCE_NOT_PROVIDED",
                    severity="warning",
                    message=f"评分项 evidence requirement 未被 provided_evidence 覆盖: {item.item_id}",
                    item_id=item.item_id,
                    details={"item_name": item.name, "missing_requirements": list(missing)},
                )
            )

    document_is_empty = not str(document_text or "").strip()
    if document_is_empty:
        issues.append(
            ShigongDiagnosticIssue(
                code="DOCUMENT_TEXT_EMPTY",
                severity="warning",
                message="未提供施组正文，诊断仅基于 tender profile / preflight / mapping。",
                details={"does_not_disqualify": True},
            )
        )
    else:
        for item in profile.scoring_items:
            missing_terms = _document_missing_requirements(item, document_text)
            if missing_terms:
                issues.append(
                    ShigongDiagnosticIssue(
                        code="EVIDENCE_REQUIREMENT_NOT_IN_DOCUMENT",
                        severity="info",
                        message=f"施组正文未直接包含部分 evidence requirement 关键词: {item.item_id}",
                        item_id=item.item_id,
                        details={
                            "item_name": item.name,
                            "missing_requirements": list(missing_terms),
                            "match_mode": "case_insensitive_contains",
                        },
                    )
                )

    issue_tuple = tuple(issues)
    counts = _diagnostic_issue_counts(issue_tuple)
    status = _diagnostic_status(issue_tuple)
    missing_evidence_item_ids = tuple(
        item.item_id for item in profile.scoring_items if not item.evidence_requirements
    )
    evidence_not_provided_count = sum(
        1 for issue in issue_tuple if issue.code == "EVIDENCE_NOT_PROVIDED"
    )
    summary = {
        "status": status,
        "issue_count": len(issue_tuple),
        "info_count": counts["info"],
        "warning_count": counts["warning"],
        "error_count": counts["error"],
        "preflight": dict(preflight_dict["summary"]),
        "mapping_coverage": dict(mapping_dict["coverage"]),
        "missing_evidence_item_count": len(missing_evidence_item_ids),
        "evidence_not_provided_count": evidence_not_provided_count,
        "document_text_empty": document_is_empty,
    }

    return ShigongDiagnosticReport(
        tender_id=profile.tender_id,
        tender_name=profile.tender_name,
        version=profile.version,
        status=status,
        issues=issue_tuple,
        preflight_status=preflight.status,
        coverage=dict(preflight_dict["coverage"]),
        unmapped_item_ids=tuple(mapping.coverage.unmapped_item_ids),
        missing_evidence_item_ids=missing_evidence_item_ids,
        hard_redline_count=mapping.coverage.hard_redline_count,
        legacy_dimension_refs=tuple(mapping.coverage.legacy_dimension_refs),
        summary=summary,
    )


def run_shigong_diagnostics_from_file(
    profile_path: str | Path,
    document_text: str = "",
    provided_evidence: dict | None = None,
) -> ShigongDiagnosticReport:
    """从 tender profile JSON 加载并生成施组诊断报告。"""
    try:
        profile = load_tender_profile(profile_path)
    except TenderProfileValidationError as exc:
        raise ShigongDiagnosticsError(f"TenderProfile 加载失败: {exc}") from exc
    return run_shigong_diagnostics(
        profile,
        document_text=document_text,
        provided_evidence=provided_evidence,
    )


def shigong_diagnostics_to_dict(report: ShigongDiagnosticReport) -> Dict[str, object]:
    """返回 JSON 友好的施组诊断报告 dict。"""
    return {
        "tender_id": report.tender_id,
        "tender_name": report.tender_name,
        "version": report.version,
        "status": report.status,
        "issues": [issue.to_dict() for issue in report.issues],
        "preflight_status": report.preflight_status,
        "coverage": dict(report.coverage),
        "unmapped_item_ids": list(report.unmapped_item_ids),
        "missing_evidence_item_ids": list(report.missing_evidence_item_ids),
        "hard_redline_count": report.hard_redline_count,
        "legacy_dimension_refs": list(report.legacy_dimension_refs),
        "summary": dict(report.summary),
    }
