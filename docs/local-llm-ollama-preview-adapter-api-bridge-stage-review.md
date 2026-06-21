# Local LLM Ollama Preview Adapter API Bridge Stage Review

## 1 Purpose

This document reviews the Step 174S no-real-model integration of the local LLM Ollama preview adapter into the existing local LLM preview/mock API bridge.

Step 174S connected the already implemented adapter to `POST /local-llm/preview-mock` only as a controlled no-real-model branch. It did not authorize real Ollama, service startup, UI integration, production scoring integration, storage writes, `data/` writes, `output/` writes, or official export-chain behavior.

This document is a docs-only stage review. It does not modify implementation code, tests, configuration, runtime behavior, or guard files.

## 2 Baseline Before Step 174S

- worktree: `/Users/youfeini/Desktop/ZhiFei_BizSystem-local-llm-clean`
- branch: `local-llm-integration-clean`
- baseline before Step 174S: `457b656ce0032c9502e8adf055b7f40032f97136`
- baseline tag: `v0.1.50-local-llm-ollama-preview-adapter-api-bridge-design`
- Step 174S result commit: `bc556ffa4bcb2dd5901944adbfd48c4c8fdef805`
- Step 174S result tag: `v0.1.51-local-llm-ollama-preview-adapter-api-bridge`

Step 174S was authorized to modify only:

- `app/main.py`
- `tests/test_local_llm_preview_mock_api_bridge.py`

No new files were added in Step 174S.

## 3 Files Changed In Step 174S

Step 174S changed only:

- `app/main.py`
- `tests/test_local_llm_preview_mock_api_bridge.py`

Step 174S did not modify:

- `app/storage.py`
- `app/engine/local_llm_preview_mock.py`
- `app/engine/local_llm_ollama_preview_adapter.py`
- `tests/test_local_llm_ollama_preview_adapter.py`
- `tests/test_local_llm_preview_mock.py`
- release guard / smoke guard files
- `data/`
- `output/`
- UI files
- DOCX / JSON / Markdown official export-chain files
- `ops_agents` files

## 4 Endpoint And Feature Flag Summary

Endpoint path:

```text
POST /local-llm/preview-mock
```

Endpoint feature flag:

```text
LOCAL_LLM_PREVIEW_MOCK_API_ENABLED
```

Adapter feature flag:

```text
LOCAL_LLM_OLLAMA_PREVIEW_ADAPTER_ENABLED
```

The endpoint feature flag remains the outer gate. The adapter feature flag is checked only after the endpoint feature flag is enabled.

## 5 Feature Flag Hierarchy Review

Step 174S implemented this hierarchy:

1. If `LOCAL_LLM_PREVIEW_MOCK_API_ENABLED` is absent, empty, `false`, `0`, `no`, or `off`, the endpoint directly returns disabled.
2. In endpoint disabled state, the endpoint does not check or call the adapter.
3. In endpoint disabled state, the endpoint does not call the mock helper.
4. If `LOCAL_LLM_PREVIEW_MOCK_API_ENABLED` is enabled and `LOCAL_LLM_OLLAMA_PREVIEW_ADAPTER_ENABLED` is absent, empty, `false`, `0`, `no`, or `off`, the endpoint keeps the existing mock-only helper path.
5. If both feature flags are enabled with true-like values, the endpoint enters the adapter preview branch.

The adapter branch still remains preview-only, no-write, and `affects_score=false`.

## 6 Endpoint Disabled Behavior Review

Endpoint disabled behavior remains unchanged.

When the endpoint flag is disabled, the response is the stable disabled payload with:

- `status=disabled`
- `enabled=false`
- `disabled=true`
- `reason=feature_flag_disabled`
- `feature_flag=LOCAL_LLM_PREVIEW_MOCK_API_ENABLED`
- `preview_only=true`
- `mock_only=true`
- `no_write=true`
- `affects_score=false`

Disabled behavior does not check `LOCAL_LLM_OLLAMA_PREVIEW_ADAPTER_ENABLED`, does not call the adapter, does not call the mock helper, does not write storage, does not write `data/`, does not write `output/`, and does not affect formal scoring results.

## 7 Mock-Only Compatibility Review

Mock-only compatibility was preserved.

When the endpoint flag is enabled and the adapter flag is absent or disabled, the endpoint still uses:

- `app.engine.local_llm_preview_mock.build_local_llm_preview_input`
- `app.engine.local_llm_preview_mock.build_local_llm_mock_response`

The default enabled behavior does not become a real model call. Existing mock API bridge assertions continue to pass. Existing helper tests continue to pass. Existing adapter independent tests continue to pass.

The mock-only response continues to preserve:

- `mode=mock_only`
- `mock_only=true`
- `preview_only=true`
- `no_write=true`
- `affects_score=false`
- `source=local_llm_preview_mock`

## 8 Adapter-Enabled No-Real-Model Behavior Review

When both flags are enabled, the endpoint enters an adapter preview branch and calls:

```text
app.engine.local_llm_ollama_preview_adapter.run_ollama_preview
```

The adapter branch is no-real-model.

It does not pass a real client or real transport into `run_ollama_preview`. Without a fake client supplied by tests, the adapter safely returns a stable `model_unavailable` failure instead of calling a model.

The adapter branch does not:

- construct a real Ollama URL
- access a localhost Ollama port
- call Ollama
- call OpenAI
- call Spark
- call Gemini
- call external networks
- start `ollama serve`
- start a service
- write `app/storage.py`
- write `data/`
- write `output/`

Adapter branch responses must keep:

- `preview_only=true`
- `no_write=true`
- `affects_score=false`

Adapter branch responses must not affect formal scoring results.

## 9 Deterministic API Test Coverage

Step 174S extended deterministic tests in:

- `tests/test_local_llm_preview_mock_api_bridge.py`

The recorded test command was:

```bash
python3 -m pytest tests/test_local_llm_preview_mock_api_bridge.py tests/test_local_llm_ollama_preview_adapter.py tests/test_local_llm_preview_mock.py -q
```

The recorded test result was:

```text
65 passed in 0.87s
```

The deterministic API tests cover:

- endpoint flag absent returns disabled
- endpoint flag empty / `false` / `0` / `no` / `off` returns disabled
- endpoint disabled does not call the mock helper
- endpoint disabled does not check or call the adapter
- endpoint disabled does not write data/output/storage
- endpoint enabled and adapter flag absent keeps mock-only helper path
- endpoint enabled and adapter flag empty / `false` / `0` / `no` / `off` keeps mock-only helper path
- endpoint enabled and adapter flag `true` enters the adapter preview branch
- adapter branch fake success returns `preview_only=true`
- adapter branch fake success returns `no_write=true`
- adapter branch fake success returns `affects_score=false`
- adapter branch without fake client returns stable no-real-model `model_unavailable`
- adapter fake `model_unavailable` failure remains stable
- adapter fake `timeout` failure remains stable
- adapter fake `invalid_response` failure remains stable
- adapter enabled success does not call forbidden runtime paths
- adapter enabled failures do not enter scoring chain
- same input and same fake adapter response are deterministic
- existing mock API bridge tests still pass
- existing independent adapter tests still pass
- existing mock helper tests still pass

Tests use monkeypatch and fake adapter responses. They do not require real Ollama, do not start a service, and do not access external networks.

## 10 Explicit Non-Integrations

Step 174S did not integrate:

- real Ollama
- OpenAI
- Spark
- Gemini
- `score_text()`
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
- production scoring main chain
- production export chain
- real model production chain

Step 174S did not run Ollama, did not start a service, and did not call external networks.

## 11 No-Write Boundary Verification

The Step 174S API bridge remains no-write.

The adapter branch does not write:

- `app/storage.py`
- `data/`
- `output/`
- qingtian-results
- evidence trace
- scoring basis
- DOCX official exports
- JSON official exports
- Markdown official exports
- official score reports
- official analysis bundles
- production evaluation write-back artifacts

The deterministic tests patch forbidden runtime paths so the test fails if the endpoint attempts to call storage or official scoring/result writers.

## 12 No-Real-Model Boundary Verification

Step 174S did not run Ollama.

Step 174S did not run `ollama serve`.

Step 174S did not start a FastAPI service.

Step 174S did not call external networks.

The adapter branch does not receive a real client or transport. In non-monkeypatched runtime, adapter enabled returns a stable `model_unavailable` failure because no client is configured.

The adapter branch is therefore a no-real-model integration point, not a real local model integration.

## 13 No-Scoring-Chain Boundary Verification

Step 174S did not call `score_text()`.

Step 174S did not connect `/rescore`.

Step 174S did not enter qingtian-results.

Step 174S did not enter `evidence_trace/latest`.

Step 174S did not enter `scoring_basis/latest`.

Adapter success, adapter failure, timeout, invalid response, disabled endpoint, and mock-only fallback states remain outside the production scoring chain.

## 14 Remaining Risks

- The adapter is connected to the API bridge, but only as a no-real-model branch.
- Adapter enabled state has not made a real Ollama call.
- Current behavior has not been validated through runtime loopback service smoke after startup.
- Real Ollama service availability has not been verified.
- This stage does not mean real local LLM integration is complete.
- This stage cannot be used for production scoring.
- UI is not connected and cannot trigger real model capability from the frontend.
- Official export chains are not connected and cannot generate DOCX / JSON / Markdown official results from this branch.
- qingtian-results, evidence trace, and scoring basis paths are not connected.
- Future service smoke must first have a separate boundary design and must be limited to `127.0.0.1` loopback.
- Future real Ollama smoke must be separately authorized and must explicitly state whether `2号窗口` runs `ollama serve`.
- If future work creates `data/`, `output/`, or storage changes, it must stop and report before cleanup or commit.
- Future debugging must not directly modify storage, scoring main chain, UI, or export chain files.

## 15 Required Next-Stage Guard Before Step 174U

Before Step 174U or any service smoke stage, the instruction must explicitly define:

- whether starting FastAPI service is allowed
- exact host and port, with loopback only
- whether `LOCAL_LLM_PREVIEW_MOCK_API_ENABLED` is enabled
- whether `LOCAL_LLM_OLLAMA_PREVIEW_ADAPTER_ENABLED` is enabled
- whether real Ollama remains forbidden
- whether `2号窗口` remains unused
- exact request payloads
- expected disabled response
- expected mock-only response
- expected adapter no-real-model response
- process shutdown verification
- `git status --short` before and after
- no-write checks for `data/`, `output/`, and storage
- stop conditions for scoring-chain, UI, export-chain, or real-model drift

Step 174U must not directly become a real Ollama stage unless separately authorized with a new boundary.

## 16 Step 174T Closure Statement

Step 174T records the Step 174S implementation as a controlled, no-real-model API bridge integration.

The current endpoint keeps the existing default-off outer gate, preserves mock-only behavior when the adapter flag is disabled, and enters the adapter branch only when both flags are enabled.

The adapter branch does not pass a real client or transport, does not call Ollama, does not access external networks, does not write storage, does not write `data/`, does not write `output/`, does not connect UI, does not trigger official exports, and does not enter scoring, rescore, qingtian-results, evidence trace, or scoring basis chains.

This document does not authorize Step 174U service smoke, real Ollama, UI integration, production scoring integration, storage writes, or export writes.
