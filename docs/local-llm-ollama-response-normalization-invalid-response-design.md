# Local LLM Ollama Response Normalization and Invalid Response Design

## Purpose

This document defines the pre-implementation boundary for investigating local LLM Ollama response parsing, response normalization, and the stable `invalid_response` result observed in Step 174AJ.

This stage is design-only. It does not implement code, does not add tests, does not run pytest, does not start FastAPI, does not run Ollama, does not run `ollama serve`, does not access `127.0.0.1:11434`, does not call external networks, and does not download or pull models.

The document must not be interpreted as permission to immediately modify `normalize_ollama_response`, rerun runtime smoke, connect UI, connect scoring chains, connect export chains, or use the preview path for production scoring.

## Baseline inherited from Step 174AJ and Step 174AK

Step 174AJ completed a controlled runtime smoke for timeout and model controls:

- real transport reached local Ollama through `127.0.0.1:11434`
- `/api/tags` returned HTTP `200` and valid JSON
- local model count was `7`
- the model list included:
  - `qwen3-next:80b-a3b-instruct-q8_0`
  - `qwen3-coder:30b`
  - `deepseek-r1:32b`
  - `qwen3:30b`
  - `qwen3:14b`
  - `qwen3:8b`
  - `qwen3:0.6b`
- lightweight model selection chose `qwen3:0.6b`
- `LOCAL_LLM_OLLAMA_TIMEOUT_SECONDS=30`
- `LOCAL_LLM_OLLAMA_NUM_PREDICT=8`
- `LOCAL_LLM_OLLAMA_MODEL=qwen3:0.6b`
- FastAPI used `127.0.0.1:18746`
- endpoint was `POST /local-llm/preview-mock`
- all three feature flags were enabled:
  - `LOCAL_LLM_PREVIEW_MOCK_API_ENABLED=true`
  - `LOCAL_LLM_OLLAMA_PREVIEW_ADAPTER_ENABLED=true`
  - `LOCAL_LLM_OLLAMA_REAL_TRANSPORT_ENABLED=true`
- the endpoint entered the real transport branch
- the response included `real_transport_enabled=true`
- the response returned HTTP `200`
- the response had `status=error`
- the response had `error_type=invalid_response`
- the response had `model=qwen3:0.6b`
- the response kept `preview_only=true`
- the response kept `no_write=true`
- the response kept `affects_score=false`

Step 174AK reviewed that runtime smoke and recorded that `invalid_response` is a stable preview failure, not a production scoring failure.

## Current invalid_response summary

The Step 174AJ result was:

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

This result proves that the real transport branch was reached and that a stable failure schema was returned. It does not prove that Ollama is unavailable, and it must not be treated as a production scoring failure.

## Possible response normalization causes

The `invalid_response` result may be related to one or more of these boundaries:

- the actual Ollama generate JSON field names
- the presence or absence of the `response` field
- whether `done=true` is present
- whether the response is empty after stripping whitespace
- whether the model output format differs from the adapter's current `_extract_response_content` assumptions
- how `stream=false` responses are parsed
- whether the transport receives a valid JSON object but no normalized text content
- whether Ollama returns an `error` field that should be normalized separately

These are hypotheses for a future investigation. This document does not authorize code changes or runtime sampling.

## Non-goals

This stage does not:

- modify `app/main.py`
- modify `app/engine/local_llm_ollama_preview_adapter.py`
- add tests
- modify tests
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
- connect `score_text()`
- connect `/rescore`
- enter `qingtian-results`
- enter `evidence_trace/latest`
- enter `scoring_basis/latest`
- write `app/storage.py`
- write `data/`
- write `output/`
- trigger DOCX, JSON, or Markdown formal export
- connect a real-model production scoring chain

## Allowed future investigation scope

A separately authorized future investigation may be allowed to design or implement limited response normalization changes if it keeps all of these constraints:

- remains default-off
- remains preview-only
- remains no-write
- keeps `affects_score=false`
- uses fake-only tests first
- does not run `ollama serve` during code implementation
- does not run real runtime smoke until a separate runtime stage
- does not save full model output
- records only minimal response structure summaries if runtime reporting is separately authorized
- keeps all scoring, UI, storage, and export paths disconnected

## Forbidden future investigation scope

Future response normalization work must not:

- call `score_text()`
- call `/rescore`
- write `qingtian-results`
- write `evidence_trace/latest`
- write `scoring_basis/latest`
- write `app/storage.py`
- write `data/`
- write `output/`
- connect UI
- trigger DOCX export
- trigger JSON export
- trigger Markdown formal export
- save full model output
- turn preview output into formal scoring evidence
- download models
- pull models
- execute `ollama pull`
- access external networks
- silently treat timeout as success
- silently treat transport failure as success
- silently treat model unavailable as success

## Response sample handling boundary

If a later authorized runtime stage records real response evidence, it must follow these boundaries:

- do not save full model long-text output
- do not write `data/`
- do not write `output/`
- do not write storage
- do not write `qingtian-results`
- do not write `evidence_trace/latest`
- do not write `scoring_basis/latest`
- do not use model output as formal scoring basis
- only record a minimal response structure summary in the smoke report
- response summary may include field names
- response summary may include HTTP status
- response summary may include `done`
- response summary may include `model`
- response summary may include `error_type`
- response summary may include response length
- response summary may include whether normalized text was empty
- do not record real tender files
- do not record real bid files
- do not record real scoring text
- do not trigger DOCX, JSON, or Markdown formal export

Any future response sample handling must be part of an explicitly authorized runtime smoke stage, not a code implementation stage.

## Normalization rule design boundary

Any future `normalize_ollama_response` design must preserve these rules:

- normalize only preview responses
- return `preview_only=true` for every result
- return `no_write=true` for every result
- return `affects_score=false` for every result
- recognize successful `response` content when present and non-empty
- account for `done=true` when the rule is explicitly defined
- return `invalid_response` for invalid JSON
- return `invalid_response` or another stable failure for missing `response`, with the exact rule defined before implementation
- return `invalid_response` or another stable failure for empty-string `response`, with the exact rule defined before implementation
- normalize Ollama `error` fields into a stable failure
- do not misclassify timeout as `ok`
- do not misclassify `transport_failure` as `ok`
- do not misclassify `model_unavailable` as `ok`
- do not output formal scoring fields
- do not call `score_text`
- do not call `/rescore`
- do not write storage
- do not write `data/`
- do not write `output/`

The success and failure rules must be locked before code changes begin.

## Failure schema preservation

Future normalization changes must preserve stable failure schemas for at least:

- `invalid_response`
- `timeout`
- `transport_failure`
- `ollama_unreachable`
- `model_unavailable`

Every failure response must preserve:

- `adapter="ollama_preview"`
- stable `source`
- stable `status`
- `preview_only=true`
- `no_write=true`
- `affects_score=false`
- a bounded `error_type` or `reason`

No failure response may become a scoring result.

## Fake-only deterministic tests requirements

Future tests must be fake-only. They must not call real Ollama, start services, access external networks, download models, or pull models.

Future test matrix should cover:

1. fake response is legal Ollama generate JSON with `response` and `done=true`, returning `ok`
2. fake response includes `response` but `done=false`, returning either `ok` or a stable partial status under an explicitly defined future rule
3. fake response missing `response`, returning `invalid_response`
4. fake response with empty-string `response`, returning `invalid_response` or another stable failure under an explicitly defined future rule
5. fake response with `error`, returning stable failure
6. fake response is not a dict, returning `invalid_response`
7. fake response is invalid JSON, returning `invalid_response`
8. fake response looks like streaming fragments, not misclassified as a formal result
9. timeout still returns `timeout`
10. transport failure still returns `transport_failure`
11. model unavailable still returns `model_unavailable`
12. every result keeps `preview_only=true`
13. every result keeps `no_write=true`
14. every result keeps `affects_score=false`
15. no result calls `score_text` or `/rescore`
16. no result enters `qingtian-results`, `evidence_trace/latest`, or `scoring_basis/latest`
17. no result writes `data/`, `output/`, or storage
18. no result triggers export chains
19. no result connects UI
20. tests do not call real Ollama
21. tests do not start services
22. tests do not access external networks
23. tests do not download or pull models
24. existing mock API bridge tests continue to pass
25. existing adapter independent tests continue to pass

## Runtime smoke requirements after future implementation

Runtime smoke after any future implementation must be separately authorized.

Required runtime boundary:

- current ChatGPT conversation remains the controller
- current Codex nifei1227 conversation remains the executor
- 2nd window is used only for `ollama serve`
- FastAPI listens only on `127.0.0.1`
- Ollama is accessed only through `127.0.0.1:11434`
- no external network is called
- no model is downloaded
- no model is pulled
- `ollama pull` is not executed
- only minimal synthetic payloads are used
- only minimal response structure summaries are recorded
- full model output is not saved
- `data/`, `output/`, and storage are not written
- scoring, UI, and export chains remain disconnected

## No-write boundary

Future response normalization work must not write:

- `app/storage.py`
- `data/`
- `output/`
- `qingtian-results`
- `evidence_trace/latest`
- `scoring_basis/latest`

The preview response must continue to carry `no_write=true`.

## No-scoring-chain boundary

Future response normalization work must not:

- call `score_text()`
- call `/rescore`
- write scoring basis
- write evidence traces
- write QingTian results
- convert preview response text into formal scoring results

The preview response must continue to carry `affects_score=false`.

## No-UI and no-export boundary

Future response normalization work must not:

- connect UI
- trigger DOCX export
- trigger JSON export
- trigger Markdown formal export
- generate formal review deliverables

The preview response must continue to carry `preview_only=true`.

## Failure and rollback boundary

If a future response normalization investigation fails:

- do not directly connect scoring to make the run pass
- do not directly connect UI to make output visible
- do not directly connect export chains to preserve output
- do not write storage, `data/`, or `output/`
- stop any FastAPI service if it was started in an authorized runtime stage
- record command, flags, request summary, response summary, and git status
- if the worktree remains clean, report the result without rollback
- if unexpected file changes appear, report the change list and wait for ChatGPT review
- do not run `git clean`
- do not push main

## Future implementation acceptance criteria

Before any future implementation, the task must explicitly define:

- that Step 174AL design has been archived
- allowed file scope
- whether `app/engine/local_llm_ollama_preview_adapter.py` may be modified
- whether `app/main.py` may be modified
- whether `tests/test_local_llm_ollama_preview_adapter.py` may be modified
- whether `tests/test_local_llm_preview_mock_api_bridge.py` may be modified
- whether any new test file may be added
- exact `normalize_ollama_response` success rules
- exact `normalize_ollama_response` failure rules
- whether `done=false` is success, partial, or stable failure
- whether empty `response` is `invalid_response` or another stable failure
- how Ollama `error` fields are normalized
- that full model output must not be saved
- that tests must be fake-only
- that code implementation stage must not run `ollama serve`
- that runtime smoke stage is the only stage allowed to use the 2nd window
- that models must not be downloaded or pulled
- that `data/`, `output/`, and storage must not be written
- that scoring chains must remain disconnected
- that UI must remain disconnected
- that export chains must remain disconnected
- that main must not be pushed
- that completion must stop for ChatGPT review

## Step 174AL closure statement

Step 174AL records the response parsing and normalization investigation boundary for the stable `invalid_response` observed in Step 174AJ with `qwen3:0.6b`, timeout `30`, and `num_predict` `8`.

This document does not authorize immediate code changes, test changes, runtime smoke, Ollama execution, response parser changes, UI integration, scoring-chain integration, export-chain integration, storage writes, or production use.
