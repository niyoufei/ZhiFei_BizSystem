from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.contracts.models import (
    CandidateEvidenceBatch,
    CandidateEvidenceRecord,
    CandidateEvidenceRequest,
    SemanticAlignmentGap,
    SemanticAlignmentRequest,
    SemanticAlignmentResponse,
)
from app.engine.openai_compat import call_openai_json, get_openai_api_key, get_openai_model

PROMPT_POLICY_VERSION = "model-boundary-v1"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _build_candidate_evidence_prompt(request: CandidateEvidenceRequest) -> str:
    artifacts = []
    for item in request.artifacts:
        excerpt = str(item.content_excerpt or "").strip()
        if excerpt:
            excerpt = excerpt[:4000]
        artifacts.append(
            {
                "source_ref": item.source_ref,
                "artifact_type": item.artifact_type,
                "file_name": item.file_name,
                "page_locator": item.page_locator,
                "content_excerpt": excerpt,
            }
        )
    payload = {
        "project_id": request.project_id,
        "submission_id": request.submission_id,
        "submission_excerpt": str(request.submission_text or "")[:8000],
        "artifacts": artifacts,
        "requirement_hints": list(request.requirement_hints or [])[:12],
        "anchor_hints": list(request.anchor_hints or [])[:12],
        "max_candidates": request.max_candidates,
    }
    return (
        "你是施工组织设计评标系统中的候选证据抽取器。"
        "只能输出候选证据，不能输出最终分数、权重、扣分结论。"
        '请严格返回 JSON 对象，格式为 {"candidates": [...]}。'
        "每条 candidate 必须包含：candidate_id, source_ref, artifact_type, file_name, page_locator,"
        " snippet, matched_requirement, dimension_id, confidence, validator_status,"
        " model_version, prompt_or_policy_version, extraction_time。"
        "其中 validator_status 固定写 pending_validation，confidence 为 0 到 1。"
        "如果证据不充分，可返回空数组。输入如下：\n" + str(payload)
    )


def _build_alignment_prompt(request: SemanticAlignmentRequest) -> str:
    payload = {
        "project_id": request.project_id,
        "artifact_text_excerpt": str(request.artifact_text or "")[:6000],
        "submission_text_excerpt": str(request.submission_text or "")[:6000],
        "max_gaps": request.max_gaps,
    }
    return (
        "你是施工组织设计评标系统中的语义对齐辅助器。"
        "只能输出候选缺项与语义不一致点，不能输出最终分数或治理结论。"
        '请严格返回 JSON 对象，格式为 {"gaps": [...]}。'
        "每条 gap 必须包含：source_ref, requirement_text, submission_gap, confidence,"
        " model_version, prompt_or_policy_version, extraction_time, validator_status。"
        "其中 validator_status 固定写 pending_validation。输入如下：\n" + str(payload)
    )


class OpenAIModelService:
    provider_name = "openai"
    mode = "openai"

    def __init__(self, *, model_name: str | None = None):
        self.model_name = model_name or get_openai_model()

    def is_available(self) -> bool:
        return bool(get_openai_api_key())

    def extract_candidate_evidence(
        self,
        request: CandidateEvidenceRequest,
    ) -> CandidateEvidenceBatch:
        started_at = _utc_now_iso()
        if not self.is_available():
            return CandidateEvidenceBatch(
                provider=self.provider_name,
                mode="no-model",
                model_version=self.model_name,
                prompt_or_policy_version=PROMPT_POLICY_VERSION,
                extraction_time=started_at,
                available=False,
                fallback_reason="missing_openai_credentials",
                candidates=[],
            )
        ok, parsed, error = call_openai_json(
            _build_candidate_evidence_prompt(request),
            model=self.model_name,
            temperature=0.1,
            max_tokens=2400,
            timeout=90,
        )
        if not ok or not isinstance(parsed, dict):
            return CandidateEvidenceBatch(
                provider=self.provider_name,
                mode="openai",
                model_version=self.model_name,
                prompt_or_policy_version=PROMPT_POLICY_VERSION,
                extraction_time=started_at,
                available=False,
                fallback_reason=str(error or "openai_candidate_evidence_failed"),
                candidates=[],
            )
        candidates = parsed.get("candidates")
        rows: list[CandidateEvidenceRecord] = []
        if isinstance(candidates, list):
            for index, item in enumerate(candidates, start=1):
                if not isinstance(item, dict):
                    continue
                normalized = dict(item)
                normalized.setdefault("candidate_id", f"openai-{index}")
                normalized.setdefault("validator_status", "pending_validation")
                normalized.setdefault("model_version", self.model_name)
                normalized.setdefault("prompt_or_policy_version", PROMPT_POLICY_VERSION)
                normalized.setdefault("extraction_time", started_at)
                try:
                    rows.append(CandidateEvidenceRecord.model_validate(normalized))
                except Exception:
                    continue
        return CandidateEvidenceBatch(
            provider=self.provider_name,
            mode="openai",
            model_version=self.model_name,
            prompt_or_policy_version=PROMPT_POLICY_VERSION,
            extraction_time=started_at,
            available=True,
            fallback_reason=None,
            candidates=rows,
        )

    def align_semantic_requirements(
        self,
        request: SemanticAlignmentRequest,
    ) -> SemanticAlignmentResponse:
        started_at = _utc_now_iso()
        if not self.is_available():
            return SemanticAlignmentResponse(
                provider=self.provider_name,
                mode="no-model",
                available=False,
                model_version=self.model_name,
                prompt_or_policy_version=PROMPT_POLICY_VERSION,
                extraction_time=started_at,
                fallback_reason="missing_openai_credentials",
                gaps=[],
            )
        ok, parsed, error = call_openai_json(
            _build_alignment_prompt(request),
            model=self.model_name,
            temperature=0.1,
            max_tokens=1800,
            timeout=90,
        )
        if not ok or not isinstance(parsed, dict):
            return SemanticAlignmentResponse(
                provider=self.provider_name,
                mode="openai",
                available=False,
                model_version=self.model_name,
                prompt_or_policy_version=PROMPT_POLICY_VERSION,
                extraction_time=started_at,
                fallback_reason=str(error or "openai_alignment_failed"),
                gaps=[],
            )
        gaps_payload = parsed.get("gaps")
        gaps: list[SemanticAlignmentGap] = []
        if isinstance(gaps_payload, list):
            for item in gaps_payload:
                if not isinstance(item, dict):
                    continue
                normalized: dict[str, Any] = dict(item)
                normalized.setdefault("model_version", self.model_name)
                normalized.setdefault("prompt_or_policy_version", PROMPT_POLICY_VERSION)
                normalized.setdefault("extraction_time", started_at)
                normalized.setdefault("validator_status", "pending_validation")
                normalized.setdefault("source_ref", "artifact:inline")
                try:
                    gaps.append(SemanticAlignmentGap.model_validate(normalized))
                except Exception:
                    continue
        return SemanticAlignmentResponse(
            provider=self.provider_name,
            mode="openai",
            available=True,
            model_version=self.model_name,
            prompt_or_policy_version=PROMPT_POLICY_VERSION,
            extraction_time=started_at,
            fallback_reason=None,
            gaps=gaps,
        )
