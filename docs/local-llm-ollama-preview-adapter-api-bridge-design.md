# Local LLM Ollama Preview Adapter API Bridge Design

## 1 Purpose

This document defines the Step 174R pre-implementation boundary for a possible future integration between the independent Ollama preview adapter and the existing local LLM preview mock API bridge.

The current stage is docs-only. It does not implement code, does not modify `app/main.py`, does not modify `POST /local-llm/preview-mock`, does not run pytest, does not start a service, does not run Ollama, does not call external networks, does not connect UI, does not connect scoring, and does not write `data/`, `output/`, or storage.

This document must not be interpreted as permission to immediately modify `app/main.py` or connect the adapter to any endpoint.

## 2 Baseline Inherited From Step 174Q

- worktree: `/Users/youfeini/Desktop/ZhiFei_BizSystem-local-llm-clean`
- branch: `local-llm-integration-clean`
- baseline tag: `v0.1.49-local-llm-ollama-preview-adapter-stage-review`
- Step 174Q commit: `30e007f905463333df64dcc8a580101dc26c4e26`
- existing endpoint: `POST /local-llm/preview-mock`
- existing endpoint feature flag: `LOCAL_LLM_PREVIEW_MOCK_API_ENABLED`
- future adapter feature flag candidate: `LOCAL_LLM_OLLAMA_PREVIEW_ADAPTER_ENABLED`

Step 174P added the independent adapter in `app/engine/local_llm_ollama_preview_adapter.py` and deterministic tests in `tests/test_local_llm_ollama_preview_adapter.py`.

Step 174Q confirmed the adapter remains independent, default-off, preview-only, no-write, and not connected to `app/main.py` or `POST /local-llm/preview-mock`.

## 3 Current API Bridge Behavior

The current API bridge endpoint is:

```text
POST /local-llm/preview-mock
```

It is controlled by:

```text
LOCAL_LLM_PREVIEW_MOCK_API_ENABLED
```

The endpoint is default-off. When the endpoint flag is absent, empty, `false`, `0`, `no`, or `off`, the endpoint returns disabled and does not call `app.engine.local_llm_preview_mock`.

When the endpoint flag is enabled with `true`, `1`, `yes`, or `on`, the endpoint currently uses only the mock-only helper path:

- `app.engine.local_llm_preview_mock.build_local_llm_preview_input`
- `app.engine.local_llm_preview_mock.build_local_llm_mock_response`

The current endpoint does not call Ollama, OpenAI, Spark, Gemini, `score_text()`, `/rescore`, qingtian-results, evidence trace, scoring basis, storage, `data/`, `output/`, UI, or official export chains.

## 4 Current Adapter Behavior

The independent adapter is implemented in:

```text
app/engine/local_llm_ollama_preview_adapter.py
```

Primary functions:

- `is_ollama_preview_enabled(value: str | None) -> bool`
- `build_disabled_response(...) -> dict`
- `build_failure_response(...) -> dict`
- `normalize_ollama_response(...) -> dict`
- `run_ollama_preview(...) -> dict`

The adapter is default-off, preview-only, no-write, and does not affect formal scoring results.

The adapter supports fake client / transport injection, so deterministic tests can cover success and failure paths without real Ollama.

The adapter is not imported by `app/main.py`, is not connected to `POST /local-llm/preview-mock`, and is not connected to production scoring, storage, UI, or export chains.

## 5 API Bridge Integration Objective

A future integration stage may consider adding an adapter preview branch inside the existing API bridge.

The objective would be limited to:

- preserving the existing endpoint default-off behavior
- preserving existing mock-only behavior when the adapter flag is disabled
- allowing adapter preview only when both endpoint and adapter flags are enabled
- returning preview-only advisory output
- preserving no-write semantics
- preserving `affects_score=false`
- using fake clients or monkeypatching in deterministic tests
- avoiding real Ollama by default

The objective is not to enable production scoring, UI use, official export generation, real model production use, storage writes, or evidence/scoring-basis write-back.

## 6 Non-Goals

Step 174R does not authorize:

- modifying `app/main.py`
- modifying `POST /local-llm/preview-mock`
- importing the adapter into the API bridge
- adding endpoint code
- adding tests
- modifying existing tests
- running pytest
- starting a service
- running Ollama
- calling external networks
- installing dependencies
- connecting UI
- connecting production scoring
- connecting `/rescore`
- connecting qingtian-results
- connecting `evidence_trace/latest`
- connecting `scoring_basis/latest`
- triggering DOCX / JSON / Markdown official exports
- writing `app/storage.py`
- writing `data/`
- writing `output/`
- pushing `main`

## 7 Feature Flag Hierarchy

A future integration must use a strict two-level feature flag hierarchy.

Endpoint feature flag:

```text
LOCAL_LLM_PREVIEW_MOCK_API_ENABLED
```

Adapter feature flag:

```text
LOCAL_LLM_OLLAMA_PREVIEW_ADAPTER_ENABLED
```

Required hierarchy:

- `LOCAL_LLM_PREVIEW_MOCK_API_ENABLED` controls whether `POST /local-llm/preview-mock` is available.
- `LOCAL_LLM_OLLAMA_PREVIEW_ADAPTER_ENABLED` controls whether the endpoint may call the Ollama preview adapter.
- If the endpoint flag is not enabled, the endpoint must directly return disabled.
- If the endpoint flag is not enabled, the endpoint must not check the adapter flag.
- If the endpoint flag is not enabled, the endpoint must not call the adapter.
- If the endpoint flag is enabled but the adapter flag is not enabled, the endpoint must continue to use the existing mock-only helper.
- If the endpoint flag is enabled and the adapter flag is enabled, only then may the endpoint enter an adapter preview branch.
- Even when the adapter flag is enabled, responses must keep `preview_only=true`, `no_write=true`, and `affects_score=false`.
- If either flag is absent, the system must not make a real Ollama call.
- If either flag is disabled, the system must not write `data/`, `output/`, or storage.
- If either flag is disabled, the system must not affect formal scoring results.

`LOCAL_LLM_OLLAMA_PREVIEW_ADAPTER_ENABLED` must not enable UI, production scoring, storage writes, exports, OpenAI, Spark, Gemini, or remote network access.

## 8 Default-Off Behavior Design

The endpoint disabled path has the highest priority.

When `LOCAL_LLM_PREVIEW_MOCK_API_ENABLED` is absent, empty, `false`, `0`, `no`, or `off`, the endpoint must:

- return the existing disabled response
- not call the mock-only helper
- not inspect or call the adapter
- not read adapter configuration
- not call Ollama
- not call client / transport
- not write `data/`, `output/`, or storage
- not affect formal scoring results

When the endpoint flag is enabled but `LOCAL_LLM_OLLAMA_PREVIEW_ADAPTER_ENABLED` is absent, empty, `false`, `0`, `no`, or `off`, the endpoint must:

- keep the current mock-only helper path
- not call the adapter
- not call adapter client / transport
- not call Ollama
- preserve existing mock API response semantics
- preserve existing mock API test assertions

## 9 Mock-Only Compatibility Design

Adapter integration must not change existing mock-only behavior.

The current mock-only path must remain the default enabled behavior unless the adapter flag is explicitly enabled.

Compatibility requirements:

- Existing disabled behavior must remain unchanged.
- Existing enabled mock-only behavior must remain unchanged when the adapter flag is disabled.
- Existing `tests/test_local_llm_preview_mock_api_bridge.py` assertion semantics must remain valid.
- Existing helper tests must remain valid.
- Existing adapter independent tests must remain valid.
- Existing response fields for mock-only mode must not be removed.
- Existing forbidden path guards in tests must remain meaningful.

Adapter integration must not convert the endpoint into a real-model endpoint by default.

## 10 Adapter-Enabled Preview Behavior Design

If a future stage authorizes adapter integration and both flags are enabled, the endpoint may enter an adapter preview branch.

Adapter-enabled responses must still include:

- `preview_only=true`
- `no_write=true`
- `affects_score=false`
- clear adapter status
- clear failure type for failure responses
- no formal scoring result fields
- no write-result fields
- no export-result fields

Adapter-enabled behavior must not:

- call `score_text()`
- call `/rescore`
- enter qingtian-results
- enter `evidence_trace/latest`
- enter `scoring_basis/latest`
- write `app/storage.py`
- write `data/`
- write `output/`
- trigger DOCX / JSON / Markdown official exports
- connect UI
- call OpenAI / Spark / Gemini
- call real Ollama unless a later real-Ollama stage explicitly authorizes it

Future API tests must use fake client / monkeypatch transport only. They must not require real Ollama, service startup, or external network access.

## 11 No-Write Boundary

Future integration must remain no-write.

Neither disabled, mock-only, nor adapter-enabled responses may write:

- `app/storage.py`
- `data/`
- `output/`
- qingtian-results
- evidence trace
- scoring basis
- DOCX official export output
- JSON official export output
- Markdown official export output
- official score reports
- official analysis bundles
- production evaluation write-back artifacts

No future integration may silently create runtime artifacts while testing or handling adapter failures.

## 12 No-Scoring-Chain Boundary

Future integration must remain outside the scoring chain.

It must not call:

- `score_text()`
- `score_text_v2()`
- `/rescore`
- `rescore_project_submissions`
- scoring main-chain handlers
- evidence trace writers
- scoring basis writers
- qingtian-results writers

Adapter success, adapter failure, timeout, invalid response, and disabled states must never fall back into production scoring.

## 13 No-Real-Model-Default Boundary

Future integration must not call a real model by default.

Absent, disabled, or mock-only states must not call Ollama.

Even if the adapter flag is enabled in deterministic API tests, the tests must use a fake client / monkeypatch path. Real Ollama calls require a separate authorization stage and a separate smoke boundary.

Future real Ollama smoke must explicitly state:

- whether `2号窗口` will run `ollama serve`
- which host is allowed
- which port is allowed
- whether the request is limited to `127.0.0.1`
- which model is used
- which timeout is used
- how data/output/storage writes are checked
- how service shutdown is verified

Real Ollama must not be introduced through Step 174R or any undocumented endpoint change.

## 14 Failure Response Design

Future adapter-enabled API failure responses must be stable.

They should distinguish:

- adapter disabled
- model unavailable
- transport failure
- timeout
- invalid response
- invalid request
- ok

Every failure response must preserve:

- `preview_only=true`
- `no_write=true`
- `affects_score=false`

Failure responses must not include:

- `final_score`
- `score_result`
- `write_result`
- `persist`
- `export`
- `apply`
- `rescore`
- `qingtian_results`
- `evidence_trace_write`
- `scoring_basis_write`
- `storage_write`

Failure responses must not trigger scoring, storage, UI, export, or real model fallback without explicit authorization.

## 15 Deterministic API Test Requirements

Future Step 174S or any later adapter API bridge implementation must add deterministic API tests before any service smoke or real model work.

Required future test matrix:

| Case | Expected result |
| --- | --- |
| endpoint flag absent | returns disabled |
| endpoint flag `false` | returns disabled |
| endpoint flag `0` | returns disabled |
| endpoint flag `no` | returns disabled |
| endpoint flag `off` | returns disabled |
| endpoint disabled | does not inspect or call adapter |
| endpoint enabled, adapter flag absent | uses mock-only helper |
| endpoint enabled, adapter flag `false` | uses mock-only helper |
| endpoint enabled, adapter flag `0` | uses mock-only helper |
| endpoint enabled, adapter flag `no` | uses mock-only helper |
| endpoint enabled, adapter flag `off` | uses mock-only helper |
| endpoint enabled, adapter flag `true` | enters adapter preview branch |
| adapter disabled fallback | remains mock-only deterministic |
| fake Ollama unavailable | returns stable failure and does not enter scoring |
| fake timeout | returns stable failure and does not enter scoring |
| fake invalid response | returns stable failure and does not enter scoring |
| fake success | returns `preview_only=true` |
| fake success | returns `no_write=true` |
| fake success | returns `affects_score=false` |
| any adapter result | does not write `data/`, `output/`, or storage |
| any adapter result | does not call `score_text()` or `/rescore` |
| any adapter result | does not enter qingtian-results / evidence trace / scoring basis |
| any adapter result | does not trigger DOCX / JSON / Markdown official exports |
| any adapter result | does not connect UI |
| same input and same fake client output | remains deterministic |
| existing mock API bridge tests | continue to pass |
| existing adapter independent tests | continue to pass |
| all tests | require no real Ollama |
| all tests | start no service |
| all tests | access no external network |

The future test command and allowed file list must be stated before implementation.

## 16 Service Smoke Requirements Before Real Ollama

Before any real Ollama stage, there must be a separate service smoke design or instruction.

Minimum service smoke requirements:

- use a clean worktree
- use local loopback only
- do not listen on `0.0.0.0`
- verify endpoint disabled first
- verify mock-only fallback before adapter branch
- verify adapter branch only with fake or separately authorized local transport
- stop the service process after verification
- confirm `git status --short` remains clean or contains only authorized docs
- stop immediately if `data/`, `output/`, or storage changes
- stop immediately if scoring-chain paths are touched
- stop immediately if qingtian-results, evidence trace, or scoring basis paths are touched

Real Ollama service smoke must be a separate step and must explicitly state whether `2号窗口` runs `ollama serve`.

## 17 Future Step 174S 准入条件

Future Step 174S may proceed only if the instruction explicitly defines:

- that modifying `app/main.py` is allowed
- whether modifying or adding `tests/test_local_llm_preview_mock_api_bridge.py` is allowed
- that modifying `app/storage.py` remains forbidden
- that modifying `app/engine/local_llm_preview_mock.py` remains forbidden
- whether modifying `app/engine/local_llm_ollama_preview_adapter.py` is allowed; default must be forbidden unless separately authorized
- the endpoint flag and adapter flag hierarchy
- endpoint disabled priority as the highest priority
- adapter flag default disabled behavior
- fake client / monkeypatch-only test strategy
- no real Ollama during tests
- no service startup during tests
- no `data/`, `output/`, or storage writes
- no scoring main-chain connection
- no UI connection
- no export-chain trigger
- exact tests to run
- exact changed-file allowlist
- completion must stop for ChatGPT review

Without these constraints, Step 174S must not modify endpoint code.

## 18 Step 174R Closure Statement

Step 174R records only the design boundary for a possible future adapter integration into the existing API bridge.

It confirms that the current `POST /local-llm/preview-mock` endpoint remains mock-only, that the independent adapter remains disconnected from `app/main.py`, and that no code, tests, service runtime, Ollama runtime, UI, scoring, storage, data, output, qingtian-results, evidence trace, scoring basis, or export chain was changed in this step.

This document does not authorize immediate endpoint integration, real Ollama, service smoke, UI integration, production scoring integration, storage writes, or export writes.
