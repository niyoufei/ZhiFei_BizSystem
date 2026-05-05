# Local LLM Ollama Timeout and Model Selection Controls Stage Review

## Purpose

This document reviews Step 174AH, which implemented controlled timeout, `num_predict`, and model selection configuration for the local LLM Ollama preview real transport path.

This review is documentation-only. It records the implementation scope, configuration boundaries, fake-only deterministic test coverage, explicit non-integrations, remaining risks, and required guardrails before any future runtime smoke validation.

## Baseline before Step 174AH

Before Step 174AH, the local LLM Ollama preview real transport path already existed behind the controlled API bridge and feature flag hierarchy, but its timeout and generation length controls were fixed by implementation defaults.

Step 174AD proved that the real transport branch could reach local Ollama through `127.0.0.1:11434`, but the 80B model timed out under the previous `DEFAULT_TIMEOUT_SECONDS = 5.0` boundary. Step 174AF and Step 174AG then defined the timeout and model selection optimization boundaries before code changes were allowed.

## Files changed in Step 174AH

Step 174AH changed only these files:

- `app/engine/local_llm_ollama_preview_adapter.py`
- `app/main.py`
- `tests/test_local_llm_ollama_preview_adapter.py`
- `tests/test_local_llm_preview_mock_api_bridge.py`

No other files were changed during Step 174AH.

## Function summary

Step 174AH added these configuration helper functions:

- `parse_ollama_timeout_seconds`
- `get_ollama_timeout_seconds`
- `parse_ollama_num_predict`
- `get_ollama_num_predict`

Step 174AH adjusted these functions:

- `_bounded_num_predict`
- `_build_local_llm_ollama_preview_response`

The endpoint bridge now reads the controlled timeout and `num_predict` values before constructing the real transport path, while preserving the existing feature flag hierarchy and preview-only response contract.

## Timeout control review

The timeout environment variable is:

- `LOCAL_LLM_OLLAMA_TIMEOUT_SECONDS`

The timeout default value is:

- `5.0` seconds

The timeout maximum value is:

- `60.0` seconds

The timeout parser uses the safe default when the value is absent, empty, non-numeric, negative, or `0`. Values above the maximum are clipped to `60.0`.

Timeout configuration affects only the preview transport. It does not affect `score_text`, `/rescore`, production scoring, storage, UI, or export behavior.

## num_predict control review

The generation length environment variable is:

- `LOCAL_LLM_OLLAMA_NUM_PREDICT`

The `num_predict` default value is:

- `128`

The `num_predict` maximum value is:

- `128`

The `num_predict` parser uses the safe default when the value is absent, empty, non-numeric, negative, or `0`. Values above the maximum are clipped to `128`.

The `num_predict` control is limited to preview generation. It must not generate formal scoring text, long model output, DOCX output, JSON export output, Markdown export output, or any production review artifact.

## Model selection review

Model selection remains controlled:

- `LOCAL_LLM_OLLAMA_MODEL` has highest priority.
- If `LOCAL_LLM_OLLAMA_MODEL` is not set, the preview transport may read local `/api/tags` and select the first local model.
- If no local model is available, the response returns stable `model_unavailable`.
- The model selection path does not download models.
- The model selection path does not pull models.
- The model selection path does not execute `ollama pull`.
- The model selection path does not access external networks.
- The model selection path does not hard-code a production scoring model.

Model selection does not affect formal scoring results, scoring basis, evidence trace, export outputs, or storage.

## Feature flag and preview boundary review

The three feature flag hierarchy remains:

1. `LOCAL_LLM_PREVIEW_MOCK_API_ENABLED`
   - Endpoint master switch.
   - When disabled or absent, the endpoint returns `disabled` directly.
   - When disabled, it must not check adapter or transport flags.

2. `LOCAL_LLM_OLLAMA_PREVIEW_ADAPTER_ENABLED`
   - Adapter branch switch.
   - When endpoint is enabled but adapter is disabled or absent, the endpoint keeps the mock-only helper path.

3. `LOCAL_LLM_OLLAMA_REAL_TRANSPORT_ENABLED`
   - Real transport switch.
   - When endpoint and adapter are enabled but real transport is disabled or absent, the endpoint keeps the no-real-model safety path.
   - Only when all three flags are enabled may the localhost real transport be constructed.

All paths continue to preserve:

- `preview_only=true`
- `no_write=true`
- `affects_score=false`

## Deterministic fake-only test coverage

Step 174AH used only fake-only deterministic tests. It did not run Ollama, did not run `ollama serve`, did not start FastAPI, did not access `127.0.0.1:11434`, did not call external networks, and did not download or pull models.

The test command was:

```bash
python3 -m pytest tests/test_local_llm_ollama_preview_adapter.py tests/test_local_llm_preview_mock_api_bridge.py tests/test_local_llm_preview_mock.py -q
```

The test result was:

```text
115 passed in 1.12s
```

The fake-only tests cover timeout parsing, `num_predict` parsing, configured model priority, local tags fallback behavior, no-model `model_unavailable`, endpoint bridge propagation, invalid configuration fallback, and preservation of preview-only/no-write/affects-score boundaries.

## Explicit non-integrations

Step 174AH did not run Ollama.

Step 174AH did not run `ollama serve`.

Step 174AH did not start FastAPI.

Step 174AH did not really access `127.0.0.1:11434`.

Step 174AH did not call external networks.

Step 174AH did not download models.

Step 174AH did not pull models.

Step 174AH did not modify `app/storage.py`.

Step 174AH did not modify `app/engine/local_llm_preview_mock.py`.

Step 174AH did not modify `tests/test_local_llm_preview_mock.py`.

Step 174AH did not connect `score_text()`.

Step 174AH did not connect `/rescore`.

Step 174AH did not connect `qingtian-results`.

Step 174AH did not connect `evidence_trace/latest`.

Step 174AH did not connect `scoring_basis/latest`.

Step 174AH did not connect UI.

Step 174AH did not trigger DOCX, JSON, or Markdown formal export.

Step 174AH did not connect any real-model production scoring chain.

## No-write boundary verification

The implementation remains preview-only and no-write.

Step 174AH did not write `data/`.

Step 174AH did not write `output/`.

Step 174AH did not write storage.

The configuration controls are runtime environment controls for preview transport behavior only. They must not create files, update persistence, or write report/export artifacts.

## No-scoring-chain boundary verification

The timeout, `num_predict`, and model selection controls do not enter the scoring chain.

They do not call `score_text()`.

They do not call `/rescore`.

They do not enter `qingtian-results`.

They do not enter `evidence_trace/latest`.

They do not enter `scoring_basis/latest`.

They do not affect formal scoring results.

## No-UI and no-export verification

The configuration controls are not connected to UI.

The configuration controls do not trigger DOCX export.

The configuration controls do not trigger JSON export.

The configuration controls do not trigger Markdown formal export.

The configuration controls do not produce formal review deliverables.

## Remaining risks

- Timeout and model selection controls have been implemented, but real runtime smoke has not yet been run after Step 174AH.
- Fake-only tests do not guarantee that local Ollama runtime generation will succeed.
- Step 174AD proved that the 80B model timed out under a `5.0` second timeout.
- The new timeout controls have not yet been validated with a longer timeout in runtime.
- The model selection controls have not yet been validated with a lightweight local model in runtime.
- The current preview transport cannot be used for production scoring.
- The current preview transport is not connected to UI.
- The current preview transport is not connected to export chains.
- The current preview transport is not connected to `qingtian-results`, `evidence_trace/latest`, or `scoring_basis/latest`.
- The current preview transport does not write storage, `data/`, or `output/`.
- Future runtime smoke must be separately authorized.
- Future runtime smoke must use a second window for `ollama serve`.
- Future runtime smoke must not download or pull models.
- Future runtime smoke must not modify scoring chain, UI, export chain, or storage to make the smoke pass.
- If any future runtime smoke creates `data/`, `output/`, or storage changes, the run must stop and report the change list.

## Required next-stage guard before runtime smoke

Before any future runtime smoke, the next stage must explicitly confirm these boundaries:

- The current ChatGPT conversation remains the controller.
- The current Codex nifei1227 conversation remains the executor.
- A second window must be enabled to run only `ollama serve`.
- The second window must not run git, pytest, code edits, commit, tag, or push.
- Codex nifei1227 may perform repository checks, FastAPI startup, loopback requests, report documentation, commit, tag, and push.
- Only Codex nifei1227 may perform repository write operations during the stage.
- FastAPI must listen only on `127.0.0.1`.
- Ollama access must be limited to `127.0.0.1:11434`.
- `LOCAL_LLM_OLLAMA_TIMEOUT_SECONDS` may be used to set a controlled timeout.
- `LOCAL_LLM_OLLAMA_NUM_PREDICT` may be used to set controlled generation length.
- `LOCAL_LLM_OLLAMA_MODEL` may be used to choose an already installed local model.
- The run must not download models.
- The run must not pull models.
- The run must not execute `ollama pull`.
- The run must not call external networks.
- The run must not write `data/`, `output/`, or storage.
- The run must not call `score_text()` or `/rescore`.
- The run must not enter `qingtian-results`, `evidence_trace/latest`, or `scoring_basis/latest`.
- The run must not connect UI.
- The run must not trigger export chains.
- The run must stop after completion and wait for ChatGPT review.

## Step 174AI closure statement

Step 174AI records the Step 174AH timeout, `num_predict`, and model selection controls implementation as a fake-only tested preview transport milestone.

This document does not authorize automatic runtime smoke, timeout adjustment, model switching, UI integration, scoring-chain integration, export-chain integration, or production use.
