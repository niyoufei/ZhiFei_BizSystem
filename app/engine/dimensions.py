from __future__ import annotations

from typing import Dict, List, Tuple

from app.engine.evidence import (
    dedupe_evidence,
    find_evidence_for_keywords,
    find_evidence_spans,
)

DIMENSIONS = {
    "01": {"name": "工程项目整体理解", "module": "整体筹划"},
    "02": {"name": "安全生产管理与措施", "module": "管理体系"},
    "03": {"name": "文明施工管理与措施", "module": "管理体系"},
    "04": {"name": "材料与部品管理", "module": "资源保障"},
    "05": {"name": "新工艺新技术", "module": "技术工艺"},
    "06": {"name": "关键工序", "module": "技术工艺"},
    "07": {"name": "重难点及危大工程", "module": "技术工艺"},
    "08": {"name": "质量保障体系", "module": "管理体系"},
    "09": {"name": "进度保障措施", "module": "管理体系"},
    "10": {"name": "专项施工工艺", "module": "技术工艺"},
    "11": {"name": "人力资源配置", "module": "资源保障"},
    "12": {"name": "总体施工工艺", "module": "技术工艺"},
    "13": {"name": "物资与设备配置", "module": "资源保障"},
    "14": {"name": "设计协调与深化", "module": "整体筹划"},
    "15": {"name": "总体配置计划", "module": "整体筹划"},
    "16": {"name": "技术措施可行性", "module": "技术工艺"},
}


def score_dimension(
    dim_id: str,
    text: str,
    rubric: Dict,
    lexicon: Dict,
) -> Tuple[float, List[str], List]:
    settings = rubric["dimensions"][dim_id]
    max_score = float(settings["max_score"])
    per_hit = float(settings.get("per_hit", 2.0))
    keywords = lexicon["dimension_keywords"].get(dim_id, [])

    hits: List[str] = []
    lower = text.lower()
    for kw in keywords:
        if kw.lower() in lower:
            hits.append(kw)

    score = min(max_score, len(hits) * per_hit)
    evidence = dedupe_evidence(find_evidence_for_keywords(text, hits))
    return score, hits, evidence


def _keyword_hits(text: str, keywords: List[str]) -> List[str]:
    hits: List[str] = []
    lower = text.lower()
    for kw in keywords:
        if kw and kw.lower() in lower:
            hits.append(kw)
    return hits


def score_dim_07(text: str, rubric: Dict) -> Tuple[float, List[str], List, List[Dict]]:
    sub_items = rubric["dimensions"]["07"].get("sub_items", [])
    sub_scores: List[Dict] = []
    total_score = 0.0
    all_hits: List[str] = []
    all_evidence = []

    for item in sub_items:
        keywords = item.get("keywords", [])
        patterns = item.get("regex", [])
        hits = _keyword_hits(text, keywords)
        evidence = find_evidence_spans(
            text, keywords=keywords, patterns=patterns, window=40, max_hits=3
        )
        score = float(item.get("weight", 2)) if evidence else 0.0
        total_score += score
        all_hits.extend(hits)
        all_evidence.extend(evidence)
        sub_scores.append(
            {
                "name": item.get("name", ""),
                "score": score,
                "hits": hits,
                "evidence": evidence,
            }
        )

    return total_score, list(dict.fromkeys(all_hits)), dedupe_evidence(all_evidence), sub_scores


def score_dim_09(text: str, rubric: Dict) -> Tuple[float, List[str], List, List[Dict]]:
    sub_items = rubric["dimensions"]["09"].get("sub_items", [])
    sub_scores: List[Dict] = []
    total_score = 0.0
    all_hits: List[str] = []
    all_evidence = []

    for item in sub_items:
        keywords = item.get("keywords", [])
        patterns = item.get("regex", [])
        hits = _keyword_hits(text, keywords)

        if item.get("id") == "09-1":
            score = float(item.get("weight", 2)) if len(hits) >= 2 else 0.0
            evidence = find_evidence_spans(
                text, keywords=hits, patterns=patterns, window=40, max_hits=3
            )
        else:
            evidence = find_evidence_spans(
                text, keywords=keywords, patterns=patterns, window=40, max_hits=3
            )
            score = float(item.get("weight", 2)) if evidence else 0.0

        total_score += score
        all_hits.extend(hits)
        all_evidence.extend(evidence)
        sub_scores.append(
            {
                "name": item.get("name", ""),
                "score": score,
                "hits": hits,
                "evidence": evidence,
            }
        )

    return total_score, list(dict.fromkeys(all_hits)), dedupe_evidence(all_evidence), sub_scores
