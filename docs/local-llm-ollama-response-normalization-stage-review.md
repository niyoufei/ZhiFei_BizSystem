# Local LLM Ollama Response Normalization Stage Review

## Purpose

This document reviews Step 174AM, which implemented a controlled response normalization fix for the local LLM Ollama preview adapter.

The review records the implementation scope, `normalize_ollama_response` behavior, canonical fake Ollama response handling, preserved failure schemas, fake-only deterministic test coverage, explicit non-integrations, remaining risks, and required guardrails before any future runtime smoke.

This document is docs-only. It does not authorize runtime smoke, Ollama execution, UI integration, export integration, scoring-chain integration, storage writes, or production use.

## Baseline before Step 174AM

Step 174AM ran after:

- Step 174AJ completed timeout/model controls runtime smoke.
- Step 174AK reviewed the stable `invalid_response` runtime result.
- Step 174AL designed the response normalization and `invalid_response` investigation boundary.

The Step 174AJ runtime smoke had shown:

- real transport reached local Ollama through `127.0.0.1:11434`
- model was `qwen3:0.6b`
- `LOCAL_LLM_OLLAMA_TIMEOUT_SECONDS=30`
- `LOCAL_LLM_OLLAMA_NUM_PREDICT=8`
- endpoint was `POST /local-llm/preview-mock`
- response returned HTTP `200`
- response had `status=error`
- response had `error_type=invalid_response`
- response kept `preview_only=true`
- response kept `no_write=true`
- response kept `affects_score=false`

Step 174AM was limited to fake-only implementation and tests. It did not perform a real runtime smoke.

## Files changed in Step 174AM

Step 174AM modified only:

- `app/engine/local_llm_ollama_preview_adapter.py`
- `tests/test_local_llm_ollama_preview_adapter.py`

Step 174AM did not modify:

- `app/main.py`
- `app/storage.py`
- `app/engine/local_llm_preview_mock.py`
- `tests/test_local_llm_preview_mock.py`
- `tests/test_local_llm_preview_mock_api_bridge.py`
- release guard files
- smoke guard files
- `data/`
- `output/`
- UI files
- DOCX, JSON, or Markdown formal export files
- `ops_agents` files
- requirements, pyproject, or lock files

## normalize_ollama_response behavior review

The primary adjusted function is:

- `normalize_ollama_response`

Step 174AM kept the existing preview-only response model and added explicit handling for:

- non-mapping response values
- Ollama responses containing an `error` field

The function still extracts content from the supported preview response fields and returns a stable `ok` preview response when non-empty content is present.

Every normalized result remains bounded by:

- `preview_only=true`
- `no_write=true`
- `affects_score=false`

## canonical ok response review

The canonical fake Ollama `/api/generate` response:

```json
{"response": "OK", "done": true}
```

now normalizes to:

- `status=ok`
- `reason=ok`
- `model=<configured model>`
- advisory summary `OK`
- `preview_only=true`
- `no_write=true`
- `affects_score=false`

A fake response containing `response`, `done=true`, `model`, `created_at`, and other extra fields also normalizes to `ok` as long as the normalized `response` content is non-empty.

The `ok` response does not include formal scoring result fields.

## invalid_response preservation review

Step 174AM preserved stable `invalid_response` for:

- non-mapping response values
- missing `response`, `content`, and `message.content`
- empty-string `response`
- whitespace-only `response`
- empty `message` content
- streaming-fragment-like non-mapping response shapes
- fake decoder failures through the real-client fake transport path
- Ollama responses containing an `error` field

The `error` field case returns a stable failure and is not normalized as `ok`, even if a `response` field is also present.

## failure schema preservation review

Step 174AM preserved existing failure schemas for:

- `invalid_response`
- `timeout`
- `transport_failure`
- `ollama_unreachable`
- `model_unavailable`

Failure responses continue to include:

- `adapter="ollama_preview"`
- `source="ollama_preview_adapter"`
- stable `status`
- stable `error_type` or reason
- `preview_only=true`
- `no_write=true`
- `affects_score=false`

No failure response is treated as a formal scoring result.

## Fake-only deterministic test coverage

Step 174AM used this test command:

```bash
python3 -m pytest tests/test_local_llm_ollama_preview_adapter.py tests/test_local_llm_preview_mock_api_bridge.py tests/test_local_llm_preview_mock.py -q
```

The result was:

```text
122 passed in 0.91s
```

The fake-only tests cover:

- canonical fake Ollama generate JSON with `response="OK"` and `done=true` returning `ok`
- fake response with `response`, `done=true`, `model`, `created_at`, and extra fields returning `ok`
- missing response content returning `invalid_response`
- empty-string response returning stable failure
- whitespace-only response returning stable failure
- non-mapping response returning `invalid_response`
- streaming-fragment-like response shape not being treated as a formal result
- Ollama `error` field returning stable failure
- fake invalid JSON / decoder failure returning `invalid_response`
- timeout returning `timeout`
- transport failure returning `transport_failure`
- model unavailable returning `model_unavailable`
- deterministic repeated fake responses
- `preview_only=true` on all ok and failure paths
- `no_write=true` on all ok and failure paths
- `affects_score=false` on all ok and failure paths
- existing mock API bridge tests continuing to pass
- existing adapter independent tests continuing to pass

The tests did not use real Ollama, did not start services, did not access `127.0.0.1:11434`, did not access external networks, and did not download or pull models.

## Explicit non-integrations

Step 174AM did not run Ollama.

Step 174AM did not run `ollama serve`.

Step 174AM did not start FastAPI.

Step 174AM did not really access `127.0.0.1:11434`.

Step 174AM did not call external networks.

Step 174AM did not download models.

Step 174AM did not pull models.

Step 174AM did not execute `ollama pull`.

Step 174AM did not modify `app/main.py`.

Step 174AM did not modify `app/storage.py`.

Step 174AM did not modify `app/engine/local_llm_preview_mock.py`.

Step 174AM did not modify `tests/test_local_llm_preview_mock.py`.

Step 174AM did not connect `score_text()`.

Step 174AM did not connect `/rescore`.

Step 174AM did not connect `qingtian-results`.

Step 174AM did not connect `evidence_trace/latest`.

Step 174AM did not connect `scoring_basis/latest`.

Step 174AM did not connect UI.

Step 174AM did not trigger DOCX, JSON, or Markdown formal export.

Step 174AM did not connect any real-model production scoring chain.

## No-write boundary verification

Step 174AM did not write:

- `app/storage.py`
- `data/`
- `output/`
- storage
- `qingtian-results`
- `evidence_trace/latest`
- `scoring_basis/latest`

The implementation does not save complete model long-text output. The preview response contains only bounded advisory content and keeps `raw_response_included=false` on ok normalization.

## No-scoring-chain boundary verification

Step 174AM did not call:

- `score_text()`
- `/rescore`

It did not enter:

- `qingtian-results`
- `evidence_trace/latest`
- `scoring_basis/latest`

All ok and failure paths continue to carry `affects_score=false`. The normalized response is not a formal scoring result.

## No-UI and no-export verification

Step 174AM did not connect UI.

Step 174AM did not trigger DOCX export.

Step 174AM did not trigger JSON export.

Step 174AM did not trigger Markdown formal export.

The normalized preview response must not be treated as a formal deliverable.

## Remaining risks

- Fake-only normalization correction has been completed, but real runtime smoke has not yet been run after Step 174AM.
- A fake canonical response returning `ok` does not guarantee that `qwen3:0.6b` runtime will return `ok`.
- Whether the Step 174AJ `invalid_response` is eliminated remains unknown until a separately authorized runtime smoke.
- The current state cannot be used for production scoring.
- The current state is not connected to UI.
- The current state is not connected to export chains.
- The current state is not connected to `qingtian-results`, `evidence_trace/latest`, or `scoring_basis/latest`.
- The current state does not write storage, `data/`, or `output/`.
- Future runtime smoke must be separately authorized.
- Future runtime smoke must use a 2nd window for `ollama serve`.
- Future runtime smoke must not download or pull models.
- Future runtime smoke must not modify scoring chain, UI, export chain, or storage to make the smoke pass.
- If any future stage creates `data/`, `output/`, or storage changes, it must stop and report the change list.

## Required next-stage guard before runtime smoke

Before any future runtime smoke, the next stage must explicitly confirm:

- the current ChatGPT conversation remains the controller
- the current Codex nifei1227 conversation remains the executor
- a 2nd window must be enabled to run only `ollama serve`
- the 2nd window must not run git
- the 2nd window must not run pytest
- the 2nd window must not modify code
- the 2nd window must not commit, tag, or push
- Codex nifei1227 may perform repository checks, FastAPI startup, loopback requests, report documentation, commit, tag, and push
- only Codex nifei1227 may perform repository write operations during the stage
- FastAPI must listen only on `127.0.0.1`
- Ollama access must be limited to `127.0.0.1:11434`
- `qwen3:0.6b` may continue to be used if present locally
- `LOCAL_LLM_OLLAMA_TIMEOUT_SECONDS=30` may continue to be used
- `LOCAL_LLM_OLLAMA_NUM_PREDICT=8` may continue to be used
- the run must not download models
- the run must not pull models
- the run must not execute `ollama pull`
- the run must not call external networks
- the run must not write `data/`, `output/`, or storage
- the run must not call `score_text()` or `/rescore`
- the run must not enter `qingtian-results`, `evidence_trace/latest`, or `scoring_basis/latest`
- the run must not connect UI
- the run must not trigger export chains
- completion must stop for ChatGPT review

## Step 174AN closure statement

Step 174AN records Step 174AM as a fake-only response normalization implementation milestone.

The canonical fake Ollama generate response now normalizes to `ok`, stable failure schemas remain preserved, and all paths retain `preview_only=true`, `no_write=true`, and `affects_score=false`.

This document does not authorize automatic runtime smoke, Ollama execution, service startup, UI integration, scoring-chain integration, export-chain integration, storage writes, or production use.
