# JSON → SQLite/WAL 存储迁移与恢复

系统默认继续使用 JSON。只有在进程启动前显式设置
`QINGTIAN_STORAGE_BACKEND=sqlite` 时，20 个核心逻辑 store 才会切换到
SQLite；材料原文件和通用缓存文件仍保留在文件系统。

## 迁移

先保持服务停止，并确认 `QINGTIAN_DATA_DIR` 指向待迁移的 JSON 数据目录：

```bash
export QINGTIAN_DATA_DIR=/absolute/path/to/data
python -m app.storage_migration migrate \
  --database "$QINGTIAN_DATA_DIR/qingtian.sqlite3"
```

迁移器会在全部 JSON store 的同一只读锁快照上生成指纹，在目标目录创建
候选数据库，把 20 个 store 放进一个 SQLite 事务，执行语义比对、
`PRAGMA integrity_check` 和 WAL checkpoint，全部通过后才原子发布目标文件。

成功报告必须满足：

- `store_count=20`
- `source_fingerprint == destination_fingerprint`
- `journal_mode=wal`
- `integrity_check=ok`
- 首次迁移 `created=true`

再次执行相同迁移会返回 `idempotent=true`。如果目标数据库已有不同数据，迁移器
直接报冲突，不覆盖、不合并，也没有强制覆盖参数。

## 显式切换

迁移成功后，在服务进程启动前设置：

```bash
export QINGTIAN_STORAGE_BACKEND=sqlite
export QINGTIAN_SQLITE_PATH="$QINGTIAN_DATA_DIR/qingtian.sqlite3"
```

环境变量必须在 import `app.storage` / `app.main` 之前生效。SQLite 后端使用
WAL、`synchronous=FULL`、busy timeout 和事务 store 作用域；未声明的只读依赖
可以共享当前事务快照，未声明写入会失败并回滚。

运行期间不得单独复制、删除或移动 `qingtian.sqlite3-wal`、
`qingtian.sqlite3-shm`。一致备份应在服务停止并完成 checkpoint 后进行。

## 恢复为 JSON

恢复导出只允许写入一个尚不存在的新目录，避免覆盖现有 JSON：

```bash
python -m app.storage_migration export-json \
  --database "$QINGTIAN_SQLITE_PATH" \
  --target-data-dir /absolute/path/to/recovered-data
```

导出先检查 SQLite 完整性，在同级候选目录生成全部 JSON，重新读取并比对指纹，
通过后原子发布恢复目录。成功报告必须满足
`source_fingerprint == recovered_fingerprint`。

恢复切换时停止服务，取消 `QINGTIAN_STORAGE_BACKEND` 和
`QINGTIAN_SQLITE_PATH`，把 `QINGTIAN_DATA_DIR` 指向已验证的恢复目录后再启动。
原 SQLite 数据库应保留，直到 JSON 路径完成业务验收。
