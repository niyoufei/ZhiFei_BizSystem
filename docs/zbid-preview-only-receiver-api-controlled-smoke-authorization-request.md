# ZBid Preview-Only Receiver API Controlled Smoke Authorization Request

## 1. Authorization Request Source

This document drafts the authorization request for a future ZBid preview-only receiver API controlled smoke. It is based on:

- Step 213: ZBid preview-only receiver API exposure code implementation.
- Step 214: ZBid preview-only receiver API exposure code implementation stage review.

This document is authorization-request-only. It does not grant authorization by itself, does not execute runtime smoke, does not start services, does not access ports, and does not call any ZBid or ZDoc endpoint.

## 2. Current ZBid Capability Baseline

The current ZBid-side preview-only baseline is:

- `app/engine/zdoc_zbid_preview_receiver.py` has been added.
- `tests/test_zdoc_zbid_preview_receiver.py` has been added.
- `app/main.py` exposes `POST /local-llm/zdoc-preview-only/receive`.
- The endpoint only calls `app.engine.zdoc_zbid_preview_receiver.receive_zdoc_zbid_preview_payload`.
- The endpoint is preview-only, no-write, and no-evidence.
- The endpoint has passed in-process unit tests only.
- Runtime smoke for the exposed endpoint has not been performed.

The Step 214 recorded verification result was:

- `python -m pytest tests/test_zdoc_zbid_preview_receiver_api.py tests/test_zdoc_zbid_preview_receiver.py -vv`: 18 passed.
- `git diff --check`: passed.
- `git diff --cached --check`: passed.
- Commit hook static checks: passed.

## 3. Requested Future Smoke Scope

The future Step 216 smoke would request explicit user authorization to:

- Start the necessary local ZBid service only for receiver API runtime verification.
- Access the local ZBid service port.
- Call `POST /local-llm/zdoc-preview-only/receive`.
- Use a preview-only payload to verify that the receiver API is reachable.
- Verify that the response remains preview-only, no-write, and no-evidence.
- Verify that `preview_packet`, `validator_result`, and `blocked_reasons` are readable.
- Verify that all five no-write / no-formal-chain flags remain false.
- Stop the service process started for the smoke.

The smoke is limited to local runtime verification of the ZBid receiver API. It must not perform cross-system integration.

## 4. Smoke Limitations

The future controlled smoke must remain limited to ZBid receiver API local runtime behavior:

- It must not call any ZDoc endpoint.
- It must not perform ZDoc -> ZBid cross-system integration.
- It must not perform real business writeback.
- It must not generate DOCX.
- It must not write `output/job/export`.
- It must not modify code, tests, docs, configuration, deployment scripts, databases, cache files, model files, or runtime data.
- It must not fix failures during the smoke. Any failure must be recorded and reported as a smoke result.

## 5. Required Flag Verification

The future Step 216 smoke must verify that these five flags are returned as false:

- `generate_called=false`
- `export_docx_called=false`
- `review_apply_called=false`
- `zbid_writeback_called=false`
- `output_job_export_written=false`

If any of these flags is true, the smoke must stop and report high risk.

## 6. Explicitly Not Authorized By This Request

This authorization request does not authorize:

- Triggering `/generate`.
- Triggering `/export_docx`.
- Triggering `/review/apply`.
- Triggering ZBid writeback.
- Generating DOCX.
- Writing `output/job/export`.
- Running Ollama.
- Calling a ZDoc endpoint.
- Calling any endpoint other than the future explicitly authorized receiver API.
- Entering real ZDoc/ZBid integration.
- Entering 50-person formal deployment design.
- Treating advisory, preview, shadow, patch, diff, rollback, or dry-run output as evidence.
- Writing ZBid formal business data.
- Writing ZDoc data.

## 7. Proposed Step 216 Scope

If the user later explicitly authorizes Step 216, the allowed runtime scope should be:

- Repository: `/Users/youfeini/Desktop/ZhiFei_BizSystem-local-llm-clean`
- Branch: `local-llm-integration-clean`
- Start HEAD: `141cb1b6139334b870cf2c44428ef36cbc521957`
- Start the minimum necessary local ZBid service.
- Access the local ZBid service port.
- Call only `POST /local-llm/zdoc-preview-only/receive`.
- Use only preview-only / no-write / no-evidence payloads.
- Verify `preview_packet`, `validator_result`, `blocked_reasons`, and five false flags.
- Stop the service started for the smoke.

Step 216 must not:

- Modify code.
- Modify tests.
- Modify docs.
- Fix failed smoke findings.
- Call a ZDoc endpoint.
- Perform ZDoc -> ZBid cross-system integration.
- Trigger formal chains.
- Write back to ZBid or ZDoc.

## 8. Hard Stop Conditions For Future Smoke

Future Step 216 execution must stop immediately if any of the following occurs:

- Repository path is incorrect.
- Branch is incorrect.
- Start HEAD is incorrect.
- `git status --short` is not clean.
- Any unauthorized endpoint is called.
- Any ZDoc endpoint is called.
- `/generate` is triggered.
- `/export_docx` is triggered.
- `/review/apply` is triggered.
- ZBid writeback is triggered.
- DOCX is generated.
- `output/job/export` is written.
- Ollama is run.
- Any no-write / no-formal-chain flag becomes true.
- The receiver response is not preview-only.
- The receiver response is not no-write.
- The receiver response produces evidence.
- The receiver writes storage, database, score basis, qingtian results, files, or formal business data.
- The local service cannot be stopped.

## 9. Future Smoke Report Requirements

The future Step 216 smoke report should include:

- User authorization text.
- Repository path.
- Branch.
- Start HEAD.
- End HEAD.
- `git status --short` before and after.
- Service start command.
- Service PID.
- Local port accessed.
- API called.
- Receiver API HTTP status.
- Whether `preview_packet` is readable.
- Whether `validator_result` is readable.
- Whether `blocked_reasons` is readable.
- Whether `generate_called=false`.
- Whether `export_docx_called=false`.
- Whether `review_apply_called=false`.
- Whether `zbid_writeback_called=false`.
- Whether `output_job_export_written=false`.
- Whether response is preview-only / no-write / no-evidence.
- Whether any ZDoc endpoint was called.
- Whether `/generate`, `/export_docx`, `/review/apply`, or ZBid writeback was triggered.
- Whether DOCX was generated.
- Whether `output/job/export` was written.
- Whether service shutdown succeeded.
- Risks and limitations.
- Next-step recommendation.

## 10. User Confirmation Wording

Before Step 216 may be executed, the user should explicitly reply with wording equivalent to:

> 我授权执行 Step 216 ZBid preview-only receiver API controlled smoke，仓库限定为 `/Users/youfeini/Desktop/ZhiFei_BizSystem-local-llm-clean`，分支限定为 `local-llm-integration-clean`，开始前 HEAD 必须为 `141cb1b6139334b870cf2c44428ef36cbc521957`；允许启动必要本地服务、访问本地端口并调用 `POST /local-llm/zdoc-preview-only/receive`；仅限 preview-only / no-write / no-evidence 验证；不得调用 ZDoc endpoint，不得触发 `/generate`、`/export_docx`、`/review/apply`、ZBid 写回，不得生成 DOCX，不得写 `output/job/export`，不得进入真实 ZDoc/ZBid 联调，不得进入 50 人正式部署设计。

Without the above or equivalent explicit authorization, Step 216 must not be executed.

## 11. Next Step Recommendation

Recommended next step:

ZBid Step 216: ZBid preview-only receiver API controlled smoke.

Step 216 must wait for explicit user authorization. It may only start the necessary local service, access the local port, and call `POST /local-llm/zdoc-preview-only/receive` for preview-only / no-write / no-evidence runtime verification. It must not modify code, fix failed findings, call ZDoc endpoints, perform cross-system integration, or trigger any writeback path.

## 12. Safety Conclusion

Step 215 only drafts the ZBid preview-only receiver API controlled smoke authorization request. It does not grant authorization, does not start services, does not access ports, does not call any API, does not run smoke, does not write back to ZBid or ZDoc, does not generate DOCX, does not write `output/job/export`, and does not enter real ZDoc/ZBid integration or 50-person formal deployment design.
