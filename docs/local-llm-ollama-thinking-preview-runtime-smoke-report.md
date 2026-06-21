# Local LLM Ollama Thinking Preview Runtime Smoke Report

## Purpose

This document records the Step 174AX runtime smoke verification for the local LLM Ollama thinking preview normalization path. The verification used only local loopback addresses and the existing local model `qwen3:0.6b`.

This step did not modify application code, tests, UI, scoring-chain code, export-chain code, `data/`, `output/`, or storage code.

## Baseline

- Current directory: `/Users/youfeini/Desktop/ZhiFei_BizSystem-local-llm-clean`
- Current branch: `local-llm-integration-clean`
- Start HEAD: `ba4d552031a7391e66f5aa0858b2af0afa1f80a9`
- Required prior tag present: `v0.1.81-local-llm-ollama-thinking-preview-normalization-stage-review`
- Initial `git status --short`: no output

## 2nd Window Boundary

- 2nd window requested: yes
- 2nd window allowed command: `ollama serve`
- 2nd window actual command: `ollama serve`
- 2nd window result: exited with `listen tcp 127.0.0.1:11434: bind: address already in use`
- Other commands in 2nd window: none

The bind failure showed that an existing local Ollama service was already listening on `127.0.0.1:11434`. The runtime smoke continued against that existing local loopback listener. No attempt was made to download, pull, replace, or switch models.

## Ollama Reachability

- Ollama reachability command: `curl -sS --max-time 5 -w '\nHTTP_STATUS:%{http_code}\n' http://127.0.0.1:11434/api/tags`
- Address used: `127.0.0.1`
- HTTP status: `200`
- JSON validity: valid JSON object
- Listener evidence: `ollama` PID `86046` listening on `127.0.0.1:11434`
- Installed model summary:
  - `qwen3-next:80b-a3b-instruct-q8_0`
  - `qwen3-coder:30b`
  - `deepseek-r1:32b`
  - `qwen3:30b`
  - `qwen3:14b`
  - `qwen3:8b`
  - `qwen3:0.6b`
- Required model present: yes
- Selected model: `qwen3:0.6b`

## FastAPI Runtime Smoke

FastAPI startup command:

```bash
LOCAL_LLM_PREVIEW_MOCK_API_ENABLED=true LOCAL_LLM_OLLAMA_PREVIEW_ADAPTER_ENABLED=true LOCAL_LLM_OLLAMA_REAL_TRANSPORT_ENABLED=true LOCAL_LLM_OLLAMA_MODEL=qwen3:0.6b LOCAL_LLM_OLLAMA_TIMEOUT_SECONDS=30 LOCAL_LLM_OLLAMA_NUM_PREDICT=8 python3 -m uvicorn app.main:app --host 127.0.0.1 --port 18748
```

- FastAPI host: `127.0.0.1`
- FastAPI port: `18748`
- FastAPI PID: `87376`
- Endpoint: `POST http://127.0.0.1:18748/local-llm/preview-mock`
- Timeout configuration: `30`
- `num_predict` configuration: `8`
- Model configuration: `qwen3:0.6b`
- Request body summary:
  - `project_id`: synthetic `p1`
  - `submission_id`: synthetic `s1`
  - `text_excerpt`: synthetic excerpt
  - `mode`: `preview_only`
  - `requested_by`: `operator`
  - synthetic `scoring_context`, `evidence_context`, and `requirement_hits`
- Real tender files used: no
- Real scoring data used: no
- Export task used: no

## Response Summary

- FastAPI response HTTP status: `200`
- Endpoint feature flag enabled: yes
- Adapter feature flag enabled: yes
- Real transport feature flag enabled: yes
- Adapter: `ollama_preview`
- Source: `ollama_preview_adapter`
- Entered real transport branch: yes
- Real transport target: `127.0.0.1:11434`
- Response normalization status: `ok`
- `preview_only`: `true`
- `no_write`: `true`
- `affects_score`: `false`
- `content_source`: `thinking`
- `preview_mode`: `thinking_preview`
- `preview_text_length`: `24`
- `raw_response_included`: `false`
- Complete `thinking` saved: no
- Complete model output saved: no
- Failure `error_type`: not applicable because status was `ok`

The response contained only bounded preview metadata and a short advisory summary. This report does not record the full Ollama response and does not record complete `thinking` text.

## Service Stop Verification

- FastAPI stop method: Ctrl-C sent to the uvicorn process
- FastAPI shutdown result: application shutdown complete, server process `87376` finished
- Port `18748` after stop: no listener
- 2nd window after smoke: no active 2nd-window `ollama serve` process from this step, because the command exited on bind conflict
- Existing local Ollama listener: left untouched

## No-Test Boundary

- `pytest` run: no
- New tests added: no
- Existing tests modified: no

## No-Download Boundary

- Model download performed: no
- Model pull performed: no
- `ollama pull` performed: no
- Model switch performed: no
- Selected existing model: `qwen3:0.6b`

## No-External-API Boundary

- OpenAI called: no
- Spark called: no
- Gemini called: no
- External model API called: no
- Runtime network target used: local loopback only

## No-Scoring-Chain Boundary

- `score_text` called: no
- `/rescore` called: no
- `qingtian-results` accessed: no
- `evidence_trace/latest` accessed: no
- `scoring_basis/latest` accessed: no
- Real model production scoring chain connected: no

## No-Write Boundary

- `app/storage.py` written: no
- `data/` written: no
- `output/` written: no
- Storage written: no
- Full model output saved: no
- Full `thinking` saved: no

## No-UI And No-Export Boundary

- UI connected: no
- DOCX export triggered: no
- JSON export triggered: no
- Markdown formal export triggered: no
- Export chain connected: no

## Git Status After Runtime Smoke

- `git status --short` after FastAPI stopped and before this report was written: no output
- `git diff --name-only` after FastAPI stopped and before this report was written: no output

## Risk Notes

- The 2nd-window `ollama serve` command did not remain running because an existing local Ollama listener already occupied `127.0.0.1:11434`.
- The runtime smoke succeeded against the existing local loopback Ollama service, not against a newly started `ollama serve` process from this step.
- The smoke result confirms this endpoint and this local runtime path returned `status=ok` with `content_source=thinking` and `preview_mode=thinking_preview` for the synthetic request.
- This smoke does not make the feature production scoring ready.
- This smoke did not connect UI, scoring chain, storage, or export chain.
- Future work must not use this result as permission to write `thinking` into scoring evidence, official exports, `data/`, `output/`, or storage.

## Next-Stage Admission Conditions

Before any later UI, scoring-chain, export-chain, or production integration work:

- ChatGPT must explicitly authorize the next step.
- The same Codex nifei1227 thread should continue unless instructed otherwise.
- Any runtime smoke with Ollama must again be scoped to `127.0.0.1`.
- Any 2nd-window use must be separately bounded.
- No model download or pull may occur without separate authorization.
- No writes to `data/`, `output/`, storage, `qingtian-results`, `evidence_trace`, or `scoring_basis` may occur without separate authorization.
- UI and export-chain work must remain blocked until explicitly authorized.

## Step 174AX Closure Statement

Step 174AX completed a local runtime smoke of the FastAPI real transport path against the existing local Ollama listener on `127.0.0.1:11434` using `qwen3:0.6b`. The endpoint returned `status=ok`, `content_source=thinking`, `preview_mode=thinking_preview`, and `preview_text_length=24`. FastAPI was stopped and port `18748` was confirmed clear. No code, tests, UI, scoring-chain, export-chain, `data/`, `output/`, or storage files were modified.
