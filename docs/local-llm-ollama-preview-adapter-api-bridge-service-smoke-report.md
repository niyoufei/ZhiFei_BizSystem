# Local LLM Ollama Preview Adapter API Bridge Service Smoke Report

## 1 Purpose

This document records the Step 174V no-real-model local service smoke verification for the local LLM Ollama preview adapter API bridge.

The smoke started FastAPI on loopback only, exercised `POST /local-llm/preview-mock` across the endpoint flag and adapter flag hierarchy, stopped every service process, and verified that the worktree remained clean before this report was written.

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
234b6fc4a0f2b1d4e58d13871245b6492443b691
```

The starting branch was aligned with `origin/local-llm-integration-clean`.

## 5 Service Listen Address And Port

- host: `127.0.0.1`
- port: `18742`
- endpoint: `POST /local-llm/preview-mock`
- request scope: loopback only

No service was bound to `0.0.0.0`.

## 6 Shared Request Payload

Each scenario used the same minimal deterministic payload:

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

No real tender file, real scoring data, real export task, or real model parameter was used.

## 7 Scenario A Startup Command

Endpoint flag disabled, adapter flag unset:

```bash
env -u LOCAL_LLM_PREVIEW_MOCK_API_ENABLED -u LOCAL_LLM_OLLAMA_PREVIEW_ADAPTER_ENABLED python3 -m uvicorn app.main:app --host 127.0.0.1 --port 18742
```

Recorded PID:

```text
73236
```

Runtime artifacts were written only under `/tmp`:

- log: `/tmp/zhifei_174v_a_uvicorn.log`
- response: `/tmp/zhifei_174v_a_response.json`

## 8 Scenario A Request Summary

Request:

```text
POST http://127.0.0.1:18742/local-llm/preview-mock
```

HTTP status:

```text
200
```

## 9 Scenario A Response Summary

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

Scenario A verification:

- endpoint returned disabled: yes
- endpoint feature flag state: disabled
- adapter branch entered: no
- adapter called: no observed call
- mock helper output present: no
- data/output/storage write observed: no
- scoring result impact observed: no
- real model call observed: no
- external network call observed: no

The Scenario A service process was stopped and verified exited.

## 10 Scenario B Startup Command

Endpoint enabled, adapter flag unset:

```bash
LOCAL_LLM_PREVIEW_MOCK_API_ENABLED=true env -u LOCAL_LLM_OLLAMA_PREVIEW_ADAPTER_ENABLED python3 -m uvicorn app.main:app --host 127.0.0.1 --port 18742
```

Recorded PID:

```text
73237
```

Runtime artifacts were written only under `/tmp`:

- log: `/tmp/zhifei_174v_b_uvicorn.log`
- response: `/tmp/zhifei_174v_b_response.json`

## 11 Scenario B Request Summary

Request:

```text
POST http://127.0.0.1:18742/local-llm/preview-mock
```

HTTP status:

```text
200
```

## 12 Scenario B Response Summary

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

Scenario B verification:

- existing mock-only helper response returned: yes
- adapter branch entered: no
- real Ollama called: no observed call
- external network call observed: no
- data/output/storage write observed: no
- scoring result impact observed: no

The Scenario B service process was stopped and verified exited.

## 13 Scenario C Startup Command

Endpoint enabled, adapter enabled, no-real-model:

```bash
LOCAL_LLM_PREVIEW_MOCK_API_ENABLED=true LOCAL_LLM_OLLAMA_PREVIEW_ADAPTER_ENABLED=true python3 -m uvicorn app.main:app --host 127.0.0.1 --port 18742
```

Recorded PID:

```text
73238
```

Runtime artifacts were written only under `/tmp`:

- log: `/tmp/zhifei_174v_c_uvicorn.log`
- response: `/tmp/zhifei_174v_c_response.json`

## 14 Scenario C Request Summary

Request:

```text
POST http://127.0.0.1:18742/local-llm/preview-mock
```

HTTP status:

```text
200
```

## 15 Scenario C Response Summary

Response summary:

- `status=error`
- `adapter=ollama_preview`
- `source=ollama_preview_adapter`
- `adapter_enabled=true`
- `adapter_feature_flag=LOCAL_LLM_OLLAMA_PREVIEW_ADAPTER_ENABLED`
- `feature_flag=LOCAL_LLM_PREVIEW_MOCK_API_ENABLED`
- `error_type=model_unavailable`
- `message=Ollama preview client is not configured.`
- `fallback_used=true`
- `fallback.mode=mock_fallback`
- `fallback.reason=model_unavailable`
- `fallback.model=local-preview-no-real-model`
- `fallback.prompt_excerpt=sample tender response excerpt`
- `preview_only=true`
- `no_write=true`
- `affects_score=false`

Scenario C verification:

- adapter preview branch entered: yes
- real client passed: no
- real transport passed: no
- real Ollama called: no observed call
- external network call observed: no
- stable no-real-model failure returned: yes
- scoring main chain entered: no observed entry
- data/output/storage write observed: no
- export-chain trigger observed: no
- UI trigger observed: no

The Scenario C service process was stopped and verified exited.

## 16 Service Process Shutdown Result

All service processes started during Step 174V were stopped:

- Scenario A PID `73236`: stopped
- Scenario B PID `73237`: stopped
- Scenario C PID `73238`: stopped

Post-smoke port check showed no listener on TCP port `18742`.

## 17 No-Ollama And No-Real-Model Result

- Ollama run: no
- `ollama serve` run: no
- real model called: no
- OpenAI called: no
- Spark called: no
- Gemini called: no
- external network called: no

Only loopback requests to `127.0.0.1:18742` were made.

## 18 No-Scoring-Chain Result

- `score_text()` called: no observed call
- `/rescore` called: no
- qingtian-results accessed: no observed access
- `evidence_trace/latest` accessed: no observed access
- `scoring_basis/latest` accessed: no observed access
- formal scoring result changed: no observed change

## 19 No-Write Result

- `app/storage.py` write observed: no
- `data/` write observed: no
- `output/` write observed: no
- qingtian-results write observed: no
- evidence trace write observed: no
- scoring basis write observed: no
- DOCX official export triggered: no
- JSON official export triggered: no
- Markdown official export triggered: no

## 20 No-UI And No-Export Result

- UI connected: no
- browser started: no
- DOCX / JSON / Markdown official export chain triggered: no
- production evaluation write-back triggered: no

## 21 Git Status After Service Smoke

Service smoke completed before this report was written with:

```text
git status --short: clean
git diff --name-only: empty
```

After this report was written, the only intended worktree change is:

```text
docs/local-llm-ollama-preview-adapter-api-bridge-service-smoke-report.md
```

## 22 Risk Notes

- This smoke verified the service path only on `127.0.0.1`.
- This smoke did not run real Ollama.
- This smoke did not run `ollama serve`.
- Scenario C verified only the no-real-model adapter branch and its stable `model_unavailable` behavior.
- This smoke does not prove real local LLM availability.
- This smoke does not authorize UI integration.
- This smoke does not authorize production scoring.
- This smoke does not authorize storage writes.
- This smoke does not authorize DOCX / JSON / Markdown official export integration.
- Future real Ollama work must use a separate boundary and explicitly state whether `2号窗口` runs `ollama serve`.

## 23 Future Step 174W Entry Conditions

Before Step 174W or any later stage, the next instruction must explicitly state:

- whether the work remains no-real-model
- whether service startup is allowed
- whether real Ollama remains forbidden
- whether `ollama serve` remains forbidden
- whether `2号窗口` is used
- whether `app/main.py` can be modified
- whether adapter code can be modified
- exact tests or smoke commands to run
- exact no-write checks for `data/`, `output/`, and storage
- exact stop conditions for scoring, UI, export, or real-model drift

No subsequent stage should directly enter real Ollama, UI, production scoring, storage, or export integration without a separate reviewed boundary.

## 24 Step 174V Closure Statement

Step 174V completed a no-real-model local loopback service smoke for `POST /local-llm/preview-mock`.

It verified:

- endpoint flag disabled returns disabled
- endpoint enabled and adapter disabled returns mock-only helper output
- endpoint enabled and adapter enabled enters adapter preview branch and returns stable no-real-model `model_unavailable`
- all responses preserve no-write and non-scoring boundaries
- all service processes were stopped
- no Ollama, `ollama serve`, external network, UI, scoring chain, storage chain, `data/`, `output/`, or export chain was used

This report does not authorize Step 174W, real Ollama, UI integration, production scoring, storage writes, or export writes.
