---
name: YanShi Implementation Plan
overview: 将已定稿的 YanShi 设计语料(spec v0.3 / governance v1.2 / implementation-path v1.2)操作化为一个 DevolaFlow 执行计划:把 8 个里程碑(M0->M5)映射为 8 个 stage,每个 stage 用 L0->L1->L2->L3 级联派发,disjoint 文件归属的 waves/tasks,逐 stage 测试门。
todos:
  - id: m0
    content: "Stage M0 骨架: pyproject/uv + git init + contracts/errors/registry/preflight/paths + adapters/base + adapters/claude(+toml) + runner(最小)/dispatch(阻塞) + doctor;claude 跑通 dispatch->exec->capture;过 M0 门(单测/mypy strict/cov>=80%/无 shell=True/paths 逃逸拒/preflight fail-fast)"
    status: pending
  - id: m1
    content: "Stage M1 流监控: record 工具录 claude fixture + stream(StreamPump)/reducer(确定性 FSM)/logsink(ring buffer 落盘)/usage(models.dev 兜底)/store($YANSHI_HOME 布局 §13 原子写+flock) + event_normalization 测试;过 M1 门 + 背压测试"
    status: pending
  - id: m15
    content: "Stage M1.5 运行时入口: monitor 内核 + dispatch 入口A(asyncio.Task)+ cli dispatch --wait 入口B + status/summary/wait/list 纯读盘 + store owner_pid 存活校验;test_runtime(库/CLI/cancel 无孤儿/owner_pid 纠正 stalled);过门 + 无孤儿断言"
    status: pending
  - id: m2
    content: "Stage M2 Supervisor: supervisor(墙钟+三层 stuck/exit 分类/分类重启+resume/熔断/cost ceiling) + policy(校验) + secrets(脱敏);注入式时钟测 stuck/timeout、cost 超限信号链、可重试/不可重试、pricing missing token 兜底;过门 + No Silent Failures 断言"
    status: pending
  - id: m3
    content: "Stage M3 多 CLI: codex/cursor/gemini 三 adapter + data/*.toml + effort 翻译表 + Capabilities;cursor 命令 cursor-agent->agent 回退、effort 与用户 model 冲突以用户 model 为准(G1.4);session 预指定/抓取(G9.2);过门 + 4 CLI fixture 全绿"
    status: pending
  - id: m35
    content: "Stage M3.5 fan-out: fleet(dispatch_many+Semaphore / fleet_status 确定性聚合 / consolidate / 可选 route);test_fleet(并行/失败隔离/聚合计数/合并观测);过门"
    status: pending
  - id: m4
    content: "Stage M4 Summarizer: summarizer(显著事件+阈值触发+debounce>=5~10s+rolling summary+降级兜底);summary() LLM/无 key 降级;节流、<=150 token、watcher 单独计量上限;过门 + 防幻觉断言"
    status: pending
  - id: m5
    content: "Stage M5 交付层+release: cli 全动词 + skill/SKILL.md(低上下文 pull 契约+策略传参)+ 可选 mcp_server/otel;e2e dispatch->status->summary->wait;过门 + 打 tag(local)"
    status: pending
isProject: false
---

# YanShi 实现计划 (DevolaFlow full-pipeline, M0->M5)

将已定稿语料 [spec.md](.local/memory/specs/yanshi/spec.md) v0.3 / [governance.md](.local/memory/specs/yanshi/governance.md) v1.2 / [implementation-path.md](.local/memory/specs/yanshi/implementation-path.md) v1.2 / [design-analysis.md](.local/memory/specs/yanshi/design-analysis.md)(rationale)操作化为可执行的 DevolaFlow 计划。设计被视为 ground truth(A-4),本计划只做"如何造",不改设计决策。

## 现状

- Greenfield:无 git 仓库、无源码;`.local/` 工作区已 scaffold。
- 工具链:Python 3.12.8 在位;`uv` 未安装(M0 需 bootstrap `uv`,否则降级 venv+pip)。
- 4 个目标 CLI 全部已装:`claude` / `codex` / `cursor-agent`(+`agent` 别名)/ `gemini`;`aider` 缺失(仅低保真示例,不在 4-CLI 范围)。
- 14 个参考仓库在 `/home/agent/reference/`(harness / headless-cli / agentbridge / orchcore / tab-conductor / streamparse / tracing_agents / mco / agent-dispatch / skill-sub-agent-dispatch ...)。

## 执行模型 (DevolaFlow)

```mermaid
flowchart LR
  L0["L0 Project: 选 stage / 评 gate / 报告"] --> L1["L1 Stage: 拆 waves / 收敛"]
  L1 --> L2["L2 Wave: 派发并行 tasks / 查冲突"]
  L2 --> L3["L3 Task: 唯一实现层(写码/测/审)"]
```

- 工作流类型:`cd(full-pipeline)`;design/plan 已完成,本计划聚焦 implement -> verify -> release。
- **P1 不变量**:L0/L1/L2 只派发,**仅 L3 写码/跑测**。每 wave ≤5 task、文件归属 disjoint、每 task ≤6 可写文件 / ≤30min。
- Gate profile = **standard**:每 stage 必须 单测全过 + `mypy --strict` 0 error + coverage ≥80% + 0 blocker,外加该 stage 的 governance MUST 断言(违反即 blocker)。对齐 workspace 规则 **Mandatory Verification**(不得 TODO 跳过测试)。
- 超时:impl=1800s / test=900s / review=1200s(每次派发必设)。
- Repo 模式:**local**(无 remote)。每个里程碑一条 feature 分支(`feat/m0-skeleton` ...),本地合并到 `main`;**不直推受保护分支**(workspace 规则);remote/MR 待用户加 remote 后再启用。

## Stage 顺序

```mermaid
flowchart LR
  M0[M0 骨架] --> M1[M1 流监控]
  M1 --> M15[M1.5 运行时入口]
  M15 --> M2[M2 Supervisor]
  M2 --> M3[M3 多CLI]
  M3 --> M35[M3.5 fan-out]
  M35 --> M4[M4 Summarizer]
  M4 --> M5[M5 交付层+release]
```

核心适配器 seam(厂商方言隔离点,贯穿全程):

```104:114:.local/memory/specs/yanshi/spec.md
class Adapter(Protocol):
    name: str
    prompt_mode: str                 # stdin | argument
    seed_paths: list[str]            # 鉴权/配置文件(供策略校验)
    def build_command(self, spec: RunSpec, env: Env) -> BuiltCommand: ...
    def parse_event(self, raw_line: str) -> YanShiEvent | None: ...  # 原生事件 → 归一化
    def parse_result(self, outcome: RawOutcome) -> RunResult: ...
    def session_id_from_event(self, ev: dict) -> str | None: ...     # 抓 session/thread id
```

---

## Stage M0 — 骨架 + claude 阻塞跑通  (分支 `feat/m0-skeleton`)

- **Wave 0**(顺序,基座)
  - `T0.1 脚手架`: `pyproject.toml`(uv;deps `pydantic` v2/`typer`/`filelock`/`httpx`;dev `pytest`/`pytest-asyncio`/`pytest-cov`/`ruff`/`mypy`)、`README.md`、`src/yanshi/__init__.py`、`git init` + 首 commit + 建分支。AC:`uv sync` 成功、`pytest` 空跑通、ruff/mypy 配置就位。
  - `T0.2 contracts.py`: RunSpec / BuiltCommand / RunResult / YanShiEvent / Usage / AgentStatus / Capabilities + FSM 枚举(spec §3)。AC:字段与 spec §3 一致;`Usage.total`;mypy strict 0。
  - `T0.3 errors.py`: `YanShiError` + `ErrorCategory`(rate_limit/server_error/overloaded | auth/billing/invalid_request/max_output_tokens | unknown)(G6.2)。AC:可重试/不可重试标注完整。
- **Wave 1**(并行,依赖 contracts)
  - `T1.1 paths.py`: realpath 规范化 + 边界校验(G8.3)。owned: `paths.py`,`tests/test_paths.py`。AC:`../` 逃逸被拒、不存在目录被拒。
  - `T1.2 adapters/base.py`: Adapter Protocol + 公共 helper + Capabilities 结构。AC:4 方法签名匹配 spec §3.3。
  - `T1.3 registry.py`: AdapterRegistry(TOML 数据驱动 + 代码适配器注册 + Capabilities 查询)。owned: `registry.py`,`tests/test_registry.py`。AC:注册/查询 claude;缺失 adapter 报错。
- **Wave 2**(并行,依赖 Wave1)
  - `T2.1 adapters/claude.py + data/claude.toml`: build_command(`--output-format stream-json --verbose`/`--model`/`--effort`/`--session-id`/read-only `--allowedTools`/yolo `--dangerously-skip-permissions`)、parse_event/parse_result/session_id_from_event。owned: `adapters/claude.py`,`adapters/data/claude.toml`,`tests/test_adapters_claude.py`。AC:argv 快照(read-only & yolo);parse_result 提取 reply/usage/is_error;G7.1 可复现。
  - `T2.2 preflight.py + doctor()`: 安装探测(cursor-agent->agent 回退)/鉴权/版本(G3.6)。owned: `preflight.py`,`tests/test_preflight.py`。AC:未鉴权 fail-fast、缺失 CLI 明确原因。
  - `T2.3 runner.py(最小同步) + dispatch.py(初版阻塞)`: argv-only spawn(no shell=True,G8.1)->收集->parse_result。owned: `runner.py`,`dispatch.py`。AC:claude 录制/真实跑通 dispatch->exec->capture;无 `shell=True` 断言。
- **Gate M0**: 标准门 + 无 shell=True + paths 逃逸被拒 + preflight fail-fast 验证。

## Stage M1 — 流监控(确定性核心)  (分支 `feat/m1-stream-monitor`)

- **Wave 0**: `record 工具`(`yanshi record <cli>` 录真实 stream-json 到 `tests/fixtures/`,避免测试计费)。AC:落 claude fixture。
- **Wave 1**(并行)
  - `stream.py` StreamPump(双管道按 `\n` 切 NDJSON,容错解析)。AC:非 JSON 行不崩、未知 type->Unknown 桶、背压不丢/不 OOM(G6.5)。
  - `reducer.py` StatusReducer 纯函数 `(status,event)->status`(FSM/计数/last tool/错误分类/token/cost)。AC:claude fixture 回放 FSM 正确;`progress_pct` 不可定为 null 不臆造(G2.3/G2.5)。
  - `logsink.py` RawLogSink(sqlite ring buffer + 字节偏移切片)。AC:原始流落盘、status 不含原文(G2.2)。
- **Wave 2**(并行)
  - `usage.py` UsageMeter(原生 cost 优先、models.dev 兜底、`pricing_status`)。AC:native/priced/missing 三态(G5.3)。
  - `store.py` StatusStore(`$YANSHI_HOME/agents/<id>/` 布局 §13,原子写+flock,run.json)。AC:原子写+flock;布局符合 §13。
  - `tests/test_event_normalization.py`(仿 agentbridge `driver_event_normalization_test`)。AC:claude 归一化序列正确。
- **Gate M1**: 标准门 + 背压测试(高频事件不丢/不 OOM)。

## Stage M1.5 — 运行时入口(库 Task + CLI `--wait`)  (分支 `feat/m15-runtime`)

- `monitor.py` 监控内核(pump->reducer->supervisor stub->summarizer stub;镜像落盘;入口 A/B 共用)。
- `dispatch.py` 入口 A(后台 `asyncio.Task` spawn+监控,内存快照+镜像落盘)+ `status`/`summary`(暂返 deterministic last_event)/`wait`/`list`(纯读盘)。
- `cli.py` `dispatch --wait` 入口 B(内联阻塞至终态打印 RunResult)。
- `store.py` owner_pid 存活校验(G10)。
- **AC(test_runtime)**: 库模式 dispatch 返回后 Task 持续监控、`status` 读到推进;CLI `--wait` 阻塞拿终态;`cancel` 杀子进程 finalize `cancelled` 无孤儿;kill 宿主后读取者据 owner_pid 纠正 `stalled`;监控者唯一读流(G2.10);**不实现 detached**。**Gate**: 标准门 + 无孤儿进程断言。

## Stage M2 — Supervisor(管控落地)  (分支 `feat/m2-supervisor`)

- `supervisor.py` Watchdog(墙钟 + 三层 stuck 检测[rate-limit 等待/长工具/真卡死]/exit 分类/分类重启[退避+`--resume`]/熔断/per-run+全局 cost ceiling)。
- `policy.py` 策略对象 + 校验(allow/workdir/add_dirs/cost_ceiling/redaction/approval)(G1.1)。
- `secrets.py` 密钥脱敏正则(G3.3)。
- **AC**: 注入式时钟驱动 stuck/timeout;cost 超限 SIGINT->SIGTERM->SIGKILL(G4.3);不可重试 fail-fast / 可重试退避(G6.3);脱敏命中;`pricing=missing` 降级 token 兜底护栏 + warning(G5.6)。**Gate**: 标准门 + **No Silent Failures** 断言(所有错误进 `status.errors` 且上报,G6.4)。

## Stage M3 — 多 CLI 适配  (分支 `feat/m3-multi-cli`)

- **Wave 1**(并行 3 task,disjoint)
  - `adapters/codex.py + data/codex.toml`(`codex exec --json --skip-git-repo-check`;`-c model_reasoning_effort=`;`--sandbox read-only -a never` / `--dangerously-bypass-...`;`thread.*`/`turn.*` 词汇)。
  - `adapters/cursor.py + data/cursor.toml`(命令解析 `cursor-agent`->`agent` 回退[注1];effort 编进 model 名;**effort 与用户显式 model 冲突时以用户 model 为准 + warning(G1.4)**;`--mode plan` / `--force`)。
  - `adapters/gemini.py + data/gemini.toml`(`--output-format stream-json`;`--model-thinking-level`;`--approval-mode plan|yolo`;exit 0/1/42/53)。
- **AC**: per-CLI build_command 快照;各家录制流回放归一化一致;effort 翻译表(claude flag/codex `-c`/cursor 后缀/gemini thinking-level)正确;Capabilities 校验明示降级(G1.3);session id 预指定(claude/gemini)/抓取(codex/cursor)(G9.2)。**Gate**: 标准门 + 4 CLI fixture 全绿。

## Stage M3.5 — fan-out / 舰队汇总  (分支 `feat/m35-fleet`)

- `fleet.py`: `dispatch_many`(`asyncio.TaskGroup`+`Semaphore` 控并发)/`fleet_status`(确定性聚合 G2.9)/`consolidate`(合并观测)/可选 `route`(JIT,不隐藏所选 cli)。
- **AC(test_fleet)**: N agent 并行、单 agent 失败不影响其它(失败隔离);`fleet_status` 状态计数/总 token/总 cost 正确;`consolidate` 合并 per-agent reply/errors。**Gate**: 标准门。

## Stage M4 — 超轻量 Summarizer  (分支 `feat/m4-summarizer`)

- `summarizer.py`(语义显著事件 + 滚动阈值触发 + debounce ≥5~10s + rolling "summary+verbatim tail" + 降级兜底)。`summary()` 返回 LLM rolling_summary;无 key/超预算->确定性拼接显著事件。
- **AC**: 触发节流(最小间隔 + 最小新事件数);输入为结构化事件摘要非原文;输出 ≤150 token(G2.6);降级路径可用(G2.7,不阻塞主流程);watcher 花费单独计量+上限(G5.4,haiku/flash/mini tier)。**Gate**: 标准门 + 防幻觉断言(决策字段全来自 reducer,不来自 LLM,G2.3)。

## Stage M5 — 交付层 + release  (分支 `feat/m5-delivery`)

- `cli.py` 全动词(`dispatch[--wait]`/`status`/`summary`/`wait`/`cancel`/`list`/`doctor`/`gc`/`record`)。
- `skill/SKILL.md`(progressive disclosure + 动词 + 策略传参约定 + "父 agent 用 status/summary 轮询,不读原始流"低上下文契约)。
- 可选 `skill/mcp_server.py`(包装 `dispatch.py`)、可选 `otel.py`(`gen_ai.*` 导出,fail-open)。
- **AC**: e2e `dispatch->status->summary->wait` 绿;skill 调用样例;OTEL smoke。**Gate**: 标准门 + e2e 绿;打 tag(local 模式,无 remote push)。

## 不变量与约束 (Constraints Checklist)

- P1 Dispatcher-not-Implementer:L0/L1/L2 派发,仅 L3 实现。
- 每 wave 内文件归属 disjoint;≤5 task/wave;task ≤6 可写文件 / ≤30min。
- 每 stage 一条 feature 分支,本地合并,不直推受保护分支。
- 每 stage 必含测试且过门(单测全过 + mypy strict 0 + cov ≥80% + 0 blocker);不得 TODO 跳过(Mandatory Verification)。
- argv-only spawn,禁 `shell=True`(G8.1);prompt 经 stdin/单 argv(G8.2)。
- No Silent Failures:错误必进 `status.errors` 并上报,不静默吞(G6.4)。
- 厂商方言隔离在单 adapter + TOML(G1.2);`RunSpec` 命令构建可复现(G7.1)。
- 监控:父 agent 零原始流,只 pull `AgentStatus`/`summary`(G2.1/G2.2);可决策字段全确定性(G2.3)。
- spec/governance/implementation-path 为 ground truth;若实现迫使设计变更,先升级对应 doc 再改码。

## 风险与缓解

- 厂商 flag/模型漂移 -> 每 CLI 一函数+TOML、per-CLI 版本集成测试。
- effort/context 不可移植 -> 适配器翻译 + 明示降级(cursor 无 effort flag、无 context flag)。
- summarizer 幻觉 -> 仅 advisory;决策字段全确定性;喂结构化摘要非原文。
- 背压丢事件 -> bounded + 落盘,背压切"摘要模式"不静默丢(避开 agentbridge drop-on-full 坑)。
- 停滞误杀 -> 三层 stuck 检测区分 rate-limit 等待/长工具/真卡死。
- 成本失控 -> per-run + 全局 ceiling + 熔断 + 不可重试 fail-fast;pricing missing 降级 token 护栏。
- 真实计费 -> 用 `record` 工具录 fixtures 回放驱动测试,CI 不打真实 CLI。