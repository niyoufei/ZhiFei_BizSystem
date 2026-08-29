# R5P2 并发可靠性基线

## 固定工作负载

探针只使用临时数据，不读取或修改生产数据：

```bash
python -m app.concurrency_probe \
  --writers 4 \
  --writes-per-writer 25 \
  --readers 4 \
  --reads-per-reader 100 \
  --output /absolute/path/to/concurrency-probe.json
```

报告 schema 为 `qingtian-concurrency-probe-v1`。JSON 使用
`path_transaction + flock + atomic replace`，SQLite 使用 WAL 和
`BEGIN IMMEDIATE`。两个后端执行完全相同的唯一事件集合。

## 验收语义

- 无丢写：最终计数和唯一事件数必须等于全部计划写入数；
- 无重复：所有事件 ID 必须唯一，两个后端的最终事件集合指纹一致；
- 读快照一致：每次读取均满足 `len(events) == value`；
- 单个 reader 观察到的计数不得倒退；
- 每个 writer 必须完成全部写入；
- `writer_fairness_ratio` 为最慢 writer 与最快 writer 的总耗时比，默认不得超过 10；
- 默认读取 p95 不超过 1000 ms，写入 p95 不超过 2000 ms；
- SQLite 必须保持 `journal_mode=wal` 且 `integrity_check=ok`。

## 同机回归参考

2026-08-29 在当前 macOS 开发机、Python 3.12 上执行一次固定工作负载：

| 指标 | JSON | SQLite/WAL |
|---|---:|---:|
| 读取 p95 | 2.359 ms | 0.539 ms |
| 写入 p95 | 2.649 ms | 3.964 ms |
| writer 公平性比 | 1.026 | 1.306 |
| 最终计数/计划写入 | 100/100 | 100/100 |

两个后端均无错误、无丢写、无重复事件，所有读快照一致且 reader 观察值单调；SQLite
保持 WAL 且完整性检查为 `ok`。这些数值只用于同机、相同 Python 主版本和相同参数的
数量级回归，不是跨机器 SLA。

R5P3 应在该并发语义基线上注入写入、提交和恢复故障；不得用吞吐改进交换正确性、
持久性或恢复能力。
