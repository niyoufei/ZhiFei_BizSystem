from __future__ import annotations

import re
from collections import Counter
from pathlib import Path
from typing import List, Tuple

import yaml


def load_keywords(lexicon_path: Path) -> List[str]:
    data = yaml.safe_load(lexicon_path.read_text(encoding="utf-8")) or {}
    return data.get("empty_promises", {}).get("keywords", [])


def find_all_positions(text: str, keyword: str) -> List[int]:
    positions: List[int] = []
    start = 0
    lower = text.lower()
    kw_lower = keyword.lower()
    while True:
        idx = lower.find(kw_lower, start)
        if idx == -1:
            break
        positions.append(idx)
        start = idx + len(keyword)
    return positions


def snippet_window(text: str, start: int, end: int, window: int = 40) -> str:
    left = max(0, start - window)
    right = min(len(text), end + window)
    return text[left:right].replace("\n", " ").strip()


def grounding_reasons(snippet: str) -> List[str]:
    reasons: List[str] = []
    if re.search(
        r"\d+(?:\.\d+)?\s*(?:m3|m³|m2|m²|㎡|㎥|㎠|mm|cm|m|t|kg|台|套|处|项|座|段|根|个|%|天|小时|h|d)",
        snippet,
        flags=re.IGNORECASE,
    ):
        reasons.append("数字+单位")
    if re.search(r"[≤≥<>]", snippet):
        reasons.append("阈值符号")
    if re.search(
        r"(?:每日|每周|每月|每班|每次|每\d+天|每\d+小时|\d+次/天|\d+次/周|\d+次|次/天|次/周)",
        snippet,
        flags=re.IGNORECASE,
    ):
        reasons.append("频次")
    if re.search(
        r"(?:项目经理|技术负责人|施工员|安全员|质检员|班组长)",
        snippet,
        flags=re.IGNORECASE,
    ):
        reasons.append("责任岗位")
    if re.search(
        r"(?:报验|签认|验收|旁站|自检|互检|交接检|隐蔽验收)",
        snippet,
        flags=re.IGNORECASE,
    ):
        reasons.append("验收动作")
    return reasons


def main() -> None:
    base = Path(__file__).resolve().parents[1]
    import sys

    if len(sys.argv) > 1:
        text_path = base / sys.argv[1]
    else:
        text_path = base / "sample_shigong.txt"
    lexicon_path = base / "app" / "resources" / "lexicon.yaml"

    text = text_path.read_text(encoding="utf-8")
    keywords = load_keywords(lexicon_path)

    total_hits = 0
    offset_hits: List[Tuple[int, str]] = []
    reason_counter: Counter[str] = Counter()

    for kw in keywords:
        positions = find_all_positions(text, kw)
        for idx in positions:
            total_hits += 1
            end = idx + len(kw)
            snippet = snippet_window(text, idx, end, window=40)
            reasons = grounding_reasons(snippet)
            if reasons:
                for reason in reasons:
                    reason_counter[reason] += 1
            else:
                offset_hits.append((idx, snippet))

    print("empty_promises 命中总次数:", total_hits)
    print("被落地要素抵消次数:", sum(reason_counter.values()))
    print("抵消原因统计:", dict(reason_counter))
    print("未被抵消的命中清单:")
    for idx, snippet in offset_hits:
        print(f"- index={idx} | {snippet}")


if __name__ == "__main__":
    main()
