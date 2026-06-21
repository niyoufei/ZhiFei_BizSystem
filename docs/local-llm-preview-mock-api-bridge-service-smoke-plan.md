# Local LLM Preview Mock API Bridge Service Smoke Plan

## 1 Purpose

This document defines a future service smoke verification plan for the local LLM preview/mock API bridge.

The current Step 174L stage is docs-only. It only designs how a future stage may safely start a local service for smoke verification. It does not start a service, does not run pytest, does not run Ollama, does not call networks, and does not connect a real model.

This document must not be interpreted as permission to immediately start a service or proceed to Step 174M.

## 2 Baseline Inherited From Step 174J And Step 174K

- worktree: `/Users/youfeini/Desktop/ZhiFei_BizSystem-local-llm-clean`
- branch: `local-llm-integration-clean`
- current baseline before this plan: `c7e2bcdb01d07f28b406a2cbab6fd49b75ed9de0`
- current stable tag: `v0.1.43-local-llm-preview-mock-api-bridge-stage-review`
- Step 174J implementation commit: `8b3870cb33ea5de78da81e70a1641dc970552f5b`
- Step 174J endpoint: `POST /local-llm/preview-mock`
- Step 174J feature flag: `LOCAL_LLM_PREVIEW_MOCK_API_ENABLED`

Step 174J implemented a default-off API bridge and deterministic API tests. Step 174K documented the implementation scope, non-integrations, no-write boundaries, no-real-model boundaries, and remaining risks.

## 3 Service Smoke Objective

A future service smoke stage may verify that the already implemented endpoint behaves correctly when exercised through a locally running application process.

The future smoke objective is limited to:

- Starting the service in a clean worktree.
- Sending local loopback requests only.
- Verifying disabled behavior first.
- Verifying enabled mock-only preview behavior second.
- Confirming no writes to storage, `data/`, or `output/`.
- Confirming no real model call.
- Confirming no scoring-chain call.
- Stopping the service process.
- Confirming `git status --short` remains clean after the smoke.

The endpoint under verification is:

```text
POST /local-llm/preview-mock
```

The feature flag under verification is:

```text
LOCAL_LLM_PREVIEW_MOCK_API_ENABLED
```

## 4 Smoke Test Non-Goals

Future service smoke verification must not include:

- Running Ollama.
- Calling Ollama.
- Calling OpenAI.
- Calling Spark.
- Calling Gemini.
- Calling `score_text()`.
- Calling `/rescore`.
- Writing `app/storage.py` data.
- Writing `data/`.
- Writing `output/`.
- Entering `qingtian-results`.
- Entering `evidence_trace/latest`.
- Entering `scoring_basis/latest`.
- Triggering DOCX / JSON / Markdown official exports.
- Connecting UI.
- Running production scoring.
- Running real evaluation write-back.
- Running external network requests.

The future smoke is not a real local model verification and is not a production readiness check.

## 5 Required Pre-Checks Before Any Future Service Start

Before any future Step 174M service start, the operator must verify:

- The current worktree path is `/Users/youfeini/Desktop/ZhiFei_BizSystem-local-llm-clean`.
- The current branch is `local-llm-integration-clean`.
- The worktree is clean.
- The branch is aligned with `origin/local-llm-integration-clean` or the intended reviewed commit.
- The stable rollback anchor `v0.1.43-local-llm-preview-mock-api-bridge-stage-review` exists.
- No Ollama process is required.
- No second window is required for this smoke stage.
- The service bind address is loopback only, such as `127.0.0.1`.
- The selected port is local and does not require browser startup.
- The request payload is the known deterministic mock payload.
- No data/output/storage write path is part of the command.

If any pre-check fails, the future smoke stage must stop before starting the service.

## 6 Disabled Feature Flag Smoke Scenario

The disabled scenario must be verified first.

The future smoke must run with `LOCAL_LLM_PREVIEW_MOCK_API_ENABLED` absent or set to a false-like value such as:

- empty
- `false`
- `0`
- `no`
- `off`

The disabled request must target only local loopback:

```text
POST http://127.0.0.1:<port>/local-llm/preview-mock
```

The expected disabled response must include:

- `status=disabled`
- `enabled=false`
- `disabled=true`
- `reason=feature_flag_disabled`
- `feature_flag=LOCAL_LLM_PREVIEW_MOCK_API_ENABLED`
- `preview_only=true`
- `mock_only=true`
- `no_write=true`
- `affects_score=false`

The disabled scenario must confirm:

- The endpoint returns disabled.
- The helper is not called.
- No scoring-chain path is entered.
- No storage path is entered.
- No export path is entered.
- No real model path is entered.

## 7 Enabled Mock-Only Smoke Scenario

The enabled scenario may only run after the disabled scenario passes.

The future smoke may set `LOCAL_LLM_PREVIEW_MOCK_API_ENABLED` to one of:

- `true`
- `1`
- `yes`
- `on`

The enabled request must target only local loopback:

```text
POST http://127.0.0.1:<port>/local-llm/preview-mock
```

The enabled payload must remain a deterministic preview/mock payload. It must not include forbidden fields such as `final_score`, `score_text`, `rescore`, `qingtian_results`, `storage_write`, `ollama`, `openai`, `spark`, or `gemini`.

The expected enabled response must include:

- `status=ok`
- `enabled=true`
- `feature_flag=LOCAL_LLM_PREVIEW_MOCK_API_ENABLED`
- `mode=mock_only`
- `mock_only=true`
- `preview_only=true`
- `no_write=true`
- `affects_score=false`
- `source=local_llm_preview_mock`

The enabled scenario must only verify mock-only preview behavior. It must not verify real local LLM behavior.

## 8 No-Write Verification

Future service smoke must verify no writes to:

- `app/storage.py` result paths.
- `data/`.
- `output/`.
- `qingtian-results`.
- `evidence_trace/latest`.
- `scoring_basis/latest`.
- DOCX official export outputs.
- JSON official export outputs.
- Markdown official export outputs.

Before and after smoke, `git status --short` must be recorded.

If any `data/`, `output/`, storage, export, qingtian-results, evidence trace, or scoring basis change appears, the smoke must stop and report the change list. No cleanup, reset, or code edit is allowed in the same step.

## 9 No-Real-Model Verification

Future service smoke must not run Ollama.

Future service smoke must not call:

- Ollama.
- OpenAI.
- Spark.
- Gemini.
- Any external model runtime.
- Any external network endpoint.

If logs, responses, process output, or runtime behavior show any real model call attempt, the operator must stop the service and report the exact evidence.

The future smoke must not use `ollama serve`. If a later real Ollama stage is needed, it must open a separate boundary and explicitly state whether a second window is enabled.

## 10 No-Scoring-Chain Verification

Future service smoke must not enter:

- `score_text()`.
- `/rescore`.
- `qingtian-results`.
- `evidence_trace/latest`.
- `scoring_basis/latest`.
- Official report generation.
- DOCX / JSON / Markdown official export chain.
- Production evaluation write-back.

If any request, response, log, or file status indicates scoring-chain access, the smoke must stop and report.

## 11 Process Shutdown And Cleanup Verification

Future service smoke must end by stopping the service process.

The future report must include:

- How the service was started.
- How the service was stopped.
- Whether the process still exists after shutdown.
- Final `git status --short`.
- Whether any files changed.

The service process must not be left running after the future smoke stage.

No cleanup of untracked files is allowed. No `git clean` is allowed. No stash is allowed.

## 12 Rollback Boundary

If future service smoke is abnormal, rollback and recovery must follow these boundaries:

- Do not directly modify business code to hotfix the smoke.
- Do not directly connect a real model for debugging.
- Do not write `data/`.
- Do not write `output/`.
- Do not clean untracked files.
- Do not execute `git clean`.
- Stop the service process first.
- Record the executed commands.
- Record environment variables relevant to the smoke.
- Record request bodies.
- Record response bodies.
- Record service logs or terminal output.
- Record `git status --short`.
- If the worktree remains clean, report findings only and do not roll back code.
- If unexpected file changes appear, report the changed-file list and wait for ChatGPT review.
- Use `v0.1.43-local-llm-preview-mock-api-bridge-stage-review` as the stable rollback anchor.
- Do not roll back `main`.
- Do not push `main`.

Rollback in this context means stopping the process and preserving evidence. It does not mean resetting commits or force-moving branches.

## 13 Failure Stop Conditions

Future Step 174M must stop immediately if any of these occur:

- The worktree is not clean before service start.
- The current branch is not `local-llm-integration-clean`.
- The service cannot bind to local loopback.
- The service attempts to use non-loopback network access.
- Ollama is started or called.
- OpenAI / Spark / Gemini is called.
- `score_text()` is called.
- `/rescore` is called.
- `qingtian-results` is accessed.
- `evidence_trace/latest` is accessed.
- `scoring_basis/latest` is accessed.
- `app/storage.py` write paths are used.
- `data/` is written.
- `output/` is written.
- DOCX / JSON / Markdown official export is triggered.
- UI entry points are connected.
- Service process cannot be stopped.
- `git status --short` is not clean after smoke.

Any stop condition must produce a report before further action.

## 14 Required Report Format For Future Step 174M

If Step 174M is separately authorized to perform actual service smoke verification, its report must include at least:

1. Current directory.
2. Current branch.
3. Starting HEAD.
4. `git status` before.
5. Service start command.
6. Feature flag state.
7. Disabled request and response summary.
8. Enabled request and response summary.
9. Whether Ollama was run.
10. Whether any real model was called.
11. Whether `score_text` / `rescore` was called.
12. Whether `data/`, `output/`, or storage was written.
13. Service shutdown method.
14. `git status` after.
15. Risk statement.

The report must explicitly state if pytest was not run, if no browser was started, and if no UI, export chain, or production scoring chain was connected.

## 15 Step 174L Closure Statement

Step 174L is a docs-only service smoke and rollback boundary design step.

It documents how a future Step 174M may safely verify the default-off API bridge through a local service smoke. It does not start a service, run pytest, run Ollama, call networks, modify code, add tests, connect UI, connect real models, enter scoring, write storage, write `data/`, write `output/`, or trigger official exports.

Work must stop after this document is archived. Any future Step 174M requires a separate instruction and a fresh boundary review.
