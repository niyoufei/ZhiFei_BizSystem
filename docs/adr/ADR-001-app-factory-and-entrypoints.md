# ADR-001：入口层收缩为 App Factory / Bootstrapping，业务编排统一下沉到应用服务层

- 状态：Accepted
- 日期：2026-04-12

## 背景

审计基线显示，原始 `app/main.py` 曾同时承担以下职责：

1. FastAPI app 创建与生命周期管理
2. 路由注册
3. 项目创建、文件上传、评分、学习、治理、巡检等业务编排
4. 材料解析 worker 与共享状态持有
5. 页面 HTML/JS 拼装

这导致两个直接问题：

1. `app.main` 既是启动入口，也是事实上的服务定位器。
2. `app/cli.py`、`app/windows_desktop.py`、`app.main` 很难共享同一套稳定用例编排，只能各自直连底层实现。

本轮规划中的过渡态如下：

- `app/main.py` 收缩为兼容入口，`__main__` 路径转给 `app.bootstrap.entrypoints.run_api()`。
- `app/bootstrap/app_factory.py` 负责 FastAPI factory 装配。
- `app/interfaces/api/app.py` 负责 API 入口适配。
- `app/application/service_registry.py` 和 `app/application/services/workflows.py` 承接项目、资料、评分、学习、治理、运维用例。
- 过渡期的大型编排收口到 `app/application/runtime.py`，`runtime_legacy.py` 仅保留兼容 shim。

这说明仓库已经不再适合继续把业务编排放回 `app/main.py`。

## 决策

`app/main.py` 只能保留以下职责：

1. 兼容入口
2. app factory 装配入口
3. bootstrapping 选择
4. 依赖装配桥接

业务编排一律下沉到统一应用服务层，由 FastAPI / CLI / Windows 安全桌面共用。

## 为什么 `app/main.py` 不能继续承担业务编排

### 1. 入口层必须可替换，而业务编排必须可复用

当前仓库已经存在三类稳定入口：

- `python -m app.main`
- `python -m app.cli`
- `zhifei-secure-desktop = app.windows_desktop:main`

如果业务编排继续留在 `app.main`：

- CLI 仍会旁路调用底层能力
- Windows 安全桌面仍会通过 Web 入口间接复用业务逻辑
- 入口之间很难共享统一的审计、幂等、回放、异常协议

### 2. 入口层不应持有运行时共享状态

材料解析队列、解析 worker、缓存、活动项目、调度统计等共享状态，本质上属于应用层或基础设施层，不应由入口模块持有。否则：

- 生命周期边界和业务边界混在一起
- 测试 patch 点和运行态对象缠在一起
- 后续再拆学习闭环、治理闭环、运维巡检时仍会被 `app.main` 拉回去

### 3. 入口层不应成为其他业务模块的依赖目标

审计时已经发现 `feedback_*`、`qingtian_dual_track`、`*_views` 对 `app.main` 的反向依赖模式。即便当前已经把 `app/main.py` 收缩为 shim，如果新的业务编排继续回填到 `app.main`，这种耦合会继续扩大。

## 与当前仓库结构的直接对应

### 规划中的过渡结构

| 角色 | 规划文件 |
| --- | --- |
| API 兼容入口 | `app/main.py` |
| CLI 兼容入口 | `app/cli.py` |
| Windows 兼容入口 | `app/windows_desktop.py` |
| Bootstrapping | `app/bootstrap/app_factory.py`、`app/bootstrap/entrypoints.py`、`app/bootstrap/config.py`、`app/bootstrap/dependencies.py` |
| API 适配器 | `app/interfaces/api/app.py` |
| CLI 适配器 | `app/interfaces/cli/runtime.py` |
| Windows 适配器 | `app/interfaces/windows/secure_desktop.py` |
| 应用服务壳 | `app/application/service_registry.py`、`app/application/services/workflows.py` |
| 过渡遗留编排层 | `app/application/runtime.py` |

### 目标收敛关系

| 当前过渡层 | 目标形态 |
| --- | --- |
| `app/application/runtime.py` | 被进一步拆入 `app/application/*`、`app/domain/*`、`app/interfaces/api/*` |
| `app/interfaces/api/app.py` | 后续承接 router 装配，而不是只转调 legacy runtime |
| `app/application/services/workflows.py` | 按项目、资料、评分、学习、治理、运维拆成独立服务模块 |

## 入口层目标职责分布

### 入口兼容层

- `app/main.py`
- `app/cli.py`
- `app/windows_desktop.py`
- `app/web_ui.py`

职责：

- 保留启动命令面
- 做最薄的入口选择
- 不直接操作 storage / engine

### Bootstrapping 层

- `app/bootstrap/app_factory.py`
- `app/bootstrap/entrypoints.py`
- `app/bootstrap/config.py`
- `app/bootstrap/dependencies.py`

职责：

- 环境装配
- 依赖注册
- app factory
- entrypoint dispatch

### 接口适配层

- `app/interfaces/api/*`
- `app/interfaces/cli/*`
- `app/interfaces/windows/*`

职责：

- HTTP/Typer/桌面事件适配
- 输入校验与响应整形
- 只调用应用服务层

### 应用服务层

- `app/application/service_registry.py`
- `app/application/services/*`

职责：

- 统一用例编排
- 事务边界
- 审计事件触发
- 错误语义与幂等策略

## 迁移约束

1. 对外命令面不变。
2. 旧导入路径保留兼容 shim。
3. FastAPI / CLI / Windows 不允许直接触碰 `app.storage` 或 `app.engine.*` 的细节。
4. 在 `runtime.py` 过渡编排未拆净之前，允许 `runtime_legacy.py` 仅作为兼容 shim 保留，但不允许新增业务总控逻辑继续堆入入口模块。

## 后果

### 正向收益

- 入口可保持稳定，业务可持续重构。
- 用例编排可以统一加审计、回放和事件记录。
- 后续存储层抽象和四层解耦有了稳定接入点。

### 成本

- 过渡期会同时存在 shim、legacy runtime、新服务层三层结构。
- 文档和测试必须明确指出“当前基线”和“最终目标”之间的差异。

## 回滚策略

1. 保留 `app/main.py` / `app/cli.py` / `app/windows_desktop.py` 的兼容入口。
2. 若新应用服务层出现问题，可让适配层暂时回退到 `runtime.py` 的兼容 helper。
3. 在 `runtime_legacy` 兼容路径仍有外部依赖前，不删除旧路径。

## 不采纳方案

### 方案 A：继续把业务 helper 堆在 `app/main.py`

不采纳原因：

- 只会重新制造超大主文件
- 继续维持入口层和业务层耦合

### 方案 B：先拆 HTTP / CLI / Windows 为多个独立服务

不采纳原因：

- 现在的问题是边界没清，不是进程不够多
- 先拆服务只会把单体内耦合升级成跨进程耦合
