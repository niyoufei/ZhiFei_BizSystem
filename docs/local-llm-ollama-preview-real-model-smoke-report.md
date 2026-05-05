# Local LLM Ollama Preview Real Model Smoke Report

## 1 Purpose

This document records the Step 174X controlled real local Ollama preview smoke and the FastAPI adapter-enabled loopback smoke.

The smoke verified local Ollama reachability through `127.0.0.1`, used the first locally installed model for one minimal `/api/generate` request, then verified that the existing `POST /local-llm/preview-mock` adapter-enabled API bridge still returns preview-only, no-write, `affects_score=false` output without passing a real client or transport through the API bridge.

This report does not represent production scoring integration, UI integration, export-chain integration, storage-chain integration, or a production-ready local model rollout.

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
3a0e1c002fa5d91c4ef992d88431734f53398b5b
```

The starting branch was aligned with `origin/local-llm-integration-clean`.

## 5 2nd Window Enablement

2nd window was enabled before this Step 174X execution.

Observed local listener:

```text
ollama 76262 youfeini TCP 127.0.0.1:11434 (LISTEN)
```

This execution window did not run `ollama serve` and did not stop the 2nd window.

## 6 2nd Window Responsibility Boundary

The 2nd window responsibility was limited to maintaining `ollama serve`.

The 2nd window was not used for:

- git commands
- pytest
- code edits
- test edits
- docs edits
- commit
- tag
- push
- repository cleanup
- model download
- model pull

2nd window shutdown remains a ChatGPT follow-up decision.

## 7 Ollama Reachability Check Command

Command:

```bash
curl --noproxy '*' -sS --max-time 5 http://127.0.0.1:11434/api/tags
```

Result:

- reachable: yes
- address: `127.0.0.1`
- HTTP status: `200`
- timeout: no
- connection refused: no
- invalid response: no

## 8 Installed Model Summary

The local `/api/tags` response returned 7 installed models.

Installed model names observed:

- `qwen3-next:80b-a3b-instruct-q8_0`
- `qwen3-coder:30b`
- `deepseek-r1:32b`
- `qwen3:30b`
- `qwen3:14b`
- `qwen3:8b`
- `qwen3:0.6b`

No model download was performed.

No model pull was performed.

## 9 Selected Local Model

Per Step 174X, the first locally installed model was selected:

```text
qwen3-next:80b-a3b-instruct-q8_0
```

## 10 Real Ollama Preview Generate Request Summary

Endpoint:

```text
POST http://127.0.0.1:11434/api/generate
```

Request summary:

```json
{
  "model": "qwen3-next:80b-a3b-instruct-q8_0",
  "prompt": "Return OK only.",
  "stream": false,
  "options": {
    "num_predict": 8
  }
}
```

Timeout boundary:

```text
30 seconds
```

The request used loopback only and did not write to the repository.

## 11 Real Ollama Preview Response Summary

Result:

- HTTP status: `200`
- curl exit code: `0`
- model: `qwen3-next:80b-a3b-instruct-q8_0`
- response excerpt: `OK`
- `done=true`
- `done_reason=stop`
- `total_duration=17036590916`

The real Ollama preview generation request was executed successfully against a locally installed model.

No full long model output was written to `data/`, `output/`, or storage.

## 12 Initial Local Request Correction Note

During command construction, an initial loopback `/api/generate` request was sent with an empty `model` field because a local shell variable was not exported into the temporary payload-generation process.

That initial request returned:

```text
HTTP_STATUS:404
model '' not found
```

No model was downloaded, no model was pulled, no external network was called, and no repository file was modified. The request was corrected immediately by exporting the selected local model name and rerunning the minimal preview request recorded above.

## 13 Whether Real Generation Was Skipped Due To No Local Model

Real generation was not skipped.

Reason:

```text
Local models were already installed.
```

## 14 FastAPI Startup Command

Command:

```bash
LOCAL_LLM_PREVIEW_MOCK_API_ENABLED=true LOCAL_LLM_OLLAMA_PREVIEW_ADAPTER_ENABLED=true python3 -m uvicorn app.main:app --host 127.0.0.1 --port 18744
```

FastAPI constraints:

- host: `127.0.0.1`
- port: `18744`
- did not bind to `0.0.0.0`
- logs written only under `/tmp`
- no pytest run
- no browser started

## 15 FastAPI PID

Recorded PID:

```text
76845
```

## 16 FastAPI Request Endpoint

Request:

```text
POST http://127.0.0.1:18744/local-llm/preview-mock
```

HTTP status:

```text
200
```

## 17 FastAPI Request Body Summary

The request used the existing deterministic minimal API bridge payload:

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

No real tender file, real scoring data, real export task, or production artifact was used.

## 18 FastAPI Response Summary

Response summary:

- `enabled=true`
- `feature_flag=LOCAL_LLM_PREVIEW_MOCK_API_ENABLED`
- `adapter_enabled=true`
- `adapter_feature_flag=LOCAL_LLM_OLLAMA_PREVIEW_ADAPTER_ENABLED`
- `adapter=ollama_preview`
- `source=ollama_preview_adapter`
- `status=error`
- `preview_only=true`
- `no_write=true`
- `affects_score=false`
- `error_type=model_unavailable`
- `message=Ollama preview client is not configured.`
- `model=local-preview-no-real-model`
- `fallback_used=true`
- `fallback.mode=mock_fallback`
- `fallback.reason=model_unavailable`
- `fallback.preview_only=true`
- `fallback.no_write=true`
- `fallback.affects_score=false`

This confirms that the current API bridge entered the adapter preview branch but still did not pass a real client or transport through the FastAPI endpoint.

The FastAPI adapter-enabled branch remained preview-only, no-write, and `affects_score=false`.

## 19 FastAPI Service Stop Method

FastAPI was stopped by sending `kill` to PID `76845`.

The process exited after the request.

Port listener check for `18744` returned no listener after shutdown.

## 20 Whether Pytest Was Run

Pytest was not run.

## 21 Whether Ollama Was Run

Ollama was running through the 2nd window as `ollama serve`.

This execution window did not run `ollama serve`.

## 22 Whether Ollama Serve Was Run

`ollama serve` was running in the 2nd window.

This execution window did not start or stop `ollama serve`.

## 23 Whether A Real Model Was Called

Yes. A real local Ollama `/api/generate` request was executed against:

```text
qwen3-next:80b-a3b-instruct-q8_0
```

The FastAPI API bridge did not call a real model because the current endpoint does not pass a real client or transport into the adapter.

## 24 Whether Models Were Downloaded Or Pulled

- downloaded model: no
- pulled model: no
- `ollama pull`: not run

## 25 Whether External Networks Were Called

External networks were not called.

All runtime verification requests were limited to:

- `http://127.0.0.1:11434`
- `http://127.0.0.1:18744`

The later git push operation, if performed after report commit, is repository bookkeeping and not a model/runtime external call.

## 26 Whether OpenAI / Spark / Gemini Were Called

OpenAI, Spark, and Gemini were not called.

## 27 Whether Score Text Or Rescore Was Called

The smoke did not call:

- `score_text()`
- `score_text_v2`
- `/rescore`

## 28 Whether qingtian-results / evidence_trace / scoring_basis Were Accessed

The smoke did not access:

- qingtian-results
- `evidence_trace/latest`
- `scoring_basis/latest`

## 29 Whether app/storage.py Was Written

`app/storage.py` was not written.

## 30 Whether data/ Was Written

`data/` was not written.

## 31 Whether output/ Was Written

`output/` was not written.

## 32 Whether UI Was Connected

UI was not connected.

No browser was started.

## 33 Whether DOCX / JSON / Markdown Official Export Was Triggered

No DOCX, JSON, or Markdown official export chain was triggered.

## 34 Git Status After Runtime Smoke

Before writing this report:

```text
git status --short
```

returned no output.

```text
git diff --name-only
```

returned no output.

After this report is written, the only expected worktree change is:

```text
docs/local-llm-ollama-preview-real-model-smoke-report.md
```

## 35 2nd Window Follow-Up State Recommendation

The 2nd window should remain under ChatGPT control.

Recommended next action:

- wait for ChatGPT instruction on whether to stop or keep `ollama serve`

Codex nifei1227 must not close the 2nd window unless separately instructed.

## 36 Risk Notes

- Real local Ollama generation was verified only with a minimal `Return OK only.` prompt.
- The FastAPI endpoint still does not pass a real client or transport into the adapter.
- The FastAPI adapter branch therefore returned stable `model_unavailable` failure, as expected for the current implementation.
- This smoke does not prove production-ready local model integration.
- This smoke does not authorize UI integration.
- This smoke does not authorize scoring-chain integration.
- This smoke does not authorize storage writes.
- This smoke does not authorize export-chain integration.
- Future real endpoint-level Ollama integration would require a separately designed and authorized client/transport boundary.
- If any future step observes `data/`, `output/`, storage, qingtian-results, evidence trace, or scoring basis changes, it must stop and report.

## 37 Future Step 174Y Entry Conditions

Future Step 174Y must be separately authorized.

At minimum, future Step 174Y must clarify:

- whether `ollama serve` should remain running or be stopped
- whether endpoint-level real client/transport design is in scope
- whether only docs are allowed or code implementation is allowed
- whether app/main.py may be modified
- whether adapter code may be modified
- whether tests may be added or modified
- whether FastAPI service startup is allowed
- whether real Ollama calls are allowed
- the exact feature flag boundary
- the exact no-write verification boundary
- the exact no-scoring-chain verification boundary
- the exact report and git archival requirements

No automatic Step 174Y work is authorized by this report.
