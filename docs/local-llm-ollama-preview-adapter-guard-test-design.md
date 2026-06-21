# Local LLM Ollama Preview Adapter Guard and Test Design

## 1 Purpose

This document defines the guard task specification and deterministic test design for a future local LLM Ollama preview adapter.

The current Step 174O stage is docs-only. It only designs guard and test boundaries. It does not implement an adapter, does not add `app/engine` files, does not modify `app/main.py`, does not add tests, does not run pytest, does not start services, does not run Ollama, and does not call external networks.

This document must not be interpreted as permission to immediately implement an adapter or call Ollama.

## 2 Baseline Inherited From Step 174N

- worktree: `/Users/youfeini/Desktop/ZhiFei_BizSystem-local-llm-clean`
- branch: `local-llm-integration-clean`
- baseline before Step 174O: `499b9aedc52aca355b4d1cf4e5652abdee5c7bb8`
- baseline tag: `v0.1.46-local-llm-ollama-preview-adapter-design`
- current mock endpoint: `POST /local-llm/preview-mock`
- current mock API feature flag: `LOCAL_LLM_PREVIEW_MOCK_API_ENABLED`
- future adapter feature flag candidate: `LOCAL_LLM_OLLAMA_PREVIEW_ADAPTER_ENABLED`

Step 174N established that a future Ollama preview adapter must be default-off, preview-only, no-write, isolated from scoring, isolated from storage, isolated from export, and isolated from UI. Step 174O only records the guard and deterministic test prerequisites for any later implementation.

## 3 Current Mock API Bridge Boundary

The current `POST /local-llm/preview-mock` endpoint must remain a mock-only helper path.

Current allowed behavior:

- Uses `app.engine.local_llm_preview_mock`.
- Returns mock-only advisory payloads.
- Preserves `preview_only=true`.
- Preserves `mock_only=true`.
- Preserves `no_write=true`.
- Preserves `affects_score=false`.
- Does not call Ollama.
- Does not call OpenAI / Spark / Gemini.
- Does not call `score_text()`.
- Does not call `/rescore`.
- Does not enter qingtian-results, evidence trace, or scoring basis paths.
- Does not write storage, `data/`, or `output/`.

Future adapter work must not attach Ollama to this endpoint unless a later stage explicitly authorizes that connection with a separate file scope, guard, and test plan.

## 4 Adapter Guard Objective

The future adapter guard must prevent a local Ollama preview adapter from crossing preview boundaries.

The guard must prove that a future adapter:

- Remains default-off.
- Remains preview-only.
- Remains no-write.
- Does not affect formal scoring results.
- Does not call scoring or rescore.
- Does not enter qingtian-results, evidence trace, or scoring basis.
- Does not trigger DOCX / JSON / Markdown official exports.
- Does not connect UI.
- Does not call OpenAI / Spark / Gemini.
- Does not call external networks.
- Does not call Ollama unless a specific real-Ollama stage authorizes it.
- Does not listen on `0.0.0.0`.
- Does not push `main`.

The guard must block unauthorized file changes, forbidden runtime paths, forbidden output paths, forbidden scoring paths, and default real-model calls.

## 5 Guard Forbidden File Scope

Future guard checks must enforce file scope.

Before explicit authorization:

- Do not add `app/engine/local_llm_ollama_preview_adapter.py`.
- Do not add any `app/engine` file.
- Do not modify `app/main.py`.
- Do not modify `app/storage.py`.
- Do not modify `app/engine/local_llm_preview_mock.py`.
- Do not modify scoring engines such as `app/engine/scorer.py` or `app/engine/v2_scorer.py`.
- Do not modify `tests/test_local_llm_preview_mock.py`.
- Do not modify `tests/test_local_llm_preview_mock_api_bridge.py`.
- Do not add tests.
- Do not modify release guard or smoke guard files unless a separate design authorizes it.
- Do not modify UI files.
- Do not modify export-chain files.
- Do not modify `ops_agents` files.
- Do not modify `data/`.
- Do not modify `output/`.

Any future implementation step must provide an explicit allowlist before editing.

## 6 Guard Forbidden Runtime Scope

Future guard checks must block forbidden runtime behavior.

The adapter must not:

- Run Ollama by default.
- Call Ollama by default.
- Call OpenAI.
- Call Spark.
- Call Gemini.
- Call external networks.
- Start subprocess model runtimes.
- Listen on `0.0.0.0`.
- Start service processes unless a later smoke stage authorizes it.
- Start a browser.
- Install dependencies.
- Execute `git clean`.

Real Ollama calls require a separate feature flag and a separately authorized stage. Real Ollama smoke must also state whether `2号窗口` is enabled to run `ollama serve`.

## 7 Guard Forbidden Scoring-Chain Scope

Future guard checks must block any scoring-chain coupling.

The adapter must not:

- Call `score_text()`.
- Call `score_text_v2()`.
- Call `compute_v2_rule_total`.
- Call `/rescore`.
- Call `rescore_project_submissions`.
- Become a branch inside `/rescore`.
- Affect formal score values.
- Affect formal scoring rules.
- Produce official scoring evidence.
- Produce official scoring basis.
- Read or write qingtian-results.
- Read or write `evidence_trace/latest`.
- Read or write `scoring_basis/latest`.

Real Ollama failure must never silently fall back into a scoring main-chain call.

## 8 Guard Forbidden Output Scope

Future guard checks must block output and persistence paths.

The adapter must not write:

- `app/storage.py` result paths.
- `data/`.
- `output/`.
- qingtian-results.
- evidence trace.
- scoring basis.
- DOCX official exports.
- JSON official exports.
- Markdown official exports.
- official score reports.
- official analysis bundles.
- production evaluation write-back artifacts.

Real Ollama failure must never silently write storage, `data/`, or `output/`.

## 9 Future Feature Flag Contract

The future adapter may use this feature flag, but Step 174O does not implement it:

```text
LOCAL_LLM_OLLAMA_PREVIEW_ADAPTER_ENABLED
```

The contract must be:

- Absent means disabled.
- Empty means disabled.
- `false` means disabled.
- `0` means disabled.
- `no` means disabled.
- `off` means disabled.
- Disabled state must not call Ollama.
- Disabled state must not write `data/`, `output/`, or storage.
- Disabled state must not affect scoring results.
- Enabled state must still return `preview_only=true`.
- Enabled state must still return `no_write=true`.
- Enabled state must still return `affects_score=false`.
- Enabled state must not call `score_text()` or `/rescore`.
- Enabled state must not enter qingtian-results, evidence trace, or scoring basis paths.
- Enabled state must not trigger export chains.
- Enabled state must not connect UI.

The feature flag must control only adapter eligibility. It must not enable production scoring, UI, storage writes, exports, OpenAI, Spark, Gemini, or remote network access.

## 10 Future Timeout Contract

Future real Ollama calls must define a timeout before implementation.

The timeout contract must specify:

- The timeout value or configuration source.
- Whether the timeout applies to connection, read, or whole request duration.
- The stable response shape for timeout.
- The failure type name, such as `timeout`.
- That timeout does not trigger retry into scoring.
- That timeout does not write storage.
- That timeout does not write `data/`.
- That timeout does not write `output/`.
- That timeout does not trigger exports.

Timeout handling must be deterministic and must not leave background work running.

## 11 Future Failure Response Contract

Future adapter failure responses must be stable and explicit.

The adapter must distinguish at least:

- `disabled`
- `model_unavailable`
- `transport_failure`
- `timeout`
- `invalid_response`
- `success`

Every failure response must include:

- `preview_only=true`
- `no_write=true`
- `affects_score=false`
- `source=ollama_preview_adapter`
- `failure_type`
- stable `message`
- no formal score fields
- no persistence fields
- no export fields

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

No failure mode may silently fall back to the scoring main chain.

## 12 Future Deterministic Tests Matrix

Future deterministic tests must be designed before implementation and must not be added in Step 174O.

Required future test matrix:

| Case | Expected result |
| --- | --- |
| feature flag absent | adapter returns disabled |
| feature flag false | adapter returns disabled |
| feature flag `0` | adapter returns disabled |
| feature flag `no` | adapter returns disabled |
| feature flag `off` | adapter returns disabled |
| disabled state | does not call Ollama |
| disabled state | does not write `data/`, `output/`, or storage |
| disabled state | does not call `score_text()` or `/rescore` |
| disabled state | does not enter qingtian-results / evidence trace / scoring basis |
| enabled but Ollama unavailable | returns stable failure |
| enabled but transport failure | returns stable failure |
| enabled but timeout | returns stable failure |
| enabled but invalid model response | returns stable failure |
| enabled success | returns `preview_only=true` |
| enabled success | returns `no_write=true` |
| enabled success | returns `affects_score=false` |
| enabled success | does not write `data/`, `output/`, or storage |
| enabled success | does not call `score_text()` or `/rescore` |
| enabled success | does not enter qingtian-results / evidence trace / scoring basis |
| enabled success | does not trigger DOCX / JSON / Markdown official export |
| enabled success | does not connect UI |
| mock fallback | output is deterministic |
| repeated mock fallback input | outputs are identical |
| empty text | returns stable error structure |
| missing fields | returns stable error structure |
| illegal fields | returns stable error structure |
| timeout boundary | is testable without real waiting |
| invalid response boundary | is testable with mocked response |
| adapter implementation | does not modify existing mock helper |
| adapter implementation | does not modify `app/storage.py` |

Tests must use mocks or monkeypatching for transport behavior. They must not require a running Ollama process.

## 13 Required Negative Tests

Future tests must include negative checks proving the adapter does not call:

- Ollama when disabled.
- OpenAI.
- Spark.
- Gemini.
- external networks.
- `score_text()`.
- `score_text_v2()`.
- `/rescore`.
- qingtian-results paths.
- `evidence_trace/latest`.
- `scoring_basis/latest`.
- DOCX official export functions.
- JSON official export functions.
- Markdown official export functions.
- UI code.
- storage writers.
- `data/` writers.
- `output/` writers.

Negative tests must fail loudly if a forbidden path is introduced.

## 14 Required No-Write Verification

Future tests and guards must verify no writes to:

- `app/storage.py` result paths.
- `data/`.
- `output/`.
- qingtian-results.
- evidence trace.
- scoring basis.
- official score reports.
- DOCX exports.
- JSON exports.
- Markdown exports.

The adapter must not persist raw model output, fallback output, prompt input, advisory response, or failure details into repository data directories.

## 15 Required No-Real-Model-Default Verification

Future tests and guards must verify:

- Default adapter state is disabled.
- Disabled state does not call Ollama.
- Disabled state does not create network requests.
- Disabled state does not start subprocesses.
- Disabled state does not require `ollama serve`.
- Disabled state does not need `2号窗口`.
- Real Ollama calls only happen when explicitly feature-flag enabled in an authorized stage.
- Real Ollama smoke requires a separate instruction and boundary.

This prevents a hidden real-model dependency from becoming part of normal tests or normal service startup.

## 16 Required No-Scoring-Chain Verification

Future tests and guards must verify:

- No `score_text()` call.
- No `score_text_v2()` call.
- No `/rescore` call.
- No rescore handler coupling.
- No qingtian-results access.
- No `evidence_trace/latest` access.
- No `scoring_basis/latest` access.
- No official evidence write.
- No official scoring basis write.
- No formal score mutation.
- No official export trigger.

The adapter must never turn model output into official scoring evidence or formal scoring basis without a separate authorized design.

## 17 Future Implementation Acceptance Criteria

A future implementation stage can proceed only if all of the following are explicitly satisfied:

- Step 174O design is archived.
- The allowed adapter file path is declared.
- The allowed test file path is declared.
- Whether `app/main.py` may be modified is declared.
- The feature flag name is declared.
- The default disabled behavior is declared.
- The timeout value or timeout configuration source is declared.
- The failure response schema is declared.
- The implementation remains preview-only.
- The implementation remains no-write.
- The implementation does not affect formal scoring results.
- The implementation does not call `score_text()` or `/rescore`.
- The implementation does not enter qingtian-results, evidence trace, or scoring basis paths.
- The implementation does not write `data/`, `output/`, or storage.
- The implementation does not connect UI.
- The implementation does not trigger export chains.
- The implementation does not run Ollama unless a separate real-smoke stage authorizes it.
- Whether `2号窗口` is required for `ollama serve` is declared before any real Ollama smoke.
- The target test command is declared.
- The stop conditions are declared.

Without these conditions, no adapter implementation should begin.

## 18 Step 174O Closure Statement

Step 174O is a docs-only adapter guard and deterministic test design step.

It does not implement the Ollama preview adapter, does not add `app/engine` files, does not modify `app/main.py`, does not modify `app/storage.py`, does not modify `app.engine.local_llm_preview_mock`, does not add tests, does not run pytest, does not start services, does not run Ollama, does not call external networks, does not write `data/`, does not write `output/`, does not connect UI, does not connect scoring, and does not trigger official exports.

The current `POST /local-llm/preview-mock` endpoint remains mock-only. Any future adapter implementation, endpoint connection, service smoke, real Ollama call, UI work, scoring work, storage work, or export work requires a separate instruction and a fresh boundary review.
