from __future__ import annotations

import os

from app.infrastructure.models.no_model import NoModelService
from app.infrastructure.models.openai_boundary import OpenAIModelService


def build_default_model_service():
    mode = str(os.getenv("ZHIFEI_MODEL_EVIDENCE_MODE") or "off").strip().lower()
    if mode in {"openai", "gpt", "llm"}:
        service = OpenAIModelService()
        if service.is_available():
            return service
        return NoModelService(reason="requested_openai_but_credentials_missing")
    return NoModelService(reason="model_mode_off")
