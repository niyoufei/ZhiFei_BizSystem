# Local LLM Preview Stage Final Review and Next Gates

## Purpose

This document closes the current local LLM preview stage for the Qingtian evaluation system and defines the next-stage admission gates.

The current stage has completed staged verification of the preview-only local LLM chain. It has not connected UI, scoring main-chain logic, formal export chains, ZDoc write operations, or production scoring.

This document is docs-only. It must not be interpreted as permission to immediately enter UI work, scoring-chain integration, export-chain integration, ZDoc write operations, model download, model pull, storage writes, or any production integration.

## Baseline and latest stable tag

- Current endpoint: `POST /local-llm/preview-mock`
- Current endpoint master flag: `LOCAL_LLM_PREVIEW_MOCK_API_ENABLED`
- Current Ollama adapter flag: `LOCAL_LLM_OLLAMA_PREVIEW_ADAPTER_ENABLED`
- Current real transport flag: `LOCAL_LLM_OLLAMA_REAL_TRANSPORT_ENABLED`
- Current timeout configuration: `LOCAL_LLM_OLLAMA_TIMEOUT_SECONDS`
- Current generation length configuration: `LOCAL_LLM_OLLAMA_NUM_PREDICT`
- Current model configuration: `LOCAL_LLM_OLLAMA_MODEL`
- Latest stable tag before this document: `v0.1.83-local-llm-ollama-thinking-preview-runtime-smoke-stage-review`
- Latest reviewed runtime model: `qwen3:0.6b`
- Latest reviewed runtime result: synthetic preview request returned `status=ok`, `content_source=thinking`, `preview_mode=thinking_preview`, and `preview_text_length=24`

The current branch remains the clean local LLM worktree branch:

```text
local-llm-integration-clean
```

## Completed capability map

| Capability | Completed | Tested | Runtime verified | Still preview-only | Affects score |
| --- | --- | --- | --- | --- | --- |
| mock helper | Yes | Yes, through deterministic mock tests and API bridge tests | Yes, service loopback smoke covered the endpoint path | Yes | No |
| default-off API bridge | Yes | Yes, disabled-state and enabled-state bridge tests | Yes, endpoint behavior smoke was verified in preview stages | Yes | No |
| API bridge deterministic tests | Yes | Yes | Not a runtime target by itself | Yes | No |
| service loopback smoke | Yes | Smoke-verified rather than pytest-verified in that step | Yes, FastAPI loopback was used in authorized smoke stages | Yes | No |
| Ollama adapter independent implementation | Yes | Yes, fake-only adapter tests | Indirectly through later real transport smoke | Yes | No |
| no-real-model adapter API bridge | Yes | Yes, adapter-enabled real-transport-disabled paths | Yes, as a stable failure boundary before real transport | Yes | No |
| real transport code path | Yes | Yes, fake transport tests for tags and `/api/generate` | Yes, Step 174AX entered real transport | Yes | No |
| timeout / model controls | Yes | Yes, fake-only tests for timeout, model, and `num_predict` controls | Yes, Step 174AX used timeout `30`, `num_predict=8`, and `qwen3:0.6b` | Yes | No |
| response normalization | Yes | Yes, fake-only tests for ok and stable failure schemas | Yes, runtime smoke confirmed normalized endpoint output | Yes | No |
| real response minimal sampling | Yes | Sampling was structured and recorded, not pytest-tested | Yes, local `/api/generate` response shape was sampled in an authorized step | Yes | No |
| thinking preview strategy | Yes | Docs-only design; later implementation tests covered it | Runtime validation came later in Step 174AX | Yes | No |
| thinking preview implementation | Yes | Yes, fake-only deterministic tests | Yes, Step 174AX confirmed the real response-empty / thinking-present path | Yes | No |
| thinking preview runtime smoke | Yes | Smoke-verified; pytest was not run in the smoke step | Yes, `qwen3:0.6b` synthetic preview runtime returned `status=ok` | Yes | No |

All completed capabilities remain scoped to preview behavior. None of them may be treated as a production scoring capability.

## Feature flag hierarchy

The current local LLM preview chain is explicitly gated.

Flag hierarchy:

1. `LOCAL_LLM_PREVIEW_MOCK_API_ENABLED`
   - Controls whether `POST /local-llm/preview-mock` is enabled.
   - Default-off boundary.
   - Disabled state does not call mock helper, adapter, real transport, scoring, storage, or export paths.
2. `LOCAL_LLM_OLLAMA_PREVIEW_ADAPTER_ENABLED`
   - Controls whether the endpoint enters the Ollama preview adapter path.
   - When disabled, the endpoint remains on the mock-only helper path.
3. `LOCAL_LLM_OLLAMA_REAL_TRANSPORT_ENABLED`
   - Controls whether the adapter may construct and use real local Ollama transport.
   - When disabled, no real Ollama transport is constructed.
4. `LOCAL_LLM_OLLAMA_MODEL`
   - Selects the local Ollama model, with runtime smoke verifying `qwen3:0.6b`.
5. `LOCAL_LLM_OLLAMA_TIMEOUT_SECONDS`
   - Controls bounded local Ollama timeout.
6. `LOCAL_LLM_OLLAMA_NUM_PREDICT`
   - Controls bounded generation length for `/api/generate`.

The hierarchy is intentionally defensive. Later higher-risk work must not bypass these flags.

## Runtime smoke milestones

The current stage has passed these runtime milestones:

- mock-only path verified under the default-off API bridge.
- no-real-model adapter path verified as a stable preview-only failure boundary.
- local Ollama reachability checked in authorized runtime and sampling stages.
- real transport code path verified through fake transport tests.
- real transport endpoint smoke verified through `POST /local-llm/preview-mock`.
- timeout and model controls verified with explicit runtime configuration.
- response normalization verified with fake-only tests.
- direct real response sampling verified that `qwen3:0.6b` can return valid JSON with empty `response` and present `thinking`.
- thinking preview normalization verified with fake-only tests.
- thinking preview runtime smoke verified with real local loopback transport and synthetic payload.

The latest runtime smoke used only local loopback and synthetic input. It did not use real bid data, real evaluation data, formal export jobs, UI actions, or scoring-chain calls.

## Thinking preview success summary

The thinking preview milestone is now proven for the current preview runtime scenario.

Verified facts:

- `qwen3:0.6b` synthetic preview runtime returned HTTP `200` from `POST /local-llm/preview-mock`.
- the endpoint master flag was enabled.
- the adapter flag was enabled.
- the real transport flag was enabled.
- the real transport branch was entered.
- the normalized response returned `status=ok`.
- `content_source=thinking`.
- `preview_mode=thinking_preview`.
- `preview_text_length=24`.
- `preview_only=true`.
- `no_write=true`.
- `affects_score=false`.
- complete `thinking` was not saved.
- complete model output was not saved.

This demonstrates that thinking preview normalization covers the observed `qwen3:0.6b` response-empty and thinking-present scenario in the preview-only runtime path.

## Explicit non-integrations

The current local LLM preview stage has not integrated:

- `score_text`.
- `/rescore`.
- `qingtian-results`.
- `evidence_trace/latest`.
- `scoring_basis/latest`.
- UI.
- DOCX formal export.
- JSON formal export.
- Markdown formal export.
- production scoring.
- ZDoc write operations.
- `ops_agents` workflows.

The current result does not represent production scoring readiness, UI readiness, export-chain readiness, ZDoc integration readiness, or a cross-system local LLM standard.

## No-write boundary

The current local LLM preview stage remains no-write.

No-write boundary:

- `data/` was not written.
- `output/` was not written.
- storage was not written.
- `app/storage.py` was not modified for local LLM preview.
- full model output was not saved.
- full `thinking` text was not saved.
- `qingtian-results` was not written.
- `evidence_trace/latest` was not written.
- `scoring_basis/latest` was not written.
- formal export artifacts were not written.

Any future step that proposes writing any of these surfaces must be separately designed, tested, smoked, and reviewed.

## No-scoring-chain boundary

The current local LLM preview stage remains outside the scoring chain.

Confirmed boundary:

- no `score_text` integration.
- no `/rescore` integration.
- no scoring result writes.
- no scoring-basis writes.
- no evidence-trace writes.
- no `qingtian-results` writes.
- all local LLM preview responses remain `affects_score=false`.

If a future scoring-chain integration is proposed, it must start with a separate design gate and must define whether model output may influence scores at all.

## No-UI and no-export boundary

The current local LLM preview stage remains outside UI and export chains.

Confirmed boundary:

- no UI entry was added.
- no UI preview button was added.
- no UI diagnostic panel was connected.
- no DOCX export was triggered.
- no JSON formal export was triggered.
- no Markdown formal export was triggered.
- no export-chain file was modified.

Current local LLM preview success must not be treated as permission to expose thinking-derived content in UI or official exports.

## Remaining risks

- Current runtime verification used a synthetic payload, not real evaluation business data.
- Current runtime verification used only `qwen3:0.6b`.
- Multi-model behavior remains unverified.
- Large payload behavior remains unverified.
- Abnormal payload coverage remains limited to fake-only tests.
- Production stability, concurrency, timeout behavior under load, and operational recovery remain unverified.
- UI behavior remains undesigned and unconnected.
- Scoring-chain behavior remains undesigned and unconnected.
- Export-chain behavior remains undesigned and unconnected.
- ZDoc reuse remains a future design topic, not an implementation result in this repository.
- m5 model routing alignment remains a reference topic, not a current write-operation target.
- Thinking-derived summaries remain sensitive and must not become scoring evidence, official export text, stored data, or UI content without a separate approved design.

## Next gate A: UI preview entry design

UI preview entry work is not authorized by this document.

If a future UI gate is opened, it must start as design-only and must define:

- a manual preview button or internal diagnostic entry only.
- no automatic local model trigger.
- no automatic background generation.
- no score writes.
- no `evidence_trace` writes.
- no `scoring_basis` writes.
- no `qingtian-results` writes.
- no export-chain trigger.
- no storage writes.
- no display of complete `thinking`.
- clear preview-only labeling.
- a user-visible boundary that local LLM preview does not affect score.

The UI entry must not be implemented until the design is reviewed and explicitly authorized.

## Next gate B: scoring-chain integration design

Scoring-chain integration is the highest-risk Qingtian gate and is not authorized by this document.

Current forbidden integrations remain:

- do not connect `score_text`.
- do not connect `/rescore`.
- do not write scoring results.
- do not write `qingtian-results`.
- do not write `evidence_trace/latest`.
- do not write `scoring_basis/latest`.

If a future scoring-chain gate is opened, it must first establish a read-only suggestion layer. Before any score can be affected, the design must define:

- whether model output is ever allowed to influence official score.
- how human review is required before score impact.
- which evidence-chain fields are allowed.
- which scoring-basis fields are allowed.
- how provenance, traceability, prompt identity, model identity, and response boundaries are recorded.
- how complete `thinking` remains excluded.
- which higher-grade guards block storage, export, and scoring side effects.
- what fake-only tests, runtime smoke, regression tests, and ChatGPT review are required.

No scoring-chain code should be written before that design gate is complete.

## Next gate C: ZDoc reuse integration design

ZDoc reuse is not authorized as a write operation by this document.

Current gate baseline:

- ZDoc has passed read-only verification in the current ChatGPT-controlled planning context.
- ZDoc may reuse Qingtian's default-off, preview-only, no-write, and fake-only-tests-first boundaries.
- ZDoc must not directly connect formal generation chains.
- ZDoc must not directly write `output/`, `job/`, `export/`, or equivalent runtime result directories.
- ZDoc must not infer that Qingtian runtime success automatically applies to document generation.

Before any ZDoc implementation, a separate docs-only gap analysis must define:

- matching endpoint or service boundary.
- default-off feature flag plan.
- preview-only payload shape.
- fake-only deterministic test matrix.
- no-write storage boundary.
- no-export boundary.
- runtime smoke admission conditions.
- repository-specific forbidden files and directories.

ZDoc integration must remain design-only until separately authorized.

## Next gate D: model routing / m5 alignment design

Model routing and m5 alignment are reference topics only at this stage.

Current gate baseline:

- `m5-max-setup` has passed read-only verification in the current planning context.
- the current m5 Codex workspace has no remote, so it is not a write-operation mainline.
- m5 may be used as a reference for model routing, Ollama API contracts, model selection strategy, and local-model configuration patterns.
- m5 must not become an implicit target for write operations from this Qingtian step.

If future m5 work is required, it must first establish a pushable local repository worktree and a separately authorized file scope. It must also define how model routing aligns with Qingtian and ZDoc without creating cross-repo side effects.

## Recommended next action order

Recommended order:

1. Complete this Step 174AZ stage final review.
2. Next, prioritize a ZDoc local LLM integration gap analysis as docs-only.
3. Place Qingtian UI preview entry design after the ZDoc gap analysis.
4. Handle Qingtian scoring-chain integration last.
5. Enter the m5 base only when unified model routing is needed.

This order keeps the highest-risk scoring surfaces last and preserves the current preview-only evidence trail.

## Step 174AZ closure statement

Step 174AZ closes the current Qingtian local LLM preview stage with a stage-level final review and next-gate checklist.

The completed preview chain now includes mock-only behavior, default-off API bridge behavior, no-real-model adapter boundaries, real transport controls, response normalization, real response sampling, thinking preview strategy, thinking preview implementation, and a synthetic `qwen3:0.6b` runtime smoke that returned `status=ok` with `content_source=thinking` and `preview_mode=thinking_preview`.

The current result remains preview-only, no-write, and `affects_score=false`. It does not authorize UI integration, scoring-chain integration, export-chain integration, ZDoc write operations, model download, model pull, storage writes, or production scoring.
