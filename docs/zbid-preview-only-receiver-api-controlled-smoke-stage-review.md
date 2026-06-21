# ZBid Preview-Only Receiver API Controlled Smoke Stage Review

## 1. Scope

This document archives Step 216: ZBid preview-only receiver API controlled smoke.

Step 216 was explicitly authorized only for local runtime verification of the ZBid receiver API. The authorized runtime scope was:

- Verify that the ZBid receiver API is locally reachable.
- Call only `POST /local-llm/zdoc-preview-only/receive`.
- Verify preview-only / no-write / no-evidence response status.
- Verify readable `preview_packet`, `validator_result`, and `blocked_reasons`.
- Verify five no-write / no-formal-chain flags are false.

Step 216 did not authorize:

- Calling any ZDoc endpoint.
- Calling `/local-trial/preview-only`.
- Entering real ZDoc/ZBid integration.
- Triggering formal generation, export, review/apply, or writeback chains.

## 2. Runtime Startup And Access Result

Step 216 used the following final successful startup command:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m uvicorn app.main:app --host 127.0.0.1 --port 18762
```

Runtime service result:

- Service address: `127.0.0.1:18762`
- Service PID: `47894`
- Startup result: successful
- Smoke completion: PID `47894` was stopped
- Post-smoke port state: `127.0.0.1:18762` had no listening process

Two earlier background-launch attempts exited before listening and did not process requests or modify the worktree:

- PID `47174`
- PID `47612`

## 3. Interface Verification Result

Step 216 called only:

```bash
POST /local-llm/zdoc-preview-only/receive
```

The runtime result was:

- HTTP status: `200`
- receiver API runtime: reachable
- `receiver=zdoc_zbid_preview_receiver`
- `status=accepted_preview_only`
- `receiver_accepted=true`
- `preview_only=true`
- `no_write=true`
- `no_evidence=true`

No other business API was called.

## 4. Data Field Verification Result

Step 216 verified that the receiver API response exposed the required preview-only fields:

- `preview_packet`: readable
- `validator_result`: readable
- `blocked_reasons`: readable

The response retained the preview-only blocked reasons:

- `preview_only_is_not_writeback_permission`
- `preview_only_is_not_evidence`

These fields were treated as preview metadata only. They were not promoted to evidence and were not written to storage.

## 5. Five No-Write / No-Formal-Chain Flags

Step 216 verified that all five no-write / no-formal-chain flags were false:

- `generate_called=false`
- `export_docx_called=false`
- `review_apply_called=false`
- `zbid_writeback_called=false`
- `output_job_export_written=false`

The nested `formal_chain_flags` object also returned the same five flags as false.

## 6. No Evidence / No Writeback Result

The runtime receiver response also returned:

- `produces_evidence=false`
- `produces_writeback=false`
- `writes_storage=false`
- `writes_scoring_basis=false`
- `calls_external_endpoint=false`

This confirms that the receiver API runtime smoke remained preview-only, no-write, and no-evidence for the tested payload.

## 7. Output Isolation Result

Step 216 checked `output/job/export` before and after the smoke using:

```bash
find output job export -maxdepth 2 -type f 2>/dev/null | sort
```

Result:

- Before smoke: empty
- After receiver API call: empty
- After service shutdown: empty
- Difference: none

No file was written under `output/job/export`.

## 8. Safety Boundary Result

During Step 216:

- Ollama was not run.
- No ZDoc endpoint was called.
- `/local-trial/preview-only` was not called.
- `/generate` was not triggered.
- `/export_docx` was not triggered.
- `/review/apply` was not triggered.
- ZBid writeback was not triggered.
- No external API was called.
- No DOCX was generated.
- `output/job/export` was not written.
- No code was modified.
- No tests were modified.
- No existing docs were modified.
- No smoke failure was repaired by code changes.
- Real ZDoc/ZBid integration was not entered.
- 50-person formal deployment design was not entered.

## 9. Verification Result

Step 216 verification and commit checks recorded:

- `git diff --check`: passed
- `git diff --cached --check`: passed
- Commit hook static checks: passed

The Step 216 smoke report was committed as:

- Commit: `1a089c0a558725b1c29667bc71708b7db23dc268`
- Tag: `v0.1.269-zbid-preview-only-receiver-api-controlled-smoke`

## 10. Risk Conclusion

The controlled smoke found no high-risk runtime issue in the ZBid receiver API itself.

However, the result is intentionally narrow:

- It only proves that the ZBid receiver API is locally runtime reachable.
- It does not prove ZDoc -> ZBid cross-system integration.
- It does not prove ZDoc outbound adapter network delivery.
- It does not authorize or prove writeback.
- It does not open any evidence chain.
- It does not open any scoring chain.
- It does not open DOCX export.
- It does not open storage writes or formal business data persistence.

## 11. Remaining Work

The following work remains outside Step 216 and outside this stage review:

- ZDoc -> ZBid cross-system runtime smoke.
- ZDoc outbound adapter network-send authorization and implementation, if needed.
- Any ZBid receiver API exposure beyond the current preview-only endpoint.
- Any storage, evidence, scoring, DOCX export, or writeback behavior.
- Any 50-person formal deployment design.

Each of those actions requires separate explicit user authorization.

## 12. Next Step Recommendation

Recommended next step options:

- Return to the ZDoc repository and draft a ZDoc-ZBid preview-only cross-system smoke authorization request.
- Or first draft a ZDoc outbound adapter network-send authorization request.

Any future cross-system service startup, port access, endpoint call, real integration, or writeback-related action must receive separate explicit authorization before execution.

## 13. Safety Conclusion

Step 217 is docs-only / stage-review-only. It only archives the Step 216 controlled smoke result.

This document does not authorize Step 218, does not start services, does not access ports, does not call any API, does not run pytest, does not run Ollama, does not write `output/job/export`, does not generate DOCX, does not trigger `/generate`, `/export_docx`, `/review/apply`, or ZBid writeback, and does not enter real ZDoc/ZBid integration or 50-person formal deployment design.
