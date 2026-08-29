# R5P3 故障注入与恢复认证

## 运行方式

探针只使用临时目录和临时数据库，不读取或修改生产数据：

```bash
python -m app.fault_injection_probe \
  --output /absolute/path/to/fault-injection-report.json
```

报告 schema 为 `qingtian-fault-injection-v1`。任一场景未满足预期恢复边界时进程返回
非零状态。

## 故障矩阵

| 类别 | 注入点 | 验收边界 |
|---|---|---|
| JSON | 发布前失败 | 原快照保持、临时文件清理、错误透传 |
| JSON | 发布后确认失败 | 新快照完整可读、错误透传、标记为提交结果不确定 |
| JSON | 损坏源更新 | 不调用更新函数、不覆盖损坏证据 |
| SQLite | 多 store 事务中断 | 数据和 revision 回到最后一次已提交状态 |
| SQLite/WAL | 写事务内进程异常退出 | 重开后只见最后一次已提交状态，完整性正常 |
| SQLite | JSON 载荷语义损坏 | 读取失败关闭，不静默覆盖；区别于物理完整性 |
| 迁移 | candidate 导入失败 | 不发布目标库并清理 candidate |
| 迁移 | candidate 发布失败 | 不留下目标库或 candidate |
| 恢复 | JSON candidate 第二次写入失败 | 不发布恢复目录，源数据库保持完整 |
| 端到端 | JSON → SQLite → JSON | 三方语义指纹和最终快照完全一致 |

## 关键运行语义

JSON 原子写在 `os.replace` 前失败时，调用方可确定旧快照仍有效；在 `os.replace` 后、
父目录 `fsync` 的发布后确认失败时，文件可能已经完整更新，但持久化确认没有成功。
调用方必须把后者视为“结果不确定”，重新读取并按幂等业务键判断，不得直接盲重试。

SQLite 的 `PRAGMA integrity_check=ok` 只证明数据库物理结构，不证明 JSON 载荷语义正确。
载荷解析失败必须关闭读取并保留证据，再通过已认证导出或备份恢复。

## 2026-08-29 认证结果

当前 macOS 开发机、Python 3.12 固定矩阵为 `10/10 PASS`。进程异常退出场景以独立
子进程状态 73 证明注入点真实到达；重开后 WAL 恢复、checkpoint 和完整性检查均通过。

本认证不代表已部署环境的磁盘、容器编排或远程备份 SLA；这些属于 R6D2 部署交付门禁。
