# Local LLM Ollama Timeout and Model Controls Runtime Smoke Report

## Current directory

`/Users/youfeini/Desktop/ZhiFei_BizSystem-local-llm-clean`

## Current branch

`local-llm-integration-clean`

## Starting HEAD

`ddb59dbf1b372da97bc994cd99c9f6d5f1d223cd`

## 2nd window status

The runtime smoke used the required 2nd window boundary for `ollama serve`.

2nd window responsibility boundary:

- Only run `ollama serve`.
- Do not run git commands.
- Do not run pytest.
- Do not modify code.
- Do not commit, tag, or push.
- Do not write repository files.

Codex nifei1227 did not close the 2nd window. Its later shutdown status remains subject to ChatGPT instruction.

## Ollama reachability check

Command:

```bash
curl -sS --max-time 5 -w '\nHTTP_STATUS:%{http_code}\n' http://127.0.0.1:11434/api/tags
```

Result:

- reachable through `127.0.0.1:11434`: yes
- HTTP status: `200`
- response type: valid JSON
- timeout: no
- connection refused: no
- invalid response: no

## Local model inventory summary

Local `/api/tags` returned 7 installed models:

- `qwen3-next:80b-a3b-instruct-q8_0`
- `qwen3-coder:30b`
- `deepseek-r1:32b`
- `qwen3:30b`
- `qwen3:14b`
- `qwen3:8b`
- `qwen3:0.6b`

No model was downloaded. No model was pulled. `ollama pull` was not executed.

## Selected model

Selected model:

```text
qwen3:0.6b
```

Selection reason:

- Step 174AJ prioritizes `qwen3:0.6b` when it exists in `/api/tags`.
- `qwen3:0.6b` was present locally.
- The model is the lightest available model in the returned local inventory.
- No fallback to `qwen3:8b` or the first model was needed.

## FastAPI startup

Command:

```bash
LOCAL_LLM_PREVIEW_MOCK_API_ENABLED=true LOCAL_LLM_OLLAMA_PREVIEW_ADAPTER_ENABLED=true LOCAL_LLM_OLLAMA_REAL_TRANSPORT_ENABLED=true LOCAL_LLM_OLLAMA_MODEL=qwen3:0.6b LOCAL_LLM_OLLAMA_TIMEOUT_SECONDS=30 LOCAL_LLM_OLLAMA_NUM_PREDICT=8 python3 -m uvicorn app.main:app --host 127.0.0.1 --port 18746
```

FastAPI PID:

```text
5061
```

Listening boundary:

- host: `127.0.0.1`
- port: `18746`
- did not listen on `0.0.0.0`

## Runtime configuration values

Timeout:

```text
LOCAL_LLM_OLLAMA_TIMEOUT_SECONDS=30
```

`num_predict`:

```text
LOCAL_LLM_OLLAMA_NUM_PREDICT=8
```

Model:

```text
LOCAL_LLM_OLLAMA_MODEL=qwen3:0.6b
```

Feature flags:

```text
LOCAL_LLM_PREVIEW_MOCK_API_ENABLED=true
LOCAL_LLM_OLLAMA_PREVIEW_ADAPTER_ENABLED=true
LOCAL_LLM_OLLAMA_REAL_TRANSPORT_ENABLED=true
```

## FastAPI request

Endpoint:

```text
POST http://127.0.0.1:18746/local-llm/preview-mock
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

The request used a minimal synthetic payload from the deterministic API test shape. It did not use real bid files, real scoring data, export tasks, or formal review data.

## FastAPI response

HTTP status:

```text
200
```

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
    "preview_only": true,
    "no_write": true,
    "affects_score": false
  }
}
```

Runtime result:

- endpoint feature flag enabled: yes
- adapter feature flag enabled: yes
- real transport feature flag enabled: yes
- timeout environment value applied: `30`
- `num_predict` environment value applied: `8`
- configured local model: `qwen3:0.6b`
- entered adapter branch: yes
- entered real transport branch: yes
- real transport accessed `127.0.0.1:11434`: yes
- status: `error`
- error type: `invalid_response`
- stable failure schema: yes
- `preview_only=true`: yes
- `no_write=true`: yes
- `affects_score=false`: yes

The result is a controlled preview failure. It is not a formal scoring failure and does not indicate production scoring behavior.

## Runtime non-integrations

pytest was not run.

Ollama was used only through the 2nd window running `ollama serve`.

`ollama serve` was not started or stopped by Codex nifei1227.

No model was downloaded.

No model was pulled.

`ollama pull` was not executed.

No external network was called.

OpenAI was not called.

Spark was not called.

Gemini was not called.

`score_text` was not called.

`/rescore` was not called.

`qingtian-results` was not accessed.

`evidence_trace/latest` was not accessed.

`scoring_basis/latest` was not accessed.

`app/storage.py` was not written.

`data/` was not written.

`output/` was not written.

UI was not connected.

DOCX formal export was not triggered.

JSON formal export was not triggered.

Markdown formal export was not triggered.

No real-model production scoring chain was connected.

## FastAPI shutdown

Stop method:

```text
kill -TERM 5061
```

Shutdown result:

- FastAPI service stopped: yes
- process `5061` exited: yes
- port `18746` listener after shutdown: none

## Git status after runtime smoke

Before adding this report, service-after self-check showed:

```text
git status --short: clean
git diff --name-only: clean
```

After this report is added, the only intended repository change is:

```text
docs/local-llm-ollama-timeout-model-runtime-smoke-report.md
```

## 2nd window follow-up recommendation

Do not close the 2nd window from Codex nifei1227.

ChatGPT should decide whether the 2nd window running `ollama serve` remains open or is stopped in a later explicit instruction.

## Risk statement

- The real transport branch was reached with controlled timeout and model selection settings.
- The selected lightweight local model returned a stable `invalid_response` failure for this payload, not an `ok` response.
- This stable preview failure is not a production scoring failure.
- This runtime smoke does not authorize UI integration.
- This runtime smoke does not authorize scoring-chain integration.
- This runtime smoke does not authorize export-chain integration.
- This runtime smoke does not authorize writing storage, `data/`, or `output/`.
- Further investigation of the `invalid_response` behavior must be separately authorized and must not bypass the preview-only, no-write, no-scoring boundaries.

## Follow-up admission conditions

Before any next stage:

- ChatGPT must explicitly authorize the next step.
- Any runtime stage must keep FastAPI bound to `127.0.0.1`.
- Ollama access must remain limited to `127.0.0.1:11434`.
- No model may be downloaded.
- No model may be pulled.
- `ollama pull` must not be executed.
- External networks must not be called.
- `data/`, `output/`, and storage must not be written.
- `score_text`, `/rescore`, `qingtian-results`, `evidence_trace/latest`, and `scoring_basis/latest` must remain disconnected.
- UI and export chains must remain disconnected unless separately designed and authorized.
