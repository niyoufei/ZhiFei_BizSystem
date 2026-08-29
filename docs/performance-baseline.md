# R5P1 性能基线

## 固定工作负载

基线命令只使用临时数据，不读取生产数据：

```bash
python -m app.performance_baseline \
  --projects 100 \
  --submissions-per-project 10 \
  --read-iterations 30 \
  --write-iterations 20 \
  --scoring-iterations 10 \
  --output /absolute/path/to/performance-baseline.json
```

报告 schema 为 `qingtian-performance-baseline-v1`。固定工作负载指纹为：

`7affcd692e737bc1aab9a2b583cb7bfb85c4a7437e9fcb9290f35f39f7e4352a`

JSON 与 SQLite 使用完全相同的项目/提交数据和 revision 更新序列。每轮结束必须
满足 `storage_semantic_parity=true`。评分语义指纹仅排除明确的
`meta.timestamp` 生成时刻；分数、维度、扣分、建议和其他 meta 字段均参与比较。

## 2026-08-29 参考结果

当前 macOS 开发机、Python 3.12 的一次参考运行：

| 指标 | JSON | SQLite/WAL |
|---|---:|---:|
| 读取 p95 | 3.891 ms | 4.358 ms |
| 双 store 原子写 p95 | 10.676 ms | 5.811 ms |
| 存储语义 | PASS | PASS |

评分引擎 p95 为 `0.513 ms`，十次语义结果一致。所有默认门禁通过。

这些数值是同机回归参考，不作为跨机器 SLA。正式比较必须使用同一工作负载指纹、
同一 Python 主版本和相同迭代参数。R5P2 应优先测量并发读写的等待、公平性和错误率，
不根据单线程读延迟做提前优化。

## 默认门禁

- JSON 与 SQLite 最终语义指纹一致；
- 每个后端的重复读取结果一致；
- 存储读取 p95 不超过 500 ms；
- 双 store 原子写 p95 不超过 1500 ms；
- 评分 p95 不超过 5000 ms，且语义结果一致。

门禁用于发现数量级退化和语义漂移。微小性能变化需要多轮统计和同机对照，不能仅凭
单次结果判定优化成功。
