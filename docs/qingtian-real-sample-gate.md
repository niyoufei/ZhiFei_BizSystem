# 青天真实样本外置门禁操作卡

## 1. 阶段定位

本操作卡用于 `qingtian-real-sample-gate-v1`，面向操作者固化真实样本外置门禁的执行命令、数据根目录要求、PASS/FAIL 判定和禁止写仓库 `data` 的边界。

本文只做操作说明和验收口径归档：
- 不改变应用代码；
- 不修改 `app.main`；
- 不修改 `app.storage`；
- 不修改 `tools/smoke_guard.py`；
- 不新增样本数据文件；
- 不写仓库 `data` 目录。

## 2. 当前基线

- branch: detached HEAD
- head: `64604ca565934eeeb5d0c18c214b4a91c1f95d26`
- tag: `v0.1.29-qingtian-real-sample-gate`

## 3. 核心命令

操作者执行真实样本外置门禁时，使用以下命令：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 tools/smoke_guard.py scenario --name qingtian-real-sample-gate-v1 --data-dir <external-data-root> --project-id p1
```

其中 `<external-data-root>` 必须替换为仓库外部的数据根目录。

## 4. 外置 data root 原则

- 必须显式传入 `--data-dir`。
- `--data-dir <external-data-root>` 指向的 data root 必须在仓库外部。
- 禁止使用仓库内置 `data` 目录。
- 禁止真实业务样本进入仓库。
- 操作卡中的结构样例应使用合成数据，不使用真实业务样本。

禁止示例：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 tools/smoke_guard.py scenario --name qingtian-real-sample-gate-v1 --data-dir data --project-id p1
```

允许方向示例：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 tools/smoke_guard.py scenario --name qingtian-real-sample-gate-v1 --data-dir /tmp/qingtian-real-sample-gate-data --project-id p1
```

## 5. 最小外置数据结构

外置 data root 至少包含：

```text
<external-data-root>/
  projects.json
  submissions.json
```

`projects.json` 至少包含 `id` 和 `name`：

```json
[
  {
    "id": "p1",
    "name": "合成样例项目"
  }
]
```

`submissions.json` 至少包含 `id`、`project_id`、`filename`、`text`、`created_at` 和 `report`：

```json
[
  {
    "id": "s1",
    "project_id": "p1",
    "filename": "synthetic-sample.txt",
    "text": "合成施组样例文本。",
    "created_at": "2026-05-05T00:00:00Z",
    "report": {
      "scoring_status": "scored",
      "total_score": 88,
      "score": 88,
      "meta": {
        "evidence_trace": [
          {
            "source": "synthetic",
            "summary": "合成证据链"
          }
        ]
      },
      "material_quality": {
        "status": "available",
        "summary": "合成材料质量字段"
      },
      "injection": {
        "status": "available",
        "summary": "合成注入字段"
      },
      "requirement_hits": [
        {
          "requirement": "合成验收点",
          "hit": true
        }
      ]
    }
  }
]
```

`report` 中至少应具备：
- `scoring_status=scored`；
- 分数，例如 `total_score` 或等价分数字段；
- `meta.evidence_trace`；
- 材料质量 / 注入相关最小字段；
- `requirement_hits`。

## 6. QINGTIAN_DATA_DIR 时序说明

`QINGTIAN_DATA_DIR` 必须在 import `app.storage` / `app.main` 前设置。

runtime 测试和 `smoke_guard` 的 `external-runtime` 阶段通过 subprocess 保证该顺序：子进程先设置环境变量，再 import `app.storage` 和 `app.main`，从而让运行态读取外置 data root，而不是仓库 `data`。

## 7. 执行阶段说明

`qingtian-real-sample-gate-v1` 分两阶段执行：

1. `data-preflight`
   - 检查外置 data root 是否存在；
   - 检查 `projects.json` 和 `submissions.json` 是否存在；
   - 检查 `project_id` 是否存在；
   - 选择最新或已评分 submission。
2. `external-runtime`
   - 通过 subprocess 设置 `QINGTIAN_DATA_DIR`；
   - 验证 `app.storage` 实际使用外置 data root；
   - 验证 `/api/v1/projects/{project_id}/evidence_trace/latest`；
   - 验证 `/api/v1/projects/{project_id}/scoring_basis/latest`。

`data-preflight` 失败必须短路 `external-runtime`，并输出 `external_runtime_skipped: true`。

PASS 要求两个阶段均通过。

## 8. PASS 判定

最终 PASS 必须同时满足：

- `data_preflight_result: PASS`
- `external_runtime_result: PASS`
- `evidence_trace/latest: 200`
- `scoring_basis/latest: 200`
- `runtime_submission_id` 与 `submission_id` 一致
- `scoring_status=scored`
- `repository_data_used=false`
- `final_result: PASS`

对应报告字段中，`runtime_submission_id` 应与 `data_preflight_selected_submission_id` 指向同一 submission。操作者记录时可把 preflight 选中的 submission 视为本轮 `submission_id`。

## 9. FAIL 判定与典型失败

缺少 `--data-dir` 时，必须判定为 FAIL：

```text
missing --data-dir for qingtian-real-sample-gate-v1
external_runtime_skipped: true
final_result: FAIL
```

使用仓库 `data` 目录时，必须判定为 FAIL：

```text
data-dir must be external for qingtian-real-sample-gate-v1
repository_data_dir_rejected: true
final_result: FAIL
```

其它典型失败包括：
- 外置 data root 不存在；
- 缺少 `projects.json`；
- 缺少 `submissions.json`；
- `projects.json` 不是数组；
- `submissions.json` 不是数组；
- 找不到目标 `project_id`；
- 目标项目没有 submission；
- submission 缺少可选择的 `id`；
- latest endpoints 未返回 200；
- `evidence_trace` 与 `scoring_basis` 返回的 submission 不一致；
- `scoring_status` 不是 `scored`。

任何 FAIL 都不得当作 PASS。

## 10. 已验证结果摘要

Step 173A 已验证结果：

- `missing_data_dir_exit_code: 1`
- `repository_data_dir_exit_code: 1`
- `tmp_real_sample_gate_exit_code: 0`
- `stable_regression_result: PASS`
- `127 passed in 8.39s`
- `final_assessment: A`

## 11. 禁止事项

- 不写仓库 `data`。
- 不提交真实业务样本。
- 不跳过 `data-preflight`。
- 不绕过 `external-runtime`。
- 不把 FAIL 当 PASS。
- 不启动服务。
- 不修改应用代码。

本操作卡不授权启动服务、运行浏览器、运行 Ollama、安装依赖、运行 pytest / npm test / build、写入仓库 `data` 或提交真实样本。

## 12. 后续推进建议

- 先完成 docs-only 操作卡归档。
- 后续如需提交，必须先处理 detached HEAD 提交策略。
- 后续不得直接改应用代码或数据目录。
- 后续如需真实样本测试，必须继续使用仓库外部数据根目录。
