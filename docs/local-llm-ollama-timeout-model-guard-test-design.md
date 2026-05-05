# Local LLM Ollama Timeout and Model Selection Guard and Test Design

## 1 Purpose

This document defines the Step 174AG guard task specification and deterministic test design for future local LLM Ollama timeout, `num_predict`, and model-selection optimization.

The current stage is docs-only. It designs guard and deterministic test boundaries only. It does not implement timeout optimization, change model selection, add tests, run pytest, start FastAPI, run Ollama, run `ollama serve`, access `127.0.0.1:11434`, call external networks, download models, or pull models.

This document must not be interpreted as permission to immediately adjust timeout, change `num_predict`, switch models, run real runtime smoke, connect UI, connect export, or connect the production scoring chain.

## 2 Baseline Inherited From Step 174AF

Step 174AF archived the timeout and model-selection pre-implementation design in:

```text
docs/local-llm-ollama-real-transport-timeout-model-design.md
```

The inherited baseline is:

- Step 174AD proved the real transport branch reached local Ollama through `127.0.0.1:11434`.
- Step 174AD returned a stable `timeout` failure.
- The timeout was caused by the current `DEFAULT_TIMEOUT_SECONDS=5.0` boundary against the selected 80B model response time.
- The selected model was `qwen3-next:80b-a3b-instruct-q8_0`.
- The timeout failure is not a production scoring failure.
- The timeout failure does not prove Ollama is unavailable.
- The endpoint response kept `preview_only=true`, `no_write=true`, and `affects_score=false`.
- Future timeout and model-selection work must remain default-off, preview-only, no-write, and isolated from scoring, UI, export, storage, `data/`, and `output/`.

## 3 Guard Objective

The future guard must prevent timeout and model-selection optimization from crossing the preview-only boundary.

The guard objective is to ensure any future implementation:

- stays default-off
- stays preview-only
- stays no-write
- keeps `affects_score=false`
- uses fake-only deterministic tests during code stages
- does not run real Ollama during tests
- does not download or pull models
- does not touch scoring, UI, export, storage, `data/`, or `output/`

The guard must also verify that runtime smoke remains a separately authorized stage where a 2nd window may be used only for `ollama serve`.

## 4 Guard Forbidden File Scope

Before explicit implementation authorization, the guard must reject changes to:

- `app/main.py`
- `app/engine/local_llm_ollama_preview_adapter.py`
- `app/storage.py`
- `app/engine/local_llm_preview_mock.py`
- `tests/test_local_llm_ollama_preview_adapter.py`
- `tests/test_local_llm_preview_mock_api_bridge.py`
- `tests/test_local_llm_preview_mock.py`
- release guard files
- smoke guard files
- UI files
- DOCX / JSON / Markdown official export-chain files
- `ops_agents` files
- `data/`
- `output/`

The guard must also reject new external dependencies and changes to:

- `requirements`
- `pyproject`
- lock files

Release guard or smoke guard changes are not allowed unless a separate design and explicit authorization opens that scope.

## 5 Guard Forbidden Runtime Scope

The future guard must reject runtime behavior that:

- starts FastAPI during code or test stages
- runs Ollama during code or test stages
- runs `ollama serve` during code or test stages
- starts or listens on `0.0.0.0`
- executes `ollama pull`
- downloads models
- pulls models
- installs dependencies
- triggers browser or UI flows
- triggers DOCX / JSON / Markdown official export
- writes storage, `data/`, or `output/`

Runtime smoke may only be allowed in a later separately authorized stage.

## 6 Guard Forbidden Network And Model Scope

The guard must reject:

- external network URLs
- cloud model endpoints
- OpenAI calls
- Spark calls
- Gemini calls
- non-loopback model calls
- any URL other than the already constrained local Ollama target when explicitly authorized
- model downloads
- model pulls
- `ollama pull`
- automatic model installation
- hardcoded production scoring model selection

When future runtime smoke is authorized, Ollama access must remain limited to:

```text
http://127.0.0.1:11434
```

The future guard must also reject `0.0.0.0` listeners.

## 7 Feature Flag Preservation

Future timeout and model-selection optimization must preserve the current feature flag hierarchy:

1. `LOCAL_LLM_PREVIEW_MOCK_API_ENABLED` is the endpoint-level gate.
2. If the endpoint flag is disabled, the endpoint must return disabled directly.
3. If the endpoint flag is disabled, adapter and real transport state must not be checked.
4. `LOCAL_LLM_OLLAMA_PREVIEW_ADAPTER_ENABLED` is the adapter branch gate.
5. If the adapter flag is disabled, the endpoint must stay on the mock-only helper path.
6. `LOCAL_LLM_OLLAMA_REAL_TRANSPORT_ENABLED` is the real transport gate.
7. If the real transport flag is disabled, no real Ollama call may be constructed.
8. Timeout and model-selection parameters must only matter after all required preview gates allow real transport.

Any new timeout or generation-limit parameter must not become an implicit real transport enablement mechanism.

## 8 Timeout Environment Variable Contract

A future implementation may introduce:

```text
LOCAL_LLM_OLLAMA_TIMEOUT_SECONDS
```

The guard and tests must require:

- a safe default timeout when the variable is absent
- a safe default timeout when the variable is an empty string
- a safe default timeout when the variable is non-numeric
- a safe default timeout when the variable is negative
- a safe default timeout when the variable is `0`
- a bounded maximum timeout
- timeout values above the maximum to be clipped or rejected according to the implementation-stage contract
- timeout to affect only the preview real transport path
- timeout not to affect `score_text()`
- timeout not to affect `/rescore`
- timeout not to write storage, `data/`, or `output/`
- timeout not to trigger UI or export chains

The maximum timeout must be explicit before implementation. Step 174AF suggested no more than 60 seconds unless a later implementation stage confirms otherwise.

## 9 num_predict Environment Variable Contract

A future implementation may introduce:

```text
LOCAL_LLM_OLLAMA_NUM_PREDICT
```

The guard and tests must require:

- a safe default `num_predict` when the variable is absent
- a safe default `num_predict` when the variable is an empty string
- a safe default `num_predict` when the variable is non-numeric
- a safe default `num_predict` when the variable is negative
- a safe default `num_predict` when the variable is `0`
- a bounded maximum `num_predict`
- values above the maximum to be clipped or rejected according to the implementation-stage contract
- smoke scenarios to use small output limits where explicitly configured
- no long model output recording
- no formal scoring text generation
- no DOCX / JSON / Markdown official export generation

The current implementation reference is a maximum of `128`, but the exact future default and maximum must be re-confirmed before implementation.

## 10 Model Selection Contract

Future model selection must preserve:

- `LOCAL_LLM_OLLAMA_MODEL` has highest priority.
- If `LOCAL_LLM_OLLAMA_MODEL` is absent, local `/api/tags` may be read only when the real transport path is otherwise allowed.
- `/api/tags` access must be read-only.
- Only installed local models may be selected.
- No model may be downloaded.
- No model may be pulled.
- `ollama pull` must not be executed.
- External networks must not be called.
- A missing specified model must return `model_unavailable` or follow an explicitly documented fallback.
- If fallback to the first local model is allowed, the reason must be recorded.
- If lightweight runtime smoke is allowed, the selected model must come from existing local tags such as already installed `qwen3:0.6b`, `qwen3:8b`, or `qwen3:14b`.
- A lightweight smoke model must not be treated as a production scoring model.

Model selection must not write score basis, evidence trace, QingTian results, export results, storage, `data/`, or `output/`.

## 11 Deterministic Tests Matrix

Future tests must be fake-only and must not create a new real Ollama dependency.

The matrix must include at least:

1. `LOCAL_LLM_OLLAMA_TIMEOUT_SECONDS` absent uses a safe default.
2. timeout empty string falls back to a safe default.
3. timeout non-numeric value falls back to a safe default.
4. timeout negative value falls back to a safe default.
5. timeout `0` falls back to a safe default.
6. timeout above maximum is clipped or rejected according to the agreed rule.
7. `LOCAL_LLM_OLLAMA_NUM_PREDICT` absent uses a safe default.
8. `num_predict` empty string falls back to a safe default.
9. `num_predict` non-numeric value falls back to a safe default.
10. `num_predict` negative value falls back to a safe default.
11. `num_predict` `0` falls back to a safe default.
12. `num_predict` above maximum is clipped or rejected according to the agreed rule.
13. `LOCAL_LLM_OLLAMA_MODEL` has priority when specified.
14. a missing specified model returns `model_unavailable` or follows the explicitly authorized fallback.
15. no specified model plus fake tags with models selects a local model.
16. no specified model plus empty fake tags returns `model_unavailable`.
17. timeout and `num_predict` configuration do not change `preview_only=true`.
18. timeout and `num_predict` configuration do not change `no_write=true`.
19. timeout and `num_predict` configuration do not change `affects_score=false`.
20. model selection does not call `score_text()` or `/rescore`.
21. model selection does not enter qingtian-results, `evidence_trace/latest`, or `scoring_basis/latest`.
22. model selection does not write `data/`, `output/`, or storage.
23. model selection does not trigger export chains.
24. model selection does not connect UI.
25. tests do not call real Ollama.
26. tests do not start FastAPI service.
27. tests do not access external networks.
28. tests do not download or pull models.
29. existing mock API bridge tests continue to pass.
30. existing adapter independent tests continue to pass.

All transport behavior in tests must use fake clients, fake transports, or monkeypatching. Tests must not access `127.0.0.1:11434`.

## 12 Required Negative Tests

Future implementation tests must include negative cases proving:

- endpoint flag disabled does not check adapter or transport.
- adapter flag disabled does not check real transport.
- real transport flag disabled does not construct real transport.
- invalid timeout values do not crash and do not escape the safe default or maximum rule.
- invalid `num_predict` values do not crash and do not escape the safe default or maximum rule.
- missing model returns a stable failure or explicitly authorized fallback.
- fake transport timeout returns stable `timeout`.
- fake unavailable model returns stable `model_unavailable`.
- fake unreachable Ollama returns stable `ollama_unreachable`.
- fake invalid response returns stable `invalid_response`.
- forbidden scoring/storage/export metadata is rejected or remains inert.

Negative tests must prove no fallback path reaches scoring, storage, UI, export, external networks, or real Ollama.

## 13 Required No-Write Verification

Future guard and tests must verify that timeout and model-selection logic does not write:

- `app/storage.py`
- `data/`
- `output/`
- qingtian-results
- `evidence_trace/latest`
- `scoring_basis/latest`
- production score reports
- official export artifacts

If a future runtime smoke creates unexpected repository changes, execution must stop and report the changed paths. The executor must not run `git clean` or remove untracked files without separate authorization.

## 14 Required No-Scoring-Chain Verification

Future guard and tests must verify no connection to:

- `score_text()`
- `score_text_v2`
- `/rescore`
- qingtian-results
- `evidence_trace/latest`
- `scoring_basis/latest`
- production score persistence
- production evaluation persistence

Timeout or `model_unavailable` must remain preview failure states only. They must not be interpreted as production scoring outcomes.

## 15 Required No-UI And No-Export Verification

Future guard and tests must verify no connection to:

- UI
- browser workflows
- DOCX official export
- JSON official export
- Markdown official export
- export bundles

The future optimized preview path may return bounded advisory response fields only. It must not generate official scoring text, official evidence text, or official export content.

## 16 Future Implementation Acceptance Criteria

Before code implementation starts, the next stage must explicitly define:

- Step 174AG design is archived.
- allowed file modification scope
- whether `app/engine/local_llm_ollama_preview_adapter.py` may be modified
- whether `app/main.py` may be modified
- whether `tests/test_local_llm_ollama_preview_adapter.py` may be modified
- whether `tests/test_local_llm_preview_mock_api_bridge.py` may be modified
- timeout environment variable name
- timeout default value
- timeout maximum value
- `num_predict` environment variable name
- `num_predict` default value
- `num_predict` maximum value
- model-selection strategy
- behavior when `LOCAL_LLM_OLLAMA_MODEL` is missing locally
- whether first local `/api/tags` fallback is allowed
- fake-only tests
- no `ollama serve` in code implementation
- 2nd window only for separately authorized runtime smoke
- no model download
- no model pull
- no `ollama pull`
- no writes to `data/`, `output/`, or storage
- no scoring-chain integration
- no UI integration
- no export-chain integration
- no push to `main`
- mandatory stop for ChatGPT review

## 17 Future Runtime Smoke Acceptance Criteria

Future runtime smoke after a separately authorized implementation must require:

- clean worktree before service start
- FastAPI listening only on `127.0.0.1`
- Ollama access only through `127.0.0.1:11434`
- 2nd window used only for `ollama serve`
- no git, pytest, code modification, commit, tag, or push in the 2nd window
- no model download
- no model pull
- no `ollama pull`
- no external network calls
- explicit feature flag values recorded
- explicit model selected from local inventory
- explicit timeout and `num_predict` values recorded
- response still has `preview_only=true`
- response still has `no_write=true`
- response still has `affects_score=false`
- FastAPI stopped after request
- final `git status --short` checked

Any runtime failure must be reported as a preview smoke result and must not trigger direct code rescue in scoring, UI, storage, or export paths.

## 18 Step 174AG Closure Statement

Step 174AG is a docs-only guard and deterministic test design stage.

It records that future timeout, `num_predict`, and model-selection optimization must remain default-off, preview-only, no-write, `affects_score=false`, fake-only in tests, and isolated from scoring, UI, export, storage, `data/`, and `output/`.

This document does not authorize code changes, test changes, pytest, service startup, Ollama execution, `ollama serve`, localhost Ollama access, model downloads, model pulls, timeout adjustment, model switching, runtime smoke, UI integration, export integration, or production scoring integration.
