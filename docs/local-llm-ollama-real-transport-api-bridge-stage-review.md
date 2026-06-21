# Local LLM Ollama Real Transport API Bridge Stage Review

## 1 Purpose

This document reviews Step 174AB, which completed the controlled real Ollama transport code path for the local LLM preview API bridge.

The stage remains implementation-only plus fake-only deterministic verification. It does not mean the FastAPI endpoint has passed a real Ollama runtime smoke test, and it must not be interpreted as permission to automatically enter real Ollama smoke.

## 2 Baseline Before Step 174AB

- worktree: `/Users/youfeini/Desktop/ZhiFei_BizSystem-local-llm-clean`
- branch: `local-llm-integration-clean`
- baseline before Step 174AB: `872a0b4c0b6c17ad357e360fd34dad5abd8a5e2e`
- Step 174AB commit: `b28a6103ec1b5f2d42b183bc8020b50feade36b1`
- Step 174AB tag: `v0.1.60-local-llm-ollama-real-transport-api-bridge`
- endpoint: `POST /local-llm/preview-mock`

Step 174AB was based on the prior real transport design and guard/test design:

- `docs/local-llm-ollama-real-transport-api-bridge-design.md`
- `docs/local-llm-ollama-real-transport-guard-test-design.md`

## 3 Files Changed In Step 174AB

Actual modified files were limited to:

- `app/main.py`
- `app/engine/local_llm_ollama_preview_adapter.py`
- `tests/test_local_llm_ollama_preview_adapter.py`
- `tests/test_local_llm_preview_mock_api_bridge.py`

No new files were added during Step 174AB.

`pre-commit` automatically formatted only files inside the allowed Step 174AB file scope. No disallowed file was modified by the hook.

## 4 Function And Feature Flag Summary

Step 174AB added or adjusted the real transport support path around these functions and constants:

- `LOCAL_LLM_OLLAMA_REAL_TRANSPORT_ENABLED`
- `LOCAL_LLM_OLLAMA_MODEL`
- `is_ollama_real_transport_enabled`
- `validate_ollama_preview_boundary`
- `select_local_ollama_model`
- `fetch_local_ollama_models`
- `build_real_ollama_preview_client`
- `_validate_local_ollama_base_url`
- `_send_json_request`
- `_bounded_num_predict`
- `_local_llm_ollama_real_transport_enabled`

The endpoint feature flag is:

```text
LOCAL_LLM_PREVIEW_MOCK_API_ENABLED
```

The adapter feature flag is:

```text
LOCAL_LLM_OLLAMA_PREVIEW_ADAPTER_ENABLED
```

The real transport feature flag is:

```text
LOCAL_LLM_OLLAMA_REAL_TRANSPORT_ENABLED
```

## 5 Real Transport Boundary Review

The feature flag hierarchy after Step 174AB is:

1. If `LOCAL_LLM_PREVIEW_MOCK_API_ENABLED` is not enabled, the endpoint returns disabled directly.
2. If the endpoint flag is disabled, the endpoint does not check adapter or real transport state.
3. If endpoint is enabled but `LOCAL_LLM_OLLAMA_PREVIEW_ADAPTER_ENABLED` is not enabled, the endpoint keeps the mock-only helper path.
4. If adapter is enabled but `LOCAL_LLM_OLLAMA_REAL_TRANSPORT_ENABLED` is not enabled, the endpoint keeps the no-real-model safety path.
5. Only when all three flags are enabled may the endpoint construct the localhost Ollama transport.

The transport boundary is intentionally narrow:

- only `http://127.0.0.1:11434` is allowed
- external URLs are not allowed
- `0.0.0.0` is not allowed
- OpenAI / Spark / Gemini are not allowed
- model downloads are not allowed
- model pulls are not allowed
- `ollama pull` is not allowed

Step 174AB did not run the transport against a real Ollama process.

## 6 Model Selection Boundary Review

The model name source order is:

1. `LOCAL_LLM_OLLAMA_MODEL`
2. local `/api/tags` first installed model
3. stable `model_unavailable` if no model is configured or installed

If `LOCAL_LLM_OLLAMA_MODEL` is set, it has priority over `/api/tags`.

When `LOCAL_LLM_OLLAMA_MODEL` is not set, the code path may read local `/api/tags` only after all three feature flags permit real transport. The intended endpoint remains local-only and preview-only.

No production scoring model was hardcoded.

## 7 Timeout And Generation Limit Review

The adapter timeout boundary is:

```text
DEFAULT_TIMEOUT_SECONDS = 5.0
```

The real `/api/generate` path uses:

```text
stream=false
num_predict <= 128
```

Timeouts return stable `timeout` failure responses and do not fall back into scoring, storage, UI, export, or remote model providers.

## 8 Failure Schema Review

The failure and status schema covers:

- `disabled`
- `ollama_unreachable`
- `model_unavailable`
- `timeout`
- `invalid_response`
- `transport_failure`
- `ok`

Every adapter result must preserve:

- `preview_only=true`
- `no_write=true`
- `affects_score=false`

Failure results include stable `error_type` and `message` fields. Success responses include bounded advisory content and do not include long raw model output.

## 9 Deterministic Fake-Only Test Coverage

Step 174AB used fake-only deterministic tests.

Test command:

```bash
python3 -m pytest tests/test_local_llm_ollama_preview_adapter.py tests/test_local_llm_preview_mock_api_bridge.py tests/test_local_llm_preview_mock.py -q
```

Result:

```text
91 passed in 0.93s
```

Coverage included:

- endpoint flag absent or false-like values do not check adapter or real transport
- adapter flag absent or false-like values keep mock-only behavior
- real transport flag absent or false-like values do not construct real transport
- fake empty tags return `model_unavailable`
- fake tags with local model select the first local model
- `LOCAL_LLM_OLLAMA_MODEL` has priority
- fake unreachable returns `ollama_unreachable`
- fake timeout returns `timeout`
- fake model unavailable returns `model_unavailable`
- fake invalid response returns `invalid_response`
- fake success returns `ok`
- all responses keep `preview_only=true`
- all responses keep `no_write=true`
- all responses keep `affects_score=false`
- same input and same fake response remain deterministic
- no fake-only test requires real Ollama
- no fake-only test starts FastAPI service
- no fake-only test accesses external networks

## 10 Explicit Non-Integrations

Step 174AB did not:

- run Ollama
- run `ollama serve`
- start FastAPI service
- truly access `127.0.0.1:11434`
- call external networks
- download models
- pull models
- execute `ollama pull`
- call OpenAI
- call Spark
- call Gemini
- connect UI
- connect production scoring
- trigger DOCX / JSON / Markdown official export
- modify release guard or smoke guard

The code path exists, but real runtime behavior remains unverified after Step 174AB.

## 11 No-Write Boundary Verification

Step 174AB did not modify:

- `app/storage.py`
- `app/engine/local_llm_preview_mock.py`
- `tests/test_local_llm_preview_mock.py`
- `data/`
- `output/`
- release guard files
- smoke guard files
- UI files
- export-chain files
- `ops_agents` files

The implementation does not write `data/`, `output/`, or storage as part of the preview transport path.

## 12 No-Scoring-Chain Boundary Verification

Step 174AB did not connect real transport to:

- `score_text()`
- `score_text_v2`
- `/rescore`
- qingtian-results
- `evidence_trace/latest`
- `scoring_basis/latest`
- production score reports
- production evaluation persistence

Adapter output remains advisory and must not be interpreted as a formal score.

## 13 No-UI And No-Export Verification

Step 174AB did not connect:

- UI
- browser workflows
- DOCX official export
- JSON official export
- Markdown official export
- export bundles

The endpoint remains backend-only and flag-gated.

## 14 Remaining Risks

- The real transport code path now exists, but it has not passed a real runtime smoke test.
- Fake-only tests do not prove that this machine's current Ollama runtime call will succeed through the new endpoint path.
- Step 174X proved Ollama itself was reachable before Step 174AB, but the Step 174AB transport path has not yet been exercised against real Ollama.
- The current state cannot be used for production scoring.
- The current state is not UI-enabled.
- The current state is not export-enabled.
- The current state is not connected to qingtian-results, `evidence_trace/latest`, or `scoring_basis/latest`.
- The current state does not write storage, `data/`, or `output/`.
- A future real smoke must be separately designed or explicitly authorized.
- A future real smoke must use a 2nd window for `ollama serve`.
- A future real smoke must not download or pull models.
- A future smoke failure must not be repaired by directly modifying scoring chain, UI, export chain, or storage.
- If a future smoke creates any `data/`, `output/`, or storage changes, execution must stop and report the changed paths.

## 15 Required Next-Stage Guard Before Real Ollama Smoke

Before any real Ollama transport smoke, the next stage must explicitly confirm:

- current ChatGPT conversation remains the controller
- current Codex nifei1227 conversation remains the execution window
- a 2nd window must run only `ollama serve`
- the 2nd window must not run git, pytest, code edits, commit, tag, or push
- only Codex nifei1227 may perform repository checks, FastAPI start, loopback requests, report document creation, commit, tag, and push
- only one Codex window may perform write operations in this repository at a time
- FastAPI must listen only on `127.0.0.1`
- Ollama must be accessed only through `127.0.0.1:11434`
- no model download
- no model pull
- no `ollama pull`
- no external network calls
- no writes to `data/`, `output/`, or storage
- no `score_text()` or `/rescore`
- no qingtian-results, `evidence_trace/latest`, or `scoring_basis/latest`
- no UI
- no export chain
- completion must stop for ChatGPT review

## 16 Step 174AC Closure Statement

This Step 174AC document is a docs-only review of the Step 174AB real transport implementation stage.

It records the implemented code path, feature flag hierarchy, localhost-only transport boundary, model selection boundary, timeout and generation limits, failure schema, fake-only deterministic test result, explicit non-integrations, remaining risks, and next-stage guard requirements.

This document does not authorize automatic real Ollama smoke, service start, UI integration, scoring-chain integration, export-chain integration, storage writes, model downloads, or model pulls.
