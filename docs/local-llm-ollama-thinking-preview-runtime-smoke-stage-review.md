# Local LLM Ollama Thinking Preview Runtime Smoke Stage Review

## Purpose

This document reviews Step 174AX, which completed a controlled real runtime smoke for local LLM Ollama thinking preview normalization.

The purpose is to archive the key runtime conclusion, success conditions, risk boundaries, difference from production readiness, and allowed or forbidden next steps after the runtime smoke report was created.

This stage review is docs-only. It does not authorize code changes, test changes, pytest execution, service startup, Ollama execution, `ollama serve`, UI integration, scoring-chain integration, export-chain integration, storage writes, model download, model pull, or production use.

## Baseline before Step 174AX

Step 174AX started after:

- Step 174AV implemented bounded thinking preview normalization with fake-only deterministic tests.
- Step 174AW reviewed that implementation and preserved the runtime-smoke admission guard.

The inherited implementation facts were:

- `response` remains the preferred source when non-empty.
- `thinking` is considered only when accepted response content is empty or missing.
- `thinking` can produce only a bounded preview-only summary.
- complete `thinking` is not saved.
- complete model output is not saved.
- every ok and failure path remains `preview_only=true`.
- every ok and failure path remains `no_write=true`.
- every ok and failure path remains `affects_score=false`.
- `THINKING_PREVIEW_SUMMARY_LIMIT=80`.

Step 174AX start HEAD was `ba4d552031a7391e66f5aa0858b2af0afa1f80a9`. Step 174AX produced smoke report commit `d0649aedff30e5d9032fa513f7b8c4a1fd3b6f13` and tag `v0.1.82-local-llm-ollama-thinking-preview-runtime-smoke-report`.

## Runtime smoke execution summary

Step 174AX completed a real runtime smoke verification.

Runtime scope:

- preview-only branch only.
- synthetic payload only.
- no real tender files.
- no real scoring data.
- no export job.
- no UI.
- no scoring main chain.
- no export chain.
- no `data/` write.
- no `output/` write.
- no storage write.

The runtime used:

- existing local Ollama listener on `127.0.0.1:11434`.
- selected local model `qwen3:0.6b`.
- `LOCAL_LLM_OLLAMA_TIMEOUT_SECONDS=30`.
- `LOCAL_LLM_OLLAMA_NUM_PREDICT=8`.
- FastAPI bound only to `127.0.0.1:18748`.
- endpoint `POST /local-llm/preview-mock`.

The endpoint feature flag, adapter feature flag, and real transport feature flag were all enabled for the smoke.

## Ollama service observation

Step 174AX required the 2nd window for `ollama serve`.

Observed 2nd-window facts:

- the 2nd window executed `ollama serve`.
- `ollama serve` did not newly start because `127.0.0.1:11434` was already occupied.
- the command exited with a bind conflict.
- no git, pytest, code modification, commit, tag, or push command was run in the 2nd window.

The existing local Ollama listener was then checked through loopback only.

Reachability facts:

- command target: `http://127.0.0.1:11434/api/tags`.
- HTTP status: `200`.
- response body: valid JSON object.
- installed model list included `qwen3:0.6b`.
- selected model: `qwen3:0.6b`.

No model was downloaded, pulled, replaced, or switched automatically.

## FastAPI runtime result review

FastAPI runtime facts:

- FastAPI listened only on `127.0.0.1:18748`.
- endpoint: `POST /local-llm/preview-mock`.
- endpoint feature flag was enabled.
- adapter feature flag was enabled.
- real transport feature flag was enabled.
- synthetic request body used `project_id=p1`, `submission_id=s1`, a synthetic excerpt, synthetic context, and synthetic requirement hits.
- endpoint returned HTTP `200`.
- the response came from `ollama_preview_adapter`.
- the real transport branch was entered.
- the real transport target was `127.0.0.1:11434`.

FastAPI was stopped after the request. Port `18748` had no listener after shutdown.

## Thinking-preview success review

The normalized runtime response was successful under the thinking-preview strategy.

Observed normalized fields:

- `status=ok`.
- `preview_only=true`.
- `no_write=true`.
- `affects_score=false`.
- `content_source=thinking`.
- `preview_mode=thinking_preview`.
- `preview_text_length=24`.
- `raw_response_included=false`.

This proves that thinking preview normalization covered the real `qwen3:0.6b` response-empty and thinking-present scenario for the Step 174AX synthetic preview runtime request.

This success is limited to the preview runtime path. It is not a production scoring success criterion.

## No-full-thinking boundary review

Step 174AX did not save complete `thinking` text.

Step 174AX did not save complete model output.

The runtime response kept only bounded preview information:

- source marker.
- preview mode marker.
- preview text length.
- short advisory summary.
- no raw response inclusion.

The result must not be reinterpreted as permission to write complete `thinking` into storage, evidence, official exports, UI, or scoring records.

## No-write boundary review

Step 174AX did not write:

- `data/`.
- `output/`.
- storage files.
- `app/storage.py`.
- official DOCX export artifacts.
- official JSON export artifacts.
- official Markdown export artifacts.

The repository status after FastAPI shutdown was clean before the smoke report was written. The only Step 174AX repository change was the smoke report document.

## No-scoring-chain boundary review

Step 174AX did not call:

- `score_text`.
- `/rescore`.

Step 174AX did not access or write:

- `qingtian-results`.
- `evidence_trace/latest`.
- `scoring_basis/latest`.

The runtime result remains `affects_score=false`. The thinking preview output is not scoring evidence and is not a production score.

## No-UI and no-export boundary review

Step 174AX did not connect UI.

Step 174AX did not trigger:

- DOCX formal export.
- JSON formal export.
- Markdown formal export.

The runtime smoke did not make any UI, export, or production result surface available.

## Difference from production readiness

The Step 174AX result is a preview runtime smoke result, not a production-readiness decision.

It proves:

- the local preview endpoint can call the real Ollama transport on loopback.
- `qwen3:0.6b` can be consumed by the preview runtime path.
- response-empty and thinking-present runtime behavior can normalize to `status=ok` with thinking-preview markers.

It does not prove:

- production scoring is safe.
- real bidding or evaluation data is safe to use.
- UI should expose thinking-derived summaries.
- export files should include thinking-derived summaries.
- `qingtian-results`, `evidence_trace`, or `scoring_basis` may use thinking-derived content.
- the system is stable across large payloads, abnormal payloads, concurrent requests, or multiple models.

This document must not be interpreted as permission to directly connect production scoring, UI, or export chains.

## Remaining risks

- The smoke used only a synthetic payload, not real evaluation business data.
- The smoke verified only the preview runtime path, not the scoring main chain.
- UI remains unconnected.
- Export chain remains unconnected.
- `qingtian-results`, `evidence_trace`, and `scoring_basis` remain unconnected.
- storage, `data/`, and `output/` remain unwritten and unverified for production behavior.
- Only `qwen3:0.6b` was verified.
- Multi-model behavior was not verified.
- Large abnormal payload behavior was not verified.
- Broad error payload behavior was not verified.
- Production stability, concurrency, latency, and operational behavior were not verified.
- The 2nd-window `ollama serve` command did not newly start because an existing local listener already occupied the port.
- Any UI, scoring-chain, or export-chain integration must be separately designed and authorized.

## What this milestone enables

This milestone enables discussion of higher-level integration boundaries.

It establishes that:

- local Ollama plus `qwen3:0.6b` can be consumed correctly by the preview runtime path.
- thinking preview normalization works for the real response-empty and thinking-present scenario observed with `qwen3:0.6b`.
- the endpoint, real transport, and normalization layers can work together under the preview-only boundary.
- a later stage may discuss whether and how to design higher-level boundaries.

This milestone does not automatically authorize UI, scoring main chain, export chain, storage, or production integration.

## What this milestone still does not allow

This milestone still does not allow:

- direct production scoring integration.
- direct UI integration.
- direct DOCX, JSON, or Markdown export integration.
- writing thinking-derived content to `qingtian-results`.
- writing thinking-derived content to `evidence_trace/latest`.
- writing thinking-derived content to `scoring_basis/latest`.
- writing complete `thinking` to `data/`, `output/`, storage, logs, reports, exports, or UI.
- using local LLM thinking content as a formal scoring basis.
- expanding runtime scope beyond loopback without separate authorization.
- downloading or pulling models without separate authorization.

Any next stage must define its own file scope, runtime scope, test scope, write boundaries, and acceptance criteria before work begins.

## Step 174AY closure statement

Step 174AY archives the Step 174AX runtime smoke result as a docs-only stage review. The recorded conclusion is that the preview-only FastAPI endpoint, real Ollama loopback transport, and bounded thinking preview normalization successfully returned `status=ok`, `content_source=thinking`, `preview_mode=thinking_preview`, and `preview_text_length=24` for a synthetic `qwen3:0.6b` runtime smoke request.

The stage remains outside UI, scoring main chain, export chain, storage, `data/`, and `output/`. It does not authorize production scoring or any automatic next-stage integration.
