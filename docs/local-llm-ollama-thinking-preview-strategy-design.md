# Local LLM Ollama Thinking Preview Strategy Design

## 1. Purpose

This document defines the Step 174AT design boundary for handling the `thinking` field observed in local Ollama `qwen3:0.6b` responses.

The current stage is design-only. It does not implement code, does not modify tests, does not run pytest, does not start services, does not run Ollama, does not run `ollama serve`, and does not access `127.0.0.1:11434`.

This document answers the response-strategy question raised by Step 174AR and Step 174AS: whether the `thinking` field may be used as a preview-only summary source when `response` is empty. It does not authorize immediate changes to `normalize_ollama_response`.

## 2. Baseline before Step 174AT

Step 174AR completed minimal real-response structure sampling for local Ollama `/api/generate`.

Step 174AS reviewed that sampling stage and recorded that:

- `qwen3:0.6b` existed for the sampling stage.
- the sampling request was `POST /api/generate`.
- the prompt was `Return OK only.`
- `stream=false`.
- `options.num_predict=8`.
- the response returned HTTP `200`.
- the response body was a valid JSON object.
- the response had `model=qwen3:0.6b`.
- the response had `done=true`.
- the response had no `error` field.
- the response had a `response` field.
- the `response` field length was `0`.
- the `response` field was empty.
- the response had a top-level `thinking` field.

The current normalizer does not treat `thinking` as preview content. The current `invalid_response` result is therefore not an Ollama reachability failure. It is a response-shape and normalization-boundary result.

## 3. Real response sampling findings

The sampled real Ollama response contained these relevant top-level fields:

- `model`
- `created_at`
- `response`
- `thinking`
- `done`
- `done_reason`
- `context`
- `total_duration`
- `load_duration`
- `prompt_eval_count`
- `prompt_eval_duration`
- `eval_count`
- `eval_duration`

The key response facts for this strategy are:

- `response` exists but is empty.
- `thinking` exists.
- `done=true`.
- no `error` field exists.
- the JSON response is structurally valid.

Step 174AR did not record the full model output and did not record the full JSON response body. This design preserves that boundary.

## 4. Current invalid_response cause

The current normalizer accepts non-empty content only from:

- `content`
- `response`
- `message.content`

It does not accept `thinking` as content.

For the sampled `qwen3:0.6b` response:

- non-empty `content` was absent.
- `response` was present but empty.
- non-empty `message.content` was absent.
- `thinking` was present but not eligible.

Therefore `invalid_response` is directly associated with empty accepted content under the current normalizer. It is unrelated to Ollama being unreachable.

## 5. Thinking field risk analysis

The `thinking` field has a higher-risk profile than ordinary preview output because it may contain reasoning text rather than a concise answer.

Risks:

- `thinking` may be long.
- `thinking` may contain intermediate reasoning rather than user-facing output.
- `thinking` may be mistaken for a formal answer if exposed without context.
- `thinking` may be incorrectly copied into scoring evidence.
- `thinking` may be incorrectly persisted in storage, `data/`, or `output/`.
- `thinking` may be incorrectly included in DOCX, JSON, or Markdown formal exports.
- `thinking` may create a misleading production-readiness signal if it makes the preview status look successful without clear boundaries.

Because of these risks, `thinking` must not become a general-purpose content source. If it is used at all, it must be constrained to preview-only controlled summaries.

## 6. Allowed preview-only use boundary

The recommended allowed boundary is narrow:

- `thinking` may be considered only when accepted answer fields are empty.
- `response` remains the preferred source when non-empty.
- `thinking` may be used only to create a controlled preview-only summary.
- the full `thinking` text must not be saved.
- only bounded derived fields may be emitted, such as short summary, text length, and field existence.
- every `thinking` path must keep `preview_only=true`.
- every `thinking` path must keep `no_write=true`.
- every `thinking` path must keep `affects_score=false`.
- every `thinking` path must keep `raw_response_included=false`.

Allowed derived information should be limited to:

- whether `thinking` exists.
- whether `thinking` is a string.
- whether `thinking` is non-empty after trimming.
- `thinking` text length.
- a short bounded preview summary.
- a clear advisory boundary saying the summary is preview-only and not scoring evidence.

## 7. Forbidden use boundary

The `thinking` field must not be used as formal scoring content.

Forbidden uses:

- must not be used as a formal scoring basis.
- must not be written to `qingtian-results`.
- must not be written to `evidence_trace/latest`.
- must not be written to `scoring_basis/latest`.
- must not be written to DOCX formal export.
- must not be written to JSON formal export.
- must not be written to Markdown formal export.
- must not be written to `data/`.
- must not be written to `output/`.
- must not be written to storage.
- must not be connected to UI in the same step.
- must not call `score_text`.
- must not call `/rescore`.
- must not be used to generate a production model score.
- must not be preserved in full for later parsing.

This document does not authorize UI, scoring-chain, export-chain, or storage integration.

## 8. Normalization strategy options

### Option A: Conservative strategy

Continue not using `thinking`. If `response` is empty and no accepted content candidate is non-empty, keep returning `invalid_response`.

Benefits:

- lowest implementation risk.
- preserves the current normalizer boundary.
- avoids any accidental use of reasoning text.
- avoids new handling rules for long `thinking` values.

Costs:

- real `qwen3:0.6b` may continue to return `invalid_response` even when the model produced useful reasoning text.
- preview usability remains poor for response shapes where `thinking` is non-empty and `response` is empty.
- the operator still cannot see a controlled local-LLM preview summary for this response shape.

### Option B: Preview-only thinking strategy

When `response` is empty and `thinking` is non-empty, return a controlled preview-only summary derived from `thinking`.

Benefits:

- addresses the observed `qwen3:0.6b` response shape.
- can preserve `preview_only=true`, `no_write=true`, and `affects_score=false`.
- can avoid storing the full `thinking` value.
- can give the operator a bounded preview diagnostic signal.

Costs:

- if implemented loosely, `thinking` could be mistaken for formal model output.
- if not bounded, long `thinking` text could leak into responses, logs, storage, UI, or exports.
- requires explicit fake-only tests for all `thinking` paths.

### Option C: Dual-field strategy

Prefer ordinary answer fields first. Use `response` when it is non-empty. Only if accepted answer fields are empty should the normalizer consider a bounded `thinking` preview summary.

Source priority:

1. non-empty `content`
2. non-empty `response`
3. non-empty `message.content`
4. bounded `thinking` preview summary, only if explicitly allowed by implementation design

Benefits:

- preserves existing behavior for normal successful responses.
- handles the observed empty-`response` plus non-empty-`thinking` shape.
- avoids replacing ordinary response content with reasoning text.
- keeps `thinking` fallback clearly secondary and preview-only.

Costs:

- slightly more complex than Option A.
- still requires strict summary bounding and no-write proof.
- requires tests that prove no scoring, storage, UI, or export path is touched.

## 9. Recommended strategy

Recommended strategy: Option C, the dual-field strategy.

Rationale:

- a non-empty `response` should remain the primary answer source.
- if `response` is empty and `thinking` is non-empty, the preview can return a controlled summary rather than a production-like answer.
- the full `thinking` text must never be saved.
- the result must remain preview-only.
- the result must not affect scoring.
- the result must not write storage.
- the result must not trigger exports.
- the result must not connect UI.

The recommended implementation direction is not to treat `thinking` as normal answer content. It should be treated as a fallback diagnostic preview source with explicit boundary metadata.

The recommended preview response should remain structurally distinct from production scoring results and should preserve:

- `preview_only=true`
- `no_write=true`
- `affects_score=false`
- `raw_response_included=false`

## 10. Fake-only test requirements

The next implementation step must start with fake-only deterministic tests. This step does not add those tests.

Required test matrix:

1. `response` non-empty and `done=true`: returns `ok`.
2. `response` empty and `thinking` non-empty: returns a preview-only controlled summary or another explicitly designed stable state.
3. `response` whitespace-only and `thinking` non-empty: returns a preview-only controlled summary or another explicitly designed stable state.
4. `response` empty and `thinking` empty: keeps `invalid_response`.
5. `response` missing and `thinking` non-empty: follows the designed preview-only policy and must not be mistaken for formal scoring.
6. `thinking` is not a string: returns a stable failure or applies explicitly designed safe summary handling.
7. `thinking` is very long: emits only bounded derived data such as length and short summary.
8. every `thinking` path must return `preview_only=true`.
9. every `thinking` path must return `no_write=true`.
10. every `thinking` path must return `affects_score=false`.
11. every `thinking` path must not call `score_text` or `/rescore`.
12. every `thinking` path must not write `data/`, `output/`, or storage.
13. every `thinking` path must not enter `qingtian-results`, `evidence_trace`, or `scoring_basis`.
14. every `thinking` path must not trigger DOCX, JSON, or Markdown export chains.
15. tests must not call real Ollama.
16. tests must not start services.
17. tests must not access external networks.

Additional recommended assertions:

- ordinary `response` content takes priority over `thinking`.
- no returned payload includes the full raw Ollama JSON response.
- no returned payload includes forbidden formal-scoring keys.
- bounded summaries stay within a fixed character limit.
- long `thinking` values are truncated deterministically.

## 11. Runtime smoke requirements

No runtime smoke is authorized by this document.

Any future runtime smoke must be separately authorized and must:

- use the current ChatGPT conversation as controller.
- use the current Codex nifei1227 conversation as executor.
- enable the 2nd window only for `ollama serve`.
- keep the 2nd window away from git, tests, code edits, commit, tag, and push.
- keep FastAPI, if used, on `127.0.0.1`.
- keep Ollama access limited to `127.0.0.1:11434`.
- not download models.
- not pull models.
- not run `ollama pull`.
- not call external model providers.
- not write `data/`, `output/`, or storage.
- not connect scoring chains, UI, or export chains.

Runtime smoke must come after fake-only tests and a separate implementation authorization.

## 12. No-write boundary

Any future thinking-preview implementation must preserve no-write behavior.

Required boundary:

- no `app/storage.py` write.
- no `data/` write.
- no `output/` write.
- no storage write.
- no `qingtian-results` write.
- no `evidence_trace/latest` write.
- no `scoring_basis/latest` write.
- no full `thinking` text persistence.
- no full Ollama JSON response persistence.

The only acceptable output for a future implementation is an in-memory preview response with bounded summary content and explicit preview-only metadata.

## 13. No-scoring-chain boundary

The thinking-preview strategy must remain outside the scoring chain.

Required boundary:

- do not call `score_text`.
- do not call `/rescore`.
- do not connect `qingtian-results`.
- do not connect `evidence_trace/latest`.
- do not connect `scoring_basis/latest`.
- do not transform `thinking` into scoring evidence.
- do not transform `thinking` into a production score.
- keep `affects_score=false`.

The existence of a controlled thinking summary must not change any formal evaluation result.

## 14. No-UI and no-export boundary

This strategy does not authorize UI or export work.

Required boundary:

- do not connect UI.
- do not add frontend controls.
- do not display `thinking` in UI.
- do not trigger DOCX export.
- do not trigger JSON export.
- do not trigger Markdown formal export.
- do not write any export-chain artifact.

Any future UI or export work must be a separate step with a separate design and authorization.

## 15. Future implementation acceptance criteria

A future implementation may be accepted only if it proves all of the following:

- `response` non-empty remains the preferred success path.
- `thinking` is considered only when ordinary accepted content is empty.
- `thinking` handling remains preview-only.
- full `thinking` text is never saved.
- any `thinking` summary is bounded and deterministic.
- `preview_only=true` is preserved.
- `no_write=true` is preserved.
- `affects_score=false` is preserved.
- `raw_response_included=false` is preserved.
- no `score_text` or `/rescore` path is called.
- no `qingtian-results`, `evidence_trace`, or `scoring_basis` path is entered.
- no `data/`, `output/`, or storage write occurs.
- no UI or export chain is connected.
- fake-only tests cover the full matrix before runtime smoke.
- runtime smoke, if needed, is separately authorized and uses the 2nd window boundary.

## 16. Step 174AT closure statement

Step 174AT is a docs-only response strategy design stage.

The recommended strategy is the dual-field strategy: prefer non-empty `response`, and only when ordinary accepted content is empty may a future implementation consider non-empty `thinking` as a bounded preview-only summary source.

This recommendation remains constrained by strict boundaries:

- `thinking` must not become formal scoring evidence.
- `thinking` must not be written to `qingtian-results`.
- `thinking` must not be written to `evidence_trace/latest`.
- `thinking` must not be written to `scoring_basis/latest`.
- `thinking` must not be written to DOCX, JSON, or Markdown formal exports.
- `thinking` must not be written to `data/`, `output/`, or storage.
- `thinking` must not be connected to UI in the same step.
- the full `thinking` text must not be saved.
- fake-only tests must come before implementation.
- runtime smoke must be separately authorized.

This document must not be interpreted as permission to immediately modify `normalize_ollama_response`, run runtime smoke, connect UI, connect scoring chains, connect export chains, or use local LLM output for production scoring.
