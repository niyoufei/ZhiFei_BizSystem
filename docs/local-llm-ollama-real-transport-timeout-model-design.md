# Local LLM Ollama Real Transport Timeout and Model Selection Design

## 1 Purpose

This document defines the Step 174AF pre-implementation boundary for future timeout and model-selection optimization of the local LLM Ollama real transport path.

The current stage is design-only. It does not implement code, adjust runtime settings, switch models, run tests, start services, run Ollama, run `ollama serve`, or access `127.0.0.1:11434`.

This document must not be interpreted as permission to immediately adjust timeout, change `num_predict`, switch models, run runtime smoke, connect UI, connect export, or connect any production scoring chain.

## 2 Baseline Inherited From Step 174AD And Step 174AE

Step 174AD completed a controlled real transport runtime smoke:

- worktree: `/Users/youfeini/Desktop/ZhiFei_BizSystem-local-llm-clean`
- branch: `local-llm-integration-clean`
- endpoint: `POST /local-llm/preview-mock`
- FastAPI loopback: `127.0.0.1:18745`
- Ollama loopback: `127.0.0.1:11434`
- endpoint flag: `LOCAL_LLM_PREVIEW_MOCK_API_ENABLED=true`
- adapter flag: `LOCAL_LLM_OLLAMA_PREVIEW_ADAPTER_ENABLED=true`
- real transport flag: `LOCAL_LLM_OLLAMA_REAL_TRANSPORT_ENABLED=true`
- selected model: `qwen3-next:80b-a3b-instruct-q8_0`

Step 174AD proved that the real transport branch can reach the local Ollama service through loopback only. `/api/tags` returned valid JSON and the local model inventory contained 7 installed models.

Step 174AE reviewed that runtime result and recorded that the endpoint reached the real transport branch, returned HTTP 200, and produced a stable `timeout` failure while preserving `preview_only=true`, `no_write=true`, and `affects_score=false`.

## 3 Current Timeout Failure Summary

The Step 174AD FastAPI response was:

- `status=error`
- `error_type=timeout`
- `real_transport_enabled=true`
- `preview_only=true`
- `no_write=true`
- `affects_score=false`

The timeout reason is the boundary between the current adapter default:

```text
DEFAULT_TIMEOUT_SECONDS = 5.0
```

and the response latency of the selected 80B model:

```text
qwen3-next:80b-a3b-instruct-q8_0
```

This timeout is a stable preview failure. It is not a production scoring failure, does not prove Ollama is unavailable, and must not be used as a reason to connect `score_text()`, `/rescore`, UI, export, storage, `data/`, or `output/`.

## 4 Timeout Configuration Objective

A future implementation may define a controlled timeout configuration for the preview real transport path.

The objective is to make runtime smoke practical without weakening the existing boundaries:

- default-off
- preview-only
- no-write
- `affects_score=false`
- loopback-only
- fake-only tests during code stages
- runtime smoke only after separate authorization

Timeout optimization must affect only the preview transport. It must not change production scoring behavior, `score_text()`, `/rescore`, QingTian result generation, evidence trace generation, scoring basis generation, storage writes, UI, or export behavior.

## 5 Model Selection Objective

A future implementation may refine how the preview real transport selects an installed local model for smoke and preview.

The objective is to avoid using an impractically slow model for small smoke requests while preserving a controlled local-only model boundary:

- `LOCAL_LLM_OLLAMA_MODEL` remains the highest-priority explicit model selector.
- If `LOCAL_LLM_OLLAMA_MODEL` is absent, the implementation may read local `/api/tags` in read-only mode.
- Only installed local models may be selected.
- No model may be downloaded, pulled, created, or installed automatically.
- No production scoring model may be hardcoded.

## 6 Non-Goals

This stage does not:

- modify `app/main.py`
- modify `app/engine/local_llm_ollama_preview_adapter.py`
- add or modify tests
- run pytest
- start FastAPI
- run Ollama
- run `ollama serve`
- access `127.0.0.1:11434`
- call external networks
- download models
- pull models
- execute `ollama pull`
- connect UI
- connect production scoring
- trigger DOCX / JSON / Markdown official export
- write `app/storage.py`
- write `data/`
- write `output/`

## 7 Feature Flag Hierarchy Preservation

Any future timeout or model-selection optimization must preserve the existing feature flag hierarchy:

1. `LOCAL_LLM_PREVIEW_MOCK_API_ENABLED` remains the endpoint-level gate.
2. If the endpoint flag is disabled, the endpoint must return disabled directly and must not check adapter or real transport state.
3. `LOCAL_LLM_OLLAMA_PREVIEW_ADAPTER_ENABLED` remains the adapter branch gate.
4. If the adapter flag is disabled, the endpoint must keep the mock-only helper path.
5. `LOCAL_LLM_OLLAMA_REAL_TRANSPORT_ENABLED` remains the real transport gate.
6. If the real transport flag is disabled, the endpoint must keep the no-real-model safe path.
7. Only when all three flags are enabled may the endpoint construct a localhost Ollama transport.
8. Even when all flags are enabled, every result must keep `preview_only=true`, `no_write=true`, and `affects_score=false`.

All flags must remain default-off. If any flag is absent by default, the path must not write `data/`, `output/`, or storage, and must not affect formal scoring results.

## 8 Timeout Parameter Contract

A future implementation may introduce:

```text
LOCAL_LLM_OLLAMA_TIMEOUT_SECONDS
```

Required timeout boundaries:

- The default value must remain conservative, for example `5.0`, or be separately confirmed in the implementation stage.
- A maximum value must be enforced. The recommended maximum is no more than 60 seconds, subject to explicit confirmation before implementation.
- Empty values must fall back to the safe default.
- Invalid values must fall back to the safe default.
- Negative values must fall back to the safe default.
- `0` must fall back to the safe default.
- Values above the maximum must be clipped to the maximum or rejected with a stable fallback rule.
- The timeout must apply only to preview transport calls.
- The timeout must not change `score_text()` or `/rescore` behavior.
- The timeout must not write storage, `data/`, or `output/`.
- The timeout must not trigger UI or export behavior.

Timeout failures must continue to use a stable failure schema and must not be treated as production scoring failures.

## 9 num_predict Parameter Contract

A future implementation may introduce:

```text
LOCAL_LLM_OLLAMA_NUM_PREDICT
```

Required generation-limit boundaries:

- The default value must be small enough for preview and smoke use.
- The maximum value must be bounded. The current upper bound of `128` may be used as the reference unless a future implementation stage explicitly changes it.
- Empty values must fall back to the safe default.
- Invalid values must fall back to the safe default.
- Negative values must fall back to the safe default.
- `0` must fall back to the safe default.
- Values above the maximum must be clipped to the maximum or rejected with a stable fallback rule.
- Smoke scenarios should use a smaller `num_predict` value where explicitly configured.
- Long model output must not be recorded.
- The preview path must not generate formal scoring text.
- The preview path must not generate DOCX / JSON / Markdown official export content.

`num_predict` changes must remain preview-only and must not affect production scoring.

## 10 Model Selection Contract

Future model selection must follow this priority:

1. Use `LOCAL_LLM_OLLAMA_MODEL` when it is set to a non-empty value.
2. If `LOCAL_LLM_OLLAMA_MODEL` is absent, read local `/api/tags` in read-only mode.
3. If `/api/tags` returns installed local models, select according to the future stage's explicit policy.
4. If no local model is available, return a stable `model_unavailable` failure.

Required model-selection boundaries:

- Only local installed models may be used.
- No model may be downloaded.
- No model may be pulled.
- `ollama pull` must not be executed.
- External networks must not be called.
- Production scoring models must not be hardcoded.
- If a specified model does not exist, the path must return `model_unavailable` or use an explicitly authorized fallback policy.
- If fallback to the first local model is allowed, the response or report must record the fallback reason.
- If a lightweight smoke model is allowed, it must be selected from existing local tags, for example already installed models such as `qwen3:0.6b`, `qwen3:8b`, or `qwen3:14b`.
- A lightweight smoke model must not be treated as a production scoring model.
- Model selection must not write scoring basis, evidence trace, QingTian result, export result, storage, `data/`, or `output/`.

## 11 Fake-Only Deterministic Tests Requirements

Future tests for timeout and model-selection code must remain fake-only.

The test matrix must cover at least:

- `LOCAL_LLM_OLLAMA_TIMEOUT_SECONDS` absent uses the default timeout.
- invalid timeout values fall back to the default timeout.
- negative timeout values fall back to the default timeout.
- `0` timeout falls back to the default timeout.
- timeout values above the maximum are clipped or rejected according to the agreed rule.
- `LOCAL_LLM_OLLAMA_NUM_PREDICT` absent uses the default value.
- invalid `num_predict` values fall back to the default value.
- negative `num_predict` values fall back to the default value.
- `0` `num_predict` falls back to the default value.
- `num_predict` values above the maximum are clipped or rejected according to the agreed rule.
- `LOCAL_LLM_OLLAMA_MODEL` has priority when specified.
- a missing specified model returns `model_unavailable` or follows the explicit fallback policy.
- absent model env plus fake tags with models selects a local model.
- absent model env plus empty fake tags returns `model_unavailable`.
- timeout configuration does not change `preview_only=true`.
- timeout configuration does not change `no_write=true`.
- timeout configuration does not change `affects_score=false`.
- model selection does not call `score_text()` or `/rescore`.
- model selection does not enter qingtian-results, `evidence_trace/latest`, or `scoring_basis/latest`.
- model selection does not write `data/`, `output/`, or storage.
- model selection does not trigger export.
- model selection does not connect UI.
- tests do not call real Ollama.
- tests do not start FastAPI service.
- tests do not access external networks.
- tests do not download or pull models.
- existing mock API bridge tests continue to pass.
- existing adapter independent tests continue to pass.

Any test that needs transport behavior must use fake client, fake transport, or monkeypatching. Tests must not access `127.0.0.1:11434`.

## 12 Runtime Smoke Requirements After Future Implementation

After any separately authorized timeout or model-selection implementation, runtime smoke must be separately authorized.

Runtime smoke requirements:

- Use the current ChatGPT conversation as controller unless changed by explicit instruction.
- Use the current Codex nifei1227 conversation as executor unless changed by explicit instruction.
- Enable a 2nd window only for `ollama serve`.
- The 2nd window must not run git, pytest, code modification, commit, tag, or push.
- FastAPI must listen only on `127.0.0.1`.
- Ollama access must remain limited to `127.0.0.1:11434`.
- No model may be downloaded.
- No model may be pulled.
- `ollama pull` must not be executed.
- External networks must not be called.
- Runtime smoke must stop FastAPI after the request.
- Runtime smoke must confirm the worktree remains clean except for an explicitly allowed report document.

## 13 No-Write Boundary

Timeout and model-selection optimization must not write:

- `app/storage.py`
- `data/`
- `output/`
- qingtian-results
- `evidence_trace/latest`
- `scoring_basis/latest`
- export artifacts
- production score reports

If any future runtime smoke creates unexpected file changes, execution must stop, list the changed files, and wait for ChatGPT review. The executor must not run `git clean` or remove untracked files unless separately authorized.

## 14 No-Scoring-Chain Boundary

Timeout and model-selection optimization must not call or connect:

- `score_text()`
- `score_text_v2`
- `/rescore`
- qingtian-results
- `evidence_trace/latest`
- `scoring_basis/latest`
- production score persistence
- production evaluation persistence

Preview response fields such as `status=error`, `error_type=timeout`, or `model_unavailable` must remain advisory preview results and must not affect formal scores.

## 15 No-UI And No-Export Boundary

Timeout and model-selection optimization must not connect:

- UI
- browser workflows
- DOCX official export
- JSON official export
- Markdown official export
- export bundles

The preview path may return bounded advisory content only. It must not produce official scoring text, official evidence text, or official export content.

## 16 Failure And Rollback Boundary

If a future implementation or runtime smoke fails:

- Do not directly modify business code to rescue the smoke.
- Do not directly connect scoring, UI, storage, or export chains.
- Do not download or pull models.
- Do not run `ollama pull`.
- Stop FastAPI if it is running.
- Record the command, feature flags, model, timeout, `num_predict`, request summary, response summary, and `git status`.
- If the worktree is clean, report the result without code rollback.
- If unexpected files changed, report the file list and wait for ChatGPT review.
- Do not execute `git clean`.
- Do not push `main`.

Stable rollback references include the current real transport runtime smoke review baseline:

```text
v0.1.63-local-llm-ollama-real-transport-runtime-smoke-stage-review
```

## 17 Future Implementation Acceptance Criteria

Before any timeout or model-selection code implementation, the next stage must explicitly define:

- that Step 174AF design has been archived
- the allowed file modification scope
- whether `app/engine/local_llm_ollama_preview_adapter.py` may be modified
- whether `app/main.py` may be modified
- whether tests may be modified or added
- timeout environment variable name
- timeout default value
- timeout maximum value
- `num_predict` environment variable name
- `num_predict` default value
- `num_predict` maximum value
- exact model-selection strategy
- behavior when `LOCAL_LLM_OLLAMA_MODEL` is missing locally
- whether first local `/api/tags` fallback is allowed
- whether a smaller local model may be used for smoke
- fake-only deterministic test requirements
- no `ollama serve` during code implementation
- 2nd window only for a separately authorized runtime smoke stage
- no model download
- no model pull
- no `ollama pull`
- no writes to `data/`, `output/`, or storage
- no scoring-chain integration
- no UI integration
- no export-chain integration
- no push to `main`
- mandatory stop for ChatGPT review after completion

## 18 Step 174AF Closure Statement

Step 174AF is a docs-only boundary design for timeout and model-selection optimization after the Step 174AD real transport runtime smoke returned a stable `timeout` failure.

This document records that the timeout was caused by the current `DEFAULT_TIMEOUT_SECONDS=5.0` boundary against the selected 80B local model response time. The result is a stable preview failure, not a production scoring failure and not evidence that Ollama is unavailable.

No code, tests, service, Ollama process, localhost Ollama endpoint, external network, model download, model pull, UI, export chain, scoring chain, storage, `data/`, or `output/` is authorized by this document.
