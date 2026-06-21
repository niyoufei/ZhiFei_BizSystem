"""
按标硬红线预检（Tender-aware Pre-flight）。

升级增量 4。依据每个标自己的硬红线（来自招标文件、存于 profile.hard_lines）做确定性闸门：
- 篇幅是否超限（如 ≤50 页）；
- 是否缺必须的「施工总平面布置图」（案例3/4 要求）；
- 是否提供了两份及以上施组（=备选方案=判废）；
- 是否未提供 / 内容空泛（=本项不得分）。

与既有 app/engine/preflight.py 互补、不重叠也不修改它：
preflight.py 查 GB50502 骨架章节 + 废止规范黑名单；本模块查「本标自己的」红线。
纯新增、确定性、零外部依赖；不接核心评分主链。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Dict, Optional, Tuple

from app.engine.tender_profile import TenderScoringProfile

# 施工总平面布置图的识别关键词
_SITE_PLAN_TERMS = (
    "施工总平面布置图",
    "施工总平面图",
    "总平面布置图",
    "总平面布置",
    "平面布置图",
)


@dataclass(frozen=True)
class RedLineFinding:
    code: str  # PAGE_LIMIT / SITE_PLAN_REQUIRED / MULTIPLE_SHIGONG / NOT_PROVIDED / FORMAT
    status: str  # pass / fail / unknown / advisory
    severity: str  # disqualify（判废） / zero（本项0分） / warn（重大失分风险） / info
    message: str
    action: str  # 修复建议

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class TenderPreflightResult:
    tender_id: str
    disqualified: bool  # 是否命中判废级红线
    findings: Tuple[RedLineFinding, ...]

    def to_dict(self) -> Dict[str, object]:
        return {
            "tender_id": self.tender_id,
            "disqualified": self.disqualified,
            "findings": [f.to_dict() for f in self.findings],
        }


def _detect_site_plan(text: str) -> bool:
    src = str(text or "")
    return any(term in src for term in _SITE_PLAN_TERMS)


def check_tender_hard_lines(
    text: str,
    profile: TenderScoringProfile,
    *,
    page_count: Optional[int] = None,
    shigong_count: int = 1,
    has_site_plan: Optional[bool] = None,
    min_chars: int = 200,
) -> TenderPreflightResult:
    """对一份施组做本标硬红线预检。

    Args:
        text: 施组全文。
        profile: 本标评分配置（提供 hard_lines）。
        page_count: 实际页数（来自 docx/pdf）；None 表示未知，跳过篇幅检查。
        shigong_count: 本次提供的施组份数（>1 触发备选方案判废）。
        has_site_plan: 显式指定是否含施工总平面图；None 则从文本关键词探测。
        min_chars: 判定"未提供/空泛"的最小正文字符数。
    """
    hl = profile.hard_lines
    findings = []
    src = str(text or "")

    # 1) 未提供 / 空泛 -> 本项不得分
    if hl.zero_if_not_provided:
        if len(src.strip()) < min_chars:
            findings.append(
                RedLineFinding(
                    code="NOT_PROVIDED",
                    status="fail",
                    severity="zero",
                    message=f"施组内容缺失或过短（<{min_chars}字），按招标文件「未提供/无针对性可行性」本项不得分。",
                    action="补齐针对本工程的实质性施组内容。",
                )
            )
        else:
            findings.append(
                RedLineFinding(
                    code="NOT_PROVIDED",
                    status="pass",
                    severity="info",
                    message="施组正文已提供。",
                    action="",
                )
            )

    # 2) 多份施组 -> 备选方案判废
    if hl.multiple_shigong_rejected:
        if shigong_count and shigong_count > 1:
            findings.append(
                RedLineFinding(
                    code="MULTIPLE_SHIGONG",
                    status="fail",
                    severity="disqualify",
                    message=f"提供了 {shigong_count} 份施工组织设计，视为备选方案，按招标文件应判废。",
                    action="仅保留一份施工组织设计。",
                )
            )
        else:
            findings.append(
                RedLineFinding(
                    code="MULTIPLE_SHIGONG",
                    status="pass",
                    severity="info",
                    message="仅提供一份施组。",
                    action="",
                )
            )

    # 3) 必含施工总平面布置图
    if hl.require_site_plan:
        present = has_site_plan if has_site_plan is not None else _detect_site_plan(src)
        if present:
            findings.append(
                RedLineFinding(
                    code="SITE_PLAN_REQUIRED",
                    status="pass",
                    severity="info",
                    message="检出施工总平面布置图。",
                    action="",
                )
            )
        else:
            findings.append(
                RedLineFinding(
                    code="SITE_PLAN_REQUIRED",
                    status="fail",
                    severity="warn",
                    message="本标考量项要求「施工总平面布置图」，未检出，将直接丢失该项得分。",
                    action="补充施工总平面布置图（含分区、道路、临设、塔吊覆盖等）。",
                )
            )

    # 4) 篇幅上限
    if hl.max_pages is not None:
        if page_count is None:
            findings.append(
                RedLineFinding(
                    code="PAGE_LIMIT",
                    status="unknown",
                    severity="info",
                    message=f"未提供页数，无法核对篇幅上限（≤{hl.max_pages}页）。",
                    action="导出时提供实际页数以核对。",
                )
            )
        elif page_count > hl.max_pages:
            findings.append(
                RedLineFinding(
                    code="PAGE_LIMIT",
                    status="fail",
                    severity="warn",
                    message=f"篇幅 {page_count} 页超过上限 {hl.max_pages} 页，超出部分可能不被评审。",
                    action=f"压缩到 {hl.max_pages} 页内，删去国家/地方现成规范的重复内容。",
                )
            )
        else:
            findings.append(
                RedLineFinding(
                    code="PAGE_LIMIT",
                    status="pass",
                    severity="info",
                    message=f"篇幅 {page_count} 页，未超上限 {hl.max_pages} 页。",
                    action="",
                )
            )

    # 5) 排版格式（说明性，不判废）
    if hl.format_hints:
        findings.append(
            RedLineFinding(
                code="FORMAT",
                status="advisory",
                severity="info",
                message="排版要求：" + "；".join(hl.format_hints),
                action="按要求排版，避免因格式被扣印象分。",
            )
        )

    disqualified = any(f.severity == "disqualify" and f.status == "fail" for f in findings)
    return TenderPreflightResult(
        tender_id=profile.tender_id,
        disqualified=disqualified,
        findings=tuple(findings),
    )
