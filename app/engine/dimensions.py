from __future__ import annotations

from typing import Dict, List, Tuple

from app.engine.evidence import (
    dedupe_evidence,
    find_evidence_for_keywords,
    find_evidence_spans,
)

DIMENSIONS = {
    "01": {"name": "工程项目整体理解与实施路径", "module": "总控管理"},
    "02": {"name": "安全生产管理体系与控制措施", "module": "安全管理"},
    "03": {"name": "文明施工管理体系与实施措施", "module": "绿色文明"},
    "04": {"name": "材料部品采购及管理机制", "module": "材料管理"},
    "05": {"name": "四新技术的应用与实施方案", "module": "技术创新"},
    "06": {"name": "工程关键工序识别与控制措施", "module": "工序控制"},
    "07": {"name": "重难点及危险性较大工程管控", "module": "风险治理"},
    "08": {"name": "工程质量管理体系与保证措施", "module": "质量管理"},
    "09": {"name": "工期目标保障与进度控制措施", "module": "进度管理"},
    "10": {"name": "成本管理与资金控制措施", "module": "成本资金"},
    "11": {"name": "人力资源配置与管理方案", "module": "资源保障"},
    "12": {"name": "总体施工工艺流程与组织逻辑", "module": "流程组织"},
    "13": {"name": "物资与施工设备配置方案", "module": "设备管理"},
    "14": {"name": "设计协调与深化实施能力", "module": "设计协同"},
    "15": {"name": "总体资源配置与实施计划", "module": "资源总控"},
    "16": {"name": "技术措施的可行性与落地性", "module": "验证落地"},
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
