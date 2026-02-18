from __future__ import annotations

from typing import Dict, List, Tuple

from app.engine.evidence import (
    dedupe_evidence,
    find_evidence_for_keywords,
    find_evidence_for_patterns,
)


def score_logic_lock(
    text: str,
    rubric: Dict,
    lexicon: Dict,
) -> Tuple[Dict, List[Dict]]:
    config = rubric["logic_lock"]
    max_step_score = float(config.get("max_step_score", 5.0))

    definition = lexicon["definition"]
    analysis = lexicon["analysis"]
    solution = lexicon["solution"]

    definition_keywords = definition.get("keywords", [])
    definition_patterns = definition.get("regexes") or definition.get("regex", [])
    analysis_keywords = analysis.get("keywords", [])
    analysis_patterns = analysis.get("regexes") or analysis.get("regex", [])
    solution_keywords = solution.get("keywords", [])
    solution_patterns = solution.get("regexes") or solution.get("regex", [])

    definition_evidence = find_evidence_for_keywords(
        text, definition_keywords
    ) + find_evidence_for_patterns(text, definition_patterns)
    analysis_evidence = find_evidence_for_keywords(
        text, analysis_keywords
    ) + find_evidence_for_patterns(text, analysis_patterns)
    solution_evidence = find_evidence_for_keywords(
        text, solution_keywords
    ) + find_evidence_for_patterns(text, solution_patterns)

    definition_score = max_step_score if definition_evidence else 0.0
    analysis_score = max_step_score if analysis_evidence else 0.0
    solution_score = max_step_score if solution_evidence else 0.0

    breaks: List[str] = []
    if not definition_evidence:
        breaks.append("definition")
    if not analysis_evidence:
        breaks.append("analysis")
    if not solution_evidence:
        breaks.append("solution")

    evidence = dedupe_evidence(definition_evidence + analysis_evidence + solution_evidence)

    penalties: List[Dict] = []
    penalty_value = float(config.get("break_penalty", 5.0))
    if breaks:
        for step in breaks:
            penalties.append(
                {
                    "code": f"LOGIC_LOCK_MISSING_{step.upper()}",
                    "message": f"三步闭环缺失：{step}",
                    "value": penalty_value,
                    "evidence_span": None,
                }
            )

    result = {
        "definition_score": definition_score,
        "analysis_score": analysis_score,
        "solution_score": solution_score,
        "breaks": breaks,
        "evidence": evidence,
    }
    return result, penalties
