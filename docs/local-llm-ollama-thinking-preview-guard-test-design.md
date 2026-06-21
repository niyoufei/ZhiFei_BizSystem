# Local LLM Ollama Thinking Preview Guard and Test Design

## 1. Purpose

This document defines the Step 174AU guard and fake-only deterministic test design for a possible future `thinking` preview implementation in the local LLM Ollama preview adapter.

The current stage is docs-only. It designs guardrails and test expectations only. It does not implement code, does not add or modify tests, does not run pytest, does not start services, does not run Ollama, does not run `ollama serve`, and does not access `127.0.0.1:11434`.

This document must not be interpreted as permission to immediately modify `normalize_ollama_response`, connect UI, connect scoring chains, connect export chains, or use local LLM output for production scoring.

## 2. Baseline inherited from Step 174AT

Step 174AT recommended a dual-field strategy:

- prefer non-empty ordinary response content.
- consider `thinking` only when ordinary accepted content is empty.
- use `thinking` only as a bounded preview-only summary source.
- never save the full `thinking` text.
- preserve `preview_only=true`.
- preserve `no_write=true`.
- preserve `affects_score=false`.

The recommendation was based on Step 174AR and Step 174AS facts:

- `qwen3:0.6b` returned valid JSON.
- `response` existed but was empty.
- `thinking` existed.
- `done=true`.
- no `error` field existed.
- the current normalizer does not treat `thinking` as preview content.
- the current `invalid_response` result is a normalization-boundary result, not an Ollama reachability result.

## 3. Guard objective

The guard objective is to make any later implementation small, deterministic, preview-only, and auditable.

The guard must ensure:

- `thinking` can never become formal scoring evidence.
- `thinking` can never be persisted as raw long text.
- `thinking` can never enter `data/`, `output/`, storage, `qingtian-results`, `evidence_trace/latest`, or `scoring_basis/latest`.
- `thinking` can never trigger DOCX, JSON, or Markdown formal export.
- `thinking` can never connect UI in the same implementation step.
- fake-only deterministic tests come before runtime smoke.
- runtime smoke remains a separately authorized stage with the 2nd window boundary.

## 4. Allowed future file scope

For a future implementation stage, the preferred code scope is:

- `app/engine/local_llm_ollama_preview_adapter.py`

For a future fake-only test stage, the preferred test scope is:

- `tests/test_local_llm_ollama_preview_adapter.py`

No other file should be modified unless ChatGPT separately authorizes it with an explicit file scope.

Future implementation should not require `app/main.py` changes. If a later proposal claims `app/main.py` must change, that must be reviewed and authorized separately before any edit.

## 5. Forbidden future file scope

Unless separately authorized, a future thinking-preview implementation must not modify:

- `app/main.py`
- `app/storage.py`
- `app/engine/local_llm_preview_mock.py`
- `tests/test_local_llm_preview_mock_api_bridge.py`
- `tests/test_local_llm_preview_mock.py`
- UI files
- DOCX export files
- JSON export files
- Markdown formal export files
- `data/`
- `output/`
- storage files

The implementation must not create new runtime artifacts to make tests pass.

## 6. Thinking field preview-only boundary

If `thinking` is used, it may be used only as a preview-only summary source.

Allowed derived information:

- `thinking` field existence.
- `thinking` field type.
- non-empty status after trimming.
- original text length.
- short bounded summary.
- boundary metadata saying the summary is preview-only and not scoring evidence.

Required output boundary:

- `preview_only=true`
- `no_write=true`
- `affects_score=false`
- `raw_response_included=false`

The full `thinking` text must not be returned, stored, logged by new code, written to files, or retained for later parsing.

## 7. Thinking field forbidden-use boundary

The `thinking` field must not:

- be used as a formal scoring basis.
- be written to `qingtian-results`.
- be written to `evidence_trace/latest`.
- be written to `scoring_basis/latest`.
- be written to `data/`.
- be written to `output/`.
- be written to storage.
- be written to DOCX formal export.
- be written to JSON formal export.
- be written to Markdown formal export.
- be connected to UI.
- call `score_text`.
- call `/rescore`.
- become part of a real-model production scoring chain.
- be preserved in full.

These restrictions apply to both implementation and tests.

## 8. Normalization decision matrix

A future implementation must define exact names and response shapes before code changes. The following matrix is the required decision basis.

| Case | Input shape | Required decision |
| --- | --- | --- |
| 1 | `response` non-empty, `done=true` | Return existing `ok` preview behavior. |
| 2 | `response` non-empty, `done=false` | Follow existing stable rules and do not mistake the result for formal scoring. |
| 3 | `response` empty, `thinking` non-empty | May return `thinking_preview` or `ok_preview`; exact status name must be confirmed during implementation authorization. |
| 4 | `response` whitespace-only, `thinking` non-empty | May return `thinking_preview` or `ok_preview`; exact status name must be confirmed during implementation authorization. |
| 5 | `response` missing, `thinking` non-empty | May return `thinking_preview` or stable failure; implementation stage must decide explicitly. |
| 6 | `response` empty, `thinking` empty | Keep `invalid_response`. |
| 7 | `response` missing, `thinking` missing | Keep `invalid_response`. |
| 8 | `thinking` is not a string | Return stable failure or record only controlled type summary; implementation stage must decide explicitly. |
| 9 | `thinking` is very long | Keep only length and short bounded summary; never save full text. |
| 10 | Ollama `error` field exists | Keep stable failure; never convert to `ok`. |
| 11 | timeout | Keep `timeout`. |
| 12 | transport failure | Keep `transport_failure`. |
| 13 | model unavailable | Keep `model_unavailable`. |

Any `thinking`-based state must remain preview-only and must not affect score.

## 9. Fake-only deterministic tests matrix

A future implementation must start with fake-only tests. Step 174AU does not add those tests.

Required tests:

1. `response` non-empty and `done=true`: returns `ok`.
2. `response` empty and `thinking` non-empty: returns a controlled preview-only summary state.
3. `response` whitespace-only and `thinking` non-empty: returns a controlled preview-only summary state.
4. `response` missing and `thinking` non-empty: returns the designed controlled state or stable failure.
5. `response` empty and `thinking` empty: returns `invalid_response`.
6. `response` missing and `thinking` missing: returns `invalid_response`.
7. `thinking` is not a string: returns stable failure or controlled type summary.
8. `thinking` is very long: does not save full text and keeps only length plus short summary.
9. every `thinking` path returns `preview_only=true`.
10. every `thinking` path returns `no_write=true`.
11. every `thinking` path returns `affects_score=false`.
12. every `thinking` path does not call `score_text` or `/rescore`.
13. every `thinking` path does not enter `qingtian-results`, `evidence_trace`, or `scoring_basis`.
14. every `thinking` path does not write `data/`, `output/`, or storage.
15. every `thinking` path does not trigger export chains.
16. every `thinking` path does not connect UI.
17. tests do not call real Ollama.
18. tests do not start services.
19. tests do not access `127.0.0.1:11434`.
20. tests do not access external networks.
21. existing mock API bridge tests continue to pass.
22. existing adapter tests continue to pass.

The test implementation should prefer injected fake responses and existing adapter test helpers. It must not rely on runtime model availability.

## 10. No-write verification

Future tests and implementation must prove that thinking-preview handling does not write:

- `app/storage.py`
- `data/`
- `output/`
- storage files
- `qingtian-results`
- `evidence_trace/latest`
- `scoring_basis/latest`
- DOCX export artifacts
- JSON export artifacts
- Markdown formal export artifacts

The future implementation should remain an in-memory normalizer behavior only.

## 11. No-scoring-chain verification

Future tests and implementation must prove that thinking-preview handling remains outside the scoring chain.

Required checks:

- no `score_text` call.
- no `/rescore` call.
- no `qingtian-results` path.
- no `evidence_trace/latest` path.
- no `scoring_basis/latest` path.
- no formal scoring fields in returned preview payloads.
- `affects_score=false` on every thinking-preview path.

The existing forbidden-fragment style source test should remain in force, and any future changes must not weaken it.

## 12. No-UI and no-export verification

Future tests and implementation must prove:

- no UI file is modified.
- no frontend control is added.
- no `thinking` text is displayed in UI.
- no DOCX export is triggered.
- no JSON export is triggered.
- no Markdown formal export is triggered.
- no export-chain artifact is written.

Any future UI or export behavior must be a separate authorized step after this local preview boundary is stable.

## 13. Runtime smoke acceptance criteria

Runtime smoke is not authorized by this document.

If a later runtime smoke is authorized, it must satisfy all of these conditions:

- fake-only tests have already passed.
- ChatGPT explicitly authorizes runtime smoke.
- the 2nd window is enabled only for `ollama serve`.
- the 2nd window does not run git.
- the 2nd window does not run pytest.
- the 2nd window does not modify code.
- the 2nd window does not commit, tag, or push.
- Codex nifei1227 keeps repository writes scoped to the authorized files.
- FastAPI, if started, listens only on `127.0.0.1`.
- Ollama access remains limited to `127.0.0.1:11434`.
- no model download or pull occurs.
- no `ollama pull` occurs.
- no external provider is called.
- no `data/`, `output/`, or storage write occurs.
- no UI, scoring-chain, or export-chain integration occurs.
- completion stops for ChatGPT review.

## 14. Future implementation acceptance criteria

A future code implementation may start only after Step 174AU is reviewed and accepted.

Minimum acceptance criteria before implementation:

- Step 174AU design has been archived.
- ChatGPT explicitly allows modifying `app/engine/local_llm_ollama_preview_adapter.py`.
- ChatGPT explicitly states whether `tests/test_local_llm_ollama_preview_adapter.py` may be modified.
- `app/main.py` remains forbidden unless separately authorized.
- `app/storage.py` remains forbidden.
- `data/`, `output/`, and storage writes remain forbidden.
- scoring-chain integration remains forbidden.
- UI integration remains forbidden.
- export-chain integration remains forbidden.
- tests are fake-only.
- the code implementation stage does not run `ollama serve`.
- the code implementation stage does not run Ollama.
- runtime smoke, if needed, is a later separately authorized stage that enables the 2nd window.
- completion waits for ChatGPT review.

Minimum acceptance criteria after implementation:

- only authorized files changed.
- fake-only tests cover the matrix in this document.
- `response` non-empty remains the preferred success path.
- `thinking` handling is used only when ordinary accepted content is empty.
- full `thinking` text is not saved.
- any `thinking` summary is bounded and deterministic.
- every thinking-preview path keeps `preview_only=true`.
- every thinking-preview path keeps `no_write=true`.
- every thinking-preview path keeps `affects_score=false`.
- no raw Ollama JSON response is included.
- no scoring, storage, UI, or export chain is touched.

## 15. Step 174AU closure statement

Step 174AU defines the guard and fake-only deterministic test design for any later local LLM Ollama thinking-preview implementation.

Recommended guard strategy:

- keep the implementation file scope narrow, preferably only `app/engine/local_llm_ollama_preview_adapter.py`.
- keep the test file scope narrow, preferably only `tests/test_local_llm_ollama_preview_adapter.py`.
- keep `app/main.py` out of scope unless separately authorized.
- keep `app/storage.py`, `data/`, `output/`, scoring chains, UI, and export chains out of scope.
- use `thinking` only as a bounded preview-only summary source when ordinary accepted content is empty.
- never save full `thinking` text.
- require fake-only deterministic tests before any runtime smoke.
- require separate authorization and the 2nd window boundary for any runtime smoke.

This document does not authorize immediate normalizer changes, test changes, runtime smoke, UI work, scoring-chain work, export-chain work, or production use.
