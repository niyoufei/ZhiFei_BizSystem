# Local LLM Ollama Thinking Preview Normalization Stage Review

## 1. Purpose

This document reviews Step 174AV, which implemented bounded `thinking` preview normalization for the local LLM Ollama preview adapter with fake-only deterministic tests.

The purpose is to archive the implementation scope, field strategy, test coverage, explicit non-integrations, remaining risks, and required guardrails before any future runtime smoke.

This document is docs-only. It does not authorize runtime smoke, Ollama execution, UI integration, export integration, scoring-chain integration, storage writes, or production use.

## 2. Baseline before Step 174AV

Step 174AV ran after:

- Step 174AT completed the local LLM Ollama `thinking` preview strategy design.
- Step 174AU completed the guard and fake-only deterministic test design.

The inherited facts were:

- Step 174AR sampled local Ollama `/api/generate` for `qwen3:0.6b`.
- the sampled response was valid JSON.
- the sampled response had `response` present but empty.
- the sampled response had `thinking` present.
- the sampled response had `done=true`.
- the sampled response had no `error` field.
- the previous normalizer did not use `thinking` as preview content.
- the previous `invalid_response` result was a normalization-boundary result, not an Ollama reachability result.

Step 174AV was limited to controlled code implementation and fake-only deterministic tests.

## 3. Files changed in Step 174AV

Step 174AV modified only:

- `app/engine/local_llm_ollama_preview_adapter.py`
- `tests/test_local_llm_ollama_preview_adapter.py`

Step 174AV did not modify:

- `app/main.py`
- `app/storage.py`
- `app/engine/local_llm_preview_mock.py`
- `tests/test_local_llm_preview_mock.py`
- `tests/test_local_llm_preview_mock_api_bridge.py`
- release guard files
- smoke guard files
- `data/`
- `output/`
- UI files
- DOCX, JSON, or Markdown formal export files
- requirements, pyproject, or lock files

## 4. Function summary

Step 174AV adjusted:

- `normalize_ollama_response`
- `_extract_response_content`

Step 174AV added:

- `_extract_thinking_preview`

The implementation also added explicit constants:

- `THINKING_PREVIEW_SUMMARY_LIMIT = 80`
- `THINKING_PREVIEW_MODE = "thinking_preview"`
- `THINKING_CONTENT_SOURCE = "thinking"`

## 5. response-priority behavior review

`response` remains the preferred successful content path.

When a response body contains non-empty ordinary content, normalization returns the existing success shape:

- `status=ok`
- `reason=ok`
- `enabled=true`
- `raw_response_included=false`
- `preview_only=true`
- `no_write=true`
- `affects_score=false`

The content extraction priority now explicitly checks non-empty `response` before `content`, then `message.content`.

This preserves the intended behavior that a non-empty Ollama `response` field wins over any `thinking` fallback.

## 6. thinking-preview behavior review

When ordinary accepted content is empty or missing, and `thinking` is a non-empty string, Step 174AV now returns a controlled preview result.

The thinking-preview path returns:

- `status=ok`
- `reason=ok`
- `content_source=thinking`
- `preview_mode=thinking_preview`
- `preview_text_length=<trimmed thinking length>`
- `advisory.summary=<bounded thinking preview>`
- `advisory.content_source=thinking`
- `advisory.preview_mode=thinking_preview`
- `advisory.preview_text_length=<trimmed thinking length>`
- `raw_response_included=false`
- `preview_only=true`
- `no_write=true`
- `affects_score=false`

This path is still a preview-only normalization result. It is not a formal score, not scoring evidence, and not production scoring output.

## 7. summary-length and no-full-thinking boundary

The thinking-preview summary is bounded by:

```text
THINKING_PREVIEW_SUMMARY_LIMIT = 80
```

The implementation records:

- a short bounded summary in `advisory.summary`.
- the trimmed `thinking` length in `preview_text_length`.

The implementation does not save the full `thinking` text.

For long `thinking` values, fake-only tests verify that:

- the returned summary is exactly the first `80` characters.
- `preview_text_length` keeps the original trimmed length.
- the complete long `thinking` text is not present in the serialized response.

## 8. failure schema preservation review

The existing failure schemas remain stable.

Still `invalid_response`:

- response is empty and `thinking` is empty.
- response is empty and `thinking` is whitespace-only.
- response is missing and `thinking` is missing.
- `thinking` is not a string.
- response is not a mapping.

Still stable failure:

- Ollama `error` field exists.
- timeout remains `timeout`.
- transport failure remains `transport_failure`.
- model unavailable remains `model_unavailable`.
- unreachable local Ollama remains `ollama_unreachable`.

The Ollama `error` field path is checked before thinking-preview extraction, so an error response cannot become `status=ok`.

Every ok and failure response remains bounded by:

- `preview_only=true`
- `no_write=true`
- `affects_score=false`

## 9. fake-only deterministic test coverage

Step 174AV used this test command:

```bash
python3 -m pytest tests/test_local_llm_ollama_preview_adapter.py tests/test_local_llm_preview_mock_api_bridge.py tests/test_local_llm_preview_mock.py -q
```

The result was:

```text
131 passed in 3.37s
```

The fake-only tests cover:

- non-empty `response` with `done=true` returns `ok`.
- non-empty `response` remains preferred over `thinking`.
- empty `response` plus non-empty `thinking` returns a controlled preview-only summary.
- whitespace-only `response` plus non-empty `thinking` returns a controlled preview-only summary.
- missing `response` plus non-empty `thinking` returns a controlled preview-only summary.
- empty `response` plus empty `thinking` returns `invalid_response`.
- missing `response` plus missing `thinking` returns `invalid_response`.
- non-string `thinking` returns `invalid_response`.
- long `thinking` keeps only length and a bounded summary.
- thinking-preview summary length is bounded by `80`.
- thinking-preview paths keep `preview_only=true`.
- thinking-preview paths keep `no_write=true`.
- thinking-preview paths keep `affects_score=false`.
- thinking-preview paths do not include forbidden formal scoring keys.
- Ollama `error` field does not become thinking preview.
- timeout still returns `timeout`.
- transport failure still returns its existing stable failure.
- model unavailable still returns `model_unavailable`.
- identical fake thinking responses are deterministic.
- existing adapter tests continue to pass.
- existing mock API bridge tests continue to pass.
- existing mock-only preview tests continue to pass.

The tests did not use real Ollama, did not start services, did not access `127.0.0.1:11434`, did not access external networks, and did not download or pull models.

## 10. explicit non-integrations

Step 174AV did not run Ollama.

Step 174AV did not run `ollama serve`.

Step 174AV did not start FastAPI.

Step 174AV did not really access `127.0.0.1:11434`.

Step 174AV did not call external networks.

Step 174AV did not download models.

Step 174AV did not pull models.

Step 174AV did not execute `ollama pull`.

Step 174AV did not modify `app/main.py`.

Step 174AV did not modify `app/storage.py`.

Step 174AV did not modify `app/engine/local_llm_preview_mock.py`.

Step 174AV did not modify `tests/test_local_llm_preview_mock.py`.

Step 174AV did not connect `score_text()`.

Step 174AV did not connect `/rescore`.

Step 174AV did not connect `qingtian-results`.

Step 174AV did not connect `evidence_trace/latest`.

Step 174AV did not connect `scoring_basis/latest`.

Step 174AV did not connect UI.

Step 174AV did not trigger DOCX, JSON, or Markdown formal export.

Step 174AV did not connect any real-model production scoring chain.

## 11. no-write boundary verification

Step 174AV did not write:

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

The implementation remains an in-memory preview normalizer behavior.

The implementation does not persist raw Ollama responses.

The implementation does not persist the full `thinking` field.

## 12. no-scoring-chain boundary verification

Step 174AV did not call:

- `score_text()`
- `/rescore`

Step 174AV did not enter:

- `qingtian-results`
- `evidence_trace/latest`
- `scoring_basis/latest`

All ok and failure paths continue to carry `affects_score=false`.

The thinking-preview result is not a formal scoring result and must not be treated as scoring evidence.

## 13. no-UI and no-export verification

Step 174AV did not connect UI.

Step 174AV did not add frontend controls.

Step 174AV did not display `thinking` text in UI.

Step 174AV did not trigger DOCX export.

Step 174AV did not trigger JSON export.

Step 174AV did not trigger Markdown formal export.

The thinking-preview result remains a preview adapter response only.

## 14. remaining risks

Remaining risks after Step 174AV:

- fake-only thinking preview normalization has been completed, but real runtime smoke has not yet been run.
- a fake thinking response passing tests does not prove that `qwen3:0.6b` runtime will return `ok`.
- Step 174AR sampling showed `qwen3:0.6b` returned an empty `response` field and a present `thinking` field.
- a later runtime smoke is still required to verify whether thinking preview covers that real response shape.
- the current state cannot be used for production scoring.
- the current state is not connected to UI.
- the current state is not connected to export chains.
- the current state is not connected to `qingtian-results`, `evidence_trace/latest`, or `scoring_basis/latest`.
- the current state does not write storage, `data/`, or `output/`.
- future runtime smoke must be separately authorized.
- future runtime smoke must enable the 2nd window for `ollama serve`.
- future runtime smoke must not download or pull models.
- future runtime smoke must not modify scoring chains, UI, export chains, or storage to make the smoke pass.
- if future runtime smoke creates `data/`, `output/`, or storage changes, the task must stop and report the change list.

## 15. required next-stage guard before runtime smoke

Before any future runtime smoke, the next stage must explicitly confirm:

- the current ChatGPT conversation remains the controller.
- the current Codex nifei1227 conversation remains the executor.
- a 2nd window must be enabled to run only `ollama serve`.
- the 2nd window must not run git.
- the 2nd window must not run pytest.
- the 2nd window must not modify code.
- the 2nd window must not commit, tag, or push.
- Codex nifei1227 may perform repository checks.
- Codex nifei1227 may start FastAPI only if explicitly authorized.
- Codex nifei1227 may perform loopback requests only if explicitly authorized.
- Codex nifei1227 may write the authorized report document, commit, tag, and push only if explicitly authorized.
- the same repository must have only one write-capable Codex window operating at a time.
- FastAPI must listen only on `127.0.0.1`.
- Ollama access must be limited to `127.0.0.1:11434`.
- the model must be the already installed local `qwen3:0.6b`.
- timeout may continue to use `30`.
- `num_predict` may continue to use `8`.
- the run must not download models.
- the run must not pull models.
- the run must not execute `ollama pull`.
- the run must not call external networks.
- the run must not write `data/`, `output/`, or storage.
- the run must not call `score_text()` or `/rescore`.
- the run must not enter `qingtian-results`, `evidence_trace/latest`, or `scoring_basis/latest`.
- the run must not connect UI.
- the run must not trigger export chains.
- completion must stop for ChatGPT review.

## 16. Step 174AW closure statement

Step 174AW reviews the Step 174AV thinking-preview normalization implementation stage.

The current facts are:

- Step 174AV completed fake-only thinking preview normalization.
- only `app/engine/local_llm_ollama_preview_adapter.py` and `tests/test_local_llm_ollama_preview_adapter.py` were changed.
- `response` remains the preferred non-empty content source.
- empty or missing `response` with non-empty string `thinking` can return a bounded thinking preview.
- thinking-preview responses use `content_source=thinking`.
- thinking-preview responses use `preview_mode=thinking_preview`.
- thinking-preview responses include `preview_text_length`.
- thinking-preview summaries are bounded by `THINKING_PREVIEW_SUMMARY_LIMIT=80`.
- full `thinking` text is not saved.
- empty accepted content and empty `thinking` still returns `invalid_response`.
- existing failure schemas remain stable.
- fake-only tests passed with `131 passed in 3.37s`.

This review does not authorize runtime smoke, Ollama execution, UI integration, scoring-chain integration, export-chain integration, storage writes, or production scoring use.
