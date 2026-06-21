# Local LLM Preview Mock API Bridge Design

## 1 Purpose

This document defines the Step 174H design boundary for a future local LLM preview/mock API bridge.

The current stage is docs-only. It only designs a default-off API bridge and does not implement API code. It does not permit immediate implementation of the API bridge.

This stage does not represent:

- API implementation.
- UI integration.
- Real Ollama calls.
- OpenAI / Spark / Gemini calls.
- Production enablement of local model behavior.
- Scoring main-chain integration.
- Storage or export-chain integration.

## 2 Current Baseline

- worktree: `/Users/youfeini/Desktop/ZhiFei_BizSystem-local-llm-clean`
- branch: `local-llm-integration-clean`
- HEAD: `b75ad2ed58f5d4e5a0902017c12078354d643d50`
- remote branch: `origin/local-llm-integration-clean`
- remote branch HEAD: `b75ad2ed58f5d4e5a0902017c12078354d643d50`
- prior stable tag: `v0.1.39-local-llm-preview-mock-helper-review`

The latest helper baseline already provides pure functions in `app.engine.local_llm_preview_mock`. A future API bridge may only call those existing pure functions and must not expand into scoring, storage, export, UI, or real model runtime behavior.

## 3 Non-Goals

This design explicitly excludes:

- Writing API implementation code.
- Modifying `app/main.py`.
- Modifying `app/storage.py`.
- Modifying `app.engine.local_llm_preview_mock`.
- Adding tests in this step.
- Connecting UI entry points.
- Calling real Ollama.
- Calling OpenAI / Spark / Gemini.
- Calling `score_text()`.
- Connecting `/rescore`.
- Writing `data/`.
- Writing `output/`.
- Entering `qingtian-results`.
- Entering `evidence_trace/latest`.
- Entering `scoring_basis/latest`.
- Triggering DOCX / JSON / Markdown official exports.
- Creating a production local model pathway.

## 4 Default-Off Behavior

The future endpoint must be disabled by default.

When the feature flag is not enabled:

- The endpoint must return a disabled response.
- The endpoint must not execute mock processing.
- The endpoint must not call `build_local_llm_preview_input()`.
- The endpoint must not call `build_local_llm_mock_response()`.
- The endpoint must not evaluate scoring context beyond basic request shape checks.
- The endpoint must not read or write `data/`.
- The endpoint must not write `output/`.
- The endpoint must not write through `app/storage.py`.
- The endpoint must not call any local or remote model runtime.

The disabled response must clearly indicate that the bridge is default-off and not active.

## 5 Allowed Future Call Boundary

If a later, separately authorized implementation stage enables the feature flag, the API bridge may only call pure functions from:

- `app.engine.local_llm_preview_mock.validate_local_llm_preview_boundary`
- `app.engine.local_llm_preview_mock.build_local_llm_preview_input`
- `app.engine.local_llm_preview_mock.build_local_llm_mock_response`

Even when enabled, the response must remain:

- `preview_only=true`
- `mock_only`
- `no_write=true`
- `affects_score=false`
- `source=local_llm_preview_mock`

The bridge must only construct a preview/mock response. It must not persist, export, apply, rescore, or alter any official scoring result.

## 6 Forbidden Paths

The future API bridge must not touch or invoke:

- Real Ollama.
- OpenAI.
- Spark.
- Gemini.
- `score_text()`.
- `/rescore`.
- `app/storage.py`.
- `data/`.
- `output/`.
- `qingtian-results`.
- `evidence_trace/latest`.
- `scoring_basis/latest`.
- UI code.
- DOCX / JSON / Markdown official exports.
- `ops_agents`.
- release guard runtime behavior.
- smoke guard runtime official gates.
- Monitoring, authentication, or rate-limit main logic.

The bridge must not introduce hidden side effects such as network calls, filesystem writes, subprocess calls, or storage writes.

## 7 Request / Response Design Constraints

A future request shape should remain minimal and explicit. It may include:

- `project_id`
- `submission_id`
- `text_excerpt`
- `mode`
- `requested_by`
- optional `scoring_context`
- optional `evidence_context`
- optional `requirement_hits`

The API response must not include official-result semantics such as:

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

If the feature flag is disabled, the response must not include a generated mock advisory payload. If enabled in a later stage, the response may include `preview_input` and `advisory`, but only as mock-only advisory content.

## 8 Guard Requirements Before Implementation

Before any API bridge implementation, a separate guard task spec must be completed and reviewed.

The guard spec must define:

- Allowed files for implementation.
- Forbidden files such as `app/storage.py`, scoring engines, export scripts, data directories, and output directories.
- Required feature flag behavior.
- Required disabled response behavior.
- Required no-write and no-network boundaries.
- Forbidden response fields.
- Forbidden imports and runtime calls.
- A hard stop if implementation touches scoring, storage, export, UI, or real model modules.

No API bridge implementation should start until this guard task spec is accepted.

## 9 Deterministic API Test Requirements Before Implementation

Before implementation, deterministic API tests must be designed.

Those tests must cover:

- Feature flag disabled response.
- Disabled state does not call mock helper functions.
- Feature flag enabled mock-only response shape.
- Response contains `preview_only=true`, `no_write=true`, `affects_score=false`.
- Response does not include official-result fields.
- No Ollama call.
- No OpenAI / Spark / Gemini call.
- No `score_text()` call.
- No `/rescore` behavior.
- No `app/storage.py` write.
- No `data/` write.
- No `output/` write.
- No `qingtian-results` / `evidence_trace/latest` / `scoring_basis/latest` behavior.
- No DOCX / JSON / Markdown official export behavior.

Tests must be deterministic and must not start services, run Ollama, call networks, or require external dependencies.

## 10 Acceptance Criteria For A Future Implementation Stage

A future implementation stage can only be considered if all of the following are true:

- The guard task spec has been completed.
- Deterministic API test design has been completed.
- The implementation remains default-off.
- The implementation only calls `app.engine.local_llm_preview_mock` pure functions.
- The implementation does not connect UI.
- The implementation does not call true model runtimes.
- The implementation does not enter scoring, storage, export, or real evaluation write-back chains.
- The implementation changes are reviewed as a separate, explicitly authorized step.

## 11 Step 174H Closure Statement

Step 174H is a docs-only boundary design step.

It only records the default-off API bridge design. It does not authorize immediate API bridge implementation, UI integration, real model invocation, scoring-chain integration, storage writes, export-chain behavior, or production enablement.

The next safe step is a guard task spec design, followed by deterministic API test design. Implementation must not begin until those prerequisites are separately reviewed and accepted.
