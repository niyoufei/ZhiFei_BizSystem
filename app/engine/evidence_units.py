from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Tuple
from uuid import uuid4

from app.config import RESOURCES_DIR

_DIMENSION_META_CACHE: List[Dict[str, Any]] | None = None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_dimension_meta() -> List[Dict[str, Any]]:
    global _DIMENSION_META_CACHE
    if _DIMENSION_META_CACHE is not None:
        return _DIMENSION_META_CACHE
    path = RESOURCES_DIR / "dimension_meta.json"
    if path.exists():
        _DIMENSION_META_CACHE = json.loads(path.read_text(encoding="utf-8"))
    else:
        _DIMENSION_META_CACHE = []
    return _DIMENSION_META_CACHE


def _is_heading(line: str) -> bool:
    s = line.strip()
    if not s:
        return False
    if re.match(r"^\d+(?:\.\d+){0,3}\s+\S+", s):
        return True
    if re.match(r"^第[一二三四五六七八九十百]+[章节篇]\s*\S*", s):
        return True
    if re.match(r"^[（(]?[一二三四五六七八九十]+[）)]\s*\S+", s):
        return True
    return False


def _split_sentences(text: str) -> List[str]:
    parts = re.split(r"[。！？；;\n]+", text)
    return [p.strip() for p in parts if p.strip()]


def _group_sentences(sentences: List[str], min_group: int = 2, max_group: int = 5) -> List[str]:
    if len(sentences) <= max_group:
        return ["。".join(sentences)]
    grouped: List[str] = []
    idx = 0
    while idx < len(sentences):
        remain = len(sentences) - idx
        size = max_group if remain >= max_group else remain
        if remain < min_group and grouped:
            grouped[-1] = grouped[-1] + "。" + "。".join(sentences[idx:])
            break
        grouped.append("。".join(sentences[idx : idx + size]))
        idx += size
    return grouped


def _split_blocks(text: str) -> List[Tuple[str, str, str]]:
    """
    返回 [(heading_path, locator, chunk_text)].
    """
    lines = text.splitlines()
    heading = "ROOT"
    buffer: List[str] = []
    blocks: List[Tuple[str, str, str]] = []
    para_idx = 0

    def flush_buffer() -> None:
        nonlocal para_idx
        if not buffer:
            return
        merged = " ".join([b.strip() for b in buffer if b.strip()]).strip()
        buffer.clear()
        if not merged:
            return
        para_idx += 1
        locator = f"para:{para_idx}"
        # 表格行优先切分
        if "\t" in merged or "|" in merged:
            rows = [r.strip() for r in re.split(r"\n|；|;", merged) if r.strip()]
            for ridx, row in enumerate(rows, start=1):
                blocks.append((heading, f"{locator}:row:{ridx}", row))
            return
        # 普通段落按 2-5 句切分
        sents = _split_sentences(merged)
        if not sents:
            return
        chunks = _group_sentences(sents, min_group=2, max_group=5)
        for cidx, c in enumerate(chunks, start=1):
            blocks.append((heading, f"{locator}:chunk:{cidx}", c))

    for line in lines:
        stripped = line.strip()
        if _is_heading(stripped):
            flush_buffer()
            heading = stripped
            continue
        if not stripped:
            flush_buffer()
            continue
        # 列表项单独成块
        if re.match(r"^[-•●·]\s*", stripped) or re.match(r"^\d+[、.)）]\s*", stripped):
            flush_buffer()
            para_idx += 1
            blocks.append((heading, f"para:{para_idx}:list", stripped))
            continue
        buffer.append(stripped)

    flush_buffer()
    return blocks


def _collect_dim_seeds(lexicon: Dict[str, Any]) -> Dict[str, List[str]]:
    seeds: Dict[str, List[str]] = {}
    for item in _load_dimension_meta():
        dim_id = str(item.get("id", ""))
        if not dim_id:
            continue
        words = list(item.get("keywords_seed") or [])
        seeds[dim_id] = words
    for dim_id, words in (lexicon.get("dimension_keywords") or {}).items():
        d = str(dim_id)
        seeds.setdefault(d, [])
        for w in words or []:
            if w not in seeds[d]:
                seeds[d].append(w)
    return seeds


def _score_dim_candidates(text: str, seeds: Dict[str, List[str]]) -> List[Dict[str, Any]]:
    hit_scores: List[Tuple[str, float]] = []
    lower = text.lower()
    for dim_id, words in seeds.items():
        score = 0.0
        for w in words:
            if not w:
                continue
            if w.lower() in lower:
                score += 1.0
        if score > 0:
            hit_scores.append((dim_id, score))
    info_primary_signals = [
        "智慧工地",
        "信息化管理",
        "数字化管理",
        "数字化信息化管理",
    ]
    info_operational_signals = [
        "在线监测",
        "远程监管",
        "远程查阅",
        "数字化闭环",
        "轨迹监控",
        "轨迹",
        "人脸识别",
        "联网",
        "北斗",
        "gps",
        "cctv",
        "数字影像档案",
        "实时监控",
    ]
    has_info_primary = any(signal.lower() in lower for signal in info_primary_signals)
    info_operational_hits = sum(1 for signal in info_operational_signals if signal.lower() in lower)
    if has_info_primary and info_operational_hits >= 2:
        score_map = {dim_id: score for dim_id, score in hit_scores}
        score_map["16"] = max(
            float(score_map.get("16", 0.0)),
            2.0 + min(1.5, 0.25 * info_operational_hits),
        )
        hit_scores = list(score_map.items())
    civil_primary_signals = [
        "文明施工",
        "绿色工地",
        "绿色环保",
        "环保标准",
        "扬尘治理",
        "黄土不见天",
        "黄土 不见天",
        "裸土覆盖",
        "裸土网格化覆盖",
        "专职环保员",
    ]
    civil_operational_signals = [
        "喷淋",
        "微雾喷淋",
        "冲洗",
        "洗车台",
        "雾炮",
        "pm2.5",
        "pm10",
        "环境监测",
        "防尘网",
        "密闭清运",
        "三级沉淀池",
        "环保巡查",
        "裸土覆盖",
    ]
    civil_primary_hits = sum(1 for signal in civil_primary_signals if signal.lower() in lower)
    civil_operational_hits = sum(
        1 for signal in civil_operational_signals if signal.lower() in lower
    )
    if (
        civil_primary_hits >= 1
        and civil_operational_hits >= 2
        and not (has_info_primary and info_operational_hits >= 3)
    ):
        score_map = {dim_id: score for dim_id, score in hit_scores}
        score_map["03"] = max(
            float(score_map.get("03", 0.0)),
            3.0
            + min(
                1.5,
                0.25 * civil_primary_hits + 0.2 * civil_operational_hits,
            ),
        )
        hit_scores = list(score_map.items())
    newtech_primary_signals = [
        "四新",
        "新技术",
        "新工艺",
        "新材料",
        "新设备",
    ]
    newtech_structural_signals = [
        "应用部位",
        "核心技术",
        "应用成效",
        "实施参数",
        "验证方式",
        "管控要点",
    ]
    has_newtech_primary = any(signal.lower() in lower for signal in newtech_primary_signals)
    newtech_structural_hits = sum(
        1 for signal in newtech_structural_signals if signal.lower() in lower
    )
    if has_newtech_primary and newtech_structural_hits >= 2:
        score_map = {dim_id: score for dim_id, score in hit_scores}
        score_map["05"] = max(
            float(score_map.get("05", 0.0)),
            2.0 + min(1.5, 0.25 * newtech_structural_hits),
        )
        hit_scores = list(score_map.items())
    material_subject_signals = [
        "管材",
        "材料",
        "部品",
        "厂家",
        "供应商",
    ]
    material_process_signals = [
        "驻厂监造",
        "监造",
        "封样",
        "源头封样",
        "进场",
        "抽检",
        "破坏抽检",
        "第三方送检",
        "送检",
        "第三方检测",
        "复验",
        "批次",
        "追溯",
    ]
    has_material_subject = any(signal.lower() in lower for signal in material_subject_signals)
    material_process_hits = sum(1 for signal in material_process_signals if signal.lower() in lower)
    if has_material_subject and material_process_hits >= 3:
        score_map = {dim_id: score for dim_id, score in hit_scores}
        score_map["04"] = max(
            float(score_map.get("04", 0.0)),
            2.0 + min(1.5, 0.2 * material_process_hits),
        )
        hit_scores = list(score_map.items())
    safety_cert_primary_signals = [
        "特殊工种",
        "特种作业",
        "持证上岗",
    ]
    safety_disclosure_signals = [
        "技术交底",
        "工艺交底",
        "技术与工艺交底",
    ]
    frontline_coverage_signals = [
        "覆盖至一线",
        "覆盖到一线",
        "一线作业",
        "一线作业终端",
    ]
    has_safety_cert_primary = any(signal.lower() in lower for signal in safety_cert_primary_signals)
    has_safety_disclosure = any(signal.lower() in lower for signal in safety_disclosure_signals)
    has_frontline_coverage = any(signal.lower() in lower for signal in frontline_coverage_signals)
    if has_safety_cert_primary and has_safety_disclosure and has_frontline_coverage:
        score_map = {dim_id: score for dim_id, score in hit_scores}
        score_map["02"] = max(float(score_map.get("02", 0.0)), 3.2)
        hit_scores = list(score_map.items())
    risk_primary_signals = [
        "危大工程",
        "专项方案",
        "专家论证",
        "监测预警",
    ]
    risk_operational_signals = [
        "高危节点",
        "风险极大",
        "前置审批",
        "预警阈值",
        "暂停施工作业",
        "停工",
        "复工",
        "排查",
        "方案未批复",
        "严禁施工",
        "自动化监测",
    ]
    has_risk_primary = any(signal.lower() in lower for signal in risk_primary_signals)
    risk_operational_hits = sum(1 for signal in risk_operational_signals if signal.lower() in lower)
    if has_risk_primary and risk_operational_hits >= 2:
        score_map = {dim_id: score for dim_id, score in hit_scores}
        score_map["07"] = max(
            float(score_map.get("07", 0.0)),
            2.0 + min(1.5, 0.25 * risk_operational_hits),
        )
        hit_scores = list(score_map.items())
    quality_primary_signals = [
        "质量保障体系",
        "质量管理体系",
        "质量总监",
        "质检部",
    ]
    quality_control_signals = [
        "一票否决",
        "停工整改",
        "整改闭环",
        "闭环整改",
        "质量闭环",
        "质量终身责任制",
    ]
    has_quality_primary = any(signal.lower() in lower for signal in quality_primary_signals)
    quality_control_hits = sum(1 for signal in quality_control_signals if signal.lower() in lower)
    if has_quality_primary and quality_control_hits >= 2:
        score_map = {dim_id: score for dim_id, score in hit_scores}
        score_map["08"] = max(
            float(score_map.get("08", 0.0)),
            2.0 + min(1.5, 0.25 * quality_control_hits),
        )
        hit_scores = list(score_map.items())
    quality_sample_primary_signals = [
        "样板首件制",
        "首件制",
    ]
    quality_sample_operational_signals = [
        "核心工序",
        "交验",
    ]
    has_quality_sample_primary = any(
        signal.lower() in lower for signal in quality_sample_primary_signals
    )
    quality_sample_operational_hits = sum(
        1 for signal in quality_sample_operational_signals if signal.lower() in lower
    )
    if has_quality_sample_primary and quality_sample_operational_hits >= 1:
        score_map = {dim_id: score for dim_id, score in hit_scores}
        score_map["08"] = max(
            float(score_map.get("08", 0.0)),
            3.0 + min(1.0, 0.5 * quality_sample_operational_hits),
        )
        hit_scores = list(score_map.items())
    resource_primary_signals = [
        "资源配置",
        "资源保障",
        "生产要素",
        "资源调配",
        "高峰保障",
        "后备资源",
        "资源蓄水池",
        "资源池",
    ]
    resource_operational_signals = [
        "触发",
        "预警",
        "报警",
        "调配",
        "增援",
        "完成时限",
        "批准",
        "高峰",
        "缺口",
        "故障",
        "延误",
        "抢工",
        "倒班",
    ]
    has_resource_primary = any(signal.lower() in lower for signal in resource_primary_signals)
    resource_operational_hits = sum(
        1 for signal in resource_operational_signals if signal.lower() in lower
    )
    if has_resource_primary and resource_operational_hits >= 2:
        score_map = {dim_id: score for dim_id, score in hit_scores}
        score_map["15"] = max(
            float(score_map.get("15", 0.0)),
            2.0 + min(1.5, 0.25 * resource_operational_hits),
        )
        hit_scores = list(score_map.items())
    schedule_primary_signals = [
        "总进度计划",
        "施工进度计划",
        "工期目标",
        "工期保障体系",
        "关键线路",
        "里程碑",
        "节点工期",
    ]
    schedule_operational_signals = [
        "动态纠偏",
        "三级预警",
        "前锋线",
        "倒排",
        "赶工",
        "分段验收移交",
        "节点攻坚",
        "月度滚动计划",
        "周作业计划",
        "日排班计划",
        "四级计划",
    ]
    schedule_primary_hits = sum(1 for signal in schedule_primary_signals if signal.lower() in lower)
    schedule_operational_hits = sum(
        1 for signal in schedule_operational_signals if signal.lower() in lower
    )
    if schedule_primary_hits >= 2 or (
        schedule_primary_hits >= 1 and schedule_operational_hits >= 2
    ):
        score_map = {dim_id: score for dim_id, score in hit_scores}
        score_map["09"] = max(
            float(score_map.get("09", 0.0)),
            2.0
            + min(
                1.5,
                0.35 * schedule_primary_hits + 0.2 * schedule_operational_hits,
            ),
        )
        hit_scores = list(score_map.items())
    design_primary_signals = [
        "图纸会审",
        "联合会审",
        "深化设计",
        "设计协调",
        "bim",
    ]
    design_operational_signals = [
        "碰撞",
        "碰撞检测",
        "防碰撞",
        "冲突点",
        "避让",
        "迁改",
        "标高",
        "走向",
        "预留预埋",
        "关闭条件",
        "复核签认",
        "问题关闭",
        "会审",
    ]
    has_design_primary = any(signal.lower() in lower for signal in design_primary_signals)
    design_operational_hits = sum(
        1 for signal in design_operational_signals if signal.lower() in lower
    )
    if has_design_primary and design_operational_hits >= 2:
        score_map = {dim_id: score for dim_id, score in hit_scores}
        score_map["14"] = max(
            float(score_map.get("14", 0.0)),
            2.0 + min(1.5, 0.25 * design_operational_hits),
        )
        hit_scores = list(score_map.items())
    process_primary_signals = [
        "施工顺序",
        "流水段",
        "流水步距",
        "工序衔接",
        "流程衔接",
        "作业面移交",
        "立体穿插",
    ]
    process_operational_signals = [
        "前置",
        "方可",
        "移交",
        "转场",
        "分段",
        "作业面",
        "封闭后",
        "多工作面",
        "穿插",
        "优先",
        "交接",
        "并行",
        "平移转场",
    ]
    has_process_primary = any(signal.lower() in lower for signal in process_primary_signals)
    process_operational_hits = sum(
        1 for signal in process_operational_signals if signal.lower() in lower
    )
    if has_process_primary and process_operational_hits >= 2:
        score_map = {dim_id: score for dim_id, score in hit_scores}
        score_map["12"] = max(
            float(score_map.get("12", 0.0)),
            2.0 + min(1.5, 0.25 * process_operational_hits),
        )
        hit_scores = list(score_map.items())
    equipment_primary_signals = [
        "机柜",
        "起重机械",
        "设备运行状态",
        "防护等级",
        "法兰盘",
        "预埋件",
        "检验合格报告",
        "维保",
    ]
    equipment_operational_signals = [
        "进场",
        "检验合格",
        "吊装前",
        "钢丝绳",
        "吊钩",
        "制动器",
        "巡检",
        "故障",
        "响应",
        "替补",
        "停机",
        "ip55",
        "镀锌钢板",
        "法兰盘",
        "预埋件",
    ]
    personnel_cert_signals = [
        "司机",
        "司索",
        "信号工",
        "持证上岗",
        "无证",
        "特种作业",
    ]
    has_equipment_primary = any(signal.lower() in lower for signal in equipment_primary_signals)
    equipment_operational_hits = sum(
        1 for signal in equipment_operational_signals if signal.lower() in lower
    )
    has_personnel_cert_signal = any(signal.lower() in lower for signal in personnel_cert_signals)
    if has_equipment_primary and equipment_operational_hits >= 2 and not has_personnel_cert_signal:
        score_map = {dim_id: score for dim_id, score in hit_scores}
        score_map["13"] = max(
            float(score_map.get("13", 0.0)),
            2.0 + min(1.5, 0.25 * equipment_operational_hits),
        )
        hit_scores = list(score_map.items())
    if not hit_scores:
        return [
            {"dimension_id": "01", "confidence": 0.34},
            {"dimension_id": "09", "confidence": 0.33},
            {"dimension_id": "07", "confidence": 0.33},
        ]
    hit_scores.sort(key=lambda x: x[1], reverse=True)
    top = hit_scores[:3]
    total = sum(s for _, s in top) or 1.0
    return [{"dimension_id": dim, "confidence": round(score / total, 4)} for dim, score in top]


def _has_pattern(text: str, patterns: Iterable[str]) -> bool:
    for p in patterns:
        if not p:
            continue
        if re.search(p, text, flags=re.IGNORECASE):
            return True
    return False


def _has_any_keyword(text: str, keywords: Iterable[str]) -> bool:
    lower = text.lower()
    for kw in keywords:
        if not kw:
            continue
        if str(kw).lower() in lower:
            return True
    return False


def _tag_logic_and_landing(text: str, lexicon: Dict[str, Any]) -> Dict[str, bool]:
    definition = lexicon.get("definition") or {}
    analysis = lexicon.get("analysis") or {}
    solution = lexicon.get("solution") or {}

    has_definition = _has_any_keyword(text, definition.get("keywords", [])) or _has_pattern(
        text, definition.get("regexes") or definition.get("regex", [])
    )
    has_analysis = _has_any_keyword(text, analysis.get("keywords", [])) or _has_pattern(
        text, analysis.get("regexes") or analysis.get("regex", [])
    )
    has_solution = _has_any_keyword(text, solution.get("keywords", [])) or _has_pattern(
        text, solution.get("regexes") or solution.get("regex", [])
    )

    has_param = bool(
        re.search(
            r"\d+(?:\.\d+)?\s*(?:m3|m³|m2|m²|㎡|㎥|㎠|mm|cm|m|t|kg|台|套|处|项|座|段|根|个|%|天|小时|h|d)",
            text,
            flags=re.IGNORECASE,
        )
        or re.search(r"[≤≥<>]", text)
    )
    has_freq = bool(
        re.search(
            r"(?:每日|每周|每月|每班|每次|每\d+天|每\d+小时|\d+次/天|\d+次/周|\d+次|次/天|次/周)",
            text,
            flags=re.IGNORECASE,
        )
    )
    has_accept = bool(
        re.search(
            r"(?:报验|签认|验收|旁站|自检|互检|交接检|隐蔽验收|销项)",
            text,
            flags=re.IGNORECASE,
        )
    )
    has_role = bool(
        re.search(
            r"(?:项目经理|技术负责人|施工员|安全员|质检员|资料员|材料员|班组长)",
            text,
            flags=re.IGNORECASE,
        )
    )

    return {
        "tag_definition": has_definition,
        "tag_analysis": has_analysis,
        "tag_solution": has_solution,
        "landing_param": has_param,
        "landing_freq": has_freq,
        "landing_accept": has_accept,
        "landing_role": has_role,
    }


def _specificity_score(text: str, tags: Dict[str, bool]) -> float:
    present = sum(
        [
            1 if tags.get("landing_param") else 0,
            1 if tags.get("landing_freq") else 0,
            1 if tags.get("landing_accept") else 0,
            1 if tags.get("landing_role") else 0,
            1 if bool(re.search(r"[≤≥<>]", text)) else 0,
            1 if bool(re.search(r"\d", text)) else 0,
        ]
    )
    return round(min(1.0, present / 6.0), 4)


def _link_anchors(text: str, anchors: List[Dict[str, Any]]) -> List[str]:
    lower = text.lower()
    links: List[str] = []
    for a in anchors:
        key = str(a.get("anchor_key") or "")
        if not key:
            continue
        value = a.get("anchor_value")
        hit = False
        for token in key.split("_"):
            if token and token.lower() in lower:
                hit = True
                break
        if not hit and isinstance(value, str) and value and value.lower() in lower:
            hit = True
        if not hit and isinstance(value, list):
            for item in value:
                if isinstance(item, str) and item and item.lower() in lower:
                    hit = True
                    break
        if not hit and a.get("value_num") is not None:
            try:
                v = int(float(a.get("value_num")))
                if str(v) in text:
                    hit = True
            except Exception:
                pass
        if hit:
            links.append(key)
    return sorted(set(links))


def build_evidence_units(
    submission_id: str,
    text: str,
    lexicon: Dict[str, Any],
    anchors: List[Dict[str, Any]] | None = None,
    doc_id: str | None = None,
) -> List[Dict[str, Any]]:
    anchors = anchors or []
    seeds = _collect_dim_seeds(lexicon)
    blocks = _split_blocks(text)
    units: List[Dict[str, Any]] = []

    for heading, locator, chunk in blocks:
        if not chunk.strip():
            continue
        candidates = _score_dim_candidates(chunk, seeds)
        primary = candidates[0]["dimension_id"] if candidates else "01"
        tags = _tag_logic_and_landing(chunk, lexicon)
        specificity = _specificity_score(chunk, tags)
        anchor_links = _link_anchors(chunk, anchors)
        units.append(
            {
                "id": str(uuid4()),
                "submission_id": submission_id,
                "doc_id": doc_id,
                "text": chunk,
                "heading_path": heading,
                "locator": locator,
                "dimension_primary": primary,
                "dimension_candidates": candidates,
                "specificity_score": specificity,
                "anchor_links": anchor_links,
                "created_at": _now_iso(),
                **tags,
            }
        )
    return units
