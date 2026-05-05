# Local LLM Ollama Preview Adapter API Bridge Service Smoke Plan

## 1 Purpose

This document defines the future service smoke and rollback boundary for the local LLM Ollama preview adapter API bridge.

The current Step 174U stage is docs-only. It only prepares a plan for a later loopback service smoke. It does not start a service, does not run pytest, does not run Ollama, does not run `ollama serve`, does not call external networks, does not modify code, does not add tests, does not connect UI, does not connect scoring, and does not write `data/`, `output/`, or storage.

This document must not be interpreted as permission to immediately start FastAPI, run Ollama, call a real model, or proceed to Step 174V.

## 2 Baseline Inherited From Step 174S And Step 174T

- worktree: `/Users/youfeini/Desktop/ZhiFei_BizSystem-local-llm-clean`
- branch: `local-llm-integration-clean`
- baseline before Step 174U: `6e3e9df470c34fd8c0066f5ede87873f21386967`
- baseline tag: `v0.1.52-local-llm-ollama-preview-adapter-api-bridge-stage-review`
- Step 174S implementation commit: `bc556ffa4bcb2dd5901944adbfd48c4c8fdef805`
- Step 174S endpoint: `POST /local-llm/preview-mock`
- endpoint feature flag: `LOCAL_LLM_PREVIEW_MOCK_API_ENABLED`
- adapter feature flag: `LOCAL_LLM_OLLAMA_PREVIEW_ADAPTER_ENABLED`

Step 174S connected the existing adapter to the API bridge only as a no-real-model branch. Step 174T documented that the adapter branch does not pass a real client or transport, does not run Ollama, does not start a service, does not call external networks, and does not connect scoring, UI, storage, or export chains.

## 3 Current Endpoint And Feature Flag Hierarchy

Current endpoint:

```text
POST /local-llm/preview-mock
```

Endpoint feature flag:

```text
LOCAL_LLM_PREVIEW_MOCK_API_ENABLED
```

Adapter feature flag:

```text
LOCAL_LLM_OLLAMA_PREVIEW_ADAPTER_ENABLED
```

Current hierarchy:

1. If `LOCAL_LLM_PREVIEW_MOCK_API_ENABLED` is absent, empty, `false`, `0`, `no`, or `off`, the endpoint directly returns disabled.
2. In endpoint disabled state, the endpoint must not check adapter flag, must not call adapter, and must not call mock helper.
3. If endpoint flag is enabled and adapter flag is absent, empty, `false`, `0`, `no`, or `off`, the endpoint keeps the existing mock-only helper path.
4. If endpoint flag is enabled and adapter flag is enabled, the endpoint enters the adapter preview branch.
5. The adapter preview branch remains no-real-model, preview-only, no-write, and `affects_score=false`.

## 4 Service Smoke Objective

A future Step 174V may verify the endpoint behavior through a local FastAPI process.

The future service smoke objective is limited to:

- starting the service only on `127.0.0.1`
- never listening on `0.0.0.0`
- sending only loopback requests
- verifying endpoint disabled behavior first
- verifying endpoint enabled + adapter disabled mock-only behavior second
- verifying endpoint enabled + adapter enabled no-real-model behavior third
- confirming no writes to storage, `data/`, or `output/`
- confirming no scoring-chain access
- confirming no UI or export-chain access
- confirming no real model call
- stopping the service process
- confirming final `git status --short` remains clean

The future smoke is not a real Ollama verification and not a production readiness check.

## 5 Smoke Test Non-Goals

Future service smoke must not:

- run Ollama
- run `ollama serve`
- call Ollama
- call OpenAI
- call Spark
- call Gemini
- call external networks
- call `score_text()`
- call `/rescore`
- enter qingtian-results
- enter `evidence_trace/latest`
- enter `scoring_basis/latest`
- write `app/storage.py`
- write `data/`
- write `output/`
- trigger DOCX / JSON / Markdown official exports
- connect UI
- run production scoring
- run production evaluation write-back
- install dependencies
- clean untracked files
- execute `git clean`

## 6 Required Pre-Checks Before Future Service Start

Before any future service start, Step 174V must record:

- current directory
- current branch
- starting HEAD
- `git status --short`
- `git rev-parse origin/local-llm-integration-clean`
- presence of `v0.1.52-local-llm-ollama-preview-adapter-api-bridge-stage-review`
- selected host and port
- exact service start command
- exact endpoint feature flag value
- exact adapter feature flag value
- exact request payload for each scenario
- confirmation that Ollama will not run
- confirmation that `ollama serve` will not run
- confirmation that no browser or UI will be started
- confirmation that only loopback requests are allowed

If the worktree is not clean, the branch is not `local-llm-integration-clean`, or the selected bind host is not `127.0.0.1`, the future smoke must stop before starting service.

## 7 Scenario A: Endpoint Flag Disabled

Scenario A must be verified first.

Startup environment:

```text
LOCAL_LLM_PREVIEW_MOCK_API_ENABLED unset
```

or an explicit false-like value:

```text
LOCAL_LLM_PREVIEW_MOCK_API_ENABLED=false
LOCAL_LLM_PREVIEW_MOCK_API_ENABLED=0
LOCAL_LLM_PREVIEW_MOCK_API_ENABLED=no
LOCAL_LLM_PREVIEW_MOCK_API_ENABLED=off
```

Request:

```text
POST http://127.0.0.1:<port>/local-llm/preview-mock
```

Expected behavior:

- response returns disabled
- response includes `status=disabled`
- response includes `enabled=false`
- response includes `disabled=true`
- response includes `reason=feature_flag_disabled`
- response includes `feature_flag=LOCAL_LLM_PREVIEW_MOCK_API_ENABLED`
- response includes `preview_only=true`
- response includes `mock_only=true`
- response includes `no_write=true`
- response includes `affects_score=false`
- adapter flag is not checked
- adapter is not called
- mock helper is not called
- no `data/` write
- no `output/` write
- no storage write
- no scoring result change

If Scenario A fails, future Step 174V must stop before Scenario B.

## 8 Scenario B: Endpoint Enabled And Adapter Disabled

Scenario B may run only after Scenario A passes.

Startup environment:

```text
LOCAL_LLM_PREVIEW_MOCK_API_ENABLED=true
LOCAL_LLM_OLLAMA_PREVIEW_ADAPTER_ENABLED unset
```

or adapter false-like value:

```text
LOCAL_LLM_OLLAMA_PREVIEW_ADAPTER_ENABLED=false
LOCAL_LLM_OLLAMA_PREVIEW_ADAPTER_ENABLED=0
LOCAL_LLM_OLLAMA_PREVIEW_ADAPTER_ENABLED=no
LOCAL_LLM_OLLAMA_PREVIEW_ADAPTER_ENABLED=off
```

Request:

```text
POST http://127.0.0.1:<port>/local-llm/preview-mock
```

Expected behavior:

- response uses existing mock-only helper path
- response includes `status=ok`
- response includes `enabled=true`
- response includes `feature_flag=LOCAL_LLM_PREVIEW_MOCK_API_ENABLED`
- response includes `mode=mock_only`
- response includes `mock_only=true`
- response includes `preview_only=true`
- response includes `no_write=true`
- response includes `affects_score=false`
- response includes `source=local_llm_preview_mock`
- adapter branch is not called
- Ollama is not called
- no external network access occurs
- no `data/` write
- no `output/` write
- no storage write

If Scenario B fails, future Step 174V must stop before Scenario C.

## 9 Scenario C: Endpoint Enabled And Adapter Enabled No-Real-Model

Scenario C may run only after Scenario A and Scenario B pass.

Startup environment:

```text
LOCAL_LLM_PREVIEW_MOCK_API_ENABLED=true
LOCAL_LLM_OLLAMA_PREVIEW_ADAPTER_ENABLED=true
```

Request:

```text
POST http://127.0.0.1:<port>/local-llm/preview-mock
```

Expected behavior:

- endpoint enters adapter preview branch
- adapter branch does not pass a real client
- adapter branch does not pass a real transport
- adapter branch does not call Ollama
- adapter branch does not access external networks
- response includes `adapter=ollama_preview` or adapter failure fields from the current adapter
- response includes `adapter_enabled=true`
- response includes `adapter_feature_flag=LOCAL_LLM_OLLAMA_PREVIEW_ADAPTER_ENABLED`
- response keeps `preview_only=true`
- response keeps `no_write=true`
- response keeps `affects_score=false`
- if response is a failure, it is a stable adapter failure such as `model_unavailable`
- failure does not enter the scoring main chain
- no `data/` write
- no `output/` write
- no storage write
- no export-chain trigger
- no UI trigger

Scenario C remains no-real-model. It must not prove real Ollama functionality.

## 10 No-Write Verification

Future service smoke must verify no writes to:

- `app/storage.py`
- `data/`
- `output/`
- qingtian-results
- evidence trace
- scoring basis
- DOCX official exports
- JSON official exports
- Markdown official exports
- official score reports
- official analysis bundles
- production evaluation write-back artifacts

Required checks:

- record `git status --short` before service start
- record `git status --short` after each scenario if feasible
- record `git status --short` after service shutdown
- record `git diff --name-only` after service shutdown

If any `data/`, `output/`, storage, qingtian-results, evidence trace, scoring basis, or export-chain change appears, the future smoke must stop and report. It must not clean or reset in the same step.

## 11 No-Real-Model Verification

Future service smoke must verify:

- Ollama was not run
- `ollama serve` was not run
- OpenAI was not called
- Spark was not called
- Gemini was not called
- no external network endpoint was called
- no model runtime process was started
- no true model response was expected from Scenario C

Scenario C must remain a no-real-model adapter branch check. Any real Ollama call belongs to a later separately authorized stage with its own boundary and explicit `2号窗口` decision.

## 12 No-Scoring-Chain Verification

Future service smoke must not enter:

- `score_text()`
- `score_text_v2()`
- `/rescore`
- qingtian-results
- `evidence_trace/latest`
- `scoring_basis/latest`
- official report generation
- DOCX / JSON / Markdown official export chains
- production evaluation write-back

If request logs, response bodies, process output, or file status indicate scoring-chain activity, the service must be stopped and the exact evidence must be reported.

## 13 Process Shutdown And Cleanup Verification

Future Step 174V must stop every service process it starts.

The future report must include:

- service start command
- process PID
- shutdown command or signal
- confirmation the PID no longer exists
- final `git status --short`
- final changed file list

No service process may be left running. No browser may be started. No cleanup of untracked files is allowed. No `git clean` is allowed. No stash is allowed.

## 14 Rollback Boundary

If future service smoke is abnormal, handling must follow this boundary:

- do not directly modify business code to hotfix the smoke
- do not directly connect real Ollama to debug
- do not run `ollama serve`
- do not write `data/`
- do not write `output/`
- do not clean untracked files
- do not execute `git clean`
- first stop the service process
- then record commands, environment variables, request bodies, response bodies, logs, and `git status`
- if the worktree remains clean, report only; do not perform code rollback
- if unexpected file changes appear, report the changed file list and wait for ChatGPT review
- stable rollback anchor is `v0.1.52-local-llm-ollama-preview-adapter-api-bridge-stage-review`
- do not roll back `main`
- do not push `main`

Rollback is a reporting boundary in the future smoke step, not permission to reset, clean, or rewrite history.

## 15 Failure Stop Conditions

Future Step 174V must stop immediately if any of these occurs:

- service binds to anything other than `127.0.0.1`
- service attempts to listen on `0.0.0.0`
- any external network call is observed
- Ollama is run
- `ollama serve` is run
- OpenAI / Spark / Gemini call is observed
- `score_text()` is called
- `/rescore` is called
- qingtian-results path is accessed
- `evidence_trace/latest` path is accessed
- `scoring_basis/latest` path is accessed
- `app/storage.py` write is observed
- `data/` write is observed
- `output/` write is observed
- DOCX / JSON / Markdown official export is triggered
- UI is opened or connected
- service process cannot be stopped
- `git status --short` shows unexpected changes

On any stop condition, do not commit, tag, push, clean, or continue to the next scenario.

## 16 Required Report Format For Future Step 174V

Future Step 174V must return at least:

1. 当前目录
2. 当前分支
3. 开始前 HEAD
4. `git status before`
5. 服务启动命令
6. endpoint feature flag 状态
7. adapter feature flag 状态
8. 场景 A 请求与响应摘要
9. 场景 B 请求与响应摘要
10. 场景 C 请求与响应摘要
11. 是否运行 Ollama
12. 是否运行 `ollama serve`
13. 是否调用真实模型
14. 是否调用外网
15. 是否调用 `score_text` / `rescore`
16. 是否访问 qingtian-results / evidence_trace / scoring_basis
17. 是否写 data/output/storage
18. 是否触发 UI / 导出链
19. 服务停止方式
20. `git status after`
21. 风险说明

The future report must explicitly state that Step 174V remains no-real-model unless a separate instruction says otherwise.

## 17 Step 174U Closure Statement

Step 174U records only the service smoke and rollback boundary for a future no-real-model loopback verification of `POST /local-llm/preview-mock`.

It confirms that no service was started, no pytest was run, no Ollama was run, `ollama serve` was not run, no external network was called, no code was modified, no tests were added or modified, no UI was connected, no scoring chain was connected, and no `data/`, `output/`, or storage write was performed in this step.

Future Step 174V must be separately authorized before any service startup. Future real Ollama work must be a separate stage with an explicit boundary and an explicit decision about whether `2号窗口` runs `ollama serve`.
