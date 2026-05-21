# ZBid Preview-Only Receiver API Controlled Smoke Report

## 1. Scope

This report records Step 216: ZBid preview-only receiver API controlled smoke.

The smoke was limited to local runtime verification of the ZBid receiver API:

- Repository: `/Users/youfeini/Desktop/ZhiFei_BizSystem-local-llm-clean`
- Branch: `local-llm-integration-clean`
- Start HEAD: `e6ae41770cd545a233eace4601160053a89762ef`
- Endpoint under test: `POST /local-llm/zdoc-preview-only/receive`
- Runtime scope: preview-only / no-write / no-evidence receiver API only

This smoke did not call any ZDoc endpoint, did not call `/local-trial/preview-only`, did not perform cross-system integration, and did not enter any writeback path.

## 2. Preflight Result

Preflight checks passed before service startup:

- Current directory: `/Users/youfeini/Desktop/ZhiFei_BizSystem-local-llm-clean`
- Current branch: `local-llm-integration-clean`
- Start HEAD: `e6ae41770cd545a233eace4601160053a89762ef`
- `git status --short`: empty
- Target report file before this step: missing

The smoke did not start when the preflight was incomplete. No code, tests, existing docs, configuration, deployment scripts, databases, model files, cache files, or runtime output directories were modified before runtime verification.

## 3. Startup Method

The final successful service startup command was:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m uvicorn app.main:app --host 127.0.0.1 --port 18762
```

Runtime service details:

- Host: `127.0.0.1`
- Port: `18762`
- Uvicorn server process PID: `47894`
- Service startup result: successful
- Port readiness: `127.0.0.1:18762` listening before API call

Two earlier background-launch attempts did not remain running in the execution environment:

- PID `47174`: exited before listening; log file was empty.
- PID `47612`: exited before listening; log file was empty.

Those attempts did not expose a listening port, did not process API requests, and did not modify the git worktree.

## 4. Output Isolation Snapshot

The authorized read-only output snapshot command was:

```bash
find output job export -maxdepth 2 -type f 2>/dev/null | sort
```

Result:

- Before smoke: empty
- After receiver API call: empty
- After service shutdown: empty
- Difference: none

No file was written under `output/job/export`.

## 5. API Call List

Only the authorized receiver API was called:

```bash
POST http://127.0.0.1:18762/local-llm/zdoc-preview-only/receive
```

The smoke did not call:

- any ZDoc endpoint
- `/local-trial/preview-only`
- `/generate`
- `/export_docx`
- `/review/apply`
- any ZBid writeback endpoint
- any external API

## 6. Receiver API Runtime Result

The receiver API returned:

- HTTP status: `200`
- `receiver=zdoc_zbid_preview_receiver`
- `status=accepted_preview_only`
- `receiver_accepted=true`
- `preview_only=true`
- `no_write=true`
- `no_evidence=true`

Readable response fields:

- `preview_packet`: readable
- `validator_result`: readable
- `blocked_reasons`: readable

The response retained the original preview-only blocked reasons:

- `preview_only_is_not_writeback_permission`
- `preview_only_is_not_evidence`

## 7. Five False Flags Result

The five no-write / no-formal-chain flags were all false:

- `generate_called=false`
- `export_docx_called=false`
- `review_apply_called=false`
- `zbid_writeback_called=false`
- `output_job_export_written=false`

The nested `formal_chain_flags` also returned all five flags as false.

## 8. No Evidence / No Writeback Result

The receiver API returned:

- `produces_evidence=false`
- `produces_writeback=false`
- `writes_storage=false`
- `writes_scoring_basis=false`
- `calls_external_endpoint=false`

This confirms that the runtime receiver API response remained preview-only, no-write, and no-evidence for the tested payload.

## 9. Service Shutdown Result

The service process started for this smoke was stopped after the API verification:

- Stopped PID: `47894`
- Stop method: `kill 47894`
- Stop result: stopped
- Post-shutdown listener check: `127.0.0.1:18762` had no listening process

No destructive batch kill was used.

## 10. Strict Non-Occurrence Confirmation

During Step 216:

- No code was modified.
- No tests were modified.
- No existing docs were modified.
- Pytest was not run.
- Ollama was not run.
- No ZDoc endpoint was called.
- `/local-trial/preview-only` was not called.
- No business endpoint other than `POST /local-llm/zdoc-preview-only/receive` was called.
- `/generate` was not triggered.
- `/export_docx` was not triggered.
- `/review/apply` was not triggered.
- ZBid writeback was not triggered.
- No DOCX was generated.
- `output/job/export` was not written.
- Real ZDoc/ZBid integration was not entered.
- 50-person formal deployment design was not entered.
- No smoke failure was repaired by code changes.

## 11. Risk And Limitations

No high-risk condition was observed in this controlled smoke.

Remaining limitations:

- This smoke only verified the ZBid receiver API in a local runtime service.
- It did not call ZDoc.
- It did not perform ZDoc -> ZBid cross-system runtime smoke.
- It did not validate network delivery from ZDoc outbound adapter to ZBid receiver API.
- It did not expose or authorize any writeback path.
- It does not prove readiness for formal scoring, evidence creation, DOCX export, storage writes, or ZBid business data persistence.

## 12. Conclusion

Step 216 verified that the ZBid preview-only receiver API is runtime reachable at `POST /local-llm/zdoc-preview-only/receive` on local port `18762`.

The endpoint returned HTTP 200 with preview-only / no-write / no-evidence status, readable `preview_packet`, readable `validator_result`, readable `blocked_reasons`, and all five no-write / no-formal-chain flags as false.

The smoke did not call ZDoc, did not enter cross-system integration, did not trigger formal chains, did not generate DOCX, and did not write `output/job/export`.

## 13. Next Step Recommendation

Recommended next step:

Step 217: ZBid preview-only receiver API controlled smoke stage review.

Step 217 should be docs-only / stage-review-only. It should not start services, access ports, call APIs, modify code, run pytest, trigger writeback, or enter real ZDoc/ZBid integration.
