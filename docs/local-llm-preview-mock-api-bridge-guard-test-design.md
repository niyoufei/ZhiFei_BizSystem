# Local LLM Preview Mock API Bridge Guard and Test Design

## 1 Purpose

This document defines the implementation-before guard task spec and deterministic API test design for a future local LLM preview/mock API bridge.

The current Step 174I stage is docs-only. It only designs guard requirements and deterministic API test requirements. It does not implement an API endpoint, does not add tests, and must not be interpreted as permission to modify `app/main.py` or add test files immediately.

## 2 Baseline Inherited From Step 174H

- worktree: `/Users/youfeini/Desktop/ZhiFei_BizSystem-local-llm-clean`
- branch: `local-llm-integration-clean`
- current baseline HEAD: `b23e99155dda302431cf5ab91f422c3cd7024e7f`
- current baseline tag: `v0.1.40-local-llm-preview-mock-api-bridge-design`
- previous API bridge design: `docs/local-llm-preview-mock-api-bridge-design.md`
- existing pure helper: `app.engine.local_llm_preview_mock`

The future API bridge must be default-off. The recommended feature flag name is `LOCAL_LLM_PREVIEW_MOCK_API_ENABLED`.

## 3 Guard Task Objective

The guard task must prevent a future API bridge implementation from crossing preview/mock boundaries.

The guard must prove that a future implementation:

- Remains default-off.
- Calls only pure functions from `app.engine.local_llm_preview_mock` when enabled.
- Returns disabled without helper execution when the feature flag is absent or false.
- Keeps helper output as `preview_only`, `mock_only`, and `no_write`.
- Does not call real model runtimes.
- Does not enter scoring, storage, export, UI, or official evaluation write-back chains.

The guard design must block unauthorized file modification, forbidden runtime calls, forbidden output paths, and real model calls.

## 4 Guard Forbidden File Scope

The guard must enforce the following file-scope constraints:

- This design stage must not modify `main`.
- This design stage must not modify `app/storage.py`.
- Before explicit authorization, implementation must not modify `app/main.py`.
- Before explicit authorization, implementation must not add tests.
- Implementation must not modify `app/engine/local_llm_preview_mock.py` unless separately authorized.
- Implementation must not modify scoring engines such as `app/engine/scorer.py` or `app/engine/v2_scorer.py`.
- Implementation must not modify real model modules for Ollama, OpenAI, Spark, or Gemini.
- Implementation must not modify `data/`.
- Implementation must not modify `output/`.
- Implementation must not modify UI, export, `ops_agents`, release guard, or smoke guard surfaces unless separately authorized.

Any unexpected file outside the explicit allowlist must stop the implementation stage.

## 5 Guard Forbidden Runtime Scope

The guard must block or detect forbidden runtime behavior:

- No Ollama call path.
- No OpenAI call path.
- No Spark call path.
- No Gemini call path.
- No `score_text()` call.
- No `/rescore` call or handler coupling.
- No network call.
- No subprocess call.
- No real model call.
- No UI integration.
- No production scoring main-chain integration.
- No official export-chain integration.

The future endpoint must only call `app.engine.local_llm_preview_mock` pure functions after the feature flag is enabled.

## 6 Guard Forbidden Output Scope

The guard must block output and persistence paths:

- No `app/storage.py` write.
- No `data/` write.
- No `output/` write.
- No `qingtian-results` write or read integration.
- No `evidence_trace/latest` integration.
- No `scoring_basis/latest` integration.
- No DOCX official export trigger.
- No JSON official export trigger.
- No Markdown official export trigger.
- No persisted official scoring result.
- No official evaluation write-back.

Forbidden response fields must also be blocked, including `final_score`, `score_result`, `write_result`, `persist`, `export`, `apply`, `rescore`, `qingtian_results`, `evidence_trace_write`, `scoring_basis_write`, and `storage_write`.

## 7 Feature Flag Contract

The recommended feature flag is:

```text
LOCAL_LLM_PREVIEW_MOCK_API_ENABLED
```

The contract must be:

- Absent flag means disabled.
- False-like flag means disabled.
- Disabled endpoint must return a stable disabled response.
- Disabled endpoint must not call helper functions.
- Enabled endpoint may call only `app.engine.local_llm_preview_mock` pure functions.
- Enabled endpoint must still return only preview/mock/no-write advisory output.
- Enabled endpoint must not alter scoring, storage, export, UI, or real model behavior.

The flag must not enable Ollama, OpenAI, Spark, Gemini, `score_text()`, `/rescore`, `qingtian-results`, `evidence_trace/latest`, or `scoring_basis/latest`.

## 8 Disabled Endpoint Behavior Design

When `LOCAL_LLM_PREVIEW_MOCK_API_ENABLED` is absent or false, the endpoint must:

- Return `disabled`.
- Include a stable reason such as `feature_flag_disabled`.
- Not call `validate_local_llm_preview_boundary()`.
- Not call `build_local_llm_preview_input()`.
- Not call `build_local_llm_mock_response()`.
- Not read or write `data/`.
- Not read or write `output/`.
- Not call or write through `app/storage.py`.
- Not call real model modules.
- Not call `score_text()`.
- Not call `/rescore`.
- Not enter `qingtian-results`, `evidence_trace/latest`, or `scoring_basis/latest`.

Disabled behavior must be cheap, deterministic, and side-effect free.

## 9 Enabled Mock-Only Endpoint Behavior Design

When `LOCAL_LLM_PREVIEW_MOCK_API_ENABLED` is explicitly enabled in a future authorized implementation, the endpoint may only:

- Validate input boundaries with `validate_local_llm_preview_boundary()`.
- Build preview input with `build_local_llm_preview_input()`.
- Build mock advisory response with `build_local_llm_mock_response()`.

The enabled response must include:

- `preview_only=true`
- `mock_only=true` or equivalent `mode=mock_only`
- `no_write=true`
- `affects_score=false`
- `source=local_llm_preview_mock`

The enabled response must not contain official scoring, persistence, export, or apply semantics. It must not call Ollama, OpenAI, Spark, Gemini, `score_text()`, `/rescore`, storage writes, data writes, output writes, UI code, or export code.

## 10 Deterministic API Test Matrix

Future tests must be designed before implementation and must cover at least:

| Case | Expected behavior |
| --- | --- |
| feature flag absent | endpoint returns disabled |
| feature flag false | endpoint returns disabled |
| disabled state | does not call `local_llm_preview_mock` helper |
| disabled state | does not write `data/`, `output/`, or storage |
| enabled state | only calls `local_llm_preview_mock` helper |
| enabled state | response contains `preview_only=true` |
| enabled state | response contains `mock_only=true` or `mode=mock_only` |
| enabled state | response contains `no_write=true` |
| enabled state | does not call `score_text()` |
| enabled state | does not call `/rescore` |
| enabled state | does not access `qingtian-results` |
| enabled state | does not access `evidence_trace/latest` |
| enabled state | does not access `scoring_basis/latest` |
| enabled state | does not trigger DOCX / JSON / Markdown official export |
| enabled state | does not call Ollama / OpenAI / Spark / Gemini |
| repeated same input | returns deterministic output |
| missing input | returns stable error structure without entering scoring main chain |
| empty text | returns stable error structure without entering scoring main chain |
| overlong text | returns stable error structure without entering scoring main chain |
| illegal fields | returns stable error structure without entering scoring main chain |

No test file may be added until a separate implementation/test step is explicitly authorized.

## 11 Required Negative Tests

Future deterministic tests must include negative checks for:

- Helper functions not called when disabled.
- Ollama not called.
- OpenAI not called.
- Spark not called.
- Gemini not called.
- `score_text()` not called.
- `/rescore` not entered.
- `qingtian-results` not accessed.
- `evidence_trace/latest` not accessed.
- `scoring_basis/latest` not accessed.
- DOCX / JSON / Markdown official export not triggered.
- UI code not touched.
- Network not called.
- Subprocess not called.

These checks must fail loudly if any forbidden path is introduced.

## 12 Required No-Write Verification

Future tests must verify no writes to:

- `app/storage.py`
- `data/`
- `output/`
- `qingtian-results`
- `evidence_trace/latest`
- `scoring_basis/latest`
- DOCX official export outputs
- JSON official export outputs
- Markdown official export outputs

The future implementation must not persist mock responses. It must not create official score reports, analysis bundles, report exports, or runtime data artifacts.

## 13 Required No-Real-Model Verification

Future tests and guards must verify that no real model runtime is called:

- No Ollama.
- No OpenAI.
- No Spark.
- No Gemini.
- No HTTP client path for model inference.
- No network dependency.
- No subprocess command that can start or call a model runtime.

The endpoint remains mock-only even when feature-flag enabled.

## 14 Required No-Scoring-Chain Verification

Future tests and guards must verify that the endpoint does not enter:

- `score_text()`
- `/rescore`
- scoring main chain
- storage write chain
- `qingtian-results`
- `evidence_trace/latest`
- `scoring_basis/latest`
- official report generation
- DOCX / JSON / Markdown official export chain

The API bridge must never turn mock advisory content into official scoring evidence or official scoring basis.

## 15 Future Implementation Acceptance Criteria

A future API bridge implementation can only be accepted if:

- It is explicitly authorized as a separate single step.
- Its file scope is predeclared and guard-checked.
- It is default-off via `LOCAL_LLM_PREVIEW_MOCK_API_ENABLED`.
- Disabled behavior returns disabled without calling helper functions.
- Enabled behavior only calls `app.engine.local_llm_preview_mock` pure functions.
- Tests cover disabled behavior before enabled mock-only behavior.
- Tests prove no writes to `data/`, `output/`, or storage.
- Tests prove no real model calls.
- Tests prove no scoring-chain, rescore, qingtian-results, evidence trace, scoring basis, UI, or export-chain access.
- The response stays preview-only, mock-only, and no-write.

## 16 Step 174I Closure Statement

Step 174I is a docs-only guard and deterministic API test design step.

This document must not be interpreted as permission to immediately modify `app/main.py`, add tests, implement an endpoint, connect UI, call real models, run services, run pytest, or touch scoring/storage/export chains.

The next implementation step, if authorized later, must first satisfy this guard and test design. Until then, local LLM API bridge work remains design-only and default-off.
