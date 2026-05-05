# Local LLM Ollama Real Transport Runtime Smoke Stage Review

## 1 Purpose

This document reviews Step 174AD, which performed a controlled runtime smoke for the local LLM Ollama real transport branch.

The purpose is to record the real transport branch reachability, the stable `timeout` failure result, explicit non-integrations, remaining risks, and the required guard conditions before any timeout or model-selection optimization.

This document is docs-only. It does not authorize automatic timeout adjustment, model switching, UI integration, export integration, scoring-chain integration, or production use.

## 2 Baseline Before Step 174AD

- worktree: `/Users/youfeini/Desktop/ZhiFei_BizSystem-local-llm-clean`
- branch: `local-llm-integration-clean`
- baseline before Step 174AD: `46286bc5d33ddcfb5c7a5174adeada745c3b9424`
- Step 174AD report commit: `40c3ef71f098aa88940741fc35489ec259512056`
- Step 174AD tag: `v0.1.62-local-llm-ollama-real-transport-runtime-smoke-report`
- Step 174AD report: `docs/local-llm-ollama-real-transport-runtime-smoke-report.md`

Step 174AD ran after:

- Step 174AB implemented the real transport code path.
- Step 174AC documented the real transport implementation stage review.

## 3 Runtime Smoke Execution Summary

Step 174AD completed a real transport runtime smoke under controlled boundaries.

Runtime setup:

- 2nd window was enabled for `ollama serve`.
- FastAPI was started only on `127.0.0.1:18745`.
- Ollama was accessed only through `127.0.0.1:11434`.
- endpoint was `POST /local-llm/preview-mock`.
- pytest was not run.
- no code or tests were modified.
- no models were downloaded or pulled.
- no external network was called.

FastAPI startup flags were:

```text
LOCAL_LLM_PREVIEW_MOCK_API_ENABLED=true
LOCAL_LLM_OLLAMA_PREVIEW_ADAPTER_ENABLED=true
LOCAL_LLM_OLLAMA_REAL_TRANSPORT_ENABLED=true
LOCAL_LLM_OLLAMA_MODEL=qwen3-next:80b-a3b-instruct-q8_0
```

FastAPI command:

```bash
LOCAL_LLM_PREVIEW_MOCK_API_ENABLED=true LOCAL_LLM_OLLAMA_PREVIEW_ADAPTER_ENABLED=true LOCAL_LLM_OLLAMA_REAL_TRANSPORT_ENABLED=true LOCAL_LLM_OLLAMA_MODEL=qwen3-next:80b-a3b-instruct-q8_0 python3 -m uvicorn app.main:app --host 127.0.0.1 --port 18745
```

FastAPI PID:

```text
91540
```

FastAPI was stopped after the request. Port `18745` had no listener after shutdown.

Codex did not close the 2nd window. The 2nd window state remains subject to ChatGPT instruction.

## 4 Ollama Reachability And Model Inventory

Ollama reachability check:

```bash
curl -sS --max-time 5 http://127.0.0.1:11434/api/tags
```

Result:

- Ollama reachable through `127.0.0.1:11434`: yes
- `/api/tags` returned HTTP 200-equivalent valid JSON
- local model count: `7`
- no timeout
- no connection refused
- no invalid response

Local model inventory summary:

- `qwen3-next:80b-a3b-instruct-q8_0`
- `qwen3-coder:30b`
- `deepseek-r1:32b`
- `qwen3:30b`
- `qwen3:14b`
- `qwen3:8b`
- `qwen3:0.6b`

The selected runtime smoke model was:

```text
qwen3-next:80b-a3b-instruct-q8_0
```

No model was downloaded. No model was pulled. `ollama pull` was not executed.

## 5 FastAPI Real Transport Request Summary

Endpoint:

```text
POST http://127.0.0.1:18745/local-llm/preview-mock
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
  "error_type": "timeout",
  "message": "Ollama preview request timed out.",
  "model": "qwen3-next:80b-a3b-instruct-q8_0",
  "fallback_used": true,
  "fallback": {
    "mode": "mock_fallback",
    "reason": "timeout",
    "model": "qwen3-next:80b-a3b-instruct-q8_0",
    "prompt_excerpt": "sample tender response excerpt",
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
- response status: `error`
- response error type: `timeout`
- response kept `preview_only=true`
- response kept `no_write=true`
- response kept `affects_score=false`

## 6 Timeout Failure Result Analysis

The Step 174AD response was a stable timeout failure.

This result means:

- the real transport branch was reachable
- the selected model was known locally
- the endpoint attempted the local Ollama preview path
- the adapter returned a bounded failure schema instead of hanging
- the response stayed preview-only and no-write

This result does not mean:

- Ollama is unavailable
- the selected model is missing
- scoring failed
- production scoring should be changed
- UI should be connected
- export should be connected

The timeout is the current boundary result between:

- `DEFAULT_TIMEOUT_SECONDS = 5.0`
- selected model: `qwen3-next:80b-a3b-instruct-q8_0`
- runtime generation latency for the 80B model

The failure is expected to remain stable until a separately authorized stage changes timeout configuration, generation limit, model selection, or smoke scenario.

## 7 Difference Between Stable Preview Failure And Production Scoring

The timeout failure is an advisory preview runtime result.

It is not:

- a formal scoring failure
- a failed QingTian result
- an evidence trace failure
- a scoring basis failure
- an export failure
- a production model quality signal

The response includes:

- `preview_only=true`
- `no_write=true`
- `affects_score=false`

Those fields are the controlling boundary. The response must not be consumed as a production score, score basis, official evidence trace, QingTian result, or export artifact.

## 8 Explicit Non-Integrations

Step 174AD did not:

- run pytest
- download models
- pull models
- execute `ollama pull`
- call external networks
- call OpenAI
- call Spark
- call Gemini
- modify code
- modify tests
- connect UI
- connect production scoring
- trigger DOCX official export
- trigger JSON official export
- trigger Markdown official export
- write storage
- write `data/`
- write `output/`

The only runtime model access was local loopback to `127.0.0.1:11434`.

## 9 No-Write Boundary Verification

Step 174AD did not modify:

- `app/main.py`
- `app/storage.py`
- `app/engine/local_llm_preview_mock.py`
- `app/engine/local_llm_ollama_preview_adapter.py`
- `tests/test_local_llm_ollama_preview_adapter.py`
- `tests/test_local_llm_preview_mock_api_bridge.py`
- `tests/test_local_llm_preview_mock.py`
- release guard files
- smoke guard files
- UI files
- export-chain files
- `ops_agents` files

Step 174AD did not write:

- `data/`
- `output/`
- storage

The Step 174AD smoke report was the only repository file added in that step.

## 10 No-Scoring-Chain Boundary Verification

Step 174AD did not connect or call:

- `score_text()`
- `score_text_v2`
- `/rescore`
- qingtian-results
- `evidence_trace/latest`
- `scoring_basis/latest`
- production score reports
- production evaluation persistence

The timeout result remained isolated to the preview adapter response.

## 11 No-UI And No-Export Verification

Step 174AD did not connect:

- UI
- browser workflows
- DOCX official export
- JSON official export
- Markdown official export
- export bundles

The real transport smoke was backend-only and loopback-only.

## 12 Remaining Risks

- Real transport has reached local Ollama, but the selected 80B model did not complete generation within the current 5s timeout.
- The timeout cannot be treated as proof that Ollama is unavailable.
- The timeout cannot be treated as a production scoring failure.
- The current endpoint cannot be used for production scoring.
- The current endpoint is not UI-enabled.
- The current endpoint is not export-enabled.
- The current endpoint is not connected to qingtian-results, `evidence_trace/latest`, or `scoring_basis/latest`.
- The current endpoint does not write storage, `data/`, or `output/`.
- Timeout adjustment requires a separate design or explicit authorization.
- Switching to a lighter local model requires a separate model-selection boundary.
- Future troubleshooting must not connect `score_text()` or `/rescore`.
- Future troubleshooting must not connect UI or export chains merely to demonstrate output.
- Future real Ollama smoke must be separately authorized and must re-enable the 2nd window.
- If future smoke creates any `data/`, `output/`, or storage change, execution must stop and report the changed paths.

## 13 Required Next-Stage Guard Before Timeout Or Model Optimization

Before any timeout or model-selection optimization, the next stage must explicitly define:

- whether `app/engine/local_llm_ollama_preview_adapter.py` may be modified
- whether `app/main.py` may be modified
- whether tests may be modified or added
- timeout source: environment variable, constant, or request-independent config
- default timeout value
- maximum timeout value
- `num_predict` default value
- `num_predict` maximum value
- model selection policy
- `LOCAL_LLM_OLLAMA_MODEL` priority
- local `/api/tags` fallback
- no model download
- no model pull
- no `ollama pull`
- whether a smaller local model may be used for smoke
- fake-only deterministic tests for code changes
- no `ollama serve` in code/test stage
- 2nd window only for runtime smoke stage
- no writes to `data/`, `output/`, or storage
- no scoring-chain integration
- no UI integration
- no export-chain integration
- mandatory stop for ChatGPT review after completion

## 14 Step 174AE Closure Statement

Step 174AE is a docs-only review of the Step 174AD runtime smoke.

It records that real transport reached local Ollama through loopback, selected `qwen3-next:80b-a3b-instruct-q8_0`, returned HTTP 200 with stable `timeout` failure, and preserved `preview_only=true`, `no_write=true`, and `affects_score=false`.

This document does not authorize automatic timeout adjustment, automatic model switching, UI integration, export integration, scoring-chain integration, storage writes, model downloads, or model pulls.
