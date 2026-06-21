# Local LLM Ollama Real Transport Runtime Smoke Report

## 1 Current Directory

`/Users/youfeini/Desktop/ZhiFei_BizSystem-local-llm-clean`

## 2 Current Branch

`local-llm-integration-clean`

## 3 Starting HEAD

`46286bc5d33ddcfb5c7a5174adeada745c3b9424`

## 4 2nd Window Status

The Step 174AD runtime smoke used the 2nd window boundary for local Ollama.

The 2nd window responsibility boundary was:

- run `ollama serve`
- do not run git
- do not run pytest
- do not modify code
- do not commit, tag, or push
- do not download models
- do not pull models

Codex nifei1227 did not close the 2nd window. The 2nd window should remain under ChatGPT's later instruction.

## 5 Ollama Reachability Check

Command:

```bash
curl -sS --max-time 5 http://127.0.0.1:11434/api/tags
```

Result:

- Ollama reachable: yes
- source: `127.0.0.1`
- response type: valid JSON
- model count: `7`
- no timeout
- no connection refused
- no invalid response

Installed local model summary:

- `qwen3-next:80b-a3b-instruct-q8_0`
- `qwen3-coder:30b`
- `deepseek-r1:32b`
- `qwen3:30b`
- `qwen3:14b`
- `qwen3:8b`
- `qwen3:0.6b`

## 6 Model Used

Environment variable:

```text
LOCAL_LLM_OLLAMA_MODEL=qwen3-next:80b-a3b-instruct-q8_0
```

The target model was present in `/api/tags`.

No model was downloaded. No model was pulled. `ollama pull` was not executed.

## 7 FastAPI Startup

Command:

```bash
LOCAL_LLM_PREVIEW_MOCK_API_ENABLED=true LOCAL_LLM_OLLAMA_PREVIEW_ADAPTER_ENABLED=true LOCAL_LLM_OLLAMA_REAL_TRANSPORT_ENABLED=true LOCAL_LLM_OLLAMA_MODEL=qwen3-next:80b-a3b-instruct-q8_0 python3 -m uvicorn app.main:app --host 127.0.0.1 --port 18745
```

Service address:

```text
http://127.0.0.1:18745
```

FastAPI PID:

```text
91540
```

Startup result:

- FastAPI started successfully
- host was `127.0.0.1`
- port was `18745`
- no `0.0.0.0` listener was used

Note: initial background launch attempts did not persist after the shell exited and produced no repository changes. The successful smoke used the foreground service process above and then stopped it explicitly.

## 8 FastAPI Request

Endpoint:

```text
POST http://127.0.0.1:18745/local-llm/preview-mock
```

Request body summary:

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

The request did not include real bid documents, real scoring data, real export tasks, or formal review data.

## 9 FastAPI Response

Response summary:

```json
{
  "enabled": true,
  "feature_flag": "LOCAL_LLM_PREVIEW_MOCK_API_ENABLED",
  "adapter_enabled": true,
  "adapter_feature_flag": "LOCAL_LLM_OLLAMA_PREVIEW_ADAPTER_ENABLED",
  "real_transport_enabled": true,
  "real_transport_feature_flag": "LOCAL_LLM_OLLAMA_REAL_TRANSPORT_ENABLED",
  "adapter": "ollama_preview",
  "source": "ollama_preview_adapter",
  "status": "error",
  "preview_only": true,
  "no_write": true,
  "affects_score": false,
  "error_type": "timeout",
  "message": "Ollama preview request timed out.",
  "model": "qwen3-next:80b-a3b-instruct-q8_0",
  "fallback_used": true,
  "fallback": {
    "mode": "mock_fallback",
    "reason": "timeout",
    "model": "qwen3-next:80b-a3b-instruct-q8_0",
    "prompt_excerpt": "sample tender response excerpt",
    "preview_only": true,
    "no_write": true,
    "affects_score": false
  }
}
```

HTTP result:

- status code: `200`
- endpoint feature flag enabled: yes
- adapter feature flag enabled: yes
- real transport feature flag enabled: yes
- entered adapter branch: yes
- entered real transport branch: yes
- real transport model: `qwen3-next:80b-a3b-instruct-q8_0`
- real transport result: stable `timeout` failure
- `preview_only=true`: yes
- `no_write=true`: yes
- `affects_score=false`: yes

The timeout is a stable failure schema result. It does not mean the response entered scoring, storage, UI, or export chains.

## 10 Real Transport Verification

The smoke performed local loopback access only:

- Ollama tags: `http://127.0.0.1:11434/api/tags`
- FastAPI endpoint: `http://127.0.0.1:18745/local-llm/preview-mock`
- adapter transport target: `http://127.0.0.1:11434`

The endpoint response showed:

- `adapter="ollama_preview"`
- `real_transport_enabled=true`
- `model="qwen3-next:80b-a3b-instruct-q8_0"`
- `error_type="timeout"`

This confirms the runtime path reached the real transport branch and returned a bounded failure rather than falling into any production chain.

## 11 Runtime Boundaries

- pytest: not run
- Ollama: run through 2nd window `ollama serve`
- `ollama serve`: run through 2nd window
- model download: not performed
- model pull: not performed
- `ollama pull`: not executed
- external network: not called
- OpenAI / Spark / Gemini: not called
- `score_text` / `rescore`: not called
- qingtian-results / `evidence_trace` / `scoring_basis`: not accessed
- `app/storage.py`: not written
- `data/`: not written
- `output/`: not written
- UI: not connected
- DOCX / JSON / Markdown official export: not triggered

## 12 FastAPI Shutdown

FastAPI was stopped by sending interrupt to the foreground uvicorn process.

Shutdown result:

- application shutdown completed
- server process `91540` finished
- port `18745` had no listener after shutdown

The 2nd window running `ollama serve` was not closed by Codex nifei1227.

## 13 Git Status After Runtime Smoke

Before writing this smoke report, service shutdown checks showed no repository changes.

After writing this smoke report, the expected untracked file is:

```text
docs/local-llm-ollama-real-transport-runtime-smoke-report.md
```

No code, test, storage, `data/`, `output/`, UI, export, release guard, smoke guard, or ops_agents file was modified.

## 14 2nd Window Follow-Up Recommendation

The 2nd window should remain under ChatGPT control.

Recommended follow-up:

- ChatGPT decides whether to stop `ollama serve`
- Codex nifei1227 should not close the 2nd window without explicit instruction
- no further real Ollama smoke should start automatically

## 15 Risks

- The selected `qwen3-next:80b-a3b-instruct-q8_0` model was present, but the FastAPI real transport request timed out under the current adapter timeout.
- The timeout is expected to remain a stable failure until a separately authorized stage adjusts model choice, timeout, or smoke scenario.
- This smoke does not authorize production scoring.
- This smoke does not authorize UI connection.
- This smoke does not authorize export-chain connection.
- This smoke does not authorize storage writes.
- This smoke does not authorize `data/` or `output/` writes.
- Future troubleshooting must not modify scoring chain, UI, export chain, or storage to make the smoke pass.
- Future smoke work must remain loopback-only and must not download or pull models unless separately authorized.

## 16 Follow-Up Entry Conditions

Before any next step, the controller must explicitly define:

- whether `ollama serve` in the 2nd window should remain running or be stopped
- whether another runtime smoke is allowed
- whether a smaller local model may be selected for runtime smoke
- whether timeout changes are allowed
- whether code changes are allowed
- whether tests may be run
- whether a docs-only follow-up is required
- continued no-write boundary for `data/`, `output/`, and storage
- continued no-scoring-chain boundary
- continued no-UI boundary
- continued no-export boundary

Completion must stop for ChatGPT review.
