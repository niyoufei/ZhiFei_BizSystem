# Local LLM Ollama Real Response Minimal Sampling Design

## Purpose

This document defines the boundary for a future minimal real-response structure sampling step for the local LLM Ollama preview path.

The current stage only designs the sampling boundary. It does not execute sampling, does not modify code, does not add or modify tests, does not run pytest, does not start FastAPI, does not run Ollama, does not run `ollama serve`, does not access `127.0.0.1:11434`, does not call external networks, and does not download or pull models.

This document must not be interpreted as permission to immediately sample real responses, modify parsing logic, rerun runtime smoke, connect UI, connect scoring chains, connect export chains, or use the preview path for production scoring.

## Baseline Inherited From Step 174AO and Step 174AP

Step 174AO completed a controlled response normalization runtime smoke:

- 2nd window was enabled for `ollama serve`.
- Ollama was reachable through `127.0.0.1:11434`.
- `/api/tags` returned HTTP `200` and valid JSON.
- local model count was `7`.
- `qwen3:0.6b` existed locally.
- selected model was `qwen3:0.6b`.
- `LOCAL_LLM_OLLAMA_TIMEOUT_SECONDS=30`.
- `LOCAL_LLM_OLLAMA_NUM_PREDICT=8`.
- FastAPI listened only on `127.0.0.1:18747`.
- endpoint was `POST /local-llm/preview-mock`.
- all three feature flags were enabled:
  - `LOCAL_LLM_PREVIEW_MOCK_API_ENABLED=true`
  - `LOCAL_LLM_OLLAMA_PREVIEW_ADAPTER_ENABLED=true`
  - `LOCAL_LLM_OLLAMA_REAL_TRANSPORT_ENABLED=true`
- `LOCAL_LLM_OLLAMA_MODEL=qwen3:0.6b`.
- endpoint entered the real transport branch.
- response included `real_transport_enabled=true`.
- real transport accessed `127.0.0.1:11434`.
- response HTTP status was `200`.
- response normalization status did not become `ok`.
- response status was `error`.
- response `error_type` was `invalid_response`.
- response kept `preview_only=true`.
- response kept `no_write=true`.
- response kept `affects_score=false`.
- FastAPI was stopped after the request.
- port `18747` had no listener after shutdown.

Step 174AP reviewed that result and recorded that fake-only canonical response normalization does not yet cover the observed real runtime response structure.

## Current invalid_response Status

The current runtime result is a stable preview failure:

```json
{
  "status": "error",
  "error_type": "invalid_response",
  "message": "Ollama response did not contain non-empty content.",
  "model": "qwen3:0.6b",
  "preview_only": true,
  "no_write": true,
  "affects_score": false
}
```

This result does not mean Ollama is unavailable. It means the real transport branch reached local Ollama, but the adapter did not normalize the observed runtime response into non-empty preview content.

The likely remaining investigation space is the minimal real response structure: field names, field types, JSON decoding result, `response` field presence, `done` state, `error` field presence, and whether the `response` content is empty or whitespace.

## Sampling Objective

A future sampling step may be authorized only to collect a minimal response structure summary that explains why the real `qwen3:0.6b` runtime response still becomes `invalid_response`.

The objective is not to preserve model output. The objective is only to understand structure:

- whether Ollama returned JSON
- which top-level fields were present
- which field types were present
- whether `response` existed
- whether `response` was empty or whitespace
- whether `done` existed and what boolean-like value it had
- whether `error` existed
- whether duration or token-count numeric fields existed
- whether normalization changed the response to `ok` or kept it as `invalid_response`

## Non-goals

The future sampling step must not:

- modify `app/main.py`
- modify `app/storage.py`
- modify `app/engine/local_llm_preview_mock.py`
- modify `app/engine/local_llm_ollama_preview_adapter.py`
- add tests
- modify tests
- run pytest
- download models
- pull models
- execute `ollama pull`
- call external networks
- call OpenAI
- call Spark
- call Gemini
- connect `score_text()`
- connect `/rescore`
- write `qingtian-results`
- write `evidence_trace/latest`
- write `scoring_basis/latest`
- write `app/storage.py`
- write `data/`
- write `output/`
- connect UI
- trigger DOCX, JSON, or Markdown formal export
- connect a real-model production scoring chain
- save full model output
- save the full Ollama JSON response body

## Allowed Minimal Response Summary Fields

If a future step is separately authorized to sample, it may record only these minimal fields:

- HTTP status code
- whether the response body is JSON
- top-level field name list
- top-level field type summary
- whether a `response` field exists
- `response` field type
- `response` field length
- whether `response` is empty or whitespace
- `response` short prefix of at most 20 characters, only if it does not contain real business text
- whether a `done` field exists
- `done` field value
- whether a `model` field exists
- `model` field value
- whether an `error` field exists
- `error` field type
- `error` short summary
- whether numeric fields such as `total_duration` exist
- whether numeric fields such as `eval_count` exist
- whether JSON decoding succeeded
- normalization before/after status summary

The sampling report must not record the complete `response` text.

The sampling report must not record long generated text.

The sampling report must not record real tender, bid, or scoring content.

## Forbidden Response Data

Future sampling must not record:

- full model output text
- full Ollama JSON response body
- real tender document content
- real bid document content
- real scoring text
- generated content that can be used as formal scoring basis
- DOCX formal export content
- JSON formal export content
- Markdown formal export content
- `qingtian-results` data
- `evidence_trace/latest` data
- `scoring_basis/latest` data
- any new `storage` content
- any new `data/` content
- any new `output/` content
- external network request content
- OpenAI response content
- Spark response content
- Gemini response content

The sampling step must not persist raw model output for later parsing.

## Sampling Runtime Boundary

Future sampling may run only if separately authorized by ChatGPT.

Required runtime boundary for a future sampling step:

- continue using the current ChatGPT conversation as controller.
- continue using the current Codex nifei1227 conversation as executor.
- enable the 2nd window only to run `ollama serve`.
- 2nd window must not execute git commands.
- 2nd window must not run pytest.
- 2nd window must not modify code.
- 2nd window must not commit, tag, or push.
- Codex nifei1227 may perform repository checks.
- Codex nifei1227 may start FastAPI only if the future step explicitly allows it.
- Codex nifei1227 may directly call Ollama `/api/generate` only if the future step explicitly allows it.
- if FastAPI starts, it must listen only on `127.0.0.1`.
- Ollama access must remain limited to `127.0.0.1:11434`.
- model must be the already installed local `qwen3:0.6b`, unless ChatGPT separately authorizes another model.
- timeout may continue to use `30`.
- `num_predict` may continue to use `8`.
- no model download is allowed.
- no model pull is allowed.
- `ollama pull` is forbidden.
- external network access is forbidden.

Only one write-capable Codex window may operate on this repository at a time.

## No-write Boundary

Future sampling must not write:

- `app/storage.py`
- `data/`
- `output/`
- storage files
- `qingtian-results`
- `evidence_trace/latest`
- `scoring_basis/latest`
- UI files
- DOCX formal export artifacts
- JSON formal export artifacts
- Markdown formal export artifacts

Only a future authorized sampling report may be added, and that report may contain only minimal response structure summaries.

If any unexpected `data/`, `output/`, storage, `qingtian-results`, `evidence_trace`, or `scoring_basis` change appears, the task must stop and report the change list. It must not clean or delete files.

## No-scoring-chain Boundary

Future sampling must not:

- call `score_text()`
- call `/rescore`
- connect `qingtian-results`
- connect `evidence_trace/latest`
- connect `scoring_basis/latest`
- transform model output into a formal score
- treat preview output as scoring evidence
- affect any formal evaluation result

Any sampled response remains preview-only and advisory-only.

## No-UI and No-export Boundary

Future sampling must not:

- connect UI
- add frontend controls
- display sampled model output in UI
- trigger DOCX formal export
- trigger JSON formal export
- trigger Markdown formal export
- produce formal report artifacts from model output

The sampling objective is limited to response structure diagnosis.

## Failure and Rollback Boundary

If future sampling fails:

- stop FastAPI first if it was started.
- record the FastAPI PID and stop method.
- leave the 2nd window state to ChatGPT instruction.
- do not modify parser logic.
- do not modify adapter code.
- do not modify API bridge code.
- do not modify tests.
- do not download or pull models.
- do not write `data/` or `output/`.
- do not execute `git clean`.
- do not remove untracked files.
- record commands, environment variables, request summary, minimal response summary, and git status.
- if the worktree is clean, report the sampling result without rollback.
- if unexpected files changed, report the changed-file list and wait for ChatGPT review.

The stable rollback anchor before this design stage is:

```text
v0.1.74-local-llm-ollama-response-normalization-runtime-smoke-stage-review
```

## Future Sampling Acceptance Criteria

A future real response structure minimal-summary sampling step must explicitly satisfy:

- current ChatGPT conversation remains the controller.
- current Codex nifei1227 conversation remains the executor.
- 2nd window is enabled only to run `ollama serve`.
- 2nd window does not execute git.
- 2nd window does not run pytest.
- 2nd window does not modify files.
- 2nd window does not commit, tag, or push.
- Codex nifei1227 performs repository checks.
- Codex nifei1227 performs FastAPI startup or direct loopback requests only if explicitly allowed.
- Codex nifei1227 writes only the authorized minimal-summary report.
- Codex nifei1227 performs commit, tag, and push only if the future step explicitly requires them.
- same repository has only Codex nifei1227 as a write-capable window.
- FastAPI, if started, listens only on `127.0.0.1`.
- Ollama is accessed only through `127.0.0.1:11434`.
- model is `qwen3:0.6b`.
- timeout may be `30`.
- `num_predict` may be `8`.
- no model is downloaded.
- no model is pulled.
- `ollama pull` is not executed.
- external networks are not called.
- full model output is not saved.
- full Ollama JSON body is not saved.
- `data/`, `output/`, and storage are not written.
- `score_text` and `/rescore` remain disconnected.
- `qingtian-results`, `evidence_trace`, and `scoring_basis` remain disconnected.
- UI remains disconnected.
- DOCX, JSON, and Markdown formal export chains remain disconnected.
- completion waits for ChatGPT review before any next step.

## Future Implementation Path After Sampling

If a future sampling report identifies a normalization gap, code changes still require a separate implementation authorization.

Any later implementation must:

- define the allowed file scope before changes.
- use fake-only deterministic tests first.
- keep runtime smoke separate from code implementation.
- keep `preview_only=true`.
- keep `no_write=true`.
- keep `affects_score=false`.
- preserve stable failure schemas.
- avoid saving full model output.
- avoid writing `data/`, `output/`, or storage.
- avoid scoring-chain, UI, and export-chain integration.

This design does not authorize implementation.

## Step 174AQ Closure Statement

Step 174AQ defines the boundary for future real Ollama response structure minimal-summary sampling. The current stage did not sample, did not start services, did not run Ollama, did not access `127.0.0.1:11434`, did not modify code, did not add or modify tests, did not write `data/` or `output/`, and did not connect UI, scoring chains, export chains, or production model paths.
