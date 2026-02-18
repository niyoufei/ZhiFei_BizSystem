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
