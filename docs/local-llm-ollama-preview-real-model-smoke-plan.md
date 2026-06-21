# Local LLM Ollama Preview Real Model Smoke Plan

## 1 Purpose

This document defines the pre-smoke boundary for a future real Ollama preview smoke.

The current Step 174W stage is docs-only. It only prepares the boundary for a later explicit authorization to call a local Ollama runtime. It does not start FastAPI, does not run pytest, does not run Ollama, does not run `ollama serve`, does not call external networks, does not modify code, does not add or modify tests, and does not write `data/`, `output/`, or storage.

This document must not be interpreted as permission to immediately call real Ollama, start a service, run `ollama serve`, connect UI, connect scoring, or proceed to Step 174X.

## 2 Baseline Inherited From Step 174V

- worktree: `/Users/youfeini/Desktop/ZhiFei_BizSystem-local-llm-clean`
- branch: `local-llm-integration-clean`
- baseline before Step 174W: `74502b431840e15eb43b3bd775546c8ee2b873a4`
- baseline tag: `v0.1.54-local-llm-ollama-preview-adapter-api-bridge-service-smoke-report`
- current endpoint: `POST /local-llm/preview-mock`
- endpoint feature flag: `LOCAL_LLM_PREVIEW_MOCK_API_ENABLED`
- adapter feature flag: `LOCAL_LLM_OLLAMA_PREVIEW_ADAPTER_ENABLED`

Step 174V verified the current no-real-model API bridge behavior through local loopback service smoke. It did not run Ollama, did not run `ollama serve`, did not call real models, did not access external networks, did not connect UI, did not connect scoring, and did not write storage, `data/`, or `output/`.

## 3 Current No-Real-Model Service Smoke Result

Step 174V used loopback only:

```text
127.0.0.1:18742
POST /local-llm/preview-mock
```

Scenario A verified endpoint flag disabled:

- `LOCAL_LLM_PREVIEW_MOCK_API_ENABLED` unset
- response returned `status=disabled`
- response kept `preview_only=true`
- response kept `mock_only=true`
- response kept `no_write=true`
- response kept `affects_score=false`
- adapter branch was not entered
- mock helper output was not produced

Scenario B verified endpoint enabled and adapter disabled:

- `LOCAL_LLM_PREVIEW_MOCK_API_ENABLED=true`
- `LOCAL_LLM_OLLAMA_PREVIEW_ADAPTER_ENABLED` unset
- response used the existing mock-only helper path
- response kept `mock_only=true`
- response kept `preview_only=true`
- response kept `no_write=true`
- response kept `affects_score=false`

Scenario C verified endpoint enabled and adapter enabled no-real-model:

- `LOCAL_LLM_PREVIEW_MOCK_API_ENABLED=true`
- `LOCAL_LLM_OLLAMA_PREVIEW_ADAPTER_ENABLED=true`
- response entered the adapter preview branch
- no real client or transport was passed
- no real Ollama call was observed
- response returned stable `model_unavailable` failure
- response kept `preview_only=true`
- response kept `no_write=true`
- response kept `affects_score=false`

All Step 174V service processes were stopped. Final `git status --short` was clean before the smoke report was written.

## 4 Real Ollama Preview Smoke Objective

A future Step 174X may verify whether the adapter and API bridge can safely attempt a real local Ollama preview call.

The future objective is limited to:

- verifying local Ollama reachability through `127.0.0.1`
- starting FastAPI only on `127.0.0.1`
- exercising `POST /local-llm/preview-mock` with both feature flags enabled
- preserving preview-only behavior
- preserving no-write behavior
- preserving `affects_score=false`
- confirming no scoring-chain access
- confirming no UI or export-chain access
- confirming no `data/`, `output/`, or storage writes
- recording whether a real Ollama call occurred
- stopping the FastAPI service after the smoke
- recording the 2nd window `ollama serve` state
- confirming final `git status --short` is clean

The future smoke is not production enablement and not a scoring-chain integration.

## 5 Non-Goals

Future real Ollama preview smoke must not:

- modify API code
- modify `app/main.py`
- modify `app/storage.py`
- modify `app/engine/local_llm_preview_mock.py`
- modify `app/engine/local_llm_ollama_preview_adapter.py`
- add or modify tests
- run pytest
- connect UI
- connect `score_text()`
- connect `/rescore`
- enter qingtian-results
- enter `evidence_trace/latest`
- enter `scoring_basis/latest`
- write storage
- write `data/`
- write `output/`
- trigger DOCX / JSON / Markdown official exports
- call OpenAI
- call Spark
- call Gemini
- access external networks
- download models
- pull models
- install dependencies
- modify Ollama configuration
- push main
- clean untracked files
- execute `git clean`

## 6 Conversation And Window Requirements

The current ChatGPT conversation remains the control window.

The current Codex nifei1227 conversation remains the execution window.

Step 174W itself does not need a 2nd window and must not run `ollama serve`.

If a future Step 174X is authorized to call real Ollama:

- a 2nd window must be explicitly enabled for `ollama serve`
- the 2nd window may run only `ollama serve`
- the 2nd window must not execute git commands
- the 2nd window must not run pytest
- the 2nd window must not modify files
- the 2nd window must not commit, tag, or push
- the 2nd window must not perform repository writes
- Codex nifei1227 remains responsible for repository checks
- Codex nifei1227 remains responsible for FastAPI startup
- Codex nifei1227 remains responsible for loopback requests
- Codex nifei1227 remains responsible for report documentation
- Codex nifei1227 remains responsible for commit, tag, and push if authorized
- only the current Codex nifei1227 window may perform repository write operations for this worktree

No new ChatGPT control conversation is required. No new Codex execution conversation is required.

## 7 2nd Window Responsibility Boundary

The 2nd window is a runtime-only support window for real Ollama smoke.

Allowed future command:

```bash
ollama serve
```

Forbidden in the 2nd window:

- `git status`
- `git add`
- `git commit`
- `git tag`
- `git push`
- `pytest`
- service startup for FastAPI
- code edits
- test edits
- docs edits
- dependency installation
- model download
- model pull
- file cleanup
- `git clean`
- any command that writes into the repository

If `ollama serve` fails in the 2nd window, the 2nd window must leave evidence visible and wait for ChatGPT direction. It must not try repository changes or workaround commands.

## 8 Codex nifei1227 Responsibility Boundary

Codex nifei1227 may perform future Step 174X execution only after explicit authorization.

Allowed future responsibilities:

- verify current directory
- verify branch
- verify starting HEAD
- verify `git status --short`
- verify target tag
- start FastAPI only on `127.0.0.1`
- send loopback requests only to `127.0.0.1`
- record request and response summaries
- stop FastAPI
- verify FastAPI process exit
- inspect `git status --short`
- inspect `git diff --name-only`
- write the authorized smoke report document if no unexpected changes appear
- commit, tag, and push only if the future step explicitly authorizes those actions

Forbidden future responsibilities unless separately authorized:

- modifying API code
- modifying adapter code
- modifying tests
- modifying storage
- modifying scoring chain
- modifying UI
- modifying export chain
- running pytest
- running `ollama serve`
- downloading or pulling models
- accessing external networks
- cleaning untracked files

## 9 Required Pre-Checks Before Future Real Ollama Smoke

Before any future real Ollama smoke, Step 174X must record:

- current directory
- current branch
- starting HEAD
- `git status --short`
- `git rev-parse origin/local-llm-integration-clean`
- presence of `v0.1.54-local-llm-ollama-preview-adapter-api-bridge-service-smoke-report`
- whether the 2nd window is enabled
- exact 2nd window command
- exact FastAPI startup command
- exact endpoint URL
- exact request payload
- endpoint feature flag value
- adapter feature flag value
- selected model name
- timeout boundary
- confirmation that no model will be downloaded
- confirmation that no model will be pulled
- confirmation that no dependency will be installed
- confirmation that only `127.0.0.1` loopback requests are allowed

If the worktree is not clean, the branch is not `local-llm-integration-clean`, or the intended bind host is not `127.0.0.1`, the future smoke must stop before service startup.

## 10 Feature Flag Requirements

Future real Ollama preview smoke must require both feature flags:

```text
LOCAL_LLM_PREVIEW_MOCK_API_ENABLED=true
LOCAL_LLM_OLLAMA_PREVIEW_ADAPTER_ENABLED=true
```

Endpoint flag behavior:

- absent, empty, `false`, `0`, `no`, or `off` must return disabled
- disabled must not inspect or call the adapter
- disabled must not call the mock helper
- disabled must not write storage, `data/`, or `output/`
- disabled must not affect scoring results

Adapter flag behavior:

- absent, empty, `false`, `0`, `no`, or `off` must keep mock-only path when endpoint flag is enabled
- enabled may allow adapter preview branch only after endpoint flag is enabled
- enabled must still preserve `preview_only=true`
- enabled must still preserve `no_write=true`
- enabled must still preserve `affects_score=false`
- enabled must not write storage, `data/`, or `output/`
- enabled must not connect scoring, UI, or export chains

## 11 Real Ollama Request Boundary

Future Step 174X must include at least these scenarios.

### Scenario A: Ollama Service Reachability Check

- 2nd window runs `ollama serve`
- Codex nifei1227 checks local Ollama reachability only through `127.0.0.1`
- no external network access is allowed
- no model download is allowed
- no model pull is allowed
- no dependency installation is allowed
- no Ollama configuration modification is allowed
- no repository file modification is allowed

If Ollama is not reachable, Step 174X must record a stable reachability failure and must not modify code to compensate.

### Scenario B: FastAPI And Adapter Enabled With Real Ollama Preview

FastAPI constraints:

- bind host must be `127.0.0.1`
- bind host must not be `0.0.0.0`
- endpoint must be `POST /local-llm/preview-mock`
- request must use a minimal preview payload
- request must not use real tender files
- request must not use production scoring data
- request must not trigger export tasks

Required flags:

```text
LOCAL_LLM_PREVIEW_MOCK_API_ENABLED=true
LOCAL_LLM_OLLAMA_PREVIEW_ADAPTER_ENABLED=true
```

If the current implementation lacks a real client or transport, Step 174X must not patch code during the smoke. It must record the stable failure and stop within the authorized boundary.

If a real local Ollama preview call is possible, the response must still include:

- `preview_only=true`
- `no_write=true`
- `affects_score=false`

The response must not enter scoring, storage, UI, or export chains.

### Scenario C: Failure And Rollback

Future real Ollama smoke must treat these as acceptable stable failure outcomes:

- Ollama unreachable
- model unavailable
- timeout
- invalid response
- transport failure

For each failure:

- return or record a stable failure structure
- do not enter the scoring main chain
- do not call `score_text()`
- do not call `/rescore`
- do not write storage, `data/`, or `output/`
- do not trigger UI or export chain
- do not modify `app/main.py`
- do not modify the adapter
- do not modify tests

## 12 Expected Response Boundary

Any future real Ollama preview response must remain advisory.

Allowed response semantics:

- preview status
- adapter status
- model availability status
- error type
- timeout status
- invalid response status
- advisory preview text
- fallback status
- `preview_only=true`
- `no_write=true`
- `affects_score=false`

Forbidden response semantics:

- `final_score`
- `score_result`
- write result
- persist result
- export result
- apply result
- official scoring basis write
- evidence trace write
- qingtian result write
- storage write
- DOCX / JSON / Markdown official export result

## 13 No-Write Verification

Future Step 174X must verify that real Ollama smoke does not write:

- `app/storage.py`
- `data/`
- `output/`
- qingtian-results
- `evidence_trace/latest`
- `scoring_basis/latest`
- report exports
- DOCX exports
- JSON exports
- Markdown exports

Required checks after the service stops:

```bash
git status --short
git diff --name-only
```

If any unexpected file appears, Step 174X must stop, avoid commit/tag/push, and report the changed file list.

## 14 No-Scoring-Chain Verification

Future Step 174X must verify no access to:

- `score_text()`
- `score_text_v2`
- `/rescore`
- qingtian-results
- `evidence_trace/latest`
- `scoring_basis/latest`
- ground truth
- ops agents
- score report writes
- calibration writes
- evolution report writes

If logs, responses, or filesystem state suggest scoring-chain access, Step 174X must stop and report.

## 15 No-UI And No-Export Verification

Future real Ollama smoke must not:

- start a browser
- click UI
- add UI controls
- change frontend scripts
- trigger DOCX official export
- trigger JSON official export
- trigger Markdown official export
- create report bundles
- write analysis bundles

The only allowed request target is the local API endpoint on `127.0.0.1`.

## 16 Process Shutdown Verification

After future real Ollama smoke:

- FastAPI PID must be recorded
- FastAPI stop method must be recorded
- FastAPI process exit must be confirmed
- port listener status must be checked if needed
- 2nd window `ollama serve` status must be recorded
- whether to stop or preserve the 2nd window must be decided by explicit ChatGPT instruction
- final `git status --short` must be recorded

FastAPI must always be stopped before writing a report or creating a commit.

## 17 Rollback Boundary

If future real Ollama smoke fails or behaves unexpectedly:

- first stop FastAPI
- record FastAPI PID and stop method
- record 2nd window `ollama serve` status
- do not modify business code to repair the failure
- do not modify adapter code to repair the failure
- do not modify tests
- do not write `data/`
- do not write `output/`
- do not clean untracked files
- do not execute `git clean`
- do not push main
- do not reset or rebase
- if the worktree remains clean, report the failure without code rollback
- if unexpected files changed, report the changed file list and wait for ChatGPT review

Stable rollback anchor:

```text
v0.1.54-local-llm-ollama-preview-adapter-api-bridge-service-smoke-report
```

No rollback to main is authorized by this plan.

## 18 Failure Stop Conditions

Future Step 174X must stop immediately if any of these occur:

- worktree is not clean before startup
- current branch is not `local-llm-integration-clean`
- FastAPI would bind to anything other than `127.0.0.1`
- a command would access external networks
- a command would download or pull a model
- a command would install dependencies
- a command would modify repository files outside the authorized smoke report
- Ollama call appears to affect scoring
- any `data/`, `output/`, or storage write appears
- any qingtian-results access appears
- any `evidence_trace/latest` access appears
- any `scoring_basis/latest` access appears
- any UI trigger appears
- any official export-chain trigger appears
- any code repair appears necessary to pass the smoke

Stop means no commit, no tag, no push, and no cleanup of unexpected files.

## 19 Required Report Format For Future Step 174X

Future Step 174X must return at least:

1. Current directory
2. Current branch
3. Starting HEAD
4. `git status before`
5. Whether the 2nd window was enabled
6. 2nd window command
7. Ollama service reachability check result
8. FastAPI startup command
9. FastAPI PID
10. Endpoint feature flag state
11. Adapter feature flag state
12. Request endpoint
13. Request payload summary
14. Response summary
15. Whether real Ollama was called
16. Whether any model was downloaded
17. Whether external networks were accessed
18. Whether OpenAI / Spark / Gemini was called
19. Whether `score_text` / `rescore` was called
20. Whether qingtian-results / `evidence_trace` / `scoring_basis` was accessed
21. Whether `app/storage.py` was written
22. Whether `data/` was written
23. Whether `output/` was written
24. Whether UI / export chain was triggered
25. FastAPI service stop method
26. 2nd window state
27. `git status after`
28. Risk notes

## 20 Step 174W Closure Statement

Step 174W is complete only when this docs-only boundary plan is added, committed, tagged, and pushed on `local-llm-integration-clean`.

Step 174W does not:

- start FastAPI
- run pytest
- run Ollama
- run `ollama serve`
- call external networks
- download or pull models
- modify code
- add or modify tests
- connect endpoint behavior beyond the already documented no-real-model bridge
- connect UI
- connect scoring
- connect export chains
- write storage
- write `data/`
- write `output/`
- push main

Any future Step 174X real Ollama smoke requires separate explicit authorization.
