# Local LLM Preview Mock API Bridge Service Smoke Report

## 1 Purpose

This document records the Step 174M local service smoke verification result for the local LLM preview/mock API bridge.

The smoke started the FastAPI application on loopback only, exercised `POST /local-llm/preview-mock` in disabled and enabled mock-only modes, stopped each service process, and verified that the worktree remained clean before writing this report.

This report does not represent real Ollama integration, UI integration, production scoring integration, storage-chain integration, or export-chain integration.

## 2 Current Directory

```text
/Users/youfeini/Desktop/ZhiFei_BizSystem-local-llm-clean
```

## 3 Current Branch

```text
local-llm-integration-clean
```

## 4 Starting HEAD

```text
5aa9dcb89c2f2ffc2d3e47e01f6906bd32a6b279
```

The starting branch was aligned with `origin/local-llm-integration-clean`.

## 5 Endpoint And Feature Flag

- endpoint: `POST /local-llm/preview-mock`
- feature flag: `LOCAL_LLM_PREVIEW_MOCK_API_ENABLED`
- host binding: `127.0.0.1`
- port: `18741`
- request scope: loopback only

No browser was started. No external network request was made.

## 6 Disabled Scenario Service Start

The disabled scenario started the service with the feature flag unset:

```bash
env -u LOCAL_LLM_PREVIEW_MOCK_API_ENABLED python3 -m uvicorn app.main:app --host 127.0.0.1 --port 18741
```

Runtime details:

- service PID: `56492`
- log path: `/tmp/zhifei_local_llm_preview_mock_disabled_174M.log`
- response capture path: `/tmp/zhifei_local_llm_preview_mock_disabled_174M.json`

The service listened on:

```text
http://127.0.0.1:18741
```

## 7 Disabled Scenario Request Summary

Request:

```text
POST http://127.0.0.1:18741/local-llm/preview-mock
```

Payload:

```json
{
  "project_id": "p1",
  "submission_id": "s1",
  "text_excerpt": "sample tender response excerpt",
  "mode": "preview_only",
  "requested_by": "operator",
  "scoring_context": {
    "dimension": "technical"
  },
  "evidence_context": {
    "source": "excerpt"
  },
  "requirement_hits": [
    {
      "requirement": "R1",
      "hit": true
    }
  ]
}
```

## 8 Disabled Scenario Response Summary

HTTP status:

```text
200
```

Response:

```json
{
  "status": "disabled",
  "enabled": false,
  "disabled": true,
  "reason": "feature_flag_disabled",
  "feature_flag": "LOCAL_LLM_PREVIEW_MOCK_API_ENABLED",
  "preview_only": true,
  "mock_only": true,
  "no_write": true,
  "affects_score": false
}
```

The response did not include `preview_input` or `advisory`.

## 9 Disabled Scenario Boundary Result

- endpoint returned disabled: yes
- helper output present: no
- helper execution expected: no
- scoring-chain entry observed: no
- storage write observed: no
- `data/` write observed: no
- `output/` write observed: no
- real model call observed: no
- export-chain trigger observed: no

The disabled service process was stopped with `kill <pid>` and verified not running.

## 10 Enabled Scenario Service Start

The enabled scenario started the service with the feature flag enabled:

```bash
LOCAL_LLM_PREVIEW_MOCK_API_ENABLED=true python3 -m uvicorn app.main:app --host 127.0.0.1 --port 18741
```

Runtime details:

- service PID: `56526`
- log path: `/tmp/zhifei_local_llm_preview_mock_enabled_174M.log`
- response capture path: `/tmp/zhifei_local_llm_preview_mock_enabled_174M.json`

The service listened on:

```text
http://127.0.0.1:18741
```

## 11 Enabled Scenario Request Summary

Request:

```text
POST http://127.0.0.1:18741/local-llm/preview-mock
```

Payload:

```json
{
  "project_id": "p1",
  "submission_id": "s1",
  "text_excerpt": "sample tender response excerpt",
  "mode": "preview_only",
  "requested_by": "operator",
  "scoring_context": {
    "dimension": "technical"
  },
  "evidence_context": {
    "source": "excerpt"
  },
  "requirement_hits": [
    {
      "requirement": "R1",
      "hit": true
    }
  ]
}
```

## 12 Enabled Scenario Response Summary

HTTP status:

```text
200
```

Response summary:

- `status=ok`
- `enabled=true`
- `feature_flag=LOCAL_LLM_PREVIEW_MOCK_API_ENABLED`
- `mode=mock_only`
- `mock_only=true`
- `preview_only=true`
- `no_write=true`
- `affects_score=false`
- `source=local_llm_preview_mock`
- `preview_input.project_id=p1`
- `advisory.boundary.no_write=true`

The response advisory summary was:

```text
Mock-only local LLM preview. No model was called.
```

## 13 Enabled Scenario Required Field Result

- returned `preview_only=true`: yes
- returned `mock_only=true`: yes
- returned `no_write=true`: yes
- returned `affects_score=false`: yes
- called only preview/mock helper path by endpoint design: yes
- real model output present: no
- official score output present: no
- official persistence output present: no

## 14 No-Ollama And No-Real-Model Result

- Ollama run: no
- Ollama called: no observed call
- OpenAI called: no observed call
- Spark called: no observed call
- Gemini called: no observed call
- external network called: no
- model runtime process started: no

The only network requests made in this smoke were loopback requests to `127.0.0.1:18741`.

## 15 No-Scoring-Chain Result

- `score_text()` called: no observed call
- `/rescore` called: no
- `qingtian-results` accessed: no observed access
- `evidence_trace/latest` accessed: no observed access
- `scoring_basis/latest` accessed: no observed access
- production scoring main chain entered: no observed entry

## 16 No-Write Result

- `app/storage.py` write observed: no
- `data/` write observed: no
- `output/` write observed: no
- qingtian-results write observed: no
- evidence trace write observed: no
- scoring basis write observed: no
- DOCX official export observed: no
- JSON official export observed: no
- Markdown official export observed: no

After both service processes were stopped, `git status --short` was empty and `git diff --name-only` was empty before this report file was created.

## 17 No-UI And No-Export Result

- UI connected: no
- browser started: no
- DOCX / JSON / Markdown official export triggered: no
- export chain connected: no
- ops_agents connected: no

## 18 Service Stop Method

Both service processes were stopped with `kill <pid>` and waited on by the shell.

Disabled scenario:

- PID: `56492`
- stop result: process stopped
- post-stop check: process not running

Enabled scenario:

- PID: `56526`
- stop result: process stopped
- post-stop check: process not running

No service process was left running by this smoke.

## 19 Git Status After Service Smoke

Before writing this report:

```text
git status --short
<empty>
```

```text
git diff --name-only
<empty>
```

After writing this report, the only intended repository change is:

```text
docs/local-llm-preview-mock-api-bridge-service-smoke-report.md
```

## 20 Risk Statement

The local loopback smoke verified the default-off endpoint behavior through a running FastAPI process. It did not verify production deployment, UI integration, real local LLM behavior, real Ollama runtime behavior, or any scoring/storage/export-chain behavior.

The endpoint remains preview/mock-only and default-off. The smoke does not authorize connecting Ollama, OpenAI, Spark, Gemini, UI, `score_text()`, `/rescore`, qingtian-results, evidence trace, scoring basis, storage writes, data writes, output writes, or official exports.

## 21 Step 174N Admission Conditions

Any Step 174N must be separately authorized and must declare:

- Whether it is docs-only, guard-only, or implementation work.
- Whether service startup is allowed.
- Whether pytest is allowed.
- Whether any browser is allowed.
- Whether any UI surface is allowed.
- Whether Ollama remains forbidden.
- Whether real model calls remain forbidden.
- Allowed files.
- Forbidden files.
- Expected verification commands.
- Stop conditions.

Step 174N must not automatically proceed from this smoke report. It must not directly enter true Ollama, UI, production scoring, storage, data/output writes, qingtian-results, evidence trace, scoring basis, or official export chains.

## 22 Closure Statement

Step 174M completed a local loopback service smoke for `POST /local-llm/preview-mock` in disabled and enabled mock-only modes.

The service was started only on `127.0.0.1`, stopped after each scenario, and no repository changes were observed before this report was written. Pytest was not run. Ollama was not run. No external network was called. No API code, helper code, storage code, tests, UI, guard, scoring, data, output, export, or ops_agents files were modified.
