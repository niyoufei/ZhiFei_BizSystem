from __future__ import annotations

import copy
import hashlib
import math
import re
from datetime import datetime, timezone
from typing import Dict, Iterable, Sequence

from app.engine.tender_profile import (
    TenderProfileValidationError,
    tender_profile_from_dict,
    tender_profile_to_dict,
)
from app.tender_criteria_catalog import (
    CATALOG_VERSION,
    PROJECT_TYPE_CATALOGS,
    THEME_ORDER,
    catalog_total,
    combined_catalog_entries,
)

_SECTION_MARKERS = (
    "评审因素",
    "评审标准",
    "评分标准",
    "评分办法",
    "技术标评分",
    "施工组织设计评分",
)
_SECTION_END_MARKERS = ("投标报价", "商务部分", "价格评分", "报价得分")
_REQUIREMENT_MARKERS = (
    "应",
    "须",
    "需",
    "包括",
    "合理",
    "可行",
    "完整",
    "针对",
    "明确",
    "满足",
    "采用",
    "确保",
    "体现",
)
_REDLINE_MARKERS = ("否决", "废标", "无效", "不得分", "零分", "扣分")
_DOMAIN_TERMS = (
    "工程概况",
    "施工部署",
    "施工方案",
    "施工方法",
    "施工工艺",
    "施工进度",
    "进度计划",
    "工期保证",
    "质量保证",
    "质量管理",
    "安全生产",
    "安全管理",
    "文明施工",
    "环境保护",
    "资源配置",
    "劳动力",
    "机械设备",
    "材料供应",
    "重难点",
    "关键工序",
    "应急预案",
    "总平面",
    "季节施工",
    "成品保护",
    "技术措施",
    "组织机构",
    "项目经理",
    "BIM",
)
_ITEM_PATTERNS = (
    re.compile(
        r"^\s*(?:第?[一二三四五六七八九十百0-9]+(?:[.、．）)]|项)\s*)?"
        r"(?P<name>[\u4e00-\u9fffA-Za-z0-9·、/（）()\-\s]{2,64}?)"
        r"\s*[（(]\s*(?P<score>\d+(?:\.\d+)?)\s*分\s*[）)]\s*$"
    ),
    re.compile(
        r"^\s*(?:第?[一二三四五六七八九十百0-9]+(?:[.、．）)]|项)\s*)"
        r"(?P<name>[\u4e00-\u9fffA-Za-z0-9·、/（）()\-\s]{2,64}?)"
        r"\s*[:：]?\s*(?P<score>\d+(?:\.\d+)?)\s*分\s*$"
    ),
)
_NUMBERED_REQUIREMENT_PATTERN = re.compile(
    r"^\s*(?:[（(]?\d+[）).、．]|[一二三四五六七八九十]+[、.．）)])\s*"
)
_UNNUMBERED_TABLE_SCORE_ROW_PATTERN = re.compile(
    r"^\s*[\u4e00-\u9fffA-Za-z][\u4e00-\u9fffA-Za-z0-9·、/（）()\-\s]{1,30}"
    r"\s+\d+(?:\.\d+)?\s*分\s*$"
)
_REQUIREMENT_BLOCK_END_MARKERS = ("注：", "注:", "较差得", "一般得", "优秀得")

_EXPERT_ATTENTION_TEMPLATES: dict[str, dict[str, object]] = {
    "project_understanding": {
        "attention": (4.0, 7.0, 10.0),
        "points": (
            ("工程概况、建设目标与范围边界", "核对项目规模、范围、目标及接口边界是否准确。", 6.0, 8.0, 10.0),
            ("现场条件、周边环境与外部约束", "核对场地、交通、既有设施、扰民及接口条件是否有事实依据。", 5.0, 7.5, 10.0),
            ("总体施工部署、施工顺序与总平面", "核对阶段划分、施工流向、平面布置和临设安排是否闭环。", 5.0, 7.5, 10.0),
            ("管理组织、职责分工与协调机制", "核对岗位责任及跨专业、参建方协调是否落实到人和流程。", 4.0, 6.5, 9.0),
            ("重难点与风险识别及针对性对策", "核对重难点是否项目特异，措施是否对应原因且可执行。", 6.0, 8.0, 10.0),
            ("成品保护、收尾与验收移交", "核对保护、整改、资料、验收和移交链条是否完整。", 4.0, 6.5, 9.0),
        ),
    },
    "innovation": {
        "attention": (0.0, 5.0, 10.0),
        "points": (
            ("适用性与项目痛点映射", "先证明适用场景；无适用场景时应明确标注不适用。", 0.0, 5.0, 10.0),
            ("技术原理、成熟度与已有应用", "核对标准、工法、案例或检测依据，避免概念堆砌。", 2.0, 5.0, 8.0),
            ("实施流程、关键参数与控制点", "核对工序、参数、责任人和验收标准。", 3.0, 6.0, 9.0),
            ("资源条件、接口与兼容性", "核对设备、人员、材料、软件及既有工序条件。", 2.0, 5.0, 8.0),
            ("试验或样板验证及质量安全风险", "核对先试后用、失败回退及风险控制。", 3.0, 6.0, 9.0),
            ("量化收益与验证证据", "核对工期、质量、成本或环保收益及其测量方法。", 2.0, 5.0, 8.0),
        ),
    },
    "schedule_quality": {
        "attention": (6.0, 8.0, 10.0),
        "points": (
            ("总进度、里程碑与关键线路", "核对逻辑关系、关键节点和合同工期一致性。", 7.0, 8.5, 10.0),
            ("工序穿插与资源联动计划", "核对人材机供给是否与关键线路同周期匹配。", 6.0, 8.0, 10.0),
            ("进度监测、偏差预警与纠偏", "核对基线、频率、阈值、责任人和赶工预案。", 6.0, 8.0, 10.0),
            ("质量目标、组织体系与制度", "核对质量目标是否量化、责任链是否完整。", 5.0, 7.5, 10.0),
            ("关键工序质量控制、检验与追溯", "核对样板、旁站、实测实量、隐蔽验收及记录链。", 7.0, 8.5, 10.0),
            ("成品保护、缺陷整改与验收移交", "核对保护责任、问题闭环和移交资料。", 5.0, 7.5, 10.0),
        ),
    },
    "resources": {
        "attention": (5.0, 7.5, 10.0),
        "points": (
            ("劳动力计划、专业结构与持证能力", "核对工种、数量、资格与各阶段需求。", 5.0, 7.5, 10.0),
            ("人员进退场、高峰调配与劳动保障", "核对时间曲线、替补机制和队伍稳定措施。", 4.0, 6.5, 9.0),
            ("材料计划、品牌规格与技术参数", "核对清单、标准和招标要求一致性。", 5.0, 7.5, 10.0),
            ("采购供应、进场检验、复验与追溯", "核对供应周期、验收、复检和批次记录。", 6.0, 8.0, 10.0),
            ("机械设备选型、数量、进场与验收", "核对产能、工况匹配、检验和操作资质。", 5.0, 7.5, 10.0),
            ("维护保养、备用能力与动态调配", "核对故障替代、保养周期和高峰保障。", 4.0, 6.5, 9.0),
        ),
    },
    "safety_civil": {
        "attention": (6.0, 8.0, 10.0),
        "points": (
            ("安全目标、组织体系与岗位责任", "核对责任链、保障资源和考核制度。", 6.0, 8.0, 10.0),
            ("重大危险源辨识与危大工程专项方案", "核对清单、专项方案、审批论证及监测要求。", 7.0, 8.5, 10.0),
            ("安全教育、技术交底、检查与防护", "核对频次、对象、记录和现场防护闭环。", 7.0, 8.5, 10.0),
            ("应急预案、救援资源与演练", "核对场景、响应链、物资、联络和演练记录。", 6.0, 8.0, 10.0),
            ("文明施工、场区卫生、围挡与秩序", "核对标准化、卫生、标识、消防和临设管理。", 4.0, 6.5, 9.0),
            ("周边交通、扰民风险与协调处置", "核对噪声时段、交通疏解、投诉及沟通机制。", 4.0, 6.5, 9.0),
        ),
    },
    "green": {
        "attention": (3.0, 6.0, 9.0),
        "points": (
            ("绿色施工目标、标准与适用性", "核对绿色等级、合同目标和项目适用条款。", 3.0, 6.0, 9.0),
            ("节能、节水、节材与节地措施", "核对具体措施、指标、责任人和计量方式。", 4.0, 7.0, 10.0),
            ("绿色材料选用、认证与有害物控制", "核对证书、环保参数、再生含量及进场证明。", 4.0, 7.0, 10.0),
            ("扬尘、噪声、污水与光污染控制", "核对控制设备、阈值、监测频率及超限处置。", 5.0, 7.5, 10.0),
            ("废弃物分类、回收与合规处置", "核对分类目录、暂存、去向和处置联单。", 4.0, 7.0, 10.0),
            ("监测记录、量化绩效与持续改进", "核对台账、趋势分析、复盘和纠偏证据。", 3.0, 6.0, 9.0),
        ),
    },
    "generic": {
        "attention": (0.0, 5.0, 10.0),
        "points": (
            ("要求理解与响应边界", "核对是否完整理解原要求及其适用范围。", 0.0, 5.0, 10.0),
            ("实施措施与责任分工", "核对措施、资源、责任人和实施时序。", 0.0, 5.0, 10.0),
            ("验证证据与闭环记录", "核对验收标准、记录、追溯和纠偏闭环。", 0.0, 5.0, 10.0),
        ),
    },
}

_EXPERT_SELECTOR_V2 = "expert-point-selector-v2"
_EXPERT_SELECTOR_V3 = "expert-point-selector-v3"
_EXPERT_SELECTOR_V4 = "expert-point-selector-v4"
_SUBJECT_OPTIMIZATION_POLICY = {
    "version": "secondary-subject-optimization-v1",
    "mode": "versioned_offline",
    "state": "baseline_locked",
    "baseline": "primary-scene-catalog",
    "evidence_source": "verified_real_cases",
    "promotion_gate": "external_expert_benchmark",
    "auto_apply": False,
}
_MIN_EXPERT_POINTS = 2
_V2_MAX_EXPERT_POINTS = 8
_V3_MAX_EXPERT_POINTS = 64
_V3_FOCUSED_MAX_EXPERT_POINTS = 5
_V3_COMPOSITE_MAX_EXPERT_POINTS = 9
_MAX_REQUIREMENTS_PER_ITEM = 24
_REQUIREMENT_SCAN_WINDOW = 64

# A requirement may legitimately span more than one professional theme.  The
# insertion order is intentional and is used as the deterministic tie breaker.
_EXPERT_TEMPLATE_SIGNALS: dict[str, tuple[str, ...]] = {
    "project_understanding": ("整体理解", "项目理解", "工程概况", "总体部署"),
    "innovation": ("新技术", "新工艺", "新材料", "创新", "BIM", "智慧建造"),
    "schedule_quality": (
        "工期",
        "进度",
        "质量",
        "关键线路",
        "里程碑",
        "验收",
        "成品保护",
    ),
    "resources": (
        "人、材、机",
        "人材机",
        "劳动力",
        "材料",
        "机械",
        "设备",
        "资源配置",
    ),
    "safety_civil": ("安全", "文明", "危大", "危险源", "应急", "消防", "围挡"),
    "green": (
        "绿色",
        "环保",
        "节能",
        "节水",
        "节材",
        "扬尘",
        "噪声",
        "废弃物",
    ),
}

_POINT_RELEVANCE_SIGNALS: tuple[str, ...] = tuple(
    dict.fromkeys(
        signal
        for signals in _EXPERT_TEMPLATE_SIGNALS.values()
        for signal in signals
    )
) + (
    "范围",
    "边界",
    "现场",
    "周边",
    "部署",
    "顺序",
    "总平面",
    "组织",
    "职责",
    "协调",
    "重难点",
    "风险",
    "移交",
    "适用",
    "成熟度",
    "参数",
    "控制点",
    "样板",
    "收益",
    "纠偏",
    "检验",
    "追溯",
    "供应",
    "进场",
    "维护",
    "救援",
    "演练",
    "交通",
    "扰民",
    "污染",
    "监测",
)

# Scene points supplement, rather than replace, the professional candidates.
# Each entry has a stable catalogue index so re-ranking does not change IDs.
_PROJECT_SCENE_CANDIDATES: dict[str, dict[str, object]] = {
    "new_building": {
        "label": "新建房建",
        "signals": ("新建", "房建", "住宅楼", "办公楼", "教学楼", "主体结构"),
        "point_themes": (
            ("project_understanding", "schedule_quality"),
            ("project_understanding", "schedule_quality", "resources"),
            ("schedule_quality",),
        ),
        "points": (
            (101, "基础主体与围护系统衔接", "核对基础、主体、围护和装饰阶段的界面及验收条件。", 4.0, 7.0, 10.0),
            (102, "垂直运输与施工总平面动态调整", "核对塔吊、施工电梯、道路和临设随阶段转换的匹配关系。", 4.0, 7.0, 10.0),
            (103, "样板引路与分阶段验收", "核对样板、首件、隐蔽和分部分项验收的闭环安排。", 4.0, 7.0, 10.0),
        ),
    },
    "building_renovation": {
        "label": "房建维修改造",
        "signals": ("改造", "维修", "修缮", "翻新", "既有建筑", "拆除", "加固"),
        "point_themes": (
            ("project_understanding", "safety_civil"),
            ("schedule_quality", "safety_civil"),
            ("project_understanding", "schedule_quality", "safety_civil"),
        ),
        "points": (
            (111, "既有状况复核与隐蔽风险排查", "核对原结构、机电、装饰现状及不可预见条件的复核机制。", 5.0, 8.0, 10.0),
            (112, "拆除加固、保护与新旧界面处理", "核对拆除顺序、临时支撑、成品保护和新旧材料连接。", 6.0, 8.0, 10.0),
            (113, "分区施工与运营连续性", "核对隔离、导改、停复工窗口和分区移交，降低对既有使用的影响。", 5.0, 8.0, 10.0),
        ),
    },
    "municipal": {
        "label": "市政工程",
        "signals": ("市政", "道路", "桥梁", "隧道", "管网", "交通导改", "地下管线"),
        "point_themes": (
            ("project_understanding", "schedule_quality", "safety_civil"),
            ("project_understanding", "schedule_quality", "safety_civil"),
            ("project_understanding", "green", "safety_civil"),
        ),
        "points": (
            (121, "交通组织与分阶段导改", "核对交通疏解、行人车辆安全和占道转换条件。", 5.0, 8.0, 10.0),
            (122, "地下管线探查与迁改保护", "核对探测、交底、迁改、监测和产权单位协同。", 6.0, 8.0, 10.0),
            (123, "排水防汛与公共界面协调", "核对临排、汛期保障、沿线单位及公众沟通。", 5.0, 7.5, 10.0),
        ),
    },
    "mep_installation": {
        "label": "机电安装",
        "signals": ("机电安装", "安装工程", "暖通", "给排水", "强电", "弱电", "消防安装"),
        "point_themes": (
            ("project_understanding", "schedule_quality"),
            ("schedule_quality", "resources"),
            ("schedule_quality",),
        ),
        "points": (
            (131, "综合管线深化与专业碰撞协调", "核对净高、检修空间、支吊架和多专业交叉界面。", 5.0, 8.0, 10.0),
            (132, "设备材料进场、安装与单机调试", "核对设备参数、到货验收、安装精度和单机试运。", 5.0, 7.5, 10.0),
            (133, "系统联调、性能测试与移交", "核对联动逻辑、检测报告、培训和运维资料。", 6.0, 8.0, 10.0),
        ),
    },
    "landscape": {
        "label": "园林工程",
        "signals": ("园林", "景观", "绿化", "苗木", "种植", "养护"),
        "point_themes": (
            ("project_understanding", "green"),
            ("schedule_quality", "resources", "green"),
            ("schedule_quality", "green"),
        ),
        "points": (
            (141, "地形整理、排水与土壤改良", "核对标高、坡向、基层排水和种植土指标。", 4.0, 7.0, 10.0),
            (142, "苗木选型、季节适应与种植质量", "核对品种规格、检疫、季节窗口和种植工艺。", 5.0, 7.5, 10.0),
            (143, "成活率目标与养护移交", "核对灌溉、修剪、病虫害防治、补植和养护责任期。", 5.0, 8.0, 10.0),
        ),
    },
}

_BUILDING_SCENE_SIGNALS = (
    "房建",
    "住宅楼",
    "办公楼",
    "教学楼",
    "综合楼",
    "厂房",
    "公寓",
    "酒店",
    "医院",
    "校舍",
    "建筑工程",
    "土建工程",
    "主体结构",
)
_RENOVATION_SIGNALS = ("改造", "维修", "修缮", "翻新", "既有建筑", "拆除", "加固")
_MUNICIPAL_SCENE_SIGNALS = (
    "市政",
    "道路",
    "桥梁",
    "隧道",
    "管网",
    "交通导改",
    "地下管线",
    "综合管廊",
)
_MEP_SCENE_SIGNALS = ("机电安装", "安装工程", "暖通", "给排水", "强电", "弱电", "消防安装")
_LANDSCAPE_SCENE_SIGNALS = ("园林", "景观", "绿化", "苗木", "种植", "养护")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean_line(value: object) -> str:
    text = str(value or "").replace("\u3000", " ").strip()
    return re.sub(r"\s+", " ", text)


def _clean_item_name(value: str) -> str:
    name = re.sub(r"^第?[一二三四五六七八九十百0-9]+(?:[.、．）)]|项)\s*", "", value)
    return name.strip(" ：:；;，,。")


def _match_item(line: str) -> tuple[str, float] | None:
    if len(line) > 96 or any(marker in line for marker in ("每", "得分", "扣", "最高得")):
        return None
    for pattern in _ITEM_PATTERNS:
        match = pattern.match(line)
        if not match:
            continue
        name = _clean_item_name(match.group("name"))
        score = float(match.group("score"))
        if len(name) < 2 or score <= 0 or score > 1000:
            return None
        return name, score
    return None


def _looks_like_unnumbered_table_score_row(line: str) -> bool:
    if len(line) > 48 or any(
        marker in line for marker in ("每", "得分", "扣", "最高", "最低", "满分")
    ):
        return False
    return _UNNUMBERED_TABLE_SCORE_ROW_PATTERN.fullmatch(line) is not None


def _split_table_technical_item(lines: Sequence[str]) -> dict[str, object] | None:
    """Recover the technical criterion when PDF table columns are emitted out of order."""
    for line_index, line in enumerate(lines):
        compact = re.sub(r"\s+", "", line)
        if not re.search(
            r"(?:依据|根据|对)投标人.*施工组织设计.*进行评审",
            compact,
        ):
            continue

        score_lines = lines[line_index : min(len(lines), line_index + 16)]
        score_window = " ".join(score_lines)
        band_upper_bounds = [
            float(value)
            for value in re.findall(r"F\s*[＜<≤]\s*(\d+(?:\.\d+)?)", score_window)
        ]
        standalone_scores = [
            float(value)
            for row in score_lines
            if (match := re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*分\s*", row))
            for value in (match.group(1),)
        ]
        score_values = band_upper_bounds or standalone_scores
        score_values = [value for value in score_values if 0 < value <= 100]
        if not score_values:
            continue
        return {
            "name": "施工组织设计",
            "max_score": max(score_values),
            "line_index": line_index,
            "source_locator": f"第 {line_index + 1} 行",
            "source_text": line,
        }
    return None


def _default_bands(max_score: float) -> list[dict[str, object]]:
    return [
        {
            "band_id": "needs-improvement",
            "label": "待改进",
            "min_score": 0.0,
            "max_score": round(max_score * 0.6, 4),
            "description": "关键要求覆盖不足。",
            "triggers": [],
        },
        {
            "band_id": "qualified",
            "label": "合格",
            "min_score": round(max_score * 0.6, 4),
            "max_score": round(max_score * 0.85, 4),
            "description": "主要要求已覆盖，仍有可补强项。",
            "triggers": [],
        },
        {
            "band_id": "excellent",
            "label": "优秀",
            "min_score": round(max_score * 0.85, 4),
            "max_score": float(max_score),
            "description": "关键要求覆盖充分且证据清晰。",
            "triggers": [],
        },
    ]


def _stable_id(prefix: str, value: str, index: int) -> str:
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:8]
    return f"{prefix}-{index:02d}-{digest}"


def _compact_text(value: object) -> str:
    return re.sub(r"\s+", "", str(value or "")).lower()


def _expert_template_keys(requirement: str) -> tuple[str, ...]:
    compact = _compact_text(requirement)
    keys = tuple(
        key
        for key, signals in _EXPERT_TEMPLATE_SIGNALS.items()
        if any(signal.lower() in compact for signal in signals)
    )
    if all(marker in compact for marker in ("人", "材", "机")) and "resources" not in keys:
        keys += ("resources",)
    return keys or ("generic",)


def _infer_scene_tags(project_name: str, source_context: str) -> tuple[str, ...]:
    def classify(value: str) -> tuple[str, ...]:
        compact = _compact_text(value)
        has_building = any(signal in compact for signal in _BUILDING_SCENE_SIGNALS)
        has_renovation = any(signal in compact for signal in _RENOVATION_SIGNALS)
        matched = {
            "new_building": has_building and not has_renovation,
            "building_renovation": has_building and has_renovation,
            "municipal": any(signal in compact for signal in _MUNICIPAL_SCENE_SIGNALS),
            "mep_installation": any(signal in compact for signal in _MEP_SCENE_SIGNALS),
            "landscape": any(signal in compact for signal in _LANDSCAPE_SCENE_SIGNALS),
        }
        signal_groups = {
            "new_building": _BUILDING_SCENE_SIGNALS,
            "building_renovation": _BUILDING_SCENE_SIGNALS,
            "municipal": _MUNICIPAL_SCENE_SIGNALS,
            "mep_installation": _MEP_SCENE_SIGNALS,
            "landscape": _LANDSCAPE_SCENE_SIGNALS,
        }

        def first_anchor(tag: str) -> int:
            positions = [
                compact.find(signal)
                for signal in signal_groups[tag]
                if compact.find(signal) >= 0
            ]
            return min(positions) if positions else len(compact)

        catalog_order = {tag: index for index, tag in enumerate(PROJECT_TYPE_CATALOGS)}
        matched_tags = [tag for tag in PROJECT_TYPE_CATALOGS if matched[tag]]
        matched_tags.sort(key=lambda tag: (first_anchor(tag), catalog_order[tag]))
        return tuple(matched_tags)

    # Project names carry the user's intended engineering category.  Tender
    # body text is only a fallback because it routinely mentions adjacent
    # trades and would otherwise manufacture false compound categories.
    project_tags = classify(project_name)
    return project_tags or classify(source_context)


def _submitted_selection_context(
    attention_profile: object,
) -> tuple[str | None, tuple[str, ...]]:
    if not isinstance(attention_profile, dict) or "selection_context" not in attention_profile:
        return None, ()
    context = attention_profile.get("selection_context")
    if not isinstance(context, dict):
        raise TenderProfileValidationError("attention_profile.selection_context 必须是对象")
    version = str(context.get("version") or "")
    if version not in {
        _EXPERT_SELECTOR_V2,
        _EXPERT_SELECTOR_V3,
        _EXPERT_SELECTOR_V4,
    }:
        raise TenderProfileValidationError("attention_profile.selection_context.version 非法")
    raw_tags = context.get("scene_tags")
    if not isinstance(raw_tags, list):
        raise TenderProfileValidationError("attention_profile.selection_context.scene_tags 必须是列表")
    tag_set = {str(tag) for tag in raw_tags}
    if len(tag_set) != len(raw_tags) or not tag_set.issubset(PROJECT_TYPE_CATALOGS):
        raise TenderProfileValidationError("attention_profile.selection_context.scene_tags 非法")
    canonical_tags = (
        tuple(str(tag) for tag in raw_tags)
        if version == _EXPERT_SELECTOR_V4
        else tuple(tag for tag in PROJECT_TYPE_CATALOGS if tag in tag_set)
    )
    return version, canonical_tags


def _select_expert_points_v2(
    requirement: str,
    *,
    item_name: str,
    tender_name: str,
    scene_tags: Sequence[str],
) -> tuple[dict[str, object], ...]:
    template_keys = _expert_template_keys(requirement)
    candidates: list[dict[str, object]] = []
    seen_names: set[str] = set()
    for template_order, key in enumerate(template_keys):
        for point_index, point in enumerate(
            tuple(_EXPERT_ATTENTION_TEMPLATES[key]["points"]), start=1
        ):
            name, description, minimum, default, maximum = point
            name = str(name)
            if name in seen_names:
                continue
            seen_names.add(name)
            candidates.append(
                {
                    "catalog_index": point_index,
                    "name": name,
                    "description": str(description),
                    "attention": (minimum, default, maximum),
                    "source_type": "expert_template",
                    "theme": key,
                    "scene_tag": None,
                    "order": (template_order, point_index),
                }
            )
    for scene_order, scene_tag in enumerate(scene_tags, start=len(template_keys)):
        scene = _PROJECT_SCENE_CANDIDATES[scene_tag]
        point_themes = tuple(scene["point_themes"])
        for point_order, point in enumerate(tuple(scene["points"]), start=1):
            if not set(template_keys).intersection(point_themes[point_order - 1]):
                continue
            catalog_index, name, description, minimum, default, maximum = point
            name = str(name)
            if name in seen_names:
                continue
            seen_names.add(name)
            candidates.append(
                {
                    "catalog_index": int(catalog_index),
                    "name": name,
                    "description": str(description),
                    "attention": (minimum, default, maximum),
                    "source_type": "expert_scene",
                    "theme": None,
                    "scene_tag": scene_tag,
                    "order": (scene_order, point_order),
                }
            )

    compact_requirement = _compact_text(requirement)
    signal_count = sum(
        1 for signal in _POINT_RELEVANCE_SIGNALS if signal.lower() in compact_requirement
    )
    clause_count = len(
        [part for part in re.split(r"[;；。。]", str(requirement)) if part.strip()]
    )
    applicable_scene_tags = tuple(
        tag
        for tag in scene_tags
        if any(candidate["scene_tag"] == tag for candidate in candidates)
    )
    scene_bonus = 0
    if applicable_scene_tags:
        scene_bonus = 2 if any(
            tag in {"building_renovation", "municipal", "mep_installation"}
            for tag in applicable_scene_tags
        ) else 1
    target_count = 2
    target_count += min(2, max(0, len(template_keys) - 1))
    target_count += min(2, max(0, clause_count - 1))
    target_count += 1 if signal_count >= 2 else 0
    target_count += scene_bonus
    target_count = max(
        target_count,
        min(_V2_MAX_EXPERT_POINTS, len(template_keys) + len(applicable_scene_tags)),
    )
    target_count = min(_V2_MAX_EXPERT_POINTS, max(_MIN_EXPERT_POINTS, target_count))
    target_count = min(target_count, len(candidates))

    selected: list[dict[str, object]] = []
    for key in template_keys:
        row = next((candidate for candidate in candidates if candidate["theme"] == key), None)
        if row is not None and row not in selected:
            selected.append(row)
    for scene_tag in applicable_scene_tags:
        row = next(
            (candidate for candidate in candidates if candidate["scene_tag"] == scene_tag),
            None,
        )
        if row is not None and row not in selected:
            selected.append(row)

    context = _compact_text(f"{requirement} {item_name} {tender_name}")

    def relevance(candidate: dict[str, object]) -> tuple[int, tuple[int, int]]:
        candidate_text = _compact_text(f"{candidate['name']} {candidate['description']}")
        overlap = sum(
            1
            for signal in _POINT_RELEVANCE_SIGNALS
            if signal.lower() in context and signal.lower() in candidate_text
        )
        scene_score = 1 if candidate["scene_tag"] is not None else 0
        return overlap * 10 + scene_score, tuple(candidate["order"])

    remaining = [candidate for candidate in candidates if candidate not in selected]
    remaining.sort(key=lambda candidate: (-relevance(candidate)[0], relevance(candidate)[1]))
    selected.extend(remaining[: max(0, target_count - len(selected))])
    return tuple(selected[:target_count])


def _requirement_scope(requirement: str, themes: Sequence[str]) -> str:
    compact = _compact_text(requirement)
    if len(themes) > 1:
        return "composite"
    theme = themes[0] if themes else "generic"
    umbrella = False
    if theme == "project_understanding":
        umbrella = any(marker in compact for marker in ("整体理解", "项目理解", "总体部署"))
    elif theme == "innovation":
        umbrella = sum(marker in compact for marker in ("新技术", "新工艺", "创新")) >= 2
    elif theme == "schedule_quality":
        umbrella = "工期" in compact and "质量" in compact
    elif theme == "resources":
        umbrella = (
            all(marker in compact for marker in ("人", "材", "机"))
            or "资源配置" in compact
        )
    elif theme == "safety_civil":
        umbrella = "安全" in compact and "文明" in compact
    elif theme == "green":
        umbrella = any(marker in compact for marker in ("绿色建筑", "绿色施工"))
    if umbrella:
        return "umbrella"
    clause_text = re.sub(r"^\s*\d+[.、）)]\s*", "", str(requirement))
    clause_count = len(
        [part for part in re.split(r"[;；。]", clause_text) if part.strip()]
    )
    return "composite" if clause_count > 1 else "focused"


def _catalog_relevance(entry: dict[str, str], requirement: str) -> int:
    requirement_text = _compact_text(requirement)
    candidate_text = _compact_text(entry.get("name"))
    signal_score = sum(
        20
        for signal in _POINT_RELEVANCE_SIGNALS
        if signal.lower() in requirement_text and signal.lower() in candidate_text
    )
    requirement_text = re.sub(
        r"(?:第?[一二三四五六七八九十0-9]+[.、）)]|应|须|需|确保|明确|合理|进行|措施|要求)",
        "",
        requirement_text,
    )
    requirement_grams = {
        requirement_text[index : index + 2]
        for index in range(max(0, len(requirement_text) - 1))
    }
    candidate_grams = {
        candidate_text[index : index + 2]
        for index in range(max(0, len(candidate_text) - 1))
    }
    return signal_score + len(requirement_grams.intersection(candidate_grams))


def _fallback_v3_points(
    requirement: str,
    *,
    item_name: str,
    tender_name: str,
) -> tuple[dict[str, object], ...]:
    rows = _select_expert_points_v2(
        requirement,
        item_name=item_name,
        tender_name=tender_name,
        scene_tags=(),
    )
    fallback: list[dict[str, object]] = []
    for row in rows:
        theme = str(row.get("theme") or _expert_template_keys(requirement)[0])
        identity = f"fallback:{theme}:{_compact_text(row['name'])}"
        fallback.append(
            {
                **row,
                "catalog_id": hashlib.sha256(identity.encode("utf-8")).hexdigest(),
                "catalog_code": f"F{THEME_ORDER.index(theme) + 1}.{int(row['catalog_index']):02d}"
                if theme in THEME_ORDER
                else f"F0.{int(row['catalog_index']):02d}",
            }
        )
    return tuple(fallback)


def _select_expert_points_v3(
    requirement: str,
    *,
    item_name: str,
    tender_name: str,
    scene_tags: Sequence[str],
) -> tuple[str, tuple[str, ...], tuple[dict[str, object], ...]]:
    themes = _expert_template_keys(requirement)
    scope = _requirement_scope(requirement, themes)
    if not scene_tags:
        return scope, themes, _fallback_v3_points(
            requirement,
            item_name=item_name,
            tender_name=tender_name,
        )

    catalog = combined_catalog_entries(scene_tags)
    candidates = (
        catalog
        if themes == ("generic",)
        else [entry for entry in catalog if entry["theme"] in themes]
    )
    if scope == "umbrella":
        selected_entries = candidates[:_V3_MAX_EXPERT_POINTS]
    else:
        ranked = sorted(
            enumerate(candidates),
            key=lambda pair: (-_catalog_relevance(pair[1], requirement), pair[0]),
        )
        positive = [
            entry for _, entry in ranked if _catalog_relevance(entry, requirement) > 0
        ]
        selected_entries: list[dict[str, str]] = []
        for theme in themes:
            seed = next(
                (
                    entry
                    for entry in positive
                    if entry["theme"] == theme
                ),
                None,
            )
            if seed is not None and seed not in selected_entries:
                selected_entries.append(seed)
        limit = (
            _V3_COMPOSITE_MAX_EXPERT_POINTS
            if scope == "composite"
            else _V3_FOCUSED_MAX_EXPERT_POINTS
        )
        for entry in positive:
            if entry not in selected_entries:
                selected_entries.append(entry)
            if len(selected_entries) >= limit:
                break
        selected_entries = selected_entries[:limit]

    if themes == ("generic",) and selected_entries:
        themes = tuple(
            theme
            for theme in THEME_ORDER
            if any(entry["theme"] == theme for entry in selected_entries)
        )

    selected: list[dict[str, object]] = []
    for order, entry in enumerate(selected_entries, start=1):
        theme = entry["theme"]
        selected.append(
            {
                "catalog_index": order,
                "catalog_id": entry["catalog_id"],
                "catalog_code": entry["code"],
                "name": entry["name"],
                "description": f"核对“{entry['name']}”是否形成明确、可执行且可追溯的响应证据。",
                "attention": tuple(_EXPERT_ATTENTION_TEMPLATES[theme]["attention"]),
                "source_type": "expert_catalog",
                "theme": theme,
                "scene_tag": entry["scene_tag"],
                "order": (THEME_ORDER.index(theme), order),
            }
        )
    return scope, themes, tuple(selected)


def _select_expert_points_v4(
    requirement: str,
    *,
    item_name: str,
    tender_name: str,
    scene_tags: Sequence[str],
) -> tuple[str, tuple[str, ...], tuple[dict[str, object], ...]]:
    """Keep umbrella requirements on one primary catalog.

    Supporting scenes remain available for focused or composite tender clauses,
    but they no longer contribute their complete catalogs to generic umbrella
    requirements.  This preserves the original compact per-project baseline.
    """
    themes = _expert_template_keys(requirement)
    scope = _requirement_scope(requirement, themes)
    selection_scene_tags = (
        tuple(scene_tags[:1]) if scope == "umbrella" else tuple(scene_tags)
    )
    return _select_expert_points_v3(
        requirement,
        item_name=item_name,
        tender_name=tender_name,
        scene_tags=selection_scene_tags,
    )


def _attention_setting(values: Sequence[float], *, current: float | None = None) -> dict[str, float]:
    minimum, default, maximum = (float(value) for value in values)
    return {
        "min": minimum,
        "default": default,
        "max": maximum,
        "current": default if current is None else float(current),
    }


def _submitted_attention_values(
    attention_profile: object,
) -> tuple[
    set[str],
    dict[str, float],
    dict[str, float],
    dict[tuple[str, str], float],
]:
    if not isinstance(attention_profile, dict):
        raise TenderProfileValidationError("attention_profile 必须是对象")
    if str(attention_profile.get("schema_version") or "") != "evidence-attention-v1":
        raise TenderProfileValidationError("attention_profile.schema_version 非法")
    items = attention_profile.get("items")
    if not isinstance(items, list):
        raise TenderProfileValidationError("attention_profile.items 必须是列表")

    item_ids: set[str] = set()
    evidence_values: dict[str, float] = {}
    point_values: dict[str, float] = {}
    point_values_by_name: dict[tuple[str, str], float] = {}
    for item_index, item in enumerate(items):
        if not isinstance(item, dict):
            raise TenderProfileValidationError(
                f"attention_profile.items[{item_index}] 必须是对象"
            )
        item_id = str(item.get("item_id") or "")
        if not item_id or item_id in item_ids:
            raise TenderProfileValidationError("attention_profile.item_id 缺失或重复")
        item_ids.add(item_id)
        evidence_rows = item.get("evidence")
        if not isinstance(evidence_rows, list):
            raise TenderProfileValidationError(
                f"attention_profile.items[{item_index}].evidence 必须是列表"
            )
        for evidence_index, evidence in enumerate(evidence_rows):
            if not isinstance(evidence, dict):
                raise TenderProfileValidationError(
                    f"attention_profile.items[{item_index}].evidence[{evidence_index}] 必须是对象"
                )
            evidence_id = str(evidence.get("evidence_id") or "")
            if not evidence_id or evidence_id in evidence_values:
                raise TenderProfileValidationError("attention_profile.evidence_id 缺失或重复")
            attention = evidence.get("attention")
            if not isinstance(attention, dict) or attention.get("current") is None:
                raise TenderProfileValidationError(
                    f"attention_profile.evidence[{evidence_id}].attention.current 缺失"
                )
            evidence_values[evidence_id] = _validated_attention_number(
                attention["current"],
                f"attention_profile.evidence[{evidence_id}].attention.current",
            )
            points = evidence.get("expert_points")
            if not isinstance(points, list):
                raise TenderProfileValidationError(
                    f"attention_profile.evidence[{evidence_id}].expert_points 必须是列表"
                )
            for point in points:
                if not isinstance(point, dict):
                    raise TenderProfileValidationError(
                        f"attention_profile.evidence[{evidence_id}].expert_points 必须包含对象"
                    )
                point_id = str(point.get("point_id") or "")
                if not point_id or point_id in point_values:
                    raise TenderProfileValidationError("attention_profile.point_id 缺失或重复")
                point_attention = point.get("attention")
                if not isinstance(point_attention, dict) or point_attention.get("current") is None:
                    raise TenderProfileValidationError(
                        f"attention_profile.point[{point_id}].attention.current 缺失"
                    )
                point_values[point_id] = _validated_attention_number(
                    point_attention["current"],
                    f"attention_profile.point[{point_id}].attention.current",
                )
                point_name = str(point.get("name") or "")
                if point_name:
                    point_values_by_name[(evidence_id, point_name)] = point_values[
                        point_id
                    ]
    return item_ids, evidence_values, point_values, point_values_by_name


def _validated_attention_number(value: object, field_name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise TenderProfileValidationError(f"{field_name} 必须是数字") from exc
    if not math.isfinite(number) or abs(number * 2 - round(number * 2)) > 0.000001:
        raise TenderProfileValidationError(f"{field_name} 必须按 0.5 级调整")
    return number


def build_evidence_attention_profile(
    profile_payload: object,
    submitted_attention_profile: object | None = None,
    *,
    source_context: str = "",
) -> dict[str, object]:
    """Build canonical two-level review attention without changing statutory scores."""
    profile = tender_profile_from_dict(profile_payload)
    submitted_item_ids: set[str] = set()
    submitted_evidence: dict[str, float] = {}
    submitted_points: dict[str, float] = {}
    submitted_points_by_name: dict[tuple[str, str], float] = {}
    submitted_selector_version: str | None = None
    scene_tags: tuple[str, ...] = ()
    if submitted_attention_profile is not None:
        (
            submitted_item_ids,
            submitted_evidence,
            submitted_points,
            submitted_points_by_name,
        ) = _submitted_attention_values(submitted_attention_profile)
        submitted_selector_version, scene_tags = _submitted_selection_context(
            submitted_attention_profile
        )
    selector_version = submitted_selector_version or _EXPERT_SELECTOR_V4
    if submitted_selector_version is None:
        scene_tags = _infer_scene_tags(profile.tender_name, source_context)

    expected_item_ids: set[str] = set()
    expected_evidence_ids: set[str] = set()
    expected_point_ids: set[str] = set()
    allocations: list[dict[str, object]] = []
    enabled_catalog_ids: set[str] = set()
    evidence_link_count = 0
    item_rows: list[dict[str, object]] = []
    for item in profile.scoring_items:
        expected_item_ids.add(item.item_id)
        evidence_rows: list[dict[str, object]] = []
        for evidence_index, requirement in enumerate(item.evidence_requirements, start=1):
            evidence_id = _stable_id(
                "evidence",
                f"{item.item_id}:{evidence_index}",
                evidence_index,
            )
            expected_evidence_ids.add(evidence_id)
            template_keys = _expert_template_keys(requirement)
            attention_values = tuple(
                _EXPERT_ATTENTION_TEMPLATES[template_keys[0]]["attention"]
            )
            evidence_current = submitted_evidence.get(evidence_id)
            evidence_attention = _attention_setting(
                attention_values,
                current=evidence_current,
            )
            _validate_attention_range(evidence_attention, f"evidence[{evidence_id}]")

            point_rows: list[dict[str, object]] = []
            if selector_version == _EXPERT_SELECTOR_V2:
                scope = "v2"
                allocation_themes = template_keys
                selected_points = _select_expert_points_v2(
                    str(requirement),
                    item_name=item.name,
                    tender_name=profile.tender_name,
                    scene_tags=scene_tags,
                )
            elif selector_version == _EXPERT_SELECTOR_V3:
                scope, allocation_themes, selected_points = _select_expert_points_v3(
                    str(requirement),
                    item_name=item.name,
                    tender_name=profile.tender_name,
                    scene_tags=scene_tags,
                )
            else:
                scope, allocation_themes, selected_points = _select_expert_points_v4(
                    str(requirement),
                    item_name=item.name,
                    tender_name=profile.tender_name,
                    scene_tags=scene_tags,
                )
            allocation_catalog_ids: list[str] = []
            for point_index, point in enumerate(selected_points, start=1):
                name = str(point["name"])
                description = str(point["description"])
                minimum, default, maximum = tuple(point["attention"])
                if selector_version == _EXPERT_SELECTOR_V2:
                    point_id = _stable_id(
                        "point",
                        f"{evidence_id}:{name}",
                        int(point["catalog_index"]),
                    )
                    catalog_id = None
                    catalog_code = None
                else:
                    catalog_id = str(point["catalog_id"])
                    catalog_code = str(point["catalog_code"])
                    point_id = _stable_id(
                        "point",
                        f"{evidence_id}:{catalog_id}",
                        point_index,
                    )
                    allocation_catalog_ids.append(catalog_id)
                    enabled_catalog_ids.add(catalog_id)
                    evidence_link_count += 1
                expected_point_ids.add(point_id)
                point_current = submitted_points.get(point_id)
                if point_current is None and submitted_selector_version is None:
                    point_current = submitted_points_by_name.get((evidence_id, name))
                point_attention = _attention_setting(
                    (minimum, default, maximum),
                    current=point_current,
                )
                _validate_attention_range(point_attention, f"point[{point_id}]")
                point_row: dict[str, object] = {
                    "point_id": point_id,
                    "name": name,
                    "description": description,
                    "source_type": str(point["source_type"]),
                    "attention": point_attention,
                }
                if catalog_id is not None and catalog_code is not None:
                    point_row["catalog_id"] = catalog_id
                    point_row["catalog_code"] = catalog_code
                point_rows.append(point_row)
            if selector_version in {_EXPERT_SELECTOR_V3, _EXPERT_SELECTOR_V4}:
                allocations.append(
                    {
                        "evidence_id": evidence_id,
                        "requirement_hash": hashlib.sha256(
                            _clean_line(requirement).encode("utf-8")
                        ).hexdigest(),
                        "scope": scope,
                        "themes": list(allocation_themes),
                        "catalog_ids": allocation_catalog_ids,
                    }
                )
            evidence_rows.append(
                {
                    "evidence_id": evidence_id,
                    "requirement": str(requirement),
                    "source_type": "tender_requirement",
                    "attention": evidence_attention,
                    "expert_points": point_rows,
                }
            )
        item_rows.append({"item_id": item.item_id, "evidence": evidence_rows})

    if submitted_attention_profile is not None:
        if submitted_item_ids != expected_item_ids:
            raise TenderProfileValidationError("attention_profile.item_id 与评分项不一致")
        if set(submitted_evidence) != expected_evidence_ids:
            raise TenderProfileValidationError("attention_profile.evidence_id 与评分证据不一致")
        if submitted_selector_version is not None and set(submitted_points) != expected_point_ids:
            raise TenderProfileValidationError("attention_profile.point_id 与专家评分点不一致")

    if selector_version == _EXPERT_SELECTOR_V2:
        selection_context: dict[str, object] = {
            "version": _EXPERT_SELECTOR_V2,
            "scene_tags": list(scene_tags),
            "scene_labels": [
                str(_PROJECT_SCENE_CANDIDATES[tag]["label"]) for tag in scene_tags
            ],
        }
    else:
        primary_scene_tag = scene_tags[0] if scene_tags else None
        supporting_scene_tags = list(scene_tags[1:])
        combined_catalog_total = (
            len(combined_catalog_entries(scene_tags)) if scene_tags else 0
        )
        baseline_catalog_total = (
            catalog_total(primary_scene_tag) if primary_scene_tag is not None else 0
        )
        categories = [
            {
                "tag": tag,
                "label": str(PROJECT_TYPE_CATALOGS[tag]["label"]),
                "catalog_total": catalog_total(tag),
                **(
                    {"role": "primary" if tag == primary_scene_tag else "supporting"}
                    if selector_version == _EXPERT_SELECTOR_V4
                    else {}
                ),
            }
            for tag in scene_tags
        ]
        catalog_summary = {
            "categories": categories,
            "combined_catalog_total": combined_catalog_total,
            "enabled_unique_count": len(enabled_catalog_ids),
            "evidence_link_count": evidence_link_count,
            "catalog_version": CATALOG_VERSION,
        }
        if selector_version == _EXPERT_SELECTOR_V4:
            catalog_summary.update(
                {
                    "baseline_catalog_total": baseline_catalog_total,
                    "primary_category": categories[0] if categories else None,
                    "supporting_categories": categories[1:],
                }
            )
        selection_context = {
            "version": selector_version,
            "scene_tags": list(scene_tags),
            "scene_labels": [
                str(_PROJECT_SCENE_CANDIDATES[tag]["label"]) for tag in scene_tags
            ],
            "sizing_policy": (
                "primary-scene-baseline-v1"
                if selector_version == _EXPERT_SELECTOR_V4
                else "catalog-scope-v1"
            ),
            "catalog_summary": catalog_summary,
            "allocations": allocations,
        }
        if selector_version == _EXPERT_SELECTOR_V4:
            selection_context.update(
                {
                    "primary_scene_tag": primary_scene_tag,
                    "supporting_scene_tags": supporting_scene_tags,
                    "optimization_policy": copy.deepcopy(
                        _SUBJECT_OPTIMIZATION_POLICY
                    ),
                }
            )
        if submitted_selector_version in {_EXPERT_SELECTOR_V3, _EXPERT_SELECTOR_V4}:
            assert isinstance(submitted_attention_profile, dict)
            submitted_context = submitted_attention_profile.get("selection_context")
            if not isinstance(submitted_context, dict):
                raise TenderProfileValidationError(
                    "attention_profile.selection_context 必须是对象"
                )
            fields = ["sizing_policy", "catalog_summary", "allocations"]
            if selector_version == _EXPERT_SELECTOR_V4:
                fields.extend(
                    (
                        "primary_scene_tag",
                        "supporting_scene_tags",
                        "optimization_policy",
                    )
                )
            for field_name in fields:
                if submitted_context.get(field_name) != selection_context[field_name]:
                    raise TenderProfileValidationError(
                        f"attention_profile.selection_context.{field_name} 与专家目录不一致"
                    )

    return {
        "schema_version": "evidence-attention-v1",
        "role": "evidence_review_priority",
        "score_effect": "none",
        "scale": {"min": 0.0, "max": 10.0, "step": 0.5},
        "selection_context": selection_context,
        "items": item_rows,
    }


def _validate_attention_range(attention: dict[str, float], field_name: str) -> None:
    minimum = float(attention["min"])
    default = float(attention["default"])
    maximum = float(attention["max"])
    current = float(attention["current"])
    if not 0 <= minimum <= default <= maximum <= 10:
        raise TenderProfileValidationError(f"{field_name} 关注度区间非法")
    if not minimum <= current <= maximum:
        raise TenderProfileValidationError(
            f"{field_name}.attention.current 必须位于 {minimum:g} 到 {maximum:g}"
        )


def _profile_candidates(lines: Sequence[str]) -> list[dict[str, object]]:
    candidates: list[dict[str, object]] = []
    for line_index, line in enumerate(lines):
        matched = _match_item(line)
        if matched is None:
            continue
        name, max_score = matched
        candidates.append(
            {
                "name": name,
                "max_score": max_score,
                "line_index": line_index,
                "source_locator": f"第 {line_index + 1} 行",
                "source_text": line,
            }
        )

    if not any("施工组织设计" in str(row["name"]) for row in candidates):
        split_table_item = _split_table_technical_item(lines)
        if split_table_item is not None:
            candidates.append(split_table_item)

    unique: list[dict[str, object]] = []
    seen: set[tuple[str, float]] = set()
    for row in candidates:
        key = (str(row["name"]), float(row["max_score"]))
        if key in seen:
            continue
        seen.add(key)
        unique.append(row)

    unique.sort(key=lambda row: int(row["line_index"]))

    if len(unique) > 1:
        total = sum(float(row["max_score"]) for row in unique)
        for row in list(unique):
            score = float(row["max_score"])
            name = str(row["name"])
            looks_like_parent = any(
                marker in name for marker in ("总分", "合计", "技术部分", "施工组织设计", "评审")
            )
            is_first_candidate = int(row["line_index"]) == min(
                int(candidate["line_index"]) for candidate in unique
            )
            if looks_like_parent and is_first_candidate and abs(score - (total - score)) <= 0.000001:
                unique.remove(row)
                break
    return unique[:40]


def _requirements_for_candidate(
    lines: Sequence[str],
    candidate: dict[str, object],
    next_line_index: int,
) -> tuple[list[str], bool]:
    start = int(candidate["line_index"]) + 1
    stop = min(len(lines), next_line_index, start + _REQUIREMENT_SCAN_WINDOW)
    numbered_requirements: list[str] = []
    fallback_requirements: list[str] = []
    truncated = False
    for line in lines[start:stop]:
        if (
            not line
            or line.startswith("--- ")
            or _match_item(line) is not None
            or _looks_like_unnumbered_table_score_row(line)
        ):
            break
        if numbered_requirements and (
            line.startswith(_REQUIREMENT_BLOCK_END_MARKERS)
            or any(marker in line for marker in ("较差得", "一般得", "优秀得"))
        ):
            break
        if len(line) > 180:
            line = line[:180].rstrip() + "…"
        if _NUMBERED_REQUIREMENT_PATTERN.match(line):
            if line not in numbered_requirements:
                if len(numbered_requirements) >= _MAX_REQUIREMENTS_PER_ITEM:
                    truncated = True
                    break
                numbered_requirements.append(line)
            continue
        if numbered_requirements:
            continue
        if any(marker in line for marker in _REQUIREMENT_MARKERS):
            if line not in fallback_requirements:
                if len(fallback_requirements) >= _MAX_REQUIREMENTS_PER_ITEM:
                    truncated = True
                    break
                fallback_requirements.append(line)
    requirements = numbered_requirements or fallback_requirements
    if not requirements:
        requirements.append(str(candidate["name"]))
    return requirements, truncated


def extract_tender_profile_draft(
    *,
    project_id: str,
    project_name: str,
    source_text: str,
) -> Dict[str, object]:
    """Extract a reviewable draft; extraction never approves or activates it."""
    lines = [_clean_line(line) for line in str(source_text or "").splitlines()]
    lines = [line for line in lines if line]
    section_seen = any(any(marker in line for marker in _SECTION_MARKERS) for line in lines)
    candidates = _profile_candidates(lines)

    scoring_items: list[dict[str, object]] = []
    sources: list[dict[str, object]] = []
    truncated_requirement_items: list[str] = []
    for index, candidate in enumerate(candidates, start=1):
        next_index = (
            int(candidates[index]["line_index"]) if index < len(candidates) else len(lines)
        )
        requirements, requirements_truncated = _requirements_for_candidate(
            lines, candidate, next_index
        )
        if requirements_truncated:
            truncated_requirement_items.append(str(candidate["name"]))
        item_id = _stable_id("criterion", str(candidate["name"]), index)
        scoring_items.append(
            {
                "item_id": item_id,
                "name": str(candidate["name"]),
                "max_score": float(candidate["max_score"]),
                "bands": _default_bands(float(candidate["max_score"])),
                "evidence_requirements": requirements,
                "legacy_dimension_refs": [],
            }
        )
        sources.append(
            {
                "item_id": item_id,
                "source_locator": candidate["source_locator"],
                "source_text": candidate["source_text"],
            }
        )

    redlines: list[dict[str, object]] = []
    for line_index, line in enumerate(lines):
        if not any(marker in line for marker in _REDLINE_MARKERS):
            continue
        redlines.append(
            {
                "redline_id": _stable_id("redline", line, len(redlines) + 1),
                "description": line[:220],
                "action": "manual_review",
                "applies_to": [],
                "source_locator": f"第 {line_index + 1} 行",
            }
        )
        if len(redlines) >= 12:
            break

    warnings: list[str] = []
    if not section_seen:
        warnings.append("未识别到明确的评审/评分章节标题，请重点复核提取范围。")
    if not scoring_items:
        warnings.append("未识别到“评分项（分值）”结构，需人工补录后再确认。")
    for item_name in truncated_requirement_items:
        warnings.append(
            f"评分项“{item_name}”证据要求超过 {_MAX_REQUIREMENTS_PER_ITEM} 条，"
            f"已按安全上限保留前 {_MAX_REQUIREMENTS_PER_ITEM} 条，请人工复核。"
        )
    score_scale = round(sum(float(item["max_score"]) for item in scoring_items), 4)
    if scoring_items and score_scale not in (5.0, 10.0, 20.0, 30.0, 40.0, 50.0, 100.0):
        warnings.append(f"提取分值合计为 {score_scale:g}，请核对是否混入父级合计项。")

    confidence = min(
        0.98,
        (0.25 if section_seen else 0.05)
        + min(0.55, len(scoring_items) * 0.08)
        + (0.12 if score_scale > 0 else 0.0),
    )
    profile: dict[str, object] | None = None
    if scoring_items:
        profile = {
            "tender_id": project_id,
            "tender_name": project_name,
            "version": "draft-1",
            "score_scale": score_scale,
            "scoring_items": scoring_items,
            "hard_redlines": [
                {key: value for key, value in row.items() if key != "source_locator"}
                for row in redlines
            ],
            "legacy_dimension_refs": [],
            "source_note": "由项目招标资料确定性提取；须人工复核并确认后才进入正式评分。",
        }
        tender_profile_from_dict(profile)

    attention_profile = (
        build_evidence_attention_profile(profile, source_context=source_text)
        if profile is not None
        else None
    )

    return {
        "status": "draft" if profile is not None else "needs_input",
        "approved": False,
        "confidence": round(confidence, 4),
        "needs_review": True,
        "profile": profile,
        "attention_profile": attention_profile,
        "sources": sources,
        "redline_sources": redlines,
        "warnings": warnings,
        "extracted_at": _now_iso(),
    }


def approve_tender_profile(
    *,
    profile_payload: object,
    draft_state: object | None = None,
    attention_profile: object | None = None,
) -> Dict[str, object]:
    profile = tender_profile_from_dict(profile_payload)
    profile_dict = tender_profile_to_dict(profile)
    draft = draft_state if isinstance(draft_state, dict) else {}
    submitted_attention = (
        attention_profile
        if attention_profile is not None
        else draft.get("attention_profile")
    )
    normalized_attention = build_evidence_attention_profile(
        profile_dict,
        submitted_attention,
    )
    return {
        "status": "approved",
        "approved": True,
        "confidence": float(draft.get("confidence") or 1.0),
        "needs_review": False,
        "profile": profile_dict,
        "attention_profile": normalized_attention,
        "sources": copy.deepcopy(draft.get("sources") or []),
        "redline_sources": copy.deepcopy(draft.get("redline_sources") or []),
        "warnings": copy.deepcopy(draft.get("warnings") or []),
        "extracted_at": draft.get("extracted_at"),
        "approved_at": _now_iso(),
    }


def project_tender_profile_state(project: Dict[str, object]) -> Dict[str, object]:
    meta = project.get("meta") if isinstance(project.get("meta"), dict) else {}
    state = meta.get("tender_profile_state")
    if not isinstance(state, dict):
        return {
            "status": "not_extracted",
            "approved": False,
            "needs_review": True,
            "profile": None,
            "attention_profile": None,
            "sources": [],
            "warnings": [],
        }
    copied = copy.deepcopy(state)
    profile = copied.get("profile")
    if isinstance(profile, dict):
        copied["attention_profile"] = build_evidence_attention_profile(
            profile,
            copied.get("attention_profile"),
        )
    else:
        copied["attention_profile"] = None
    return copied


def _keywords(value: object) -> list[str]:
    text = _clean_line(value)
    terms = [term for term in _DOMAIN_TERMS if term.lower() in text.lower()]
    for match in re.finditer(
        r"(?:结合|明确|包括|包含|设置|制定|建立|说明)([\u4e00-\u9fffA-Za-z0-9]{2,12})",
        text,
    ):
        phrase = match.group(1)
        phrase = re.split(r"(?:和|及|与|并|，|。|；)", phrase, maxsplit=1)[0]
        if len(phrase) >= 2 and phrase not in terms:
            terms.append(phrase)
    if terms:
        return terms[:8]
    chunks = [chunk for chunk in re.split(r"[，,。；;：:\s/、（）()]+", text) if len(chunk) >= 2]
    return chunks[:6]


def _evidence_for(text: str, keywords: Iterable[str]) -> tuple[list[str], list[dict[str, object]]]:
    hits: list[str] = []
    evidence: list[dict[str, object]] = []
    lowered = text.lower()
    for keyword in keywords:
        key = str(keyword).strip()
        if not key:
            continue
        index = lowered.find(key.lower())
        if index < 0:
            continue
        hits.append(key)
        start = max(0, index - 36)
        end = min(len(text), index + len(key) + 72)
        evidence.append(
            {"keyword": key, "start_index": index, "end_index": index + len(key), "snippet": text[start:end]}
        )
    return hits, evidence[:8]


def _band_for_score(bands: Sequence[dict[str, object]], score: float) -> str:
    for band in bands:
        lower = float(band.get("min_score") or 0.0)
        upper = float(band.get("max_score") or lower)
        if lower - 0.000001 <= score <= upper + 0.000001:
            return str(band.get("label") or band.get("band_id") or "")
    return ""


def score_document_against_profile(profile_payload: object, document_text: str) -> Dict[str, object]:
    profile = tender_profile_from_dict(profile_payload)
    profile_dict = tender_profile_to_dict(profile)
    text = str(document_text or "")
    item_rows: list[dict[str, object]] = []
    raw_total = 0.0

    for item in profile_dict["scoring_items"]:
        requirements = list(item.get("evidence_requirements") or [])
        requirement_rows: list[dict[str, object]] = []
        matched_requirements = 0
        all_hits: list[str] = []
        all_evidence: list[dict[str, object]] = []
        for requirement in requirements:
            keywords = _keywords(requirement)
            hits, evidence = _evidence_for(text, keywords)
            required_hits = 1 if len(keywords) <= 2 else 2
            matched = len(hits) >= required_hits
            if matched:
                matched_requirements += 1
            all_hits.extend(hit for hit in hits if hit not in all_hits)
            all_evidence.extend(evidence)
            requirement_rows.append(
                {
                    "requirement": requirement,
                    "matched": matched,
                    "keywords": keywords,
                    "hits": hits,
                }
            )

        requirement_count = len(requirements)
        coverage = matched_requirements / requirement_count if requirement_count else 0.0
        max_score = float(item["max_score"])
        score = round(max_score * coverage, 4)
        raw_total += score
        item_rows.append(
            {
                "item_id": item["item_id"],
                "name": item["name"],
                "score": score,
                "max_score": max_score,
                "coverage": round(coverage, 4),
                "band": _band_for_score(list(item.get("bands") or []), score),
                "matched_requirements": matched_requirements,
                "requirement_count": requirement_count,
                "requirements": requirement_rows,
                "hits": all_hits,
                "evidence": all_evidence[:10],
            }
        )

    raw_total = round(raw_total, 4)
    normalized_total = round(raw_total / float(profile.score_scale) * 100.0, 4)
    return {
        "profile_id": profile.tender_id,
        "profile_version": profile.version,
        "score_scale": float(profile.score_scale),
        "raw_total": raw_total,
        "normalized_total": normalized_total,
        "item_count": len(item_rows),
        "items": item_rows,
        "hard_redlines": profile_dict.get("hard_redlines") or [],
        "scoring_basis": "approved_tender_profile",
    }


def apply_approved_tender_score(
    *,
    project: Dict[str, object],
    report: Dict[str, object],
    document_text: str,
) -> bool:
    state = project_tender_profile_state(project)
    profile = state.get("profile")
    if not bool(state.get("approved")) or not isinstance(profile, dict):
        return False

    tender_score = score_document_against_profile(profile, document_text)
    legacy_score = {
        "total_score": report.get("total_score"),
        "rule_total_score": report.get("rule_total_score"),
        "pred_total_score": report.get("pred_total_score"),
        "dimension_scores": copy.deepcopy(report.get("dimension_scores") or {}),
    }
    normalized_total = float(tender_score["normalized_total"])
    report["legacy_score"] = legacy_score
    report["tender_score"] = tender_score
    report["total_score"] = normalized_total
    report["rule_total_score"] = normalized_total
    report["pred_total_score"] = normalized_total
    meta = report.get("meta") if isinstance(report.get("meta"), dict) else {}
    meta = dict(meta)
    meta["score_basis"] = "approved_tender_profile"
    meta["tender_profile_version"] = tender_score["profile_version"]
    meta["tender_score_scale"] = tender_score["score_scale"]
    meta["legacy_16d_role"] = "secondary_diagnostic"
    report["meta"] = meta
    return True
