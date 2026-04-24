from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List
from uuid import uuid4

DIMENSION_IDS = [f"{i:02d}" for i in range(1, 17)]
BASE_REQUIREMENT_PACK_PATH = (
    Path(__file__).resolve().parents[2] / "config" / "qingtian_hefei_chapter_factors_v1.json"
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _first_match(text: str, pattern: str) -> str | None:
    m = re.search(pattern, text, flags=re.IGNORECASE | re.MULTILINE)
    if not m:
        return None
    return m.group(1) if m.groups() else m.group(0)


def _extract_sentences_with_keywords(text: str, keywords: List[str], limit: int = 3) -> List[str]:
    parts = re.split(r"[。\n；;]", text)
    hits: List[str] = []
    for p in parts:
        s = p.strip()
        if not s:
            continue
        if any(k in s for k in keywords):
            hits.append(s)
        if len(hits) >= limit:
            break
    return hits


def _extract_material_section(text: str, section_name: str) -> str:
    pattern = rf"===\s*{re.escape(section_name)}\s*===\s*(.*?)(?=\n===\s*[^=\n]+?\s*===|\Z)"
    m = re.search(pattern, text, flags=re.IGNORECASE | re.DOTALL)
    if not m:
        return ""
    return str(m.group(1) or "").strip()


def _is_v2_engine(version: str | None) -> bool:
    value = str(version or "").strip().lower()
    if value.startswith("v2"):
        return True
    return False


def _load_base_requirement_pack() -> Dict[str, Any]:
    if not BASE_REQUIREMENT_PACK_PATH.exists():
        return {}
    try:
        return json.loads(BASE_REQUIREMENT_PACK_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _expand_pack_requirements(
    project_id: str,
    pack: Dict[str, Any],
) -> List[Dict[str, Any]]:
    now = _now_iso()
    version = str(pack.get("version") or "")
    pack_id = str(pack.get("pack_id") or "")
    rows: List[Dict[str, Any]] = []

    for item in pack.get("requirements") or []:
        if not isinstance(item, dict):
            continue
        base_id = str(item.get("id") or "").strip()
        req_label = str(item.get("req_label") or "").strip()
        req_type = str(item.get("req_type") or "presence")
        mandatory = bool(item.get("mandatory", True))
        weight = float(item.get("weight", 1.0))
        patterns = item.get("patterns") or {}
        lint = item.get("lint") or {}

        dim_ids = item.get("dimension_ids")
        if not isinstance(dim_ids, list) or not dim_ids:
            dim_single = str(item.get("dimension_id") or "").strip()
            dim_ids = [dim_single] if dim_single else []

        for dim_id in [str(d).zfill(2) for d in dim_ids]:
            if dim_id not in DIMENSION_IDS:
                continue
            req_id = base_id
            if len(dim_ids) > 1:
                req_id = f"{base_id}-{dim_id}"
            rows.append(
                {
                    "id": req_id,
                    "project_id": project_id,
                    "dimension_id": dim_id,
                    "req_label": req_label,
                    "req_type": req_type,
                    "patterns": patterns,
                    "mandatory": mandatory,
                    "weight": weight,
                    "source_anchor_id": None,
                    "source_pack_id": pack_id,
                    "source_pack_version": version,
                    "priority": 100.0,
                    "override_key": req_id,
                    "lint": lint,
                    "version_locked": version,
                    "created_at": now,
                }
            )
    return rows


def _merge_requirements_with_priority(requirements: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    selected: Dict[str, Dict[str, Any]] = {}

    def _key(row: Dict[str, Any]) -> str:
        row_id = str(row.get("override_key") or row.get("id") or "").strip()
        if row_id:
            return f"id::{row_id}"
        return f"dim_label::{row.get('dimension_id')}::{row.get('req_label')}"

    for row in requirements:
        key = _key(row)
        incoming_pri = float(row.get("priority", 0.0))
        old = selected.get(key)
        if not old:
            selected[key] = row
            continue
        old_pri = float(old.get("priority", 0.0))
        if incoming_pri >= old_pri:
            selected[key] = row

    return list(selected.values())


def extract_project_anchors_from_text(project_id: str, text: str) -> List[Dict[str, Any]]:
    anchors: List[Dict[str, Any]] = []
    now = _now_iso()

    def add_anchor(
        key: str,
        value: Any,
        *,
        value_num: float | None = None,
        value_unit: str | None = None,
        confidence: float = 0.75,
        source_locator: str = "materials_merged",
    ) -> None:
        anchors.append(
            {
                "id": str(uuid4()),
                "project_id": project_id,
                "anchor_key": key,
                "anchor_value": value,
                "value_num": value_num,
                "value_unit": value_unit,
                "source_doc_id": None,
                "source_locator": source_locator,
                "confidence": confidence,
                "created_at": now,
            }
        )

    duration = _first_match(text, r"工期[^\n。]{0,30}?(\d{2,4})\s*(?:日历天|天)")
    if duration and duration.isdigit():
        add_anchor(
            "contract_duration_days",
            f"{duration}天",
            value_num=float(duration),
            value_unit="天",
            confidence=0.9,
        )

    quality_sentences = _extract_sentences_with_keywords(
        text, ["质量标准", "验收标准", "合格", "优良", "创优"], limit=2
    )
    if quality_sentences:
        add_anchor("quality_standard", quality_sentences, confidence=0.8)

    milestone_sentences = _extract_sentences_with_keywords(
        text, ["节点", "里程碑", "关键线路", "总控计划"], limit=3
    )
    if milestone_sentences:
        add_anchor("key_milestones", milestone_sentences, confidence=0.78)

    safety_sentences = _extract_sentences_with_keywords(
        text, ["安全", "文明施工", "危大工程", "应急预案"], limit=3
    )
    if safety_sentences:
        add_anchor("safety_civil_clauses", safety_sentences, confidence=0.8)

    danger_sentences = _extract_sentences_with_keywords(
        text, ["危大工程", "专项方案", "专家论证"], limit=3
    )
    if danger_sentences:
        add_anchor("dangerous_works_list", danger_sentences, confidence=0.82)

    scoring_sentences = _extract_sentences_with_keywords(
        text, ["评分办法", "评分点", "评审因素"], limit=2
    )
    if scoring_sentences:
        add_anchor("scoring_method_items", scoring_sentences, confidence=0.7)

    scope_sentences = _extract_sentences_with_keywords(
        text, ["工程范围", "施工范围", "工程内容"], limit=2
    )
    if scope_sentences:
        add_anchor("project_scope", scope_sentences, confidence=0.72)

    tender_section = _extract_material_section(text, "招标文件和答疑")
    boq_section = _extract_material_section(text, "清单")
    drawing_section = _extract_material_section(text, "图纸")
    site_photo_section = _extract_material_section(text, "现场照片")

    if tender_section:
        qa_hits = _extract_sentences_with_keywords(
            tender_section,
            ["答疑", "澄清", "变更", "签证", "工期", "质量标准", "计价规则"],
            limit=4,
        )
        if qa_hits:
            add_anchor(
                "qa_clarifications",
                qa_hits,
                confidence=0.82,
                source_locator="materials_tender_qa",
            )

    if boq_section:
        boq_hits = _extract_sentences_with_keywords(
            boq_section,
            ["工程量", "清单", "综合单价", "措施费", "暂估价", "甲供材", "设备"],
            limit=5,
        )
        if boq_hits:
            add_anchor(
                "boq_cost_control_points",
                boq_hits,
                confidence=0.84,
                source_locator="materials_boq",
            )

    if drawing_section:
        drawing_hits = _extract_sentences_with_keywords(
            drawing_section,
            ["图纸", "平面", "剖面", "节点", "BIM", "碰撞", "深化", "机电综合"],
            limit=5,
        )
        if drawing_hits:
            add_anchor(
                "drawing_coordination_points",
                drawing_hits,
                confidence=0.84,
                source_locator="materials_drawing",
            )

    if site_photo_section:
        photo_hits = _extract_sentences_with_keywords(
            site_photo_section,
            ["临边", "高处", "深基坑", "塔吊", "脚手架", "扬尘", "围挡", "积水", "消防"],
            limit=5,
        )
        if photo_hits:
            add_anchor(
                "site_photo_risk_points",
                photo_hits,
                confidence=0.8,
                source_locator="materials_site_photo",
            )

    return anchors


def build_project_requirements_from_anchors(
    project_id: str,
    anchors: List[Dict[str, Any]],
    *,
    region: str | None = None,
    scoring_engine_version: str | None = None,
) -> List[Dict[str, Any]]:
    now = _now_iso()
    requirements: List[Dict[str, Any]] = []

    def add_req(
        dimension_id: str,
        req_label: str,
        req_type: str,
        patterns: Dict[str, Any],
        *,
        mandatory: bool = True,
        weight: float = 1.0,
        source_anchor_id: str | None = None,
    ) -> None:
        requirements.append(
            {
                "id": str(uuid4()),
                "project_id": project_id,
                "dimension_id": dimension_id,
                "req_label": req_label,
                "req_type": req_type,
                "patterns": patterns,
                "mandatory": mandatory,
                "weight": weight,
                "source_anchor_id": source_anchor_id,
                "priority": 10.0,
                "override_key": None,
                "lint": {},
                "created_at": now,
            }
        )

    for a in anchors:
        key = str(a.get("anchor_key", ""))
        aid = str(a.get("id", ""))
        if key == "contract_duration_days":
            add_req(
                "09",
                "工期总天数应与项目锚点一致",
                "numeric",
                {"expected_days": a.get("value_num"), "raw": a.get("anchor_value")},
                mandatory=True,
                weight=2.0,
                source_anchor_id=aid,
            )
        elif key == "quality_standard":
            add_req(
                "08",
                "质量标准应明确且与项目要求一致",
                "consistency",
                {"expected": a.get("anchor_value")},
                mandatory=True,
                weight=1.6,
                source_anchor_id=aid,
            )
        elif key == "key_milestones":
            add_req(
                "09",
                "进度节点与里程碑应覆盖关键线路",
                "presence",
                {"keywords": ["节点", "里程碑", "关键线路", "总控计划"]},
                mandatory=True,
                weight=1.6,
                source_anchor_id=aid,
            )
        elif key == "safety_civil_clauses":
            add_req(
                "02",
                "安全管理条款需完整响应",
                "presence",
                {"keywords": ["安全生产", "隐患排查", "应急预案"]},
                mandatory=True,
                weight=1.5,
                source_anchor_id=aid,
            )
            add_req(
                "03",
                "文明施工条款需完整响应",
                "presence",
                {"keywords": ["文明施工", "扬尘治理", "噪声控制"]},
                mandatory=True,
                weight=1.2,
                source_anchor_id=aid,
            )
        elif key == "dangerous_works_list":
            add_req(
                "07",
                "危大工程及专项方案应识别并闭环",
                "presence",
                {"keywords": ["危大工程", "专项方案", "论证", "监测", "应急预案"]},
                mandatory=True,
                weight=2.0,
                source_anchor_id=aid,
            )
        elif key == "scoring_method_items":
            add_req(
                "01",
                "评分办法关注项应在整体实施路径中体现",
                "semantic",
                {"hints": a.get("anchor_value")},
                mandatory=False,
                weight=1.0,
                source_anchor_id=aid,
            )
            add_req(
                "15",
                "评分办法关注项应映射到资源配置计划",
                "semantic",
                {"hints": a.get("anchor_value")},
                mandatory=False,
                weight=1.0,
                source_anchor_id=aid,
            )
        elif key == "project_scope":
            add_req(
                "01",
                "工程范围需在项目理解章节中明确",
                "presence",
                {"keywords": ["工程范围", "施工范围", "工程内容"]},
                mandatory=True,
                weight=1.3,
                source_anchor_id=aid,
            )
        elif key == "qa_clarifications":
            add_req(
                "01",
                "答疑澄清与计价/工期边界应在施组中闭环响应",
                "consistency",
                {"expected": a.get("anchor_value")},
                mandatory=True,
                weight=1.8,
                source_anchor_id=aid,
            )
        elif key == "boq_cost_control_points":
            add_req(
                "04",
                "主材、设备与特殊材料闭环需映射清单关键项",
                "semantic",
                {"hints": a.get("anchor_value")},
                mandatory=False,
                weight=1.4,
                source_anchor_id=aid,
            )
            add_req(
                "13",
                "物资设备配置应与清单计价口径一致",
                "semantic",
                {"hints": a.get("anchor_value")},
                mandatory=True,
                weight=1.7,
                source_anchor_id=aid,
            )
            add_req(
                "15",
                "总体配置计划需映射清单关键工程量与措施项",
                "presence",
                {"keywords": ["工程量", "清单", "综合单价", "措施费", "设备"]},
                mandatory=True,
                weight=1.8,
                source_anchor_id=aid,
            )
        elif key == "drawing_coordination_points":
            add_req(
                "14",
                "设计协调与深化应响应图纸节点及碰撞风险",
                "semantic",
                {
                    "hints": [
                        "图纸节点",
                        "碰撞风险",
                        "BIM",
                        "碰撞",
                        "防碰撞",
                        "管线综合",
                        "标高",
                        "走向",
                        "预留预埋",
                        "图纸会审",
                        "联合会审",
                        "深化设计",
                        "设计协调",
                        "避让",
                        "迁改",
                    ]
                },
                mandatory=True,
                weight=1.9,
                source_anchor_id=aid,
            )
            add_req(
                "16",
                "技术措施可行性应包含图纸落地与节点做法",
                "presence",
                {"keywords": ["节点", "深化", "图纸", "BIM", "碰撞"]},
                mandatory=True,
                weight=1.6,
                source_anchor_id=aid,
            )
            add_req(
                "06",
                "工程关键工序识别与控制措施需体现图纸节点与净高/碰撞控制",
                "semantic",
                {"hints": a.get("anchor_value")},
                mandatory=False,
                weight=1.45,
                source_anchor_id=aid,
            )
            add_req(
                "12",
                "专业穿插与移交条件需覆盖图纸接口和预留预埋",
                "presence",
                {"keywords": ["预留", "预埋", "接口", "穿插", "移交", "净高"]},
                mandatory=False,
                weight=1.35,
                source_anchor_id=aid,
            )
        elif key == "site_photo_risk_points":
            add_req(
                "07",
                "重难点及危大工程应覆盖现场风险实况",
                "semantic",
                {"hints": a.get("anchor_value")},
                mandatory=True,
                weight=1.8,
                source_anchor_id=aid,
            )
            add_req(
                "02",
                "安全生产措施应对应现场照片识别的隐患点",
                "presence",
                {"keywords": ["临边", "高处", "深基坑", "塔吊", "脚手架", "消防"]},
                mandatory=True,
                weight=1.6,
                source_anchor_id=aid,
            )
            add_req(
                "03",
                "文明施工管理体系与实施措施应对应现场照片中的扬尘/围挡/道路等实况",
                "presence",
                {"keywords": ["扬尘", "围挡", "道路", "冲洗", "材料堆放", "污水"]},
                mandatory=False,
                weight=1.3,
                source_anchor_id=aid,
            )

    region_value = str(region or "").strip()
    if region_value == "合肥" and _is_v2_engine(scoring_engine_version):
        pack = _load_base_requirement_pack()
        if pack:
            requirements.extend(_expand_pack_requirements(project_id, pack))

    return _merge_requirements_with_priority(requirements)
