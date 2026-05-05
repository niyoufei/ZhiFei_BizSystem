# Local LLM Ollama Real Transport Guard and Test Design

## 1 Purpose

This document defines the guard task specification and deterministic test design for a future local Ollama real transport implementation.

The current Step 174AA stage is docs-only. It does not implement code, does not modify `app/main.py`, does not modify `app/engine/local_llm_ollama_preview_adapter.py`, does not add or modify tests, does not run pytest, does not start FastAPI, does not run Ollama, does not run `ollama serve`, does not call external networks, does not download or pull models, and does not write `data/`, `output/`, or storage.

This document must not be interpreted as permission to immediately implement real transport.

## 2 Baseline Inherited From Step 174Z

- worktree: `/Users/youfeini/Desktop/ZhiFei_BizSystem-local-llm-clean`
- branch: `local-llm-integration-clean`
- baseline before Step 174AA: `4f4f54e129d2c82aefc95311c8182563137d9f90`
- baseline tag: `v0.1.58-local-llm-ollama-real-transport-api-bridge-design`
- Step 174Z design: `docs/local-llm-ollama-real-transport-api-bridge-design.md`
- endpoint: `POST /local-llm/preview-mock`
- endpoint feature flag: `LOCAL_LLM_PREVIEW_MOCK_API_ENABLED`
- adapter feature flag: `LOCAL_LLM_OLLAMA_PREVIEW_ADAPTER_ENABLED`
- proposed real transport feature flag: `LOCAL_LLM_OLLAMA_REAL_TRANSPORT_ENABLED`

Step 174Z documented that Step 174X proved local Ollama reachability and minimal generation with `qwen3-next:80b-a3b-instruct-q8_0`, while the FastAPI endpoint still did not pass a real client or transport into the adapter.

## 3 Guard Objective

The future guard must prevent a real transport implementation from expanding beyond preview scope.

Guard objectives:

- keep real transport default-off
- keep real transport preview-only
- keep real transport no-write
- keep real transport `affects_score=false`
- block scoring-chain integration
- block storage writes
- block `data/` and `output/` writes
- block UI integration
- block export-chain integration
- block non-local network calls
- block model downloads and pulls
- require deterministic tests to use fake clients or monkeypatching
- require separate authorization for real Ollama service smoke

The guard must treat any production scoring, persistence, UI, export, or external model provider connection as a stop condition.

## 4 Guard Forbidden File Scope

Until a future implementation step explicitly authorizes otherwise, the guard must reject changes to:

- `app/main.py`
- `app/storage.py`
- `app/engine/local_llm_preview_mock.py`
- `app/engine/local_llm_ollama_preview_adapter.py`
- `tests/test_local_llm_preview_mock_api_bridge.py`
- `tests/test_local_llm_ollama_preview_adapter.py`
- `tests/test_local_llm_preview_mock.py`
- release guard files
- smoke guard files
- `data/`
- `output/`
- UI files
- DOCX / JSON / Markdown official export-chain files
- `ops_agents` files

The guard must also reject:

- new tests before authorization
- new runtime dependencies before authorization
- changes to `requirements`, `pyproject`, lock files, or dependency metadata
- release guard / smoke guard modifications unless separately designed and authorized

Future implementation must explicitly list allowed files before any code edit starts.

## 5 Guard Forbidden Runtime Scope

The future guard must reject runtime actions that are not explicitly authorized:

- running pytest in design-only stages
- starting FastAPI in design-only stages
- running Ollama in design-only stages
- running `ollama serve` in design-only or deterministic-test stages
- calling real Ollama from deterministic tests
- executing `ollama pull`
- downloading models
- pulling models
- installing dependencies
- starting browsers
- invoking UI workflows
- triggering export workflows
- cleaning untracked files
- executing `git clean`

Real Ollama runtime smoke must be a separate step with 2nd-window authorization.

## 6 Guard Forbidden Network Scope

Future real transport may only target local Ollama:

```text
http://127.0.0.1:11434
```

The guard must reject:

- external URLs
- remote model providers
- OpenAI calls
- Spark calls
- Gemini calls
- arbitrary user-provided base URLs
- non-loopback base URLs
- `0.0.0.0` listeners
- broad host binding
- network calls from deterministic tests

The guard must also reject new `requests`, `httpx`, or `urllib` usage unless the future implementation explicitly authorizes the exact transport file and proves the URL is loopback-only. No new external dependency may be added merely to call local Ollama.

## 7 Guard Forbidden Scoring-Chain Scope

The guard must reject any real transport implementation that calls or connects:

- `score_text()`
- `score_text_v2`
- `/rescore`
- qingtian-results
- `evidence_trace/latest`
- `scoring_basis/latest`
- ground truth paths
- calibration write paths
- score report writes
- production scoring write-back
- production evaluation persistence

Real transport output must remain advisory and must never become a scoring input or scoring result.

## 8 Guard Forbidden Output Scope

The guard must reject writes to:

- `app/storage.py`
- `data/`
- `output/`
- qingtian-results
- `evidence_trace/latest`
- `scoring_basis/latest`
- score reports
- evolution reports
- export bundles
- DOCX official exports
- JSON official exports
- Markdown official exports

The guard must reject response fields or code paths with official result semantics such as:

- `final_score`
- `score_result`
- `write_result`
- `persist`
- `export`
- `apply`
- `storage_write`
- `evidence_trace_write`
- `scoring_basis_write`

## 9 Feature Flag Contract

Future real transport must use explicit feature flags with this hierarchy:

```text
LOCAL_LLM_PREVIEW_MOCK_API_ENABLED
LOCAL_LLM_OLLAMA_PREVIEW_ADAPTER_ENABLED
LOCAL_LLM_OLLAMA_REAL_TRANSPORT_ENABLED
```

Contract:

1. `LOCAL_LLM_PREVIEW_MOCK_API_ENABLED` is the endpoint total gate.
2. If endpoint flag is absent, empty, `false`, `0`, `no`, or `off`, the endpoint must return disabled directly.
3. Endpoint disabled state must not check adapter flag.
4. Endpoint disabled state must not check real transport flag.
5. `LOCAL_LLM_OLLAMA_PREVIEW_ADAPTER_ENABLED` is the adapter branch gate.
6. If adapter flag is absent, empty, `false`, `0`, `no`, or `off`, the endpoint must keep the mock-only helper path.
7. Adapter disabled state must not check real transport flag.
8. `LOCAL_LLM_OLLAMA_REAL_TRANSPORT_ENABLED` is the real transport gate.
9. Real transport flag must default to disabled.
10. If real transport flag is absent, empty, `false`, `0`, `no`, or `off`, the endpoint must not call real Ollama.
11. Only when all required flags are true-like may real local Ollama be called.

Even when all flags are enabled, every result must include:

- `preview_only=true`
- `no_write=true`
- `affects_score=false`

No flag state may write `data/`, `output/`, or storage or affect scoring results.

## 10 Model Selection Contract

Future real transport must use a controlled local model source.

Allowed model source order:

1. `LOCAL_LLM_OLLAMA_MODEL`
2. local `/api/tags` first installed model
3. stable `model_unavailable` if no local model exists

Required boundaries:

- do not hardcode a production scoring model
- do not download models
- do not pull models
- do not execute `ollama pull`
- do not access external networks
- do not silently call OpenAI / Spark / Gemini
- do not use request-provided arbitrary remote model URLs
- do not store full model output
- record only model name summaries in reports

If `LOCAL_LLM_OLLAMA_MODEL` is set, it must take precedence over `/api/tags`. If the named model is unavailable, return stable `model_unavailable`; do not download or pull it.

## 11 Timeout Contract

Future real transport must set a timeout.

Recommended defaults:

- deterministic tests: fake client timeout behavior only
- adapter default timeout: `5` seconds unless explicitly changed
- authorized real smoke maximum: `30` seconds

Timeout behavior:

- return stable `timeout`
- preserve `preview_only=true`
- preserve `no_write=true`
- preserve `affects_score=false`
- do not retry into scoring chain
- do not write files
- do not call remote providers
- do not widen timeout in code without authorization

Timeout failures are expected runtime outcomes, not a reason to connect scoring, UI, or export chains.

## 12 Failure Schema Contract

Future real transport must return stable failure schemas.

Required statuses or error types:

- `ollama_unreachable`
- `model_unavailable`
- `timeout`
- `invalid_response`
- `ok`

Required fields for every response:

- `adapter=ollama_preview`
- `source=ollama_preview_adapter`
- `status`
- `model` when selected or requested
- `preview_only=true`
- `no_write=true`
- `affects_score=false`

Required failure fields:

- `error_type`
- `message`
- `fallback_used` when fallback is used
- bounded fallback summary when applicable

Mapping:

- connection refused or service unavailable: `ollama_unreachable`
- no local model and no configured model: `model_unavailable`
- configured model missing: `model_unavailable`
- request timeout: `timeout`
- malformed tags or generate response: `invalid_response`
- successful fake or real local preview response: `ok`

Failures must not enter scoring, storage, UI, or export chains.

## 13 Deterministic Tests Matrix

Future tests must be deterministic and must not call real Ollama.

Required matrix:

1. endpoint flag absent: does not check adapter or transport
2. adapter flag absent: does not check real transport
3. real transport flag absent: does not call real Ollama
4. real transport flag `false` / `0` / `no` / `off`: does not call real Ollama
5. real transport flag `true` but no model name and fake tags empty: returns `model_unavailable`
6. real transport flag `true` and fake tags has models: selects a local model
7. `LOCAL_LLM_OLLAMA_MODEL` set: uses that model first
8. fake Ollama unreachable: returns `ollama_unreachable`
9. fake timeout: returns `timeout`
10. fake model unavailable: returns `model_unavailable`
11. fake invalid response: returns `invalid_response`
12. fake success: returns `ok`
13. every result includes `preview_only=true`
14. every result includes `no_write=true`
15. every result includes `affects_score=false`
16. every result does not call `score_text()` or `/rescore`
17. every result does not enter qingtian-results / `evidence_trace` / `scoring_basis`
18. every result does not write `data/`, `output/`, or storage
19. every result does not trigger export chain
20. every result does not connect UI
21. same input and same fake response are deterministic
22. tests do not call real Ollama
23. tests do not start FastAPI service
24. tests do not access external networks
25. tests do not download or pull models
26. existing mock API bridge tests continue to pass
27. existing adapter independent tests continue to pass

Tests must use fake client, fake tags, fake generate response, monkeypatching, or injected callables. They must not require a running `ollama serve`.

## 14 Required Negative Tests

Future tests must include negative assertions for forbidden behavior:

- endpoint disabled does not call adapter
- adapter disabled does not inspect real transport
- transport disabled does not call real Ollama
- fake unreachable does not retry external providers
- fake timeout does not call scoring
- fake invalid response does not write output
- missing model does not call `ollama pull`
- forbidden payload fields do not produce formal score semantics
- output does not contain `final_score`
- output does not contain `score_result`
- output does not contain `write_result`
- output does not contain `storage_write`
- output does not contain export result semantics

Negative tests must fail if scoring, storage, UI, export, or external model provider paths are touched.

## 15 Required No-Write Verification

Future tests and implementation review must prove no writes to:

- `app/storage.py`
- `data/`
- `output/`
- qingtian-results
- `evidence_trace/latest`
- `scoring_basis/latest`
- score reports
- export bundles
- DOCX / JSON / Markdown official exports

Implementation self-check must include:

```bash
git diff --name-only
git status --short
```

Runtime smoke must stop and report if any unexpected file appears.

## 16 Required No-Scoring-Chain Verification

Future tests must monkeypatch or otherwise guard against:

- `score_text()`
- `score_text_v2`
- `/rescore`
- qingtian-results access
- `evidence_trace/latest` access
- `scoring_basis/latest` access
- production scoring writes
- calibration writes
- ground truth writes

Any call to these paths must fail deterministic tests.

## 17 Required No-Real-Model-In-Tests Verification

Deterministic tests must not use:

- real `ollama serve`
- real `http://127.0.0.1:11434`
- real `/api/tags`
- real `/api/generate`
- external network
- downloaded models
- pulled models
- real local model output

Allowed test techniques:

- fake client callable
- fake transport object
- monkeypatch
- fake tags payload
- fake generate payload
- fake timeout exception
- fake connection failure exception
- fake invalid response

Real Ollama may be exercised only in a separately authorized smoke stage with 2nd-window `ollama serve`.

## 18 Future Implementation Acceptance Criteria

Before any real transport code implementation, the next step must explicitly define:

- that Step 174AA guard/test design has been archived
- allowed file modification scope
- whether `app/engine/local_llm_ollama_preview_adapter.py` may be modified
- whether `app/main.py` may be modified
- whether `tests/test_local_llm_preview_mock_api_bridge.py` may be modified
- whether `tests/test_local_llm_ollama_preview_adapter.py` may be added or modified
- whether any new tests may be added
- real transport feature flag name
- timeout default value
- model name source
- failure response schema
- deterministic test command
- confirmation that tests must not call real Ollama
- confirmation that implementation stage must not run `ollama serve`
- confirmation that real smoke stage alone may use 2nd-window `ollama serve`
- no-write boundary
- no-scoring-chain boundary
- no-UI boundary
- no-export boundary
- no-model-download boundary
- no-model-pull boundary
- no-main-push boundary
- final report format
- requirement to stop and wait for ChatGPT review after completion

Without these conditions, implementation must not begin.

## 19 Step 174AA Closure Statement

Step 174AA is complete only when this docs-only guard/test design is added, committed, tagged, and pushed on `local-llm-integration-clean`.

Step 174AA does not:

- write API code
- modify `app/main.py`
- modify `app/storage.py`
- modify `app/engine/local_llm_preview_mock.py`
- modify `app/engine/local_llm_ollama_preview_adapter.py`
- add or modify tests
- run pytest
- start FastAPI
- run Ollama
- run `ollama serve`
- call external networks
- download models
- pull models
- install dependencies
- connect UI
- connect production scoring
- connect export chain
- write `data/`
- write `output/`
- push main

Future real transport implementation requires a new explicit instruction and must not start automatically.
