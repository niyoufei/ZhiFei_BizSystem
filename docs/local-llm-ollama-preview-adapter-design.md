# Local LLM Ollama Preview Adapter Design

## 1 Purpose

This document defines the Step 174N pre-implementation design for a future local LLM Ollama preview adapter.

The current stage is docs-only. It does not implement adapter code, does not start a service, does not run pytest, does not run Ollama, does not call external networks, and does not connect a real model.

This document is a boundary design only. It must not be interpreted as permission to immediately call Ollama or to implement a real model pathway.

## 2 Baseline Inherited From Step 174M

- worktree: `/Users/youfeini/Desktop/ZhiFei_BizSystem-local-llm-clean`
- branch: `local-llm-integration-clean`
- baseline before Step 174N: `a74619c5409084113ccf11bb365b726b8ff3c663`
- baseline tag: `v0.1.45-local-llm-preview-mock-api-bridge-service-smoke-report`
- Step 174M verified endpoint: `POST /local-llm/preview-mock`
- Step 174M verified feature flag: `LOCAL_LLM_PREVIEW_MOCK_API_ENABLED`

Step 174M verified the mock API bridge through local loopback service smoke only. It did not run Ollama, did not call OpenAI / Spark / Gemini, did not access external networks, did not write `data/`, did not write `output/`, did not write storage, did not connect UI, and did not enter scoring or export chains.

## 3 Current Mock API Bridge Boundary

The current endpoint remains:

```text
POST /local-llm/preview-mock
```

The current endpoint is default-off and, when enabled, only calls pure functions from:

```text
app.engine.local_llm_preview_mock
```

The current endpoint returns preview/mock/no-write responses and must remain mock-only until a separate stage explicitly authorizes a different integration.

`POST /local-llm/preview-mock` must not be connected to a future Ollama preview adapter in an unauthorized stage. The current bridge must continue to use the mock-only helper only.

## 4 Ollama Preview Adapter Objective

A future Ollama preview adapter may provide an isolated local model transport layer for preview-only advisory output.

The adapter objective is limited to:

- Building a controlled request to a local Ollama-compatible endpoint.
- Applying a strict timeout.
- Normalizing success and failure responses.
- Returning preview-only advisory output.
- Returning no-write output.
- Returning `affects_score=false`.
- Isolating transport failures from business workflows.
- Keeping deterministic mock fallback boundaries available.

The adapter must never become a scoring engine, storage writer, evidence writer, scoring-basis writer, export trigger, UI feature, or `/rescore` branch.

## 5 Non-Goals

Step 174N does not authorize:

- Writing adapter code.
- Adding `app/engine` files.
- Modifying `app/main.py`.
- Modifying `app/storage.py`.
- Modifying `app/engine/local_llm_preview_mock.py`.
- Adding or modifying tests.
- Running pytest.
- Starting FastAPI service.
- Running Ollama.
- Calling Ollama.
- Calling OpenAI / Spark / Gemini.
- Calling external networks.
- Installing dependencies.
- Connecting UI.
- Connecting production scoring.
- Connecting official export chains.
- Writing `data/`.
- Writing `output/`.
- Pushing `main`.

## 6 Default-Off Contract

A future Ollama preview adapter must be disabled by default.

The future real Ollama call must require an independent feature flag, separate from `LOCAL_LLM_PREVIEW_MOCK_API_ENABLED`. A future flag may be named:

```text
LOCAL_LLM_OLLAMA_PREVIEW_ADAPTER_ENABLED
```

Absent, empty, or false-like values must keep the adapter disabled.

When disabled:

- The adapter must not call Ollama.
- The adapter must not build a network request.
- The adapter must not access storage.
- The adapter must not read or write `data/`.
- The adapter must not read or write `output/`.
- The adapter must return a stable disabled structure.

The adapter must not silently call a real model in any default configuration.

## 7 Preview-Only Contract

A future Ollama preview adapter must remain preview-only.

Successful adapter output must include:

- `preview_only=true`
- `no_write=true`
- `affects_score=false`
- a source that clearly identifies the preview adapter
- advisory content that is not an official score

The adapter must not directly rewrite scoring results. It must not participate in the production scoring main flow. It must not produce official evidence, official scoring basis, official qingtian results, or official export artifacts.

## 8 No-Write Contract

A future Ollama preview adapter must be no-write.

It must not write:

- `app/storage.py` result paths.
- `data/`.
- `output/`.
- qingtian-results.
- evidence trace.
- scoring basis.
- DOCX export output.
- JSON export output.
- Markdown export output.
- runtime report artifacts.

It must not modify or persist official evaluation results.

## 9 No-Scoring-Chain Contract

A future Ollama preview adapter must stay isolated from scoring and rescore flows.

It must not call:

- `score_text()`
- `score_text_v2()`
- `/rescore`
- `rescore_project_submissions`
- scoring main-chain handlers
- evidence trace writers
- scoring basis writers
- qingtian-results writers

It must not be used as a branch inside `/rescore`. It must not read from or write to `qingtian-results`, `evidence_trace/latest`, or `scoring_basis/latest`.

## 10 Transport Boundary

A future adapter may only target a local Ollama endpoint after a separate implementation stage explicitly authorizes real local model calls.

The transport boundary must require:

- loopback-only base URL, such as `http://127.0.0.1:11434`
- no non-loopback host by default
- no external network access
- no OpenAI / Spark / Gemini transport
- no subprocess model launch
- no automatic `ollama serve`
- explicit operator control over whether a second window runs `ollama serve`

The adapter must not start Ollama itself. If a future real Ollama stage is authorized, the instruction must explicitly state whether a second window is used for `ollama serve`.

## 11 Timeout And Failure Boundary

A future Ollama preview adapter must define strict timeout behavior.

At minimum it must distinguish:

- `disabled`
- `transport_failure`
- `model_unavailable`
- `timeout`
- `invalid_response`
- `success`

For all failure classes, the adapter must return stable failure structures with:

- `preview_only=true`
- `no_write=true`
- `affects_score=false`
- `fallback_used=true` when fallback is used
- no official score fields
- no storage-write fields

Timeouts must not cascade into scoring, storage, export, or UI chains.

## 12 Response Normalization Design

A future adapter should normalize raw Ollama output into a small preview response shape.

The normalized success shape should include:

- `status=ok`
- `source=ollama_preview_adapter`
- `preview_only=true`
- `no_write=true`
- `affects_score=false`
- `model`
- `advisory`
- `raw_response_included=false` by default

The normalized failure shape should include:

- `status=unavailable` or `status=error`
- `source=ollama_preview_adapter`
- `preview_only=true`
- `no_write=true`
- `affects_score=false`
- `failure_type`
- `message`
- optional deterministic fallback block

The response must not include:

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

## 13 Fallback Design

A future adapter must support deterministic fallback or mock fallback boundaries.

Fallback requirements:

- Model unavailable returns a stable fallback response.
- Timeout returns a stable fallback response.
- Invalid model response returns a stable fallback response.
- Transport failure returns a stable fallback response.
- Mock fallback for the same input must be deterministic.
- Fallback must remain preview-only.
- Fallback must remain no-write.
- Fallback must not call scoring.
- Fallback must not write storage.
- Fallback must not trigger exports.

The existing `app.engine.local_llm_preview_mock` helper may be used as a design reference for deterministic fallback, but it must not be modified in Step 174N.

## 14 Forbidden Integration Paths

A future Ollama preview adapter must not integrate with:

- `POST /local-llm/preview-mock` unless a later stage explicitly authorizes that connection.
- `score_text()`.
- `score_text_v2()`.
- `/rescore`.
- `qingtian-results`.
- `evidence_trace/latest`.
- `scoring_basis/latest`.
- `app/storage.py` writes.
- `data/`.
- `output/`.
- DOCX official export.
- JSON official export.
- Markdown official export.
- UI.
- `ops_agents`.
- release guard runtime behavior.
- smoke guard runtime official gates.
- OpenAI.
- Spark.
- Gemini.

The current `POST /local-llm/preview-mock` endpoint still only uses the mock-only helper. A future adapter must not be attached to that endpoint without a separate boundary, guard, and test design.

## 15 Future Guard Requirements

Before any future adapter implementation, Step 174O or another explicitly authorized stage must first design guard requirements.

Future guard requirements must cover:

- Allowed files.
- Forbidden files.
- No `app/main.py` change unless authorized.
- No `app/storage.py` change.
- No modification to `app.engine.local_llm_preview_mock`.
- No scoring-engine modification.
- No UI modification.
- No export-chain modification.
- No `data/` or `output/` writes.
- No OpenAI / Spark / Gemini call path.
- No `score_text()` or `/rescore` call path.
- No qingtian-results / evidence trace / scoring basis access.
- No real Ollama call unless the stage explicitly authorizes it.
- Required timeout and failure classes.
- Required stable response fields.
- Required no-write proof.

Step 174O, if authorized, must only design adapter guard and test boundaries. It must not directly implement the adapter.

## 16 Future Deterministic Test Requirements

Future tests must be designed before implementation and must not be added in Step 174N.

The future test direction must cover at least:

1. Adapter is disabled by default.
2. Disabled adapter does not call Ollama.
3. Disabled adapter does not write `data/`, `output/`, or storage.
4. Enabled adapter with Ollama unavailable returns a stable failure.
5. Enabled adapter with timeout returns a stable failure.
6. Enabled adapter with invalid model response returns a stable failure.
7. Enabled adapter success still returns `preview_only=true`.
8. Enabled adapter success still returns `no_write=true`.
9. Enabled adapter success still returns `affects_score=false`.
10. Enabled adapter success does not call `score_text` or `/rescore`.
11. Enabled adapter success does not enter qingtian-results, evidence trace, or scoring basis.
12. Enabled adapter success does not trigger export chains.
13. Mock fallback output is deterministic.
14. Adapter does not modify `app/storage.py`.
15. Adapter does not modify the existing mock helper.

Additional negative tests should patch forbidden call paths and fail loudly if scoring, storage, export, UI, or non-Ollama real model paths are introduced.

## 17 Future Step 174O准入条件

Step 174O must be separately authorized.

Before Step 174O starts, the instruction must declare:

- Whether the step is docs-only.
- Whether code implementation is forbidden.
- Whether tests may be added.
- Whether pytest may run.
- Whether service startup is forbidden.
- Whether Ollama remains forbidden.
- Whether a second window is allowed.
- Which files may be read.
- Which files may be changed.
- Which feature flags are in scope.
- Which rollback anchor applies.

The safe next step is adapter guard/test design only. Step 174O must not directly implement an Ollama adapter and must not run Ollama.

## 18 Step 174N Closure Statement

Step 174N is a docs-only Ollama preview adapter pre-design step.

It records the required isolation boundaries for a future real local model call layer. It does not implement adapter code, start services, run pytest, run Ollama, call external networks, modify tests, modify `app/main.py`, modify `app/storage.py`, modify `app.engine.local_llm_preview_mock`, write `data/`, write `output/`, connect UI, connect scoring, or trigger official exports.

The current `POST /local-llm/preview-mock` endpoint remains mock-only. Any future Ollama adapter, real Ollama call, or endpoint connection requires separate authorization, guard design, deterministic test design, and a fresh boundary review.
