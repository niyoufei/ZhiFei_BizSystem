# Local LLM Ollama Preview Real Model Smoke Stage Review

## 1 Purpose

This document reviews the Step 174X real local Ollama preview smoke.

Step 174X verified that the local Ollama service was reachable through `127.0.0.1:11434`, performed one minimal real `/api/generate` request against an already installed local model, and verified that the current FastAPI `POST /local-llm/preview-mock` adapter-enabled branch still remains preview-only, no-write, and `affects_score=false`.

This Step 174Y review is docs-only. It does not start service, does not run Ollama, does not run `ollama serve`, does not run pytest, does not call external networks, does not modify code, does not add or modify tests, and does not write `data/`, `output/`, or storage.

## 2 Baseline Before Step 174X

- worktree: `/Users/youfeini/Desktop/ZhiFei_BizSystem-local-llm-clean`
- branch: `local-llm-integration-clean`
- Step 174X starting HEAD: `3a0e1c002fa5d91c4ef992d88431734f53398b5b`
- Step 174X result commit: `e71589e1a773ae4b61cfd2fa23325f8a1aeb9f57`
- Step 174X result tag: `v0.1.56-local-llm-ollama-real-model-smoke-report`
- Step 174X report: `docs/local-llm-ollama-preview-real-model-smoke-report.md`
- endpoint: `POST /local-llm/preview-mock`
- endpoint feature flag: `LOCAL_LLM_PREVIEW_MOCK_API_ENABLED`
- adapter feature flag: `LOCAL_LLM_OLLAMA_PREVIEW_ADAPTER_ENABLED`

Step 174X was a controlled smoke verification step. It did not modify API code, adapter code, tests, storage, UI, scoring chain, or export chain.

## 3 Ollama Service Reachability Result

Step 174X completed a local Ollama reachability check:

```bash
curl --noproxy '*' -sS --max-time 5 http://127.0.0.1:11434/api/tags
```

Result:

- local Ollama service reachable: yes
- address: `127.0.0.1:11434`
- `GET /api/tags` HTTP status: `200`
- timeout: no
- connection refused: no
- invalid response: no
- external network call: no

This confirmed that local Ollama itself was available during Step 174X.

## 4 Local Model Inventory Summary

Step 174X observed 7 locally installed models:

- `qwen3-next:80b-a3b-instruct-q8_0`
- `qwen3-coder:30b`
- `deepseek-r1:32b`
- `qwen3:30b`
- `qwen3:14b`
- `qwen3:8b`
- `qwen3:0.6b`

The selected smoke model was the first installed model:

```text
qwen3-next:80b-a3b-instruct-q8_0
```

No model was downloaded.

No model was pulled.

`ollama pull` was not run.

## 5 Real Ollama Minimal Generation Result

Step 174X executed one minimal real local Ollama generation request.

Endpoint:

```text
POST http://127.0.0.1:11434/api/generate
```

Request summary:

```json
{
  "model": "qwen3-next:80b-a3b-instruct-q8_0",
  "prompt": "Return OK only.",
  "stream": false,
  "options": {
    "num_predict": 8
  }
}
```

Result:

- HTTP status: `200`
- selected model: `qwen3-next:80b-a3b-instruct-q8_0`
- response summary: `OK`
- `done=true`
- `done_reason=stop`
- output length: minimal preview response
- external network call: no
- model download: no
- model pull: no
- OpenAI / Spark / Gemini call: no

This verified that the local Ollama runtime and at least one local model could perform a minimal preview generation.

## 6 FastAPI Adapter Enabled Loopback Result

Step 174X started FastAPI only on loopback:

```bash
LOCAL_LLM_PREVIEW_MOCK_API_ENABLED=true LOCAL_LLM_OLLAMA_PREVIEW_ADAPTER_ENABLED=true python3 -m uvicorn app.main:app --host 127.0.0.1 --port 18744
```

Runtime facts:

- FastAPI host: `127.0.0.1`
- FastAPI port: `18744`
- endpoint: `POST /local-llm/preview-mock`
- endpoint feature flag: `LOCAL_LLM_PREVIEW_MOCK_API_ENABLED=true`
- adapter feature flag: `LOCAL_LLM_OLLAMA_PREVIEW_ADAPTER_ENABLED=true`
- recorded FastAPI PID: `76845`
- FastAPI service stopped: yes
- port `18744` listener after shutdown: none

Request summary:

```text
POST http://127.0.0.1:18744/local-llm/preview-mock
```

Response summary:

- HTTP status: `200`
- `enabled=true`
- `adapter_enabled=true`
- `adapter=ollama_preview`
- `source=ollama_preview_adapter`
- `status=error`
- `error_type=model_unavailable`
- `message=Ollama preview client is not configured.`
- `model=local-preview-no-real-model`
- `fallback_used=true`
- `preview_only=true`
- `no_write=true`
- `affects_score=false`

The FastAPI endpoint entered the adapter branch, but the current endpoint still did not pass a real client or transport into the adapter.

## 7 Difference Between Ollama Availability And Endpoint Real Transport

Step 174X established two separate facts:

1. Local Ollama itself was available and could generate a minimal response with an installed model.
2. The FastAPI endpoint still returned `model_unavailable` because the current API bridge does not pass a real client or transport into `run_ollama_preview`.

The endpoint `model_unavailable` response is therefore the expected no-real-transport boundary result. It does not mean the local Ollama service or selected model was unavailable.

The current endpoint remains a controlled adapter branch with no real transport wired into the FastAPI route.

## 8 Explicit Non-Integrations

Step 174X did not integrate:

- production scoring
- `score_text()`
- `score_text_v2`
- `/rescore`
- qingtian-results
- `evidence_trace/latest`
- `scoring_basis/latest`
- `app/storage.py` writes
- `data/` writes
- `output/` writes
- UI
- DOCX official export
- JSON official export
- Markdown official export
- OpenAI
- Spark
- Gemini
- true endpoint-level model transport
- true model production scoring chain

Step 174Y also does not integrate any of these.

## 9 No-Write Boundary Verification

Step 174X recorded:

- `app/storage.py` was not written
- `data/` was not written
- `output/` was not written
- no qingtian-results write occurred
- no `evidence_trace/latest` write occurred
- no `scoring_basis/latest` write occurred
- no DOCX / JSON / Markdown official export was triggered

Before the Step 174X smoke report was written:

```text
git status --short
```

returned no output.

```text
git diff --name-only
```

returned no output.

The only Step 174X repository change was the smoke report document.

## 10 No-Scoring-Chain Boundary Verification

Step 174X did not call:

- `score_text()`
- `score_text_v2`
- `/rescore`
- qingtian-results
- `evidence_trace/latest`
- `scoring_basis/latest`
- ground truth write paths
- score report write paths
- calibration write paths
- production scoring write-back

The FastAPI endpoint response kept:

- `preview_only=true`
- `no_write=true`
- `affects_score=false`

This preserved the no-scoring-chain boundary.

## 11 No-UI And No-Export Verification

Step 174X did not:

- start a browser
- connect UI
- add UI controls
- trigger frontend behavior
- trigger DOCX official export
- trigger JSON official export
- trigger Markdown official export
- generate official report bundles
- write official export output

The real Ollama preview request and the FastAPI endpoint request were both loopback API checks only.

## 12 Incident Note: Empty Model Payload Retry

Step 174X had one local request construction issue:

- an initial `/api/generate` request used an empty `model` field because a shell variable was not exported into the temporary payload-generation process
- Ollama returned `HTTP_STATUS:404`
- error summary: `model '' not found`

Impact:

- no repository file was modified
- no model was downloaded
- no model was pulled
- no external network was called
- no code was changed
- no test was changed

Correction:

- the selected local model name was exported correctly
- the request was rerun against `qwen3-next:80b-a3b-instruct-q8_0`
- the corrected request returned HTTP `200`
- response summary was `OK`

This incident does not affect the smoke result, but it should be retained as an execution note.

## 13 Remaining Risks

Remaining risks:

- real Ollama itself was verified, but the FastAPI endpoint still does not pass a real client or transport
- current endpoint `model_unavailable` is the no-real-transport boundary result
- current endpoint cannot be used for production scoring
- current endpoint is not UI-connected
- current endpoint is not export-chain-connected
- current endpoint is not connected to qingtian-results
- current endpoint is not connected to `evidence_trace/latest`
- current endpoint is not connected to `scoring_basis/latest`
- current endpoint does not write storage
- current endpoint does not write `data/`
- current endpoint does not write `output/`
- future real transport work must first define a design boundary
- future real transport must remain default-off
- future real transport must remain preview-only
- future real transport must remain no-write
- future real transport must keep `affects_score=false`
- future real transport must have timeout handling
- future real transport must have stable failure schema
- future real transport must define model-name source and local-model selection boundary
- future real transport should consider a model allowlist or explicit local tag selection boundary
- future real transport must remain limited to `127.0.0.1`
- future work must not download or pull models unless separately authorized
- future work must not call OpenAI / Spark / Gemini
- future work must not connect `score_text()` or `/rescore` to pass a smoke
- future work must not connect UI or export chain for demonstration convenience
- future work must stop and report if any storage, `data/`, or `output/` changes appear

## 14 Required Next-Stage Guard Before Step 174Z

Any future Step 174Z must be separately authorized and must first clarify:

- whether a true client/transport design document is required before implementation
- whether `app/engine/local_llm_ollama_preview_adapter.py` may be modified
- whether `app/main.py` may be modified
- whether deterministic tests may be added or modified
- whether real Ollama may be called
- whether a 2nd window is required
- whether `ollama serve` is expected to run
- model name source: environment variable, first local `/api/tags` result, or fixed allowlist
- timeout value
- failure response schema
- disabled behavior
- no-write verification method
- no-scoring-chain verification method
- UI non-integration boundary
- export-chain non-integration boundary
- whether FastAPI service startup is allowed
- whether commit/tag/push is authorized
- required final report format
- explicit instruction to stop and wait for ChatGPT review after completion

Step 174Z must not proceed directly into production scoring, UI integration, storage writes, export-chain behavior, or unbounded real model runtime behavior.

## 15 Step 174Y Closure Statement

Step 174Y is complete only when this docs-only review is added, committed, tagged, and pushed on `local-llm-integration-clean`.

Step 174Y does not:

- start FastAPI
- run pytest
- run Ollama
- run `ollama serve`
- call external networks
- download or pull models
- modify `app/main.py`
- modify `app/storage.py`
- modify `app/engine/local_llm_preview_mock.py`
- modify `app/engine/local_llm_ollama_preview_adapter.py`
- add or modify tests
- write `data/`
- write `output/`
- connect `score_text()`
- connect `/rescore`
- connect qingtian-results
- connect `evidence_trace/latest`
- connect `scoring_basis/latest`
- connect UI
- trigger DOCX / JSON / Markdown official exports
- connect a true model production scoring chain
- push main

Future Step 174Z requires a new explicit instruction and must not start automatically.
