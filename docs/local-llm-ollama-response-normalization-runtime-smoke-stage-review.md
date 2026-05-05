# Local LLM Ollama Response Normalization Runtime Smoke Stage Review

## Purpose

This document reviews Step 174AO, which performed a controlled runtime smoke after the local LLM Ollama response normalization fix.

The purpose is to archive the real transport reachability result, the `qwen3:0.6b` runtime `invalid_response` result, the preserved no-write and no-scoring boundaries, remaining risks, and the required guardrails before any future real response structure sampling.

This document is docs-only. It does not authorize response structure sampling, code changes, runtime smoke, UI integration, scoring-chain integration, export-chain integration, storage writes, or production use.

## Baseline before Step 174AO

Step 174AO ran after:

- Step 174AM completed response normalization fixes.
- Step 174AN reviewed the response normalization implementation stage.

Relevant baseline:

- worktree: `/Users/youfeini/Desktop/ZhiFei_BizSystem-local-llm-clean`
- branch: `local-llm-integration-clean`
- Step 174AO starting HEAD: `b80dc3ae536c13c6dd4c4558ecf5cd474d20e4a2`
- Step 174AO report commit: `ebc5d43d35c0e6675b4091d481e89a241bc44401`
- Step 174AO report tag: `v0.1.73-local-llm-ollama-response-normalization-runtime-smoke-report`
- Step 174AO report: `docs/local-llm-ollama-response-normalization-runtime-smoke-report.md`

The Step 174AM fake-only normalization fix had proven that canonical fake Ollama generate responses such as `{"response":"OK","done":true}` normalize to `status=ok`. Step 174AO was the first runtime check after that fix.

## Runtime Smoke Execution Summary

Step 174AO completed a controlled runtime smoke for `POST /local-llm/preview-mock`.

Runtime setup:

- 2nd window was enabled for `ollama serve`.
- Ollama was reachable through `127.0.0.1:11434`.
- FastAPI was started only on `127.0.0.1:18747`.
- endpoint was `POST /local-llm/preview-mock`.
- all three feature flags were enabled:
  - `LOCAL_LLM_PREVIEW_MOCK_API_ENABLED=true`
  - `LOCAL_LLM_OLLAMA_PREVIEW_ADAPTER_ENABLED=true`
  - `LOCAL_LLM_OLLAMA_REAL_TRANSPORT_ENABLED=true`
- model environment variable was set:
  - `LOCAL_LLM_OLLAMA_MODEL=qwen3:0.6b`
- runtime controls were set:
  - `LOCAL_LLM_OLLAMA_TIMEOUT_SECONDS=30`
  - `LOCAL_LLM_OLLAMA_NUM_PREDICT=8`
- pytest was not run.
- code was not modified.
- tests were not modified.
- models were not downloaded.
- models were not pulled.
- `ollama pull` was not executed.
- external networks were not called.

FastAPI was stopped after the request. Port `18747` had no listener after shutdown.

Codex did not close the 2nd window during Step 174AO. The 2nd window state remained subject to ChatGPT instruction.

## Ollama Reachability and Model Inventory

Step 174AO checked Ollama using loopback only:

```bash
curl -sS --max-time 5 -w '\nHTTP_STATUS:%{http_code}\n' http://127.0.0.1:11434/api/tags
```

Result:

- Ollama reachable through `127.0.0.1:11434`: yes
- `/api/tags` returned HTTP status `200`
- `/api/tags` returned valid JSON
- local model count: `7`
- `qwen3:0.6b` existed locally: yes
- timeout: no
- connection refused: no
- invalid `/api/tags` response: no

Local model inventory:

- `qwen3-next:80b-a3b-instruct-q8_0`
- `qwen3-coder:30b`
- `deepseek-r1:32b`
- `qwen3:30b`
- `qwen3:14b`
- `qwen3:8b`
- `qwen3:0.6b`

No model was downloaded. No model was pulled.

## qwen3:0.6b Runtime Result

Selected model:

```text
qwen3:0.6b
```

Selection reason:

- Step 174AO required `qwen3:0.6b`.
- `qwen3:0.6b` was present in local `/api/tags`.
- no fallback model was used.
- no download or pull was performed.

FastAPI startup command:

```bash
LOCAL_LLM_PREVIEW_MOCK_API_ENABLED=true LOCAL_LLM_OLLAMA_PREVIEW_ADAPTER_ENABLED=true LOCAL_LLM_OLLAMA_REAL_TRANSPORT_ENABLED=true LOCAL_LLM_OLLAMA_MODEL=qwen3:0.6b LOCAL_LLM_OLLAMA_TIMEOUT_SECONDS=30 LOCAL_LLM_OLLAMA_NUM_PREDICT=8 python3 -m uvicorn app.main:app --host 127.0.0.1 --port 18747
```

FastAPI PID:

```text
13734
```

Endpoint:

```text
POST http://127.0.0.1:18747/local-llm/preview-mock
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

The request used a minimal synthetic payload. It did not include real bid files, real scoring data, export jobs, formal review data, or production model parameters.

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
  "fallback_used": true
}
```

Response HTTP status:

```text
200
```

## Response Normalization Result Review

Step 174AO confirmed:

- endpoint entered adapter branch: yes
- endpoint entered real transport branch: yes
- response included `real_transport_enabled=true`: yes
- real transport accessed `127.0.0.1:11434`: yes
- access scope was loopback-only: yes
- response normalization status changed to `ok`: no
- response status: `error`
- response error type: `invalid_response`
- response model: `qwen3:0.6b`
- response kept `preview_only=true`: yes
- response kept `no_write=true`: yes
- response kept `affects_score=false`: yes

This proves that fake-only canonical response normalization does not yet cover the observed `qwen3:0.6b` runtime response shape.

## invalid_response Persistence Analysis

The persistent `invalid_response` result means:

- local Ollama was reachable.
- the selected local model existed.
- the endpoint reached the real transport branch.
- the adapter returned a stable preview failure schema.
- the response was not treated as a formal scoring result.

The persistent `invalid_response` does not mean:

- Ollama is unavailable.
- `qwen3:0.6b` is unavailable.
- the production scoring chain failed.
- a formal evaluation result was generated.

The likely remaining investigation space is the actual runtime response structure, response fields, JSON decoding path, model output format, `stream=false` response body, prompt handling, or normalization rule assumptions.

## Difference Between Stable Preview Failure and Production Scoring

The Step 174AO result is a preview failure. It is not a production scoring failure.

The preview response kept:

- `preview_only=true`
- `no_write=true`
- `affects_score=false`

It did not call the scoring chain, did not write scoring evidence, and did not create export artifacts.

## Explicit Non-Integrations

Step 174AO did not run pytest.

Step 174AO did not modify code.

Step 174AO did not modify tests.

Step 174AO did not download models.

Step 174AO did not pull models.

Step 174AO did not execute `ollama pull`.

Step 174AO did not call external networks.

Step 174AO did not call OpenAI.

Step 174AO did not call Spark.

Step 174AO did not call Gemini.

Step 174AO did not modify `app/main.py`.

Step 174AO did not modify `app/storage.py`.

Step 174AO did not modify `app/engine/local_llm_preview_mock.py`.

Step 174AO did not modify `app/engine/local_llm_ollama_preview_adapter.py`.

Step 174AO did not add or modify tests.

Step 174AO did not connect `score_text()`.

Step 174AO did not connect `/rescore`.

Step 174AO did not connect `qingtian-results`.

Step 174AO did not connect `evidence_trace/latest`.

Step 174AO did not connect `scoring_basis/latest`.

Step 174AO did not connect UI.

Step 174AO did not trigger DOCX, JSON, or Markdown formal export.

Step 174AO did not connect a real-model production scoring chain.

## No-Write Boundary Verification

Step 174AO did not write:

- `app/storage.py`
- `data/`
- `output/`
- storage
- `qingtian-results`
- `evidence_trace/latest`
- `scoring_basis/latest`
- DOCX formal export artifacts
- JSON formal export artifacts
- Markdown formal export artifacts

Step 174AP is docs-only and did not write any of those paths.

## No-Scoring-Chain Boundary Verification

Step 174AO did not call:

- `score_text()`
- `/rescore`
- `qingtian-results`
- `evidence_trace/latest`
- `scoring_basis/latest`

The runtime response kept `affects_score=false`.

Step 174AP did not call or modify scoring-chain paths.

## No-UI and No-Export Verification

Step 174AO did not connect UI.

Step 174AO did not trigger DOCX, JSON, or Markdown formal export.

Step 174AP did not connect UI and did not trigger export paths.

## Remaining Risks

- Fake-only normalization has fixed the canonical response path, but real `qwen3:0.6b` runtime still returns `invalid_response`.
- `invalid_response` cannot be interpreted as Ollama being unavailable.
- `invalid_response` may relate to real response structure, response fields, JSON decoding, model output format, `stream=false` response body, or normalization rules.
- The current `invalid_response` cannot be used as a production scoring failure.
- The current state cannot be used for production scoring.
- UI is not connected.
- Export chains are not connected.
- `qingtian-results`, `evidence_trace`, and `scoring_basis` are not connected.
- storage, `data/`, and `output/` remain unwritten.
- Future real response structure sampling requires a separate minimal-summary sampling boundary design.
- Future work must not save full model long-text output.
- Future work must not write model output to `data/`, `output/`, or storage.
- Any future `normalize_ollama_response` adjustment must be preceded by fake-only deterministic tests.
- Future work must not connect `score_text()` or `/rescore` to pass a smoke.
- Future work must not connect UI or export chains for display purposes.
- Any future real Ollama smoke must be separately authorized and must re-enable the 2nd window only for `ollama serve`.
- If any `data/`, `output/`, or storage change appears, the task must stop and report the change list.

## Required Next-Stage Guard Before Response Structure Sampling

Before any future real response structure minimal-summary sampling, the next step must explicitly define:

- whether FastAPI service startup is allowed.
- whether direct Ollama `/api/generate` calls are allowed.
- whether the 2nd window may run `ollama serve`.
- that the sampling model remains an already installed local `qwen3:0.6b`.
- that model download is forbidden.
- that model pull is forbidden.
- that `ollama pull` is forbidden.
- that external network access is forbidden.
- that full model output must not be saved.
- that only minimal response structure summaries may be recorded.
- allowed summary fields such as field names, field types, response length, `done` state, whether `error` exists, and whether `response` exists.
- that `data/`, `output/`, and storage must not be written.
- that scoring chains must remain disconnected.
- that UI must remain disconnected.
- that DOCX, JSON, and Markdown formal export chains must remain disconnected.
- that completion must wait for ChatGPT review before any next step.

## Step 174AP Closure Statement

Step 174AP documents the Step 174AO runtime smoke result. The real transport branch reached local Ollama through `127.0.0.1:11434`, used the local `qwen3:0.6b` model, and still returned `status=error` with `error_type=invalid_response` after response normalization. All preview-only, no-write, no-scoring, no-UI, and no-export boundaries remain intact. This document does not authorize response structure sampling, parser changes, runtime smoke, UI work, scoring-chain work, export-chain work, or production use.
