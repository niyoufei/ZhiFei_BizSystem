# Local LLM Ollama Preview Adapter Stage Review

## 1 Purpose

This document reviews the Step 174P implementation of the independent local LLM Ollama preview adapter.

Step 174P completed an isolated adapter and deterministic tests only. It did not connect an endpoint, did not connect UI, did not connect scoring, did not connect storage, did not connect exports, did not start a service, did not run Ollama, and did not call external networks.

This document is a stage review and closure record. It does not authorize Step 174R, endpoint integration, UI integration, production scoring integration, real Ollama smoke, or real model use.

## 2 Baseline Before Step 174P

- worktree: `/Users/youfeini/Desktop/ZhiFei_BizSystem-local-llm-clean`
- branch: `local-llm-integration-clean`
- pre-implementation design tag: `v0.1.47-local-llm-ollama-preview-adapter-guard-test-design`
- Step 174P result commit: `4af945e1b23f99d7a70db4652c01baeb2421a6f2`
- Step 174P result tag: `v0.1.48-local-llm-ollama-preview-adapter`

Step 174P was authorized to add only an independent adapter file and deterministic adapter tests. It was not authorized to modify `app/main.py`, `app/storage.py`, the existing mock helper, existing tests, guard files, UI files, export-chain files, `data/`, or `output/`.

## 3 Files Added In Step 174P

Step 174P added exactly these implementation files:

- `app/engine/local_llm_ollama_preview_adapter.py`
- `tests/test_local_llm_ollama_preview_adapter.py`

No endpoint file was connected as part of this stage. The adapter remains independent and is not imported by `app/main.py`.

## 4 Adapter Function Summary

The adapter file added these primary functions:

- `is_ollama_preview_enabled(value: str | None) -> bool`
- `build_disabled_response(...) -> dict`
- `build_failure_response(...) -> dict`
- `normalize_ollama_response(...) -> dict`
- `run_ollama_preview(...) -> dict`

The adapter also defines stable boundary constants, forbidden exact keys, an injected client type, and internal helpers for response construction, request validation, forbidden-key scanning, content extraction, fallback construction, and optional text normalization.

`run_ollama_preview()` supports fake client / transport injection. This is the mechanism used by deterministic tests so that tests do not need real Ollama, network access, a service process, or a model runtime.

## 5 Default-Off Behavior Review

The adapter remains default-off.

`is_ollama_preview_enabled()` returns enabled only for explicit true-like values:

- `true`
- `1`
- `yes`
- `on`

Absent, empty, false-like, or unknown values keep the adapter disabled. When disabled, `run_ollama_preview()` returns the stable disabled response and does not call the supplied client.

The disabled response preserves:

- `adapter=ollama_preview`
- `source=ollama_preview_adapter`
- `status=disabled`
- `reason=feature_flag_disabled`
- `preview_only=true`
- `no_write=true`
- `affects_score=false`

## 6 Preview-Only And No-Write Review

The adapter keeps preview-only and no-write semantics in disabled, failure, and success responses.

Every response path is required to preserve:

- `preview_only=true`
- `no_write=true`
- `affects_score=false`
- `adapter=ollama_preview`
- `source=ollama_preview_adapter`

The adapter does not write `app/storage.py`, does not write `data/`, does not write `output/`, and does not persist official evaluation artifacts.

The adapter does not affect formal scoring results and is not a scoring engine.

## 7 Failure Response Behavior Review

The adapter can distinguish these runtime states:

- `disabled`
- `model_unavailable`
- `transport_failure`
- `timeout`
- `invalid_response`
- `ok`

Failure responses are stable preview-only structures. They include explicit `error_type` and `message` fields, preserve `preview_only=true`, preserve `no_write=true`, preserve `affects_score=false`, and avoid formal scoring result fields.

The adapter returns deterministic fallback content for failure paths where fallback is enabled. The fallback remains `mock_fallback`, preview-only, no-write, and non-scoring.

The adapter handles:

- missing or empty prompt as `invalid_request`
- missing or empty model as `model_unavailable`
- forbidden exact keys in metadata as `invalid_request`
- no configured client as `model_unavailable`
- `TimeoutError` as `timeout`
- `ConnectionError` as `model_unavailable`
- `OSError` as `transport_failure`
- invalid response shape as `invalid_response`
- valid injected response content as `ok`

## 8 Deterministic Tests Coverage

Step 174P added deterministic tests in:

- `tests/test_local_llm_ollama_preview_adapter.py`

The recorded test command was:

```bash
python3 -m pytest tests/test_local_llm_ollama_preview_adapter.py tests/test_local_llm_preview_mock.py tests/test_local_llm_preview_mock_api_bridge.py -q
```

The recorded result was:

```text
51 passed in 0.75s
```

The tests cover:

- feature flag absent returns disabled
- empty / false / `0` / `no` / `off` return disabled
- disabled state does not call the fake client
- disabled response preserves `preview_only=true`
- disabled response preserves `no_write=true`
- disabled response preserves `affects_score=false`
- true-like feature flag values are recognized
- enabled fake client success returns `ok`
- enabled success includes `preview_only=true`
- enabled success includes `no_write=true`
- enabled success includes `affects_score=false`
- enabled success excludes formal score fields
- timeout returns a stable failure
- connection and transport failures return stable failures
- invalid Ollama response shapes return stable failures
- same input and same fake response are deterministic
- empty prompt, missing prompt, missing model, and forbidden metadata return stable errors
- missing client returns `model_unavailable`
- failure response builder remains preview-only and no-write
- adapter source does not import forbidden modules or contain forbidden path fragments

The tests use fake clients and static source assertions. They do not require real Ollama and do not start services.

## 9 Explicit Non-Integrations

Step 174P did not integrate the adapter with:

- `app/main.py`
- `POST /local-llm/preview-mock`
- `app/storage.py`
- `app/engine/local_llm_preview_mock.py`
- `score_text()`
- `/rescore`
- `qingtian-results`
- `evidence_trace/latest`
- `scoring_basis/latest`
- UI
- DOCX official export
- JSON official export
- Markdown official export
- `data/`
- `output/`
- real Ollama runtime
- OpenAI
- Spark
- Gemini
- production scoring or production model chains

The current `POST /local-llm/preview-mock` endpoint still remains a mock-only helper path and must continue to use `app.engine.local_llm_preview_mock` unless a later stage explicitly authorizes an endpoint integration.

## 10 No-Write Boundary Verification

Step 174P did not write `data/`, did not write `output/`, and did not write storage.

The adapter code does not import `app.storage`, does not call storage helpers, does not open files, does not use `Path(`, and does not contain `data/` or `output/` path fragments.

No DOCX / JSON / Markdown official export path was connected or triggered.

If any future stage observes changes under `data/`, `output/`, storage, qingtian-results, evidence trace, or scoring basis paths, it must stop and report the change list before any commit, tag, or push.

## 11 No-Real-Model Boundary Verification

Step 174P did not run Ollama.

Step 174P did not start a service, did not open a browser, did not call external networks, and did not call OpenAI / Spark / Gemini.

The adapter supports fake client injection for deterministic testing. That support is not production model integration. It only proves that the adapter can normalize injected responses and failures without depending on a real runtime.

The adapter does not start `ollama serve`, does not manage subprocesses, and does not listen on any port.

## 12 No-Scoring-Chain Boundary Verification

Step 174P did not call `score_text()`.

Step 174P did not connect `/rescore`.

Step 174P did not enter qingtian-results, `evidence_trace/latest`, or `scoring_basis/latest`.

The adapter does not import scoring engines and must not be treated as part of the production scoring main chain.

## 13 Remaining Risks

- The adapter exists, but it is not connected to any API endpoint.
- The adapter has not been verified against a real Ollama service.
- The adapter is currently verified only through fake client / deterministic tests.
- The adapter does not mean real local model integration is complete.
- The adapter cannot be used for production scoring.
- The adapter is not connected to UI and cannot be triggered from the frontend.
- The adapter is not connected to the export chain and cannot generate DOCX / JSON / Markdown official results.
- The adapter is not connected to qingtian-results, evidence trace, or scoring basis outputs.
- A future endpoint connection must be separately authorized and must remain feature-flagged, default-off, preview-only, and no-write.
- A future real Ollama stage must be separately authorized and must explicitly state whether `2号窗口` is enabled to run `ollama serve`.
- A future service smoke must be limited to `127.0.0.1` loopback and must not access external networks.
- If any future stage creates `data/`, `output/`, or storage changes, it must stop and report before cleanup or commit.

## 14 Required Next-Stage Guard Before Step 174R

Before any Step 174R work, the next-stage instruction must explicitly state:

- whether endpoint integration is allowed
- whether `app/main.py` may be modified
- whether a new API bridge is in scope
- whether the adapter may be imported anywhere
- whether real Ollama remains forbidden
- whether service smoke is allowed
- whether `2号窗口` is used for `ollama serve`
- the exact feature flag behavior
- the exact allowed file list
- the exact deterministic tests to run
- the exact no-write checks for `data/`, `output/`, and storage
- the exact stop conditions for scoring-chain, export-chain, UI, and real-model drift

No future stage should directly enter real Ollama smoke, endpoint integration, UI integration, production scoring, storage writes, or export writes without a separate boundary and review.

## 15 Step 174Q Closure Statement

Step 174Q records the Step 174P adapter implementation as an isolated, default-off, preview-only, no-write adapter stage.

It confirms that the adapter is not connected to `app/main.py`, is not connected to `POST /local-llm/preview-mock`, does not affect formal scoring results, and remains outside storage, data, output, UI, export, qingtian-results, evidence trace, and scoring basis chains.

This document does not authorize Step 174R implementation, endpoint integration, service smoke, real Ollama, UI integration, export integration, or scoring-chain integration.
