# Local LLM Ollama Response Normalization Runtime Smoke Report

## Purpose

This report records the Step 174AO controlled runtime smoke for the local LLM Ollama response normalization path.

The smoke verified `POST /local-llm/preview-mock` with the adapter real transport branch enabled, using only `127.0.0.1` loopback and the existing local `qwen3:0.6b` model. It did not modify API code, tests, UI, scoring chains, storage, export paths, or model inventory.

## Baseline

- Current directory: `/Users/youfeini/Desktop/ZhiFei_BizSystem-local-llm-clean`
- Current branch: `local-llm-integration-clean`
- Starting HEAD: `b80dc3ae536c13c6dd4c4558ecf5cd474d20e4a2`
- Required prior tag checked: `v0.1.72-local-llm-ollama-response-normalization-stage-review`
- Service listen address: `127.0.0.1`
- FastAPI smoke port: `18747`
- Ollama loopback address: `127.0.0.1:11434`

## 2nd Window Boundary

- 2nd window enabled: yes
- 2nd window role: run `ollama serve` only
- Codex did not close the 2nd window.
- Observed `ollama serve` process after FastAPI shutdown: PID `13196`
- 2nd window follow-up state: leave open or close only by later ChatGPT instruction.

## Ollama Reachability Check

Command:

```bash
curl -sS --max-time 5 -w '\nHTTP_STATUS:%{http_code}\n' http://127.0.0.1:11434/api/tags
```

Result:

- Ollama reachable: yes
- Source: `127.0.0.1`
- HTTP status: `200`
- Response format: valid JSON
- Timeout: no
- Connection refused: no
- Invalid `/api/tags` response: no
- Local model count: `7`
- `qwen3:0.6b` present: yes

Installed model summary:

- `qwen3-next:80b-a3b-instruct-q8_0`
- `qwen3-coder:30b`
- `deepseek-r1:32b`
- `qwen3:30b`
- `qwen3:14b`
- `qwen3:8b`
- `qwen3:0.6b`

Selected model:

```text
qwen3:0.6b
```

Selection reason:

- Step 174AO required `qwen3:0.6b`.
- `qwen3:0.6b` was present in local `/api/tags`.
- No fallback model was used.
- No model was downloaded or pulled.

## FastAPI Runtime Smoke

Startup command:

```bash
LOCAL_LLM_PREVIEW_MOCK_API_ENABLED=true LOCAL_LLM_OLLAMA_PREVIEW_ADAPTER_ENABLED=true LOCAL_LLM_OLLAMA_REAL_TRANSPORT_ENABLED=true LOCAL_LLM_OLLAMA_MODEL=qwen3:0.6b LOCAL_LLM_OLLAMA_TIMEOUT_SECONDS=30 LOCAL_LLM_OLLAMA_NUM_PREDICT=8 python3 -m uvicorn app.main:app --host 127.0.0.1 --port 18747
```

FastAPI PID:

```text
13734
```

Runtime configuration:

- endpoint feature flag: `LOCAL_LLM_PREVIEW_MOCK_API_ENABLED=true`
- adapter feature flag: `LOCAL_LLM_OLLAMA_PREVIEW_ADAPTER_ENABLED=true`
- real transport feature flag: `LOCAL_LLM_OLLAMA_REAL_TRANSPORT_ENABLED=true`
- model env: `LOCAL_LLM_OLLAMA_MODEL=qwen3:0.6b`
- timeout: `LOCAL_LLM_OLLAMA_TIMEOUT_SECONDS=30`
- num_predict: `LOCAL_LLM_OLLAMA_NUM_PREDICT=8`

Endpoint:

```text
POST http://127.0.0.1:18747/local-llm/preview-mock
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

The request used a minimal synthetic payload. It did not include real bid files, real scoring data, export jobs, formal review data, or production model parameters.

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
  "error_type": "invalid_response",
  "message": "Ollama response did not contain non-empty content.",
  "model": "qwen3:0.6b",
  "fallback_used": true,
  "fallback": {
    "mode": "mock_fallback",
    "reason": "invalid_response",
    "model": "qwen3:0.6b",
    "prompt_excerpt": "",
    "preview_only": true,
    "no_write": true,
    "affects_score": false
  }
}
```

Response HTTP status:

```text
200
```

Runtime result:

- endpoint entered adapter branch: yes
- endpoint entered real transport branch: yes
- response included `real_transport_enabled=true`: yes
- real transport accessed `127.0.0.1:11434`: yes
- access scope was loopback-only: yes
- response normalization status is `ok`: no
- failure error type: `invalid_response`
- response kept `preview_only=true`: yes
- response kept `no_write=true`: yes
- response kept `affects_score=false`: yes

## Service Shutdown

- FastAPI stop method: `kill -TERM 13734`
- FastAPI shutdown observed: yes
- Application shutdown complete: yes
- Finished server process: yes
- Port `18747` listener after shutdown: none
- `lsof -nP -iTCP:18747 -sTCP:LISTEN` returned no listener.

## Explicit Non-Integrations

- pytest was run: no
- Ollama was run by Codex: no
- `ollama serve` was run through the 2nd window: yes
- models were downloaded: no
- models were pulled: no
- `ollama pull` was executed: no
- external network was called: no
- OpenAI was called: no
- Spark was called: no
- Gemini was called: no
- `score_text()` was called: no
- `/rescore` was called: no
- `qingtian-results` was accessed: no
- `evidence_trace/latest` was accessed: no
- `scoring_basis/latest` was accessed: no
- `app/storage.py` was written: no
- `data/` was written: no
- `output/` was written: no
- UI was connected: no
- DOCX formal export was triggered: no
- JSON formal export was triggered: no
- Markdown formal export was triggered: no
- real-model production scoring chain was connected: no

## Git Status

Service-after check before writing this report:

```text
git status --short: clean
git diff --name-only: clean
```

Report self-check result:

```text
git status --short: docs/local-llm-ollama-response-normalization-runtime-smoke-report.md only
git diff --name-only: docs/local-llm-ollama-response-normalization-runtime-smoke-report.md only
```

## Risk Notes

- Step 174AO confirmed that the real transport branch still reaches local Ollama through `127.0.0.1:11434`.
- Step 174AO did not confirm an `ok` runtime response for `qwen3:0.6b`; the response remains a stable `invalid_response` failure.
- The `invalid_response` result is not a production scoring failure and must not be treated as a formal evaluation result.
- The result may still relate to runtime response content, model output format, prompt handling, or normalization assumptions.
- This smoke did not write storage, `data/`, or `output/`.
- This smoke did not connect UI, scoring chains, or export chains.
- Any follow-up response investigation must be separately authorized, must start with fake-only tests, and must not save full model output.

## Future Admission Conditions

Before any follow-up response parsing, runtime smoke, UI work, scoring-chain work, or export-chain work:

- ChatGPT must authorize the next step separately.
- The current Codex nifei1227 conversation should remain the only write-capable execution window for repository work.
- The 2nd window may run only `ollama serve` when a runtime smoke step explicitly requires it.
- FastAPI must listen only on `127.0.0.1`.
- Ollama access must remain limited to `127.0.0.1:11434`.
- No models may be downloaded.
- No models may be pulled.
- `ollama pull` must not be executed.
- No external network may be called.
- `data/`, `output/`, and storage must remain unwritten.
- `score_text`, `/rescore`, `qingtian-results`, `evidence_trace`, and `scoring_basis` must remain disconnected.
- UI and formal export chains must remain disconnected.

## Step 174AO Closure Statement

Step 174AO completed the controlled response normalization runtime smoke and archived the result in this report. The FastAPI service was stopped. The 2nd window was not closed by Codex. The runtime result remains `status=error` with `error_type=invalid_response`, while all preview-only, no-write, and no-scoring boundaries remained intact.
