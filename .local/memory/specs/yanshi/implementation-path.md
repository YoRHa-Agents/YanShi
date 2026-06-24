# YanShi 实现路径方案 (Implementation Path) v1.2 — 定稿

> 配套: `spec.md`(设计规范)、`governance.md`(核心管控规范)
> 语言定稿: **Python 3.12+ / async-first**。包管理 `uv`,lint `ruff`,类型 `mypy --strict`,测试 `pytest` + `pytest-asyncio`。
> 最后更新: 2026-06-18 00:20 (UTC+8)
> v1.1 review 补足: preflight/paths/monitor_proc/fleet 模块、磁盘布局、M1.5 运行时 + M3.5 fan-out 里程碑、fixtures 录制工具。
> v1.2 收敛: 运行时去 detached 监控,`monitor_proc.py`→`monitor.py`(内核,入口 A/B 共用),M1.5 重写为"库 Task + CLI `--wait`";修 M1/M3 重复交付行 + cursor 命令解析/effort 冲突落到 M3。

## 1. 工程骨架

```
yanshi/
├── pyproject.toml              # uv 管理; deps: 见 §2
├── README.md
├── src/yanshi/
│   ├── __init__.py             # __version__
│   ├── contracts.py            # RunSpec / BuiltCommand / RunResult / YanShiEvent / Usage / AgentStatus (dataclass/Pydantic)
│   ├── errors.py               # 错误分类: YanShiError + ErrorCategory 枚举 (rate_limit/auth/billing/server/invalid/...)
│   ├── registry.py             # AdapterRegistry: 数据驱动 (adapters/*.toml) + 代码适配器装载 + Capabilities 声明
│   ├── preflight.py            # doctor: CLI 安装/鉴权/版本探测; 派发前校验 (G3.6)
│   ├── adapters/
│   │   ├── base.py             # Adapter Protocol + 公共 helper
│   │   ├── claude.py           # build_command / parse_event / parse_result / session_id_from_event
│   │   ├── codex.py
│   │   ├── cursor.py
│   │   ├── gemini.py
│   │   └── data/               # 每 CLI 一个 TOML: flag 模板 / effort 翻译表 / 事件词汇映射 / 默认 model
│   ├── runner.py               # Runner: spawn (argv-only, 无 shell) + 生命周期 (单 agent)
│   ├── monitor.py              # 监控内核: 编排 pump/reducer/supervisor/summarizer; 入口 A(asyncio.Task) 与入口 B(--wait 内联) 共用 (§11)
│   ├── fleet.py                # 多 agent: dispatch_many / fleet_status / fleet_summary / consolidate / route (§12)
│   ├── stream.py               # StreamPump: 双管道按行缓冲 NDJSON, 容错解析
│   ├── reducer.py              # StatusReducer: 纯函数 (status,event)->status (确定性, 无 LLM)
│   ├── supervisor.py           # Watchdog: 超时/停滞/exit 分类/重启/熔断/cost ceiling
│   ├── logsink.py              # RawLogSink: NDJSON 落盘 + ring buffer + 字节偏移切片
│   ├── usage.py                # UsageMeter: token/cost 归一 + models.dev 兜底定价
│   ├── summarizer.py           # Summarizer: 超轻量模型 rolling summary + debounce + 降级兜底
│   ├── store.py                # StatusStore: $YANSHI_HOME/agents/<id>/ 布局(§13), 原子写+flock, run.json/result.json/stream.ndjson, owner_pid 存活校验, gc
│   ├── paths.py                # workdir/add_dirs 规范化与边界校验 (G8.3)
│   ├── policy.py               # 策略对象 (allow/workdir/add_dirs/cost_ceiling/redaction/approval) + 校验
│   ├── secrets.py              # 密钥脱敏正则
│   ├── otel.py                 # (可选) OTEL gen_ai.* 导出, fail-open
│   ├── dispatch.py             # 顶层 API: dispatch / dispatch_many / status / summary / wait / cancel / list / fleet_status / consolidate
│   └── cli.py                  # `yanshi` CLI (typer): dispatch[--wait]/status/summary/wait/cancel/list/doctor/gc/record
├── skill/
│   ├── SKILL.md                # progressive disclosure; 暴露动词 + 策略传参约定
│   └── mcp_server.py           # (可选) MCP server 包装 dispatch.py
└── tests/
    ├── fixtures/               # 录制的真实 stream-json 样本 (claude/codex/cursor/gemini)
    ├── test_adapters_*.py
    ├── test_reducer.py
    ├── test_event_normalization.py   # 仿 agentbridge driver_event_normalization_test
    ├── test_supervisor.py            # 注入式时钟
    ├── test_paths.py                 # 边界/逃逸校验 (G8.3)
    ├── test_runtime.py               # 入口 A(Task) + 入口 B(--wait) + 纯读盘 status/cancel/wait + owner_pid 纠正 stalled (§11)
    ├── test_fleet.py                 # dispatch_many / fleet_status / consolidate
    └── test_e2e.py
```

> **fixtures 来源**:用 `yanshi record <cli> --prompt ...` 开发工具录制真实 stream-json 到 `tests/fixtures/`(实测 4 个 CLI 已装),回放驱动归一化测试,避免测试时真实计费。

设计要点(源自源码精读):
- **构建/执行分离**(harness): `Adapter.build_command()` 只产 argv,Runner 负责 spawn+监控。
- **纯函数适配器 seam**(headless): `(spec, env) -> BuiltCommand`,厂商方言隔离在单文件 + TOML。
- **registry-as-data**(orchcore): 新增/调整 CLI 优先改 TOML,零代码。
- **事件归一化纪律**(agentbridge): 抑制 delta、只发完整块、终止事件带 Usage。

## 2. 依赖(最小集)
| 用途 | 选型 |
|---|---|
| 进程/并发 | stdlib `asyncio`(create_subprocess_exec, TaskGroup, wait_for) |
| 数据模型 | `pydantic` v2(契约校验 + JSON schema 生成) |
| CLI | `typer` |
| 落盘 | stdlib `sqlite3`(ring buffer/transcript)+ `filelock`(flock 跨平台) |
| summarizer 模型调用 | 轻量 HTTP(`httpx`)直连 OpenAI/Anthropic 兼容端点;模型 tier=haiku/flash/mini |
| 定价兜底 | models.dev pricing JSON(`httpx` 拉取 + 本地缓存) |
| OTEL(可选) | `opentelemetry-sdk` + gen_ai 语义约定 |
| 测试 | `pytest` `pytest-asyncio` `pytest-cov` |
> 不引入重型 agent 框架;summarizer 模型可插拔(接口 + 环境变量配置),无 key 时自动降级为确定性拼接。

## 3. 分阶段实现(每阶段 = 一个 PR,带测试门)

> 遵守 workspace 规则:每阶段**必须**含测试,不得 TODO 跳过;受保护分支不直推,走 feature 分支 + MR。

### M0 — 契约全集 + 注册表 + preflight + claude 跑通(骨架)
- 交付: `contracts.py`(含 `RunResult`/`Usage`/`Capabilities`) `errors.py` `registry.py` `preflight.py` `paths.py` `adapters/base.py` `adapters/claude.py` + `data/claude.toml`;`runner.py` 最小同步版(argv-only spawn→收集→parse_result)。
- API: `doctor()`(CLI 安装/鉴权/版本)+ `dispatch(RunSpec)->RunResult`(阻塞版)。
- 验收/测试: claude `build_command` argv 快照(read-only/yolo);`parse_result` 提取 reply/usage/is_error;exit code 分类;preflight 未鉴权 fail-fast(G3.6);`paths` 边界逃逸被拒(G8.3);确认无 `shell=True`(G8.1)。
- 门: 单测全过,mypy strict 0 error,coverage ≥80%。

### M1 — 流监控(确定性核心)
- 交付: `stream.py` `reducer.py` `logsink.py` `usage.py` `store.py`(`$YANSHI_HOME/agents/<id>/` 布局 §13);claude stream-json 事件归一化。
- API: dispatch 改为异步流式;`status(agent_id)->AgentStatus`,`get_summary` 暂返 deterministic last_event。
- 验收/测试: `test_event_normalization`(claude fixture)→ 归一化事件序列正确;reducer FSM 状态/计数/token/错误分类正确;原始流落盘且 status 不含原文;UsageMeter 原生 cost 优先、缺失走 models.dev;run.json 原子写 + flock。
- 门: 同上 + 背压测试(高频事件不丢、不 OOM)。

### M1.5 — 运行时入口(库 Task + CLI `--wait`)
- 交付: `monitor.py`(监控内核)+ `dispatch.py` 入口 A(后台 `asyncio.Task` spawn+监控,内存快照+镜像落盘)+ `cli.py` 的 `dispatch --wait`(入口 B 内联阻塞)+ `status/summary/wait/list`(纯读盘)+ `store.py` owner_pid 存活校验。
- 能力: 库/MCP 长驻内 `dispatch()→status()` 拉归一化状态;`yanshi dispatch --wait` 阻塞至终态打印 `RunResult`;另一进程纯读盘观测(监控宿主存活期间);监控者唯一读流(G2.10);owner_pid 死 → 读取者纠正 `stalled`(G10);**不实现 detached fire-and-forget 监控**。
- 验收/测试: `test_runtime` —— 库模式 dispatch 返回后 Task 持续监控、`status` 读到推进;CLI `--wait` 阻塞拿到终态;`cancel` 同杀子进程并 finalize `cancelled` 无孤儿;kill 监控宿主后读取者据 owner_pid 纠正为 `stalled`。
- 门: 同上;无孤儿进程断言。

### M2 — Supervisor(管控落地)
- 交付: `supervisor.py` `policy.py` `secrets.py`。
- 能力: 墙钟超时 + 停滞超时(区分 rate-limit 等待/长工具/真卡死,三层 stuck 检测)、exit 分类、分类重启(可重试退避 + `--resume`)、熔断、per-run/全局 cost ceiling、密钥脱敏。
- 验收/测试: 注入式时钟驱动 stuck/timeout;cost 超限触发 SIGINT→SIGTERM→SIGKILL;不可重试错误 fail-fast、可重试退避重试;脱敏正则命中。
- 门: 同上;**No Silent Failures** 断言(所有错误进 status.errors 且上报)。

### M3 — 多 CLI 适配
- 交付: `adapters/codex.py` `cursor.py` `gemini.py` + 各 `data/*.toml` + 各自事件解析 + effort 翻译表 + 各自 `Capabilities` 声明。cursor 适配器命令解析 `cursor-agent`→`agent` 回退(spec 注1)。
- 验收/测试: per-CLI build_command 快照测试;每家录制流回放 → 归一化一致;effort 翻译(claude flag / codex `-c` / cursor 模型后缀 / gemini thinking-level)正确;cursor effort 与用户显式 model 冲突时以用户 model 为准并记 warning(G1.4);不支持项经 Capabilities 校验明示降级(G1.3);session id 预指定/抓取(G9.2)。
- 门: 同上;4 个 CLI 的 fixture 全绿。

### M3.5 — 多 agent 编排(fan-out / 舰队汇总)
- 交付: `fleet.py`(`dispatch_many` / `fleet_status` / `consolidate`,可选 `route`)。
- 能力: `Semaphore` 控并发并行派发异构 sub-agent;确定性 `fleet_status` 聚合;失败隔离;`consolidate` 合并观测(§12)。
- 验收/测试: `test_fleet` —— N 个 agent 并行、单 agent 失败不影响其它;fleet_status 计数/总 token/总 cost 正确;consolidate 合并 per-agent reply/errors。
- 门: 同上。

### M4 — 超轻量 Summarizer
- 交付: `summarizer.py`(事件/阈值触发 + debounce + rolling "summary+verbatim tail" + 降级兜底)。
- API: `summary(agent_id)->str` 返回 LLM rolling_summary;无 key/超预算→确定性拼接。
- 验收/测试: 触发节流(≥5~10s/最小事件数);输入为结构化摘要非原文;输出 ≤150 token;降级路径可用;watcher 花费单独计量且有上限。
- 门: 同上 + 防幻觉断言(决策字段不来自 LLM)。

### M5 — 交付层(CLI + Skill + 可选 MCP/OTEL)
- 交付: `cli.py`、`skill/SKILL.md`、`skill/mcp_server.py`(可选)、`otel.py`(可选)。
- 能力: `yanshi dispatch/status/summary/wait/cancel/list`;SKILL.md 暴露动词 + 策略传参 + "低上下文 pull"约定;策略由调度方透传。
- 验收/测试: 端到端 dispatch→status→summary→wait;skill 调用样例;OTEL 导出 smoke。
- 门: 同上 + e2e 绿;打 tag 发布(local/git 模式按 repo 探测)。

## 4. 关键并发/健壮性实现约束
- spawn 必须 `create_subprocess_exec(stdout=PIPE, stderr=PIPE)` 并**同时**读两管道(否则满缓冲死锁)。
- 每个等待包 `asyncio.wait_for`;每个循环有 `max_iterations`;每个失败被分类(retry/escalate/abort)。
- 取消时先 drain 已缓冲输出再 finalize(避免丢最终输出);用 `TaskGroup` 保证 pump/watchdog 不成孤儿。
- 解析容错: 非 JSON 行 → 落 log 不崩;未知 event type → Unknown 桶继续(前向兼容)。

## 5. 首个可演示里程碑
- **库模式(入口 A,默认)**: 到 **M1** 即可演示核心价值(长驻进程内 `dispatch()`→`status()` 拉归一化状态,不读原始流)。
- **CLI 模式(入口 B)**: 到 **M1.5** 用 `yanshi dispatch --wait` 阻塞演示完整 dispatch→监控→`RunResult`;期间另一进程可纯读盘 `yanshi status`(监控宿主存活期间)。**不做** detached fire-and-forget。
- 建议交付到 M1.5 后做第一次评审,再推进 M2(管控)。
