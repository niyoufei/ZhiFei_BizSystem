# Local LLM Ollama Timeout and Model Controls Runtime Smoke Stage Review

## Purpose

This document reviews Step 174AJ, which performed a controlled runtime smoke for the local LLM Ollama timeout and model selection controls.

The purpose is to record the real transport reachability, lightweight model selection, runtime timeout and `num_predict` configuration, stable `invalid_response` failure, explicit non-integrations, remaining risks, and the required guard conditions before any response normalization investigation.

This document is docs-only. It does not authorize response parser changes, runtime re-smoke, UI integration, export integration, scoring-chain integration, or production use.

## Baseline before Step 174AJ

Step 174AJ ran after:

- Step 174AH implemented timeout, `num_predict`, and model selection controls.
- Step 174AI documented the implementation stage review.

Relevant baseline:

- worktree: `/Users/youfeini/Desktop/ZhiFei_BizSystem-local-llm-clean`
- branch: `local-llm-integration-clean`
- Step 174AJ starting HEAD: `ddb59dbf1b372da97bc994cd99c9f6d5f1d223cd`
- Step 174AJ report commit: `af906ed5c8ca2cb5f42cf91e33eaf583bf858bc0`
- Step 174AJ report tag: `v0.1.68-local-llm-ollama-timeout-model-runtime-smoke-report`
- Step 174AJ report: `docs/local-llm-ollama-timeout-model-runtime-smoke-report.md`

## Runtime smoke execution summary

Step 174AJ completed a controlled runtime smoke for the timeout and model controls.

Runtime setup:

- 2nd window was enabled for `ollama serve`.
- Ollama was reachable through `127.0.0.1:11434`.
- FastAPI was started only on `127.0.0.1:18746`.
- endpoint was `POST /local-llm/preview-mock`.
- pytest was not run.
- code was not modified.
- tests were not modified.
- models were not downloaded.
- models were not pulled.
- `ollama pull` was not executed.
- no external network was called.

FastAPI was stopped after the request. Port `18746` had no listener after shutdown.

Codex did not close the 2nd window. The 2nd window state remains subject to ChatGPT instruction.

## Ollama reachability and model inventory

Ollama reachability check in Step 174AJ:

```bash
curl -sS --max-time 5 -w '\nHTTP_STATUS:%{http_code}\n' http://127.0.0.1:11434/api/tags
```

Result:

- Ollama reachable through `127.0.0.1:11434`: yes
- `/api/tags` returned HTTP status `200`
- `/api/tags` returned valid JSON
- local model count: `7`
- no timeout
- no connection refused
- no invalid response from `/api/tags`

Local model inventory:

- `qwen3-next:80b-a3b-instruct-q8_0`
- `qwen3-coder:30b`
- `deepseek-r1:32b`
- `qwen3:30b`
- `qwen3:14b`
- `qwen3:8b`
- `qwen3:0.6b`

No model was downloaded. No model was pulled.

## Lightweight model selection result

Selected model:

```text
qwen3:0.6b
```

Selection reason:

- Step 174AJ prioritized `qwen3:0.6b` when present in `/api/tags`.
- `qwen3:0.6b` was present locally.
- It was the lightest local model in the returned inventory.
- No fallback to `qwen3:8b` was required.
- No fallback to the first returned model was required.

## Timeout and num_predict runtime configuration

Step 174AJ configured:

```text
LOCAL_LLM_OLLAMA_TIMEOUT_SECONDS=30
LOCAL_LLM_OLLAMA_NUM_PREDICT=8
LOCAL_LLM_OLLAMA_MODEL=qwen3:0.6b
```

FastAPI startup used all three feature flags enabled:

```text
LOCAL_LLM_PREVIEW_MOCK_API_ENABLED=true
LOCAL_LLM_OLLAMA_PREVIEW_ADAPTER_ENABLED=true
LOCAL_LLM_OLLAMA_REAL_TRANSPORT_ENABLED=true
```

FastAPI startup command:

```bash
LOCAL_LLM_PREVIEW_MOCK_API_ENABLED=true LOCAL_LLM_OLLAMA_PREVIEW_ADAPTER_ENABLED=true LOCAL_LLM_OLLAMA_REAL_TRANSPORT_ENABLED=true LOCAL_LLM_OLLAMA_MODEL=qwen3:0.6b LOCAL_LLM_OLLAMA_TIMEOUT_SECONDS=30 LOCAL_LLM_OLLAMA_NUM_PREDICT=8 python3 -m uvicorn app.main:app --host 127.0.0.1 --port 18746
```

FastAPI PID:

```text
5061
```

## FastAPI real transport request summary

Endpoint:

```text
POST http://127.0.0.1:18746/local-llm/preview-mock
```

Request body summary:

```json
{
  "project_id": "p1",
  "submission_id": "s1",
  "text_excerpt": "sample tender response excerpt",
  "mode": "preview_only",
  "requested_by": "operator",
  "scoring_context": {
    "dimension": "technical"
  },
  "evidence_context": {
    "source": "excerpt"
  },
  "requirement_hits": [
    {
      "requirement": "R1",
      "hit": true
    }
  ]
}
```

The request used a minimal synthetic payload. It did not include real bid files, formal scoring data, export tasks, or production review data.

Response summary:

```json
{
  "enabled": true,
  "feature_flag": "LOCAL_LLM_PREVIEW_MOCK_API_ENABLED",
  "adapter_enabled": true,
  "adapter_feature_flag": "LOCAL_LLM_OLLAMA_PREVIEW_ADAPTER_ENABLED",
  "real_transport_enabled": true,
  "real_transport_feature_flag": "LOCAL_LLM_OLLAMA_REAL_TRANSPORT_ENABLED",
  "adapter": "ollama_preview",
  "source": "ollama_preview_adapter",
  "status": "error",
  "preview_only": true,
  "no_write": true,
  "affects_score": false,
  "error_type": "invalid_response",
  "message": "Ollama response did not contain non-empty content.",
  "model": "qwen3:0.6b",
  "fallback_used": true,
  "fallback": {
    "mode": "mock_fallback",
    "reason": "invalid_response",
    "model": "qwen3:0.6b",
    "preview_only": true,
    "no_write": true,
    "affects_score": false
  }
}
```

Runtime result:

- response HTTP status: `200`
- endpoint entered adapter branch: yes
- endpoint entered real transport branch: yes
- response included `real_transport_enabled=true`
- real transport accessed `127.0.0.1:11434`
- loopback-only access boundary was preserved
- response status: `error`
- response error type: `invalid_response`
- response model: `qwen3:0.6b`
- response kept `preview_only=true`
- response kept `no_write=true`
- response kept `affects_score=false`

## invalid_response failure result analysis

The Step 174AJ response was a stable `invalid_response` failure.

This result means:

- the endpoint feature flag was enabled
- the adapter feature flag was enabled
- the real transport feature flag was enabled
- the real transport branch was reached
- local Ollama was reached through `127.0.0.1:11434`
- the selected local model was `qwen3:0.6b`
- the runtime response passed through the adapter failure schema
- the endpoint returned HTTP `200`
- the response stayed preview-only and no-write

This result does not mean:

- Ollama is unavailable
- the selected model is missing
- production scoring failed
- UI can be connected
- export can be connected
- storage can be written

The `invalid_response` result is the current response parsing and normalization boundary. It may be related to response fields, model output format, empty normalized content, or another adapter normalization assumption. It must not be treated as a production scoring failure.

## Difference between stable preview failure and production scoring

The `invalid_response` failure is an advisory preview runtime result.

It is not:

- a formal scoring failure
- a failed QingTian result
- an evidence trace failure
- a scoring basis failure
- an export failure
- a production model quality signal

The response explicitly preserved:

- `preview_only=true`
- `no_write=true`
- `affects_score=false`

Therefore, it remains outside the production scoring chain.

## Explicit non-integrations

Step 174AJ did not run pytest.

Step 174AJ did not download models.

Step 174AJ did not pull models.

Step 174AJ did not execute `ollama pull`.

Step 174AJ did not call external networks.

Step 174AJ did not call OpenAI.

Step 174AJ did not call Spark.

Step 174AJ did not call Gemini.

Step 174AJ did not modify `app/main.py`.

Step 174AJ did not modify `app/storage.py`.

Step 174AJ did not modify `app/engine/local_llm_preview_mock.py`.

Step 174AJ did not modify `app/engine/local_llm_ollama_preview_adapter.py`.

Step 174AJ did not add or modify tests.

Step 174AJ did not connect `score_text()`.

Step 174AJ did not connect `/rescore`.

Step 174AJ did not connect `qingtian-results`.

Step 174AJ did not connect `evidence_trace/latest`.

Step 174AJ did not connect `scoring_basis/latest`.

Step 174AJ did not connect UI.

Step 174AJ did not trigger DOCX, JSON, or Markdown formal export.

Step 174AJ did not connect any real-model production scoring chain.

## No-write boundary verification

Step 174AJ did not write `app/storage.py`.

Step 174AJ did not write `data/`.

Step 174AJ did not write `output/`.

The runtime smoke report recorded that service-after self-check was clean before adding the report document.

The preview response remained `no_write=true`.

## No-scoring-chain boundary verification

Step 174AJ did not call `score_text()`.

Step 174AJ did not call `/rescore`.

Step 174AJ did not enter `qingtian-results`.

Step 174AJ did not enter `evidence_trace/latest`.

Step 174AJ did not enter `scoring_basis/latest`.

The preview response remained `affects_score=false`.

The `invalid_response` failure is not a scoring-chain result.

## No-UI and no-export verification

Step 174AJ did not connect UI.

Step 174AJ did not trigger DOCX export.

Step 174AJ did not trigger JSON export.

Step 174AJ did not trigger Markdown formal export.

The runtime result cannot be used as a UI or export capability.

## Remaining risks

- Real transport reached local Ollama, but the lightweight model returned `invalid_response`.
- `invalid_response` cannot be interpreted as Ollama being unavailable.
- `invalid_response` may be related to response parsing, response fields, model output format, or adapter normalization boundaries.
- The current `invalid_response` cannot be used as a production scoring failure basis.
- The current state cannot be used for production scoring.
- The current state is not connected to UI.
- The current state is not connected to export chains.
- The current state is not connected to `qingtian-results`, `evidence_trace/latest`, or `scoring_basis/latest`.
- The current state does not write storage, `data/`, or `output/`.
- Future response parsing investigation must first define a response parsing boundary.
- Future changes to `normalize_ollama_response` must start with fake-only tests.
- Future investigation must not connect `score_text()` or `/rescore` just to pass runtime smoke.
- Future investigation must not connect UI or export chains just to demonstrate output.
- Future real Ollama smoke must be separately authorized and must re-enable the 2nd window.
- If any future stage creates `data/`, `output/`, or storage changes, it must stop and report the change list.

## Required next-stage guard before response normalization investigation

Before any response normalization investigation, the next stage must explicitly define:

- whether `app/engine/local_llm_ollama_preview_adapter.py` may be modified
- whether `app/main.py` may be modified
- whether tests may be modified
- whether only `tests/test_local_llm_ollama_preview_adapter.py` may be modified
- whether `tests/test_local_llm_preview_mock_api_bridge.py` may be modified
- whether a minimal response summary may be recorded
- that long model output must not be saved
- that `data/`, `output/`, and storage must not be written
- that tests must be fake-only
- that code stages must not run `ollama serve`
- that runtime smoke stages are the only stages allowed to use the 2nd window
- that models must not be downloaded or pulled
- that `ollama pull` must not be executed
- that scoring chains must remain disconnected
- that UI must remain disconnected
- that export chains must remain disconnected
- that completion must stop for ChatGPT review

## Step 174AK closure statement

Step 174AK records Step 174AJ as a controlled runtime smoke milestone: timeout and model controls reached the real transport branch with `qwen3:0.6b`, `LOCAL_LLM_OLLAMA_TIMEOUT_SECONDS=30`, and `LOCAL_LLM_OLLAMA_NUM_PREDICT=8`, then returned a stable `invalid_response` preview failure.

This document must not be interpreted as permission to automatically change response parsing, rerun runtime smoke, connect UI, connect scoring chains, connect export chains, write storage, or use this path for production scoring.
