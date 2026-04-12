from __future__ import annotations

import json
import logging

from fastapi import HTTPException

from app.application.task_runtime import (
    classify_failure,
    current_runtime_context,
    log_structured,
    runtime_context,
)


class _CaptureHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())


def test_runtime_context_binds_structured_log_fields() -> None:
    logger = logging.getLogger("tests.task_runtime")
    handler = _CaptureHandler()
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    try:
        with runtime_context(
            correlation_id="corr-1",
            project_id="p-1",
            run_id="run-1",
            task_kind="scoring",
            task_name="score_submission_text",
        ):
            log_structured(logger, event="task_probe", total_score=88.5)
            assert current_runtime_context()["project_id"] == "p-1"
    finally:
        logger.removeHandler(handler)

    payload = json.loads(handler.messages[-1])
    assert payload["event"] == "task_probe"
    assert payload["correlation_id"] == "corr-1"
    assert payload["project_id"] == "p-1"
    assert payload["run_id"] == "run-1"
    assert payload["task_kind"] == "scoring"
    assert payload["task_name"] == "score_submission_text"
    assert payload["total_score"] == 88.5


def test_classify_failure_maps_common_error_types() -> None:
    assert classify_failure(HTTPException(status_code=422, detail="bad payload")) == "validation"
    assert classify_failure(HTTPException(status_code=403, detail="forbidden")) == "permission"
    assert classify_failure(TimeoutError("timeout")) == "timeout"
    assert classify_failure(FileNotFoundError("missing")) == "storage"
