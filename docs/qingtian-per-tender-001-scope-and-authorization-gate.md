# QINGTIAN-PER-TENDER-001 Scope And Authorization Gate

## 1. Baseline

- Repository: `/Users/youfeini/Desktop/ZhiFei_BizSystem`
- Branch: `main`
- Baseline HEAD: `2b28cdc58278e0ae165f234346e66d27e626f870`
- Baseline tag: `v0.1.33-qingtian-main-worktree-untracked-inventory`
- Gate node: `QINGTIAN-PER-TENDER-001-SCOPE-AUTHORIZATION-GATE`
- This document is a scope and authorization gate only. It does not enter implementation.

## 2. Worktree Dirty Baseline

The worktree is intentionally not clean. The frozen baseline from node 000A and the preflight check for this node show no tracked modifications and no staged modifications. Existing untracked items include, but are not limited to:

- `.playwright-cli/`
- `data/`
- `docs/final/`
- `docs/next/`
- `output/`
- `tmp/`
- `青天评标.app/`
- `pyproject 2.toml`
- `config/qingtian_hefei_chapter_factors_v1 2.json`
- `scripts/browser_button_smoke 2.py`
- `tests/* 2.py`

Dirty baseline policy:

- Do not clean existing untracked items.
- Do not stage existing untracked items.
- Do not modify, delete, move, or inspect the content of existing untracked output, cache, archive, app bundle, duplicate, data, job, export, log, or secret paths.
- Do not write into `docs/final/`, `docs/next/`, `data/`, `output/`, or `tmp/`.
- Before every commit, run `git diff --cached --name-only` and confirm that only explicitly authorized files are staged.
- Final `git status --short` may continue to show the frozen untracked baseline, but must not show unauthorized tracked changes or newly staged unrelated files.

## 3. Current System Facts

The current system is a FastAPI-centered single application entry in `app/main.py`. That file directly wires project management, upload handling, scoring, material retrieval, calibration, evidence, history, and web UI routes with engine and storage modules.

The current 2.5 focus panel remains fixed to 16 dimensions. `tests/test_main.py` asserts that the index page renders slider IDs `w_01` through `w_16` on first paint. `app/main.py` also builds default and normalized weights from `DIMENSION_IDS`.

`app/engine/dimensions.py` defines `DIMENSIONS` as fixed IDs `01` through `16`.

`app/engine/scorer.py` iterates over fixed `DIMENSIONS` and scores the legacy 16-dimension structure.

`app/engine/v2_scorer.py` defines `DIMENSION_IDS = [f"{i:02d}" for i in range(1, 17)]`, normalizes missing weights to 1/16, and computes the v2 rule total through the 16-dimension path.

`app/storage.py` persists current runtime data under `data` JSON files and does not currently define tender profile or calibration config loaders.

Current inventory from node 000A did not find the following planned modules or directories:

- `app/engine/tender_profile.py`
- `app/engine/target_mapping.py`
- `app/engine/strategy_advisor.py`
- `app/engine/tender_preflight.py`
- `app/engine/judge_aggregation.py`
- `app/engine/shigong_diagnostics.py`
- `app/engine/compilation_advisor.py`
- `app/engine/text_calibration.py`
- `app/engine/shigong_analyzer.py`
- `config/tender_profiles/`
- `config/calibration/`

## 4. Per-Tender Refactor Goal

The target is to move QingTian scoring from a fixed 16-dimension focus model to a per-tender scoring model.

Target behavior:

- Load each tender's official scoring basis by tender ID.
- Support per-tender score scale, evaluation items, score bands, hard red lines, and eligibility/preflight rules.
- Downgrade the current 16 dimensions to internal features, compatibility display, or derived evidence buckets.
- Preserve compatibility where useful, but do not let the 16-dimension model remain the official scoring surface for per-tender evaluation.
- Bind projects and submissions to a `tender_id` before official per-tender scoring.

## 5. Data Package Scope

The provided node describes `anbiao_data_bundle.json` as the future data source for:

- `tender_profiles`: 15 complete tender configurations.
- `calibration_artifacts`: 5 calibration artifacts.

This gate does not import, split, copy, transform, or write `anbiao_data_bundle.json` into the repository. The bundle is treated only as scope evidence for planning the staged implementation.

## 6. Implementation Route

Recommended staged route:

1. `QINGTIAN-PER-TENDER-002-DATA-MODEL-PROFILE-LOADER`
   Add the tender profile data model, profile loader, validation helpers, and preflight checks without wiring them into `app/main.py`.

2. `QINGTIAN-PER-TENDER-003-TENDER-PROFILES-CALIBRATION-DATA-LANDING`
   Add authorized tender profile and calibration config data under explicit config paths.

3. `QINGTIAN-PER-TENDER-004-HOME-25-PANEL-PER-TENDER-OFFICIAL-VIEW`
   Adapt the home 2.5 panel to display the selected tender's official scoring basis instead of only fixed 16-dimension focus sliders.

4. `QINGTIAN-PER-TENDER-005-PROJECT-TENDER-ID-BINDING`
   Add project-level `tender_id` binding and persistence.

5. `QINGTIAN-PER-TENDER-006-PER-TENDER-FOCUS-WEIGHTS-PERSISTENCE`
   Persist per-tender focus or attention settings without treating 16 dimensions as the official scoring target.

6. `QINGTIAN-PER-TENDER-007-PER-TENDER-SCORING-API`
   Add scoring API behavior that evaluates by tender profile.

7. `QINGTIAN-PER-TENDER-008-CALIBRATION-INTEGRATION`
   Integrate calibration artifacts into scoring and diagnostics.

8. `QINGTIAN-PER-TENDER-009-TERMINOLOGY-PREFLIGHT-OPTIMIZATION`
   Tighten terminology, red-line checks, and preflight explanations around official tender rules.

9. `QINGTIAN-PER-TENDER-010-END-TO-END-VALIDATION-GRAY-SWITCH`
   Add full-chain validation and a controlled gray switch.

## 7. First Implementation Recommendation

Recommended first implementation node:

`QINGTIAN-PER-TENDER-002-DATA-MODEL-PROFILE-LOADER`

The first implementation should start with pure new engine modules and explicit config directory scaffolding. It should not directly modify `app/main.py`.

Recommended first-batch file scope:

- `app/engine/tender_profile.py`
- `app/engine/target_mapping.py`
- `app/engine/tender_preflight.py`
- `config/tender_profiles/README.md`
- `config/calibration/README.md`
- `tests/test_tender_profile_loader.py`
- `tests/test_tender_preflight.py`

Do not modify in the first implementation batch:

- `app/main.py`
- existing `app/engine/*.py` files
- existing `app/storage.py`
- existing `config/*.json`
- existing tests unrelated to the new loader/preflight behavior
- `scripts/`
- existing untracked duplicate files

## 8. First Implementation Verification

Recommended future verification commands for node 002:

```text
python -m py_compile app/engine/tender_profile.py app/engine/target_mapping.py app/engine/tender_preflight.py
python -m pytest tests/test_tender_profile_loader.py tests/test_tender_preflight.py
python -m ruff check app/engine/tender_profile.py app/engine/target_mapping.py app/engine/tender_preflight.py tests/test_tender_profile_loader.py tests/test_tender_preflight.py
git diff --check
git diff --cached --check
git diff --cached --name-only
git status --short
```

These commands are for the future implementation node only. They are not authorized for this 001 gate except the git check commands explicitly allowed by this node.

## 9. Governance And Tooling

- Codex is the only tool authorized to modify files.
- Claude Code may only perform read-only review if explicitly requested by the controller.
- Do not create, fork, delegate, or parallel-start other Codex conversations.
- Do not transfer the task to another thread.
- Do not enter implementation from this node.
- Do not enter data landing, web refactor, runtime execution, or any unrelated project task from this node.

## 10. Explicit Prohibitions

This gate and the next implementation authorization must continue to prohibit:

- Runtime execution.
- Endpoint/API access.
- HTTP, localhost, or port probing.
- Ollama commands.
- Model inference.
- Prompt input.
- Reading real project material content.
- Reading secrets, output, job, export, or log content.
- Importing, splitting, copying, or writing `anbiao_data_bundle.json` into the repository unless a later node explicitly authorizes a specific data landing path.

## 11. Authorization Decision

Recommendation: proceed to `QINGTIAN-PER-TENDER-002-DATA-MODEL-PROFILE-LOADER` only after ChatGPT controller review.

The next node should allow writes only to the explicitly listed first-batch files. It should continue to preserve the dirty baseline and must stage only authorized files.

This node is complete after this document is committed, tagged, and pushed. After completion, stop and wait for ChatGPT controller review.
