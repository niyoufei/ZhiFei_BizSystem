# 青天本地分支状态治理操作说明

## 1. 文档用途

本文用于记录当前评标系统仓库中本地 `main` 与 `origin/main` 不一致的状态、形成原因、后续 Codex 操作禁区和安全治理建议。

本文只做 docs-only 状态说明：
- 不移动任何分支；
- 不覆盖本地 `main`；
- 不修改应用代码；
- 不修改 `tools/smoke_guard.py`；
- 不修改测试文件；
- 不写仓库 `data` 目录。

## 2. 当前仓库状态

- 当前分支：`docs/qingtian-real-sample-gate-card`
- 当前 HEAD：`d44aff9a045b2c6243694ddda7dd3abb29c61168`
- `origin/main`：`d44aff9a045b2c6243694ddda7dd3abb29c61168`
- 本地 `main`：`69817e47085961e929b548438aaeeb37e0538db6`
- 标签：`v0.1.30-qingtian-real-sample-gate-card`

当前结论：
- 当前工作分支已与远端主线 `origin/main` 对齐。
- 本地 `main` 指针仍停留在旧提交 `69817e4`。
- 本地 `main` 未在 Step 173C / 173D 中被移动、覆盖或 reset。

## 3. 形成原因

该状态形成于 qingtian-real-sample-gate 操作卡提交与推送过程：

1. 之前仓库处于 `detached HEAD` 状态。
2. 当时当前 HEAD 与 `origin/main` 一致，均指向 `64604ca565934eeeb5d0c18c214b4a91c1f95d26`。
3. 本地 `main` 指针不在当前远端主线，而是指向 `69817e47085961e929b548438aaeeb37e0538db6`。
4. 为避免在 `detached HEAD` 上直接 commit，创建了独立分支 `docs/qingtian-real-sample-gate-card`。
5. 操作卡文档提交后，通过 `git push origin HEAD:main` 将远端 `main` 快进更新到 `d44aff9a045b2c6243694ddda7dd3abb29c61168`。
6. 推送过程未切换到本地 `main`，也未移动本地 `main`。

## 4. 当前风险

- 直接切换本地 `main` 可能回到旧提交 `69817e4`，与当前 `origin/main` 不一致。
- 在未确认前继续使用本地 `main` 可能产生误提交。
- 随意执行 `reset --hard`、`branch -f main`、merge 或 rebase 可能破坏提交可回溯性。
- 多个 Codex 对话框同时写同一仓库，可能造成分支、工作区和远端状态混乱。
- 在未确认 `origin/main` 的情况下提交，可能把新工作建立在错误基线上。

## 5. 后续操作禁区

在完成本地分支治理前，后续 Codex 操作必须遵守：

- 禁止直接在本地 `main` 上继续优化，除非先完成分支治理。
- 禁止执行 `reset --hard`。
- 禁止执行 `branch -f main`。
- 禁止执行 force push。
- 禁止删除 `docs/qingtian-real-sample-gate-card`，除非另行确认。
- 禁止在未确认 `origin/main` 的情况下提交。
- 禁止跨仓库操作。
- 禁止让多个 Codex 对话框同时写同一仓库。

## 6. 建议治理路径

建议按阶段治理，不把分支修复、业务开发和提交动作混在同一轮：

1. 第一阶段：只读确认远端和本地分支。
   - `pwd`
   - `git status --short`
   - `git branch --show-current`
   - `git rev-parse HEAD`
   - `git rev-parse origin/main`
   - `git rev-parse main`
2. 第二阶段：docs-only 记录治理策略。
   - 只新增或更新治理说明文档。
   - 不移动本地 `main`。
   - 不 reset、不 merge、不 rebase。
3. 第三阶段：如要恢复本地 `main`，应单独下达“快进本地 main 到 origin/main”的受控指令。
   - 先确认工作区 clean。
   - 先确认 `origin/main` 指向预期提交。
   - 再用受控命令快进本地 `main`。
   - 不使用 force push。

在完成本地 `main` 治理前，后续评标系统优化应继续以当前已验证分支 `docs/qingtian-real-sample-gate-card` 或新分支承载，不直接使用旧 `main`。

## 7. 后续 Codex 执行原则

每次进入本仓库执行任务前，先做只读状态确认：

```bash
pwd
git status --short
git branch --show-current
git rev-parse HEAD
git rev-parse origin/main
```

确认顺序：

1. 先确认当前路径是否为预期仓库。
2. 先确认工作区是否 clean。
3. 再确认是否在预期分支。
4. 再确认 HEAD 与 `origin/main` 的 ahead/behind 状态。
5. 再确认本轮是否允许提交、推送或移动分支。

执行边界：
- 不允许跨仓库操作。
- 不允许同时多个 Codex 对话框写同一仓库。
- 不允许在状态不清楚时继续提交。
- 不允许把本地 `main` 当作已对齐远端主线，除非已完成单独治理。

## 8. 当前推荐口径

当前可继续用于后续工作的已验证分支是 `docs/qingtian-real-sample-gate-card`。

当前远端主线 `origin/main` 已包含 `d44aff9a045b2c6243694ddda7dd3abb29c61168`。

当前本地 `main` 仍为 `69817e47085961e929b548438aaeeb37e0538db6`，在治理前只作为需要处理的本地旧指针记录，不作为默认开发基线。
