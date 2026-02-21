from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List, Sequence

from app.config import RESOURCES_DIR

GB50502_CORE_SECTIONS = [
    "编制依据",
    "工程概况",
    "施工部署",
    "施工进度计划",
    "施工准备与资源配置计划",
    "主要施工方法",
    "质量管理",
    "安全管理",
]

OUTDATED_NORMS_BLACKLIST_PATH = RESOURCES_DIR / "outdated_norms_blacklist.txt"


class PreFlightFatalError(ValueError):
    """红线命中时，直接中止评分。"""


def _load_outdated_norms_blacklist(path: Path = OUTDATED_NORMS_BLACKLIST_PATH) -> List[str]:
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    return [line.strip() for line in lines if line.strip() and not line.strip().startswith("#")]


def _heading_hit(text: str, heading: str) -> bool:
    pattern = re.compile(
        rf"(?m)^\s*(?:第?[一二三四五六七八九十百零0-9]+[章节部分、.)）\s]*)?{re.escape(heading)}\s*$"
    )
    if pattern.search(text):
        return True
    # 兼容“章节 + 扩展说明”
    return bool(re.search(rf"{re.escape(heading)}[：:]", text))


def pre_flight_check(
    text: str,
    *,
    required_sections: Sequence[str] | None = None,
    outdated_blacklist: Sequence[str] | None = None,
    raise_on_fatal: bool = True,
) -> Dict[str, object]:
    """
    模块三：评分前红线拦截。
    1) 8 大骨架章节缺失
    2) 废止规范黑名单命中
    """
    src = str(text or "")
    sections = list(required_sections or GB50502_CORE_SECTIONS)
    blacklist = list(outdated_blacklist or _load_outdated_norms_blacklist())

    missing_sections = [name for name in sections if not _heading_hit(src, name)]
    outdated_norm_refs = [item for item in blacklist if item and item in src]

    fatal = bool(missing_sections or outdated_norm_refs)
    result = {
        "ok": not fatal,
        "missing_sections": missing_sections,
        "outdated_norm_refs": outdated_norm_refs,
        "fatal": fatal,
    }
    if fatal and raise_on_fatal:
        reasons: List[str] = []
        if missing_sections:
            reasons.append("缺失骨架章节：" + "、".join(missing_sections))
        if outdated_norm_refs:
            reasons.append("命中废止规范：" + "、".join(outdated_norm_refs))
        raise PreFlightFatalError("；".join(reasons))
    return result
