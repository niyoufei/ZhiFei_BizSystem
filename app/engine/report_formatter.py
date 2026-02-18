from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from app.i18n import t

HIGH_PRIORITY = ["07", "09", "02", "03"]


def _safe_snippet(evidence: Dict[str, Any], locale: Optional[str] = None) -> str:
    return (evidence or {}).get("snippet", "") or t("dimension.no_evidence", locale=locale)


def _truncate_cn(text: str, max_len: int = 60) -> str:
    if text is None:
        return ""
    text = text.strip()
    if len(text) <= max_len:
        return text
    return text[:max_len] + "…"


def _qingtian_comment(code: str, message: str, locale: Optional[str] = None) -> str:
    if code == "P-ACTION-001":
        return t("penalty.code_action", locale=locale)
    if code == "P-EMPTY-001":
        return t("penalty.code_empty", locale=locale)
    return (message or "")[:40]


def _render_penalty_line(p: Dict[str, Any], locale: Optional[str] = None) -> str:
    code = p.get("code", "")
    deduct = p.get("deduct", 0)
    message = p.get("message", "")
    evidence = _safe_snippet(p.get("evidence_span") or p.get("evidence"), locale=locale)
    evidence = _truncate_cn(evidence, 60)
    comment = _qingtian_comment(code, message, locale=locale)
    deduct_label = t("penalty.deduct", locale=locale)
    points_label = t("penalty.points", locale=locale)
    reason_label = t("penalty.reason", locale=locale)
    evidence_label = t("penalty.evidence", locale=locale)
    qingtian_label = t("penalty.qingtian_comment", locale=locale)
    return f"【{code}】{deduct_label}{deduct}{points_label}｜{reason_label}：{message}｜{evidence_label}：{evidence}｜{qingtian_label}：{comment}"


def _format_four_parts(
    dim_id: str,
    dim_name: str,
    dim_data: Dict[str, Any],
    spark_called: bool,
    locale: Optional[str] = None,
) -> str:
    if spark_called:
        definition_list = dim_data.get("definition_points", [])[:3]
        defects_list = dim_data.get("defects", [])[:3]
        improvements_list = dim_data.get("improvements", [])[:3]
        evidence_list = dim_data.get("evidence", [])[:2]
    else:
        hits = dim_data.get("hits", [])[:3]
        definition_list = hits or [t("dimension.no_definition", locale=locale)]
        defects_list = [t("dimension.default_defect", locale=locale)]
        improvements_list = [t("dimension.default_improvement", locale=locale)]
        evidence_list = dim_data.get("evidence", [])[:2]

    sep = "；" if locale in (None, "zh") else "; "
    definition = sep.join(definition_list)
    defects = sep.join(defects_list)
    improvements = sep.join(improvements_list)
    evidence = sep.join(
        [_truncate_cn(_safe_snippet(e, locale=locale), 60) for e in (evidence_list or [{}])]
    )

    def_label = t("dimension.definition_points", locale=locale)
    defects_label = t("dimension.defects", locale=locale)
    improvements_label = t("dimension.improvements", locale=locale)
    evidence_label = t("dimension.evidence", locale=locale)

    return (
        f"{dim_id} {dim_name}\n"
        f"{def_label}：{definition}\n"
        f"{defects_label}：{defects}\n"
        f"{improvements_label}：{improvements}\n"
        f"{evidence_label}：{evidence}\n"
    )


def _top_penalties(report: Dict[str, Any], limit: int = 10) -> List[Dict[str, Any]]:
    penalties = report.get("penalties", []) or []
    penalties_sorted = sorted(penalties, key=lambda x: float(x.get("deduct", 0.0)), reverse=True)
    return penalties_sorted[:limit]


def _collect_missing_tags(report: Dict[str, Any]) -> List[str]:
    tags: List[str] = []
    for p in report.get("penalties", []) or []:
        if p.get("code") == "P-ACTION-001":
            tags.extend(p.get("tags") or [])
    return list(dict.fromkeys(tags))


def _tags_hint(tags: List[str], locale: Optional[str] = None) -> str:
    if not tags:
        return ""
    parts = []
    for tag in tags:
        key = f"tags.{tag}"
        translated = t(key, locale=locale)
        if translated != key:
            parts.append(translated)
    if not parts:
        return ""
    needs_label = t("tags.needs_completion", locale=locale)
    sep = "、" if locale in (None, "zh") else ", "
    if locale in (None, "zh"):
        return f"（{needs_label}：" + sep.join(parts) + "）"
    else:
        return f" ({needs_label}: " + sep.join(parts) + ")"


def build_action_template_07(tags: List[str], locale: Optional[str] = None) -> str:
    return t("templates.template_07", locale=locale)


def build_action_template_09(tags: List[str], locale: Optional[str] = None) -> str:
    return t("templates.template_09", locale=locale)


def build_action_template_02(tags: List[str], locale: Optional[str] = None) -> str:
    return t("templates.template_02", locale=locale)


def build_action_template_03(tags: List[str], locale: Optional[str] = None) -> str:
    return t("templates.template_03", locale=locale)


def _build_template_action(dim_id: str, tags: List[str], locale: Optional[str] = None) -> str:
    if dim_id == "07":
        return build_action_template_07(tags, locale=locale) + _tags_hint(tags, locale=locale)
    if dim_id == "09":
        return build_action_template_09(tags, locale=locale) + _tags_hint(tags, locale=locale)
    if dim_id == "02":
        return build_action_template_02(tags, locale=locale) + _tags_hint(tags, locale=locale)
    if dim_id == "03":
        return build_action_template_03(tags, locale=locale) + _tags_hint(tags, locale=locale)
    return t("templates.template_default", locale=locale)


def _improvement_actions(
    report: Dict[str, Any], limit: int = 8, locale: Optional[str] = None
) -> List[Tuple[str, str, float]]:
    actions: List[Tuple[str, str, float]] = []
    penalties = report.get("penalties", []) or []
    tags = _collect_missing_tags(report)

    for p in penalties:
        code = p.get("code")
        if code == "P-ACTION-001":
            actions.append(("", t("templates.action_fix", locale=locale), 0.5))
        elif code == "P-EMPTY-001":
            actions.append(("", t("templates.empty_fix", locale=locale), 0.3))

    for dim_id in HIGH_PRIORITY:
        actions.append(
            (
                dim_id,
                _build_template_action(dim_id, tags, locale=locale),
                1.0,
            )
        )

    # 去重 + 按预计加分排序
    seen = set()
    uniq: List[Tuple[str, str, float]] = []
    for dim_id, action, gain in actions:
        key = (dim_id, action, gain)
        if key in seen:
            continue
        seen.add(key)
        uniq.append((dim_id, action, gain))

    uniq.sort(key=lambda x: x[2], reverse=True)
    return uniq[:limit]


def format_summary(report: Dict[str, Any], locale: Optional[str] = None) -> str:
    """格式化评分摘要报告，支持多语言输出。

    Args:
        report: 评分报告数据
        locale: 语言代码 (zh/en)，默认使用全局设置

    Returns:
        格式化的报告文本
    """
    judge_mode = report.get("judge_mode")
    judge_source = report.get("judge_source")
    spark_called = report.get("spark_called")

    total_score = report.get("total_score")
    if total_score is None:
        total_score = report.get("overall", {}).get("total_score_0_100")

    lines: List[str] = []
    lines.append(t("report.title", locale=locale))
    lines.append("")
    lines.append(t("report.section_a", locale=locale))
    lines.append(f"- {t('scoring.total_score', locale=locale)}：{total_score}")
    lines.append(f"- {t('scoring.judge_mode', locale=locale)}：{judge_mode}")
    lines.append(f"- {t('scoring.judge_source', locale=locale)}：{judge_source}")
    lines.append(f"- {t('scoring.spark_called', locale=locale)}：{spark_called}")
    lines.append(f"- {t('scoring.high_priority_strategy', locale=locale)}：07/09/02/03")
    lines.append("")

    lines.append(t("report.section_b", locale=locale))
    if spark_called:
        dim_scores = report.get("dimension_scores", {})
    else:
        dim_scores = report.get("dimension_scores", {})
    for dim_id in HIGH_PRIORITY:
        dim = dim_scores.get(dim_id, {})
        dim_name = dim.get("name", "")
        lines.append(_format_four_parts(dim_id, dim_name, dim, bool(spark_called), locale=locale))
    lines.append("")

    lines.append(t("report.section_c", locale=locale))
    for p in _top_penalties(report, limit=10):
        lines.append(_render_penalty_line(p, locale=locale))
    lines.append("")

    lines.append(t("report.section_d", locale=locale))
    expected_gain_label = t("scoring.expected_gain", locale=locale)
    for dim_id, action, gain in _improvement_actions(report, limit=8, locale=locale):
        dim_prefix = f"{dim_id} " if dim_id else ""
        lines.append(f"- {dim_prefix}{action} {expected_gain_label}：+{gain}")
    lines.append("")

    lines.append(t("report.section_e", locale=locale))
    lines.append(f"- {t('appendix.evidence_note', locale=locale)}")
    lines.append("")
    lines.append(t("appendix.spark_hint", locale=locale))

    return "\n".join(lines)


def format_qingtian_word_report(report: Dict[str, Any], locale: Optional[str] = None) -> str:
    """格式化青天评标官终版报告，支持多语言输出。

    Args:
        report: 评分报告数据
        locale: 语言代码 (zh/en)，默认使用全局设置

    Returns:
        格式化的报告文本
    """
    judge_mode = report.get("judge_mode")
    judge_source = report.get("judge_source")
    spark_called = report.get("spark_called")
    confidence = report.get("overall", {}).get("confidence_0_1")

    total_score = report.get("total_score")
    if total_score is None:
        total_score = report.get("overall", {}).get("total_score_0_100")

    lines: List[str] = []
    lines.append(t("report.title_word", locale=locale))
    lines.append("")
    lines.append(t("report.section_a_word", locale=locale))
    lines.append(f"- {t('scoring.total_score', locale=locale)}：{total_score}")
    lines.append(f"- {t('scoring.confidence', locale=locale)}：{confidence}")
    lines.append(f"- {t('scoring.judge_mode', locale=locale)}：{judge_mode}")
    lines.append(f"- {t('scoring.judge_source', locale=locale)}：{judge_source}")
    lines.append(f"- {t('scoring.spark_called', locale=locale)}：{spark_called}")
    lines.append(f"- {t('scoring.high_priority_strategy', locale=locale)}：07/09/02/03")
    lines.append("")

    lines.append(t("report.section_b_word", locale=locale))
    dim_scores = report.get("dimension_scores", {})
    for dim_id in HIGH_PRIORITY:
        dim = dim_scores.get(dim_id, {})
        dim_name = dim.get("name", "")
        lines.append(_format_four_parts(dim_id, dim_name, dim, bool(spark_called), locale=locale))
    lines.append("")

    lines.append(t("report.section_c", locale=locale))
    for p in _top_penalties(report, limit=10):
        lines.append(_render_penalty_line(p, locale=locale))
    lines.append("")

    lines.append(t("report.section_d", locale=locale))
    expected_gain_label = t("scoring.expected_gain", locale=locale)
    for dim_id, action, gain in _improvement_actions(report, limit=8, locale=locale):
        dim_prefix = f"{dim_id} " if dim_id else ""
        lines.append(f"- {dim_prefix}{action} {expected_gain_label}：+{gain}")
    lines.append("")

    lines.append(t("report.section_e", locale=locale))
    lines.append(f"- {t('appendix.evidence_note', locale=locale)}")
    lines.append("")
    lines.append(t("appendix.spark_hint", locale=locale))

    return "\n".join(lines)
