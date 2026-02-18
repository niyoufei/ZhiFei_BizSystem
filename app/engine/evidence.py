from __future__ import annotations

import re
from typing import Iterable, List, Tuple

from app.schemas import EvidenceSpan


def _snippet(text: str, start: int, end: int, window: int = 40) -> str:
    left = max(0, start - window)
    right = min(len(text), end + window)
    snippet = text[left:right].replace("\n", " ")
    return snippet.strip()


def find_evidence_for_keywords(text: str, keywords: Iterable[str]) -> List[EvidenceSpan]:
    evidence: List[EvidenceSpan] = []
    lower = text.lower()
    for kw in keywords:
        if not kw:
            continue
        kw_lower = kw.lower()
        start = 0
        while True:
            idx = lower.find(kw_lower, start)
            if idx == -1:
                break
            end = idx + len(kw)
            evidence.append(
                EvidenceSpan(
                    start_index=idx,
                    end_index=end,
                    snippet=_snippet(text, idx, end),
                )
            )
            start = end
    return evidence


def find_evidence_for_patterns(text: str, patterns: Iterable[str]) -> List[EvidenceSpan]:
    evidence: List[EvidenceSpan] = []
    for pattern in patterns:
        if not pattern:
            continue
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            start, end = match.span()
            evidence.append(
                EvidenceSpan(
                    start_index=start,
                    end_index=end,
                    snippet=_snippet(text, start, end),
                )
            )
    return evidence


def find_evidence_spans(
    text: str,
    keywords: Iterable[str] | None = None,
    patterns: Iterable[str] | None = None,
    window: int = 40,
    max_hits: int = 3,
) -> List[EvidenceSpan]:
    spans: List[EvidenceSpan] = []
    if keywords:
        lower = text.lower()
        for kw in keywords:
            if not kw:
                continue
            kw_lower = kw.lower()
            start = 0
            while True:
                idx = lower.find(kw_lower, start)
                if idx == -1:
                    break
                end = idx + len(kw)
                spans.append(
                    EvidenceSpan(
                        start_index=idx,
                        end_index=end,
                        snippet=_snippet(text, idx, end, window=window),
                    )
                )
                start = end
                if len(spans) >= max_hits:
                    return spans
    if patterns:
        for pattern in patterns:
            if not pattern:
                continue
            for match in re.finditer(pattern, text, flags=re.IGNORECASE):
                start, end = match.span()
                spans.append(
                    EvidenceSpan(
                        start_index=start,
                        end_index=end,
                        snippet=_snippet(text, start, end, window=window),
                    )
                )
                if len(spans) >= max_hits:
                    return spans
    return spans


def dedupe_evidence(spans: List[EvidenceSpan]) -> List[EvidenceSpan]:
    seen: set[Tuple[int, int]] = set()
    unique: List[EvidenceSpan] = []
    for span in spans:
        key = (span.start_index, span.end_index)
        if key in seen:
            continue
        seen.add(key)
        unique.append(span)
    return unique
