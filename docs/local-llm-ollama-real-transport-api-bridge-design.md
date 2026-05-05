# Local LLM Ollama Real Transport API Bridge Design

## 1 Purpose

This document defines the pre-implementation boundary for connecting a real local Ollama client / transport to the existing local LLM preview API bridge.

The current Step 174Z stage is docs-only. It only designs the boundary for a future implementation. It does not modify `app/main.py`, does not modify `app/engine/local_llm_ollama_preview_adapter.py`, does not add or modify tests, does not run pytest, does not start FastAPI, does not run Ollama, does not run `ollama serve`, does not call external networks, does not download or pull models, and does not write `data/`, `output/`, or storage.

This document must not be interpreted as permission to immediately implement real transport or call Ollama from the FastAPI endpoint.

## 2 Baseline Inherited From Step 174X And Step 174Y

- worktree: `/Users/youfeini/Desktop/ZhiFei_BizSystem-local-llm-clean`
- branch: `local-llm-integration-clean`
- baseline before Step 174Z: `93aa0c2a28c884e560a886ae47618904a6071f15`
- baseline tag: `v0.1.57-local-llm-ollama-real-model-smoke-stage-review`
- Step 174X report: `docs/local-llm-ollama-preview-real-model-smoke-report.md`
- Step 174Y review: `docs/local-llm-ollama-preview-real-model-smoke-stage-review.md`
- current endpoint: `POST /local-llm/preview-mock`
- endpoint feature flag: `LOCAL_LLM_PREVIEW_MOCK_API_ENABLED`
- adapter feature flag: `LOCAL_LLM_OLLAMA_PREVIEW_ADAPTER_ENABLED`

Step 174X established two facts:

1. Local Ollama was reachable through `127.0.0.1:11434`.
2. The current FastAPI endpoint still did not pass a real client or transport into the adapter.

Step 174Y documented that the endpoint `model_unavailable` response is the expected no-real-transport boundary result and does not mean the local Ollama service was unavailable.

## 3 Current Endpoint No-Real-Transport Boundary

The current endpoint is:

```text
POST /local-llm/preview-mock
```

The current feature flag hierarchy is:

1. `LOCAL_LLM_PREVIEW_MOCK_API_ENABLED` controls whether the endpoint is enabled.
2. `LOCAL_LLM_OLLAMA_PREVIEW_ADAPTER_ENABLED` controls whether the endpoint enters the adapter branch.

Current endpoint behavior:

- endpoint flag disabled: return disabled directly
- endpoint flag enabled + adapter flag disabled: use mock-only helper
- endpoint flag enabled + adapter flag enabled: call `run_ollama_preview` without a real client or transport

Current adapter-enabled endpoint response:

- `status=error`
- `error_type=model_unavailable`
- `message=Ollama preview client is not configured.`
- `preview_only=true`
- `no_write=true`
- `affects_score=false`

This is the no-real-transport boundary. It is intentional and must remain stable until a separate implementation step explicitly authorizes real transport.

## 4 Real Transport Objective

A future real transport implementation may allow the adapter-enabled API bridge to call the local Ollama HTTP API through an explicitly controlled client.

The objective must remain limited to preview:

- call only local Ollama on `127.0.0.1:11434`
- use only an already installed local model
- return preview-only advisory output
- preserve `preview_only=true`
- preserve `no_write=true`
- preserve `affects_score=false`
- never write storage, `data/`, or `output/`
- never enter the scoring main chain
- never trigger UI or export chains
- return stable failures for unavailable runtime, unavailable model, timeout, and invalid response

Real transport does not mean production scoring integration.

## 5 Non-Goals

Future real transport work must not:

- connect production scoring
- call `score_text()`
- call `score_text_v2`
- call `/rescore`
- enter qingtian-results
- enter `evidence_trace/latest`
- enter `scoring_basis/latest`
- write `app/storage.py`
- write `data/`
- write `output/`
- trigger DOCX / JSON / Markdown official exports
- connect UI
- call OpenAI
- call Spark
- call Gemini
- access external networks
- download models
- pull models
- execute `ollama pull`
- install dependencies
- modify release guard or smoke guard without separate design and authorization
- push main

Current Step 174Z does not implement any code.

## 6 Feature Flag Hierarchy

Future real transport must continue to use the existing outer gates:

```text
LOCAL_LLM_PREVIEW_MOCK_API_ENABLED
LOCAL_LLM_OLLAMA_PREVIEW_ADAPTER_ENABLED
```

Future real transport may add or reuse this explicit transport gate:

```text
LOCAL_LLM_OLLAMA_REAL_TRANSPORT_ENABLED
```

Recommended hierarchy:

1. If `LOCAL_LLM_PREVIEW_MOCK_API_ENABLED` is absent, empty, `false`, `0`, `no`, or `off`, the endpoint must return disabled directly.
2. When endpoint flag is disabled, the endpoint must not check adapter flag and must not check real transport flag.
3. If endpoint flag is enabled and `LOCAL_LLM_OLLAMA_PREVIEW_ADAPTER_ENABLED` is absent, empty, `false`, `0`, `no`, or `off`, the endpoint must use the existing mock-only helper.
4. When adapter flag is disabled, the endpoint must not check real transport flag.
5. If endpoint flag and adapter flag are enabled but `LOCAL_LLM_OLLAMA_REAL_TRANSPORT_ENABLED` is absent, empty, `false`, `0`, `no`, or `off`, the endpoint must not call real Ollama.
6. Only when all required flags are true-like may the endpoint call real local Ollama transport.

All flags default to disabled.

If any flag is absent, default behavior must:

- not call real Ollama
- not write `data/`
- not write `output/`
- not write storage
- not affect scoring results

Even when real transport flag is enabled, response semantics must keep:

- `preview_only=true`
- `no_write=true`
- `affects_score=false`

## 7 Model Selection Boundary

Future real transport must define an explicit local model source.

Required constraints:

- do not hardcode a production scoring model
- do not download models
- do not pull models
- do not execute `ollama pull`
- do not access external networks
- do not silently switch to OpenAI / Spark / Gemini
- do not store model output in `data/`, `output/`, or storage

Recommended model source order:

1. Read `LOCAL_LLM_OLLAMA_MODEL`.
2. If `LOCAL_LLM_OLLAMA_MODEL` is not set, read local Ollama `/api/tags` through `127.0.0.1:11434`.
3. If `/api/tags` returns local models, use the first local model for preview only.
4. If no local model exists, return stable `model_unavailable`.

Step 174X observed that `qwen3-next:80b-a3b-instruct-q8_0` was the first installed local model and could complete a minimal generation. That observation may be used as evidence, but future implementation must not assume that every machine has the same model list.

Smoke reports may record the model name summary. They must not record large model outputs.

## 8 Localhost-Only Transport Boundary

Future real transport may call only:

```text
http://127.0.0.1:11434
```

Allowed local endpoints:

- `GET /api/tags`
- `POST /api/generate`

Forbidden transport behavior:

- listening on `0.0.0.0`
- calling external networks
- calling remote model APIs
- calling OpenAI
- calling Spark
- calling Gemini
- calling arbitrary URLs from request payload
- accepting user-provided base URLs
- silently falling back to non-local model providers

If a base URL is configurable in a future implementation, its default must be `http://127.0.0.1:11434`, and non-loopback values must be rejected unless separately authorized.

## 9 Timeout Boundary

Future real transport must set explicit timeouts.

Recommended default timeout:

```text
5 seconds for adapter unit tests through fake clients
30 seconds maximum for authorized smoke requests
```

Implementation requirements:

- timeout must be bounded
- timeout must produce stable `timeout` failure
- timeout must not trigger retries that write files
- timeout must not fall back to scoring chain
- timeout must not call remote providers
- timeout must preserve `preview_only=true`
- timeout must preserve `no_write=true`
- timeout must preserve `affects_score=false`

Long-running model calls must be reported as smoke risk, not repaired by widening runtime scope without authorization.

## 10 Failure Schema Design

Future real transport must normalize failures into stable preview responses.

Required statuses or error types:

- `ollama_unreachable`
- `model_unavailable`
- `timeout`
- `invalid_response`
- `ok`

Recommended response fields for every result:

- `adapter=ollama_preview`
- `source=ollama_preview_adapter`
- `status`
- `error_type` when failed
- `message` when failed
- `model`
- `preview_only=true`
- `no_write=true`
- `affects_score=false`
- `fallback_used` when applicable

Failure behavior:

- Ollama connection refused: `ollama_unreachable`
- `/api/tags` unavailable or malformed: `ollama_unreachable` or `invalid_response`
- no local model available: `model_unavailable`
- selected model missing: `model_unavailable`
- request timeout: `timeout`
- malformed `/api/generate` response: `invalid_response`
- successful local preview generation: `ok`

Failures must not write storage, must not enter scoring, and must not trigger export behavior.

## 11 Response Normalization Design

Future real transport must normalize the local Ollama response into advisory preview output.

Allowed normalized fields:

- adapter name
- source
- status
- model name
- advisory preview excerpt
- failure type
- failure message
- fallback summary
- `preview_only=true`
- `no_write=true`
- `affects_score=false`

Forbidden response semantics:

- `final_score`
- `score_result`
- `write_result`
- `persist`
- `export`
- `apply`
- `rescore`
- qingtian result write
- evidence trace write
- scoring basis write
- storage write
- official DOCX / JSON / Markdown export result

Large raw model outputs should not be returned or stored by default. If a preview excerpt is returned, it must remain bounded and advisory.

## 12 No-Write Boundary

Future real transport must not write:

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

Future implementation tests and smoke checks must confirm:

```bash
git status --short
git diff --name-only
```

Only explicitly authorized source or test files may change during implementation. Runtime smoke must not create repository artifacts.

## 13 No-Scoring-Chain Boundary

Future real transport must not call:

- `score_text()`
- `score_text_v2`
- `/rescore`
- qingtian-results
- `evidence_trace/latest`
- `scoring_basis/latest`
- ground truth paths
- calibration write paths
- production scoring write-back

Real transport output must remain advisory.

It must not affect formal scoring results, ranking, reports, exports, or persisted evaluation state.

## 14 No-UI And No-Export Boundary

Future real transport must not:

- add UI controls
- modify frontend behavior
- start a browser
- trigger copy/export UI flows
- trigger DOCX official export
- trigger JSON official export
- trigger Markdown official export
- create official report bundles
- write analysis bundles

Any future UI exposure requires a separate design, test, and authorization phase after real transport is proven safe.

## 15 Deterministic Tests Requirements

Future tests must be deterministic and must not call real Ollama.

The future test matrix must cover at least:

1. real transport flag absent: does not call real Ollama
2. real transport flag `false` / `0` / `no` / `off`: does not call real Ollama
3. endpoint flag disabled: does not check adapter or transport
4. adapter flag disabled: does not check real transport
5. transport enabled but no model name: returns stable failure or uses fake tags
6. transport enabled but Ollama unreachable: returns `ollama_unreachable`
7. transport enabled but model unavailable: returns `model_unavailable`
8. transport enabled but timeout: returns `timeout`
9. transport enabled but invalid response: returns `invalid_response`
10. transport enabled with fake success: returns `ok`
11. every result includes `preview_only=true`
12. every result includes `no_write=true`
13. every result includes `affects_score=false`
14. every result does not call `score_text()` or `/rescore`
15. every result does not enter qingtian-results / `evidence_trace` / `scoring_basis`
16. every result does not write `data/`, `output/`, or storage
17. every result does not trigger export chain
18. every result does not connect UI
19. tests use fake client / monkeypatch only
20. tests do not call real Ollama
21. tests do not start FastAPI service
22. tests do not access external networks
23. existing mock API bridge tests continue to pass
24. existing adapter independent tests continue to pass

Tests may monkeypatch a fake local tags response and a fake generate response. They must not require a running `ollama serve`.

## 16 Future Real Transport Smoke Requirements

Future real transport smoke must be a separate authorized step.

Required smoke boundaries:

- 2nd window explicitly runs `ollama serve`
- FastAPI listens only on `127.0.0.1`
- no service listens on `0.0.0.0`
- requests use loopback only
- no external network access
- no model download
- no model pull
- no `ollama pull`
- no dependency installation
- no pytest unless explicitly authorized
- no UI
- no scoring chain
- no export chain
- no storage write
- no `data/` write
- no `output/` write

Future smoke must report:

- selected model source
- selected model name
- endpoint flags
- transport flag
- request endpoint
- request body summary
- response summary
- whether real Ollama was called
- whether timeout or failure occurred
- whether FastAPI stopped
- whether 2nd window remains running
- final `git status --short`

## 17 Rollback Boundary

If future implementation or smoke fails:

- stop FastAPI first
- record process ID and stop method
- record 2nd window `ollama serve` state if used
- do not modify business code as an ad hoc repair
- do not modify adapter code outside authorized files
- do not write `data/`
- do not write `output/`
- do not clean untracked files
- do not execute `git clean`
- do not push main
- report unexpected files if any appear
- wait for ChatGPT review before recovery actions

Stable rollback anchor before future real transport implementation:

```text
v0.1.57-local-llm-ollama-real-model-smoke-stage-review
```

## 18 Future Implementation Acceptance Criteria

Before any code implementation, the next step must explicitly define:

- allowed files to modify
- whether `app/engine/local_llm_ollama_preview_adapter.py` may be modified
- whether `app/main.py` may be modified
- whether `tests/test_local_llm_preview_mock_api_bridge.py` may be modified
- whether new tests may be added
- real transport feature flag name
- timeout default value
- model name source
- failure response schema
- whether code implementation may call real Ollama
- whether code implementation may start service
- whether tests may run
- confirmation that tests must not call real Ollama
- confirmation that code implementation stage must not run `ollama serve`
- confirmation that real smoke stage requires separate 2nd window authorization
- no-write boundary
- no-scoring-chain boundary
- no-UI boundary
- no-export boundary
- no-main-push boundary
- final report format
- explicit stop after completion for ChatGPT review

Without those conditions, real transport implementation must not start.

## 19 Step 174Z Closure Statement

Step 174Z is complete only when this docs-only design document is added, committed, tagged, and pushed on `local-llm-integration-clean`.

Step 174Z does not:

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
