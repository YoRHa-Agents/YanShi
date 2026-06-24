# YanShi 设计规范 (Design Spec) v0.3

> 源头需求: `.local/human/input/what_i_want.md`
> 调研依据: 本机四个 CLI 实测 + 2026 官方文档 + `/home/agent/reference/` 下 14 个参考仓库的源码精读
> 最后更新: 2026-06-18 00:20 (UTC+8)
> 配套文档: `design-analysis.md`(完备性缺口分析)、`implementation-path.md`(实现路径)、`governance.md`(管控规范)
> v0.2 review 补足: 契约补全(RunResult/Usage/Capabilities)、运行时与进程模型(§11)、多 agent 编排与路由(§12)、持久化布局(§13)。
> v0.3 收敛: 运行时收敛为"一内核+两入口(A 库/MCP 默认 / B CLI `--wait`)+纯磁盘读",去除 fire-and-forget detached 监控(§11);cursor 命令解析 `cursor-agent`→`agent` 回退、effort/用户 model 冲突规则(§4);cost ceiling 无原生定价降级(§6)。功能范围不变(4 CLI / 全特性 / 4 文档)。

## 1. 定位与目标

YanShi 是一个**厂商中立的 sub-agent 调度层**:让"派生子智能体"这一步不再局限于单一工具,而是能把任务派发给任意主流 agent-CLI(claude / codex / cursor-agent / gemini / aider / opencode …),并对其执行过程做**低上下文、低成本的监控**。

### 1.1 核心目标
1. **派发**: 用统一契约把一个任务派发给某个 agent-CLI 的 headless 模式。
2. **兼容**: adapter-per-CLI,新增一个 CLI 只改一个适配器(数据驱动优先)。
3. **控制**: 统一控制 model / reasoning effort / 输入上下文体量 / 权限沙箱 / 超时 / 工作目录 / 环境变量。
4. **监控**: 解析子 agent 的 NDJSON 事件流,维护一个**确定性归一化状态对象**;用**超轻量模型**做按需的"语义进度/健康"叙述。父 agent 只 **pull** 一个小状态对象 + 1~3 句摘要,**永不读原始流**。

### 1.2 交付形态(已定)
三层同一内核(**core 语言已定稿: Python 3.12+ async**,可直接站在 `orchcore` 肩上):
- **core 库**(Python,async-first): 契约 + adapter 注册表 + 流解析 + 监控/supervisor。
- **CLI**(`yanshi dispatch/status/...`): 可被任意编排器/脚本调用。
- **skill 层**(SKILL.md + 可选 MCP server): 把派发与**安全/隔离策略**暴露给调用方(上层 agent)管控。YanShi 是 sub-agent 的调度入口,策略由 skill 层透传给调度方。

### 1.3 非目标(已定)
- **不做 git-worktree / 容器隔离**。文件/工作区隔离由调用方通过 skill 层传入的策略(cwd、可写目录、沙箱级别)自行管控,YanShi 只如实执行并约束。
- 不内置 GUI/TUI(可选 reporter 接口,但默认 headless)。
- 不做提示词模板化(prompt 原样透传,参考 agentbridge/harness 的做法)。

### 1.4 需求修正(基于实测)
- **"控制上下文长度"**: 主流 CLI **均不暴露** context-window flag。改为"控制输入上下文体量"(prompt/instructions 大小 + 模型选择),并依赖各 CLI 自动 compaction。文档须明示该限制。
- **"用模型监控"**: 修正为**确定性优先**——~90% 监控(状态机/计数/错误分类/token/cost)无需 LLM;LLM 仅产出 `rolling_summary` 一个自由文本字段,且不可用时降级为"拼接显著事件"。

## 2. 架构总览

```
                       上层 Agent (Cursor/Claude/...)
                            │  via skill 层 (SKILL.md / MCP: dispatch/status/summary/cancel)
                            ▼
┌──────────────────────────────────────────────────────────────┐
│ YanShi core (async Python 库)                                  │
│                                                                │
│  Dispatcher ──> AdapterRegistry(数据驱动 TOML + 代码适配器)     │
│      │            └─ build_command(RunSpec) → BuiltCommand      │
│      ▼                                                          │
│  Runner(spawn + 双管道 StreamPump,按行缓冲 NDJSON,容错解析)    │
│      │ normalized events                                        │
│      ├──────────────┬───────────────┬──────────────┐           │
│      ▼              ▼               ▼              ▼           │
│  StatusReducer   Supervisor     RawLogSink     UsageMeter       │
│ (确定性 status   (超时/停滞/     (落盘 ring     (token/cost +    │
│  快照, 无 LLM)    分类重启/熔断/  buffer, 不进    models.dev      │
│      │            花费上限)       父上下文)       兜底定价)       │
│      │ 显著事件摘要窗口                                          │
│      ▼                                                          │
│  Summarizer(超轻量模型, 节流触发, rolling_summary, 可降级)      │
│      │                                                          │
│      ▼                                                          │
│  StatusStore(原子写 + flock)  ──(可选)──> OTEL gen_ai.* 导出     │
└──────────────────────────────────────────────────────────────┘
                            ▲  pull: get_status / get_summary(几十 token)
                       上层 Agent
```

**关键原则:可见性平面 与 上下文平面分离**(被 Claude Dynamic Workflows / OpenHands / headless-cli 共同验证):原始流落盘,父 agent 只按需拉取小状态对象。

## 3. 核心契约(从 harness / headless-cli / agentbridge 提炼)

### 3.1 任务输入 `RunSpec`(借 harness `RunSpec` + headless `BuildOptions`)
```python
@dataclass
class RunSpec:
    cli: str                      # claude | codex | cursor | gemini | ...
    prompt: str                   # 原样透传, 不做模板化
    prompt_mode: str = "stdin"    # stdin | argument (per-adapter 默认)
    model: str | None = None      # 规范 id, 适配器翻译成各家方言
    reasoning_effort: str | None = None  # low|medium|high|xhigh (见 3.5 翻译)
    allow: str = "read-only"      # read-only | yolo (权限/沙箱模型, 见 §6)
    workdir: str | None = None    # cwd, 由调用方策略决定 (无 worktree)
    add_dirs: list[str] = ()      # 额外可写目录
    env: dict[str, str] = {}
    timeout_s: int | None = None  # 总墙钟超时
    stall_timeout_s: int | None = None   # 无输出停滞超时
    session_mode: str = "new"     # new | resume
    session_id: str | None = None # 原生 id(resume 用)
    session_alias: str | None = None     # YanShi 侧友好名
    output_schema: dict | None = None    # 约束最终结构化输出 (per-CLI json-schema)
    cost_ceiling_usd: float | None = None
```

### 3.2 命令构建 `BuiltCommand`(借 harness `build_command` / headless `BuiltCommand`)
关键设计:**构建与执行分离**。adapter 只产出 argv,不 fork;Runner 负责 spawn + 监控。
```python
@dataclass
class BuiltCommand:
    command: str
    args: list[str]
    env: dict[str, str] | None = None
    stdin_file: str | None = None
    stdin_text: str | None = None
```

### 3.3 适配器契约 `Adapter`(借 headless `AgentHarness` 的纯函数 seam)
```python
class Adapter(Protocol):
    name: str
    prompt_mode: str                 # stdin | argument
    seed_paths: list[str]            # 鉴权/配置文件(供策略校验)
    def build_command(self, spec: RunSpec, env: Env) -> BuiltCommand: ...
    def parse_event(self, raw_line: str) -> YanShiEvent | None: ...  # 原生事件 → 归一化
    def parse_result(self, outcome: RawOutcome) -> RunResult: ...
    def session_id_from_event(self, ev: dict) -> str | None: ...     # 抓 session/thread id
```
> 厂商方言(尤其 cursor 的 effort-as-model-suffix、codex 的 `-c` override)**隔离在单个适配器内**,版本漂移只改一处。

### 3.4 归一化事件 `YanShiEvent`(借 agentbridge `TurnEvent` 的纪律)
规则:**抑制 delta、只发完整块、角色正确、去重、终止事件携带 Usage**。
```python
@dataclass
class YanShiEvent:
    kind: str   # started|assistant_text|tool_use|tool_result|reasoning|file_change|usage|error|completed
    text: str = ""
    usage: Usage | None = None        # input/cached_input/output/reasoning tokens
    err: str | None = None
    raw: str = ""                     # 原始行, 落盘用, 不进父上下文
    ts: float = 0.0
```

### 3.5 归一化状态快照 `AgentStatus`(agentbridge 缺、headless 部分有 —— YanShi 的增量)
parent **pull** 的唯一对象(见 `design-analysis.md` §4 完整 schema)。FSM:
`pending → starting → running → {waiting_rate_limit | waiting_tool} → running → (succeeded | failed | stalled | cancelled | killed)`
只有 `rolling_summary` 来自 LLM;`state/progress/tokens/cost/errors` 全部确定性。

### 3.6 `Usage` / `RunResult`(v0.2 补全 —— 此前被引用但未定义)
```python
@dataclass
class Usage:
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    @property
    def total(self) -> int: ...

@dataclass
class RunResult:
    agent_id: str
    cli: str
    state: str                      # succeeded | failed | stalled | cancelled | killed
    is_error: bool
    reply: str | None = None        # 最终答案文本(extractFinalMessage 等价)
    structured_output: dict | None = None  # 当 output_schema 约束时
    session_id: str | None = None   # 原生 id,供 resume
    usage: Usage = Usage()
    cost_usd: float | None = None
    pricing_status: str = "missing" # native | priced | missing
    exit_code: int | None = None
    duration_ms: int | None = None
    error_category: str | None = None  # 见 governance G6.2
    artifacts: list[dict] = ()      # {type, path, action}
    log_dir: str = ""               # 原始流/transcript 落盘路径(不进父上下文)
```

### 3.7 能力声明 `Capabilities`(v0.2 新增 —— 派发前校验 + 明示降级)
每个 adapter 在注册表里声明能力,`policy.py` 据此**派发前**校验 `RunSpec`,不支持项按 G1.3 降级并记 warning。
```python
@dataclass
class Capabilities:
    effort: str            # "flag" | "config" | "model_suffix" | "thinking_level" | "none"
    context_window_flag: bool       # 全部为 False(无 CLI 暴露)
    session_resume: bool
    preassign_session_id: bool      # 能否预指定 session id(claude/gemini 可)
    output_schema: bool
    stream_json: bool               # 有结构化事件流;否则 PTY 低保真
    permission_modes: list[str]     # ["read-only","yolo"]
```

## 4. Per-CLI 适配映射(实测 flag,见 headless `agents.ts` 验证)

| 维度 | claude | codex | cursor | gemini |
|---|---|---|---|---|
| headless 基座 | `claude -p` (stdin) | `codex exec --json --skip-git-repo-check` | `cursor-agent -p --output-format stream-json` (arg)〔注1〕 | `gemini -p --output-format stream-json` (stdin) |
| 结构化输出 | `--output-format stream-json --verbose` | `--json` | `--output-format stream-json` | `--output-format stream-json` |
| model | `--model`(`claudeModel` 归一) | `-m/--model` | `--model`(折进 reasoning 变体) | `-m/--model` |
| **effort** | `--effort low\|medium\|high\|xhigh` | `-c model_reasoning_effort=` | **无 flag,编进 model 名**(`gpt-5.5-high`) | `--model-thinking-level` |
| 权限 read-only | `--allowedTools Read,Grep,...` | `--sandbox read-only -a never` | `--mode plan` | `--approval-mode plan` |
| 权限 yolo | `--dangerously-skip-permissions` | `--dangerously-bypass-approvals-and-sandbox` | `--force` | `--approval-mode yolo` |
| session resume | `--resume <id>` / `--session-id` | `exec resume <id>` | `--resume <id>` | `--resume <id>` / `--session-id` |
| 终止事件 | `result`(`is_error`) | `turn.completed`/`turn.failed` | `result`(`is_error`) | `result`(+ exit 0/1/42/53) |
| 输出 schema | `--json-schema` | `--output-schema` | (json result) | `--schema-file` |
| 事件词汇 | `system`/`assistant`/`stream_event`/`result` | `thread.*`/`turn.*`/`item.*` | `system`/`assistant`/`tool_call`/`result` | `init`/`message`/`tool_use`/`result` |

成功判定**分层**: ① 进程 exit code(gemini 最丰富)→ ② 终止事件 `is_error`/`turn.failed` → ③ 错误字符串分类(rate_limit/auth/billing/server_error)。

**〔注1〕cursor 命令解析**: 安装器会同时落 `cursor-agent` 与短别名 `agent`(实测同一二进制的两个 symlink)。cursor 适配器 **MUST** 按 `cursor-agent` → `agent` 顺序解析可执行名,任一存在即用,**MUST NOT** 硬编码单一名字(否则只装其一的机器上 preflight 会误报缺失)。

**〔注2〕effort 与用户 model 冲突(cursor / 任何"effort 编进 model 名"的 CLI)**: 当调用方**同时**显式给了 `model` 与 `reasoning_effort`,而该 CLI 只能把 effort 编进 model 名(如 `gpt-5.5-high`)时,**以用户显式 `model` 为准**,effort 无法表达即按 G1.3 记结构化 warning(`MUST NOT` 擅自改写用户指定的 model)。仅当用户**未**显式给 model 时,适配器才可用 effort 合成带后缀的 model 名。

## 5. 监控子系统(YanShi 的核心差异化)

| 组件 | 职责 | 用 LLM? | 参考 |
|---|---|---|---|
| **StreamPump** | async 双管道(stdout+stderr 都要读, 否则满缓冲死锁), 字节缓冲按 `\n` 切, 容错解析(非 JSON 行→log 不崩, 未知 type→Unknown 桶) | 否 | orchcore 4-stage pipeline; streamparse 部分 JSON |
| **StatusReducer** | 纯函数 `(status, event)→status`: 计数器/FSM/last tool/错误分类/token/cost | 否 | headless 8-state + agentbridge TurnEvent |
| **Supervisor/Watchdog** | 墙钟超时 + 停滞超时(区分 rate-limit 等待 / 长工具 / 真卡死)、exit 分类、graceful→SIGKILL、分类重启(可重试退避 + `--resume`)、熔断、**总花费上限** | 否 | tab-conductor `stuck_detector`(心跳/SHA 重复/pgrep 三层); orchcore rate-limit recovery |
| **RawLogSink** | 原始 NDJSON + transcript 落盘(SQLite/ring buffer), 字节偏移切片, **永不进父上下文** | 否 | headless native-transcripts byte-offset slice |
| **UsageMeter** | token/cost 归一; 原生 cost 优先, 否则 models.dev 兜底定价(`native\|priced\|missing`) | 否 | headless `usage.ts` |
| **Summarizer** | 事件/阈值触发 + debounce(≥5~10s), 输入=紧凑事件摘要(非原始日志), 输出≤150 token 的 1~3 句; 模型不可用→拼接显著事件兜底 | **是(唯一)** | 研究 §2 rolling summary |

**触发策略**: 只在语义显著事件(工具完成/错误/阶段切换/完成)+ 滚动阈值触发;token delta 仅作存活证据,不喂 summarizer。
**防幻觉**: 所有可决策字段来自 reducer;`progress_pct` 无法确定时为 null,绝不让 LLM 编。

## 6. 安全 / 隔离 / 策略(通过 skill 层暴露给调用方)

无 worktree,因此安全边界靠**显式策略 + 如实执行 + 强约束**:
- **权限模型**: `allow = read-only | yolo`(借 headless 二值模型);yolo 才注入各家 `--dangerously-*`/`--force`/`--yolo`。默认 read-only。
- **可写边界**: `workdir` + `add_dirs` 由调用方传入;Runner 以 `cwd` spawn,过滤 agent 环境(orchcore "filtered agent environments by default")。
- **密钥脱敏**: 落盘/喂 summarizer 前正则脱敏(tab-conductor `secret_filter`)。
- **花费护栏**: per-run + 全局 cost ceiling,超限 SIGINT→SIGTERM→SIGKILL(tab-conductor `cost_guard`);防"$47K 死循环"。当 `pricing_status=missing`(既无原生 cost 又无 models.dev 命中)时,USD ceiling 无法可靠执行,**MUST** 降级:① 改用 token 上限兜底(`cost_ceiling_usd` 换算为粗略 token 阈值,或策略另给 `token_ceiling`),② 在 `AgentStatus` 记 warning 明示"成本护栏以 token 估算执行",**MUST NOT** 假装 USD 护栏生效。
- **审批**: 默认 auto-approve(无人值守);策略可声明需人工审批点(透传给调用方)。
- 遵守 workspace 规则 **No Silent Failures**: 不可吞错,错误必须落 status.errors + 上报。

## 7. Skill 层设计(交付给调用方)
- `SKILL.md`(progressive disclosure)+ 可选 MCP server,暴露动词: `dispatch`(派发,返回 agent_id)、`status`(pull 状态快照)、`summary`(pull rolling_summary)、`wait`(阻塞至 idle/超时)、`cancel`、`list`。
- 参考已有 `skill-sub-agent-dispatch` + `agents-mcp`(MCP 派发)、`agent-dispatch`(JIT router)。调用方在 dispatch 时传入策略(model/effort/allow/workdir/cost_ceiling),实现"策略由调度方管控"。
- 监控低上下文契约: skill 文档明确"父 agent 用 `status`/`summary` 轮询,不读原始流"。

## 8. 实现计划(里程碑)
> 详见 `implementation-path.md`。概要(v0.2 已补 preflight/runtime/fan-out):
- **M0 骨架**: 契约全集(含 RunResult/Usage/Capabilities)+ AdapterRegistry + claude adapter + preflight/doctor(CLI 安装/鉴权/版本探测)跑通 dispatch→exec→capture。
- **M1 流监控**: StreamPump + StatusReducer + RawLogSink + UsageMeter + 磁盘布局(§13);claude stream-json 归一化。
- **M1.5 运行时入口**: 监控内核 + 入口 A(库/MCP 后台 Task)+ 入口 B(CLI `dispatch --wait`)+ `status/wait/cancel/list` 纯读盘 + owner_pid 存活校验(§11)。
- **M2 supervisor**: 超时/停滞/exit 分类/分类重启/熔断/cost ceiling。
- **M3 多 CLI**: codex/cursor/gemini adapter + effort 翻译表 + Capabilities 声明。
- **M3.5 fan-out**: `dispatch_many`/`fleet_status`/`consolidate`(§12),可选 route。
- **M4 summarizer**: 超轻量模型 rolling summary + debounce + 降级 + watcher 限额。
- **M5 交付层**: CLI 全动词 + SKILL.md(+ 可选 MCP)+ 策略透传 + 可选 OTEL。

## 9. 风险与缓解

| 风险 | 缓解 |
|---|---|
| 厂商 flag/模型变体漂移 | 每 CLI 一个适配器函数/表,数据驱动;per-CLI 版本集成测试 |
| effort/context 不可移植 | 适配器翻译 + 明示降级(cursor 无 effort flag;无 context flag) |
| summarizer 幻觉 | 仅 advisory 文本;决策字段全确定性;喂结构化摘要非原文 |
| 背压丢事件 | 不用 drop-on-full(agentbridge 的坑);bounded+落盘,背压时切"摘要模式"不静默丢 |
| 停滞误杀 | 事件语义区分 rate-limit 等待 / 长工具 / 真卡死(三层 stuck 检测) |
| 成本失控 | per-run + 全局 ceiling + 熔断 + 分类(不可重试 fail-fast) |
| 无隔离的越权写 | read-only 默认 + 显式 add_dirs + 环境过滤 + 密钥脱敏 |
| PTY-only CLI(aider) | 标注"低保真监控",PTY 抓取 + 存活 watchdog 兜底 |

## 10. 参考仓库索引(`/home/agent/reference/`)
核心三件套: **harness**(RunSpec/build_command 分离)、**headless-cli**(控制面 + 8-state + transcript 切片监控)、**agentbridge**(TurnEvent 归一化 + steering)。
Python 骨架候选: **orchcore**(async 多 agent + 4-stage 流管线 + registry-as-data + rate-limit recovery)。
监控/安全: **tab-conductor**(stuck_detector/cost_guard/secret_filter + skill 打包)、**streamparse**(部分 JSON)、**tracing_agents**(OTEL gen_ai.*)。
交付层: **skill-sub-agent-dispatch** + agents-mcp、**agent-dispatch**(JIT router)。
隔离(本项目不用但可参考): claude-squad / uzi(worktree)、container-use(容器)、humanlayer(并行会话+审批)、mco(fan-out 聚合)。
未 clone(网络失败,功能冗余): opencode / goose / vibe-kanban。

## 11. 运行时与进程模型(v0.3 收敛 —— 一个内核 + 两个入口 + 一份磁盘状态)

`dispatch()→agent_id` 后用 `status()` 轮询的异步模型,要求子 agent 进程生命周期跨越调用边界。收敛原则:**监控内核只有一份,读取者恒为纯磁盘读,差异只在"谁来跑这个内核"。**

### 11.1 监控内核(唯一)
StreamPump→Reducer→Supervisor→Summarizer 是同一套代码,产出 `AgentStatus` 并镜像落盘(§13)。子进程 stdout/stderr **MUST** 只有一个读者(G2.10)。

### 11.2 两个入口
- **入口 A(默认):库 / MCP / 长驻编排器。** 父进程持有事件循环,`dispatch()` 在后台 `asyncio.Task` 里 spawn 子进程并跑监控内核;`status()`/`summary()` 直接读内存快照(同时镜像落盘)。MCP server / skill 常驻时天然成立,是**首选模式**。
- **入口 B:CLI 阻塞式 `yanshi dispatch --wait`。** 单进程内联跑监控内核至终态,打印 `RunResult`;期间状态实时镜像落盘,另一进程可纯读盘观测。

> **不做** fire-and-forget detached 监控(`dispatch` 退出后仍有独立监控进程驻留)。它只服务"CLI 派发即退、稍后另起 status"这一非核心用法,却独自背负 heartbeat 失活判定、跨宿主孤儿回收等全部增量复杂度。需要"派了就走"的调用方应用入口 A(长驻宿主)承载。

### 11.3 读取者恒为纯磁盘读
`status`/`summary`/`wait`/`list`/`fleet_status` **MUST** 是对 `$YANSHI_HOME/agents/<id>/`(§13)的纯读,零子进程交互、零 LLM(summary 读已生成的 rolling_summary)。监控内核运行时镜像落盘,终态由内核 finalize 落盘;**监控宿主存活即监控存活**。
- `cancel <agent_id>`:入口 A 为进程内 API;入口 B 为前台中断或据 run.json 子 `pid` 发信号,均按 G4.3 graceful→SIGKILL 后 finalize `cancelled`。
- `wait <agent_id> --timeout`:轮询磁盘 `AgentStatus.state` 至终态或超时(不重新解析流)。
- 监控宿主崩溃 → 子进程可能成孤儿;读取者据 `owner_pid` 存活校验把陈旧 `running` 纠正为 `stalled`(G10),孤儿按 pid 回收。

## 12. 多 agent 编排与路由(v0.2 新增 —— 兑现"sub-agent 不局限单工具"的核心)

单 agent 派发是基元;真实价值在并行派发多个异构 sub-agent。

### 12.1 批量/并行派发
- `dispatch_many(specs: list[RunSpec], max_parallel: int) -> list[agent_id]`:`asyncio.TaskGroup` + `Semaphore(max_parallel)` 控并发;每个 agent 独立 run record、独立监控者、独立 cost 计量,**互不串扰上下文**(参考 OpenHands 子 agent 隔离)。
- 失败隔离:单 agent 失败 MUST NOT 影响其它;聚合层汇总。

### 12.2 舰队级状态汇总(headless 明确缺失的一项 —— YanShi 补)
- `fleet_status(agent_ids) -> FleetStatus`:确定性聚合 N 个 `AgentStatus`(各状态计数、总 token/cost、最早开始/最晚更新、阻塞项列表)。**纯确定性,无 LLM。**
- `fleet_summary(agent_ids)`:可选 map-reduce —— 每 agent 已有 rolling_summary,cheap 模型再做一次 reduce 合并成一句舰队级叙述;父只在显式请求时调用。

### 12.3 路由(可选,默认显式指定)
- 默认:调用方在 `RunSpec.cli` 显式指定 CLI。
- 可选 `route(task) -> cli`:基于 keyword/能力/配额的 JIT 路由(参考 `agent-dispatch` 的 router + `skill-sub-agent-dispatch` 的配额回退)。配额/不可用时按优先级回退到次选 CLI,并记 warning。路由是**便利层**,不得隐藏所选 CLI(MUST 在 status 暴露实际 cli)。

### 12.4 结果合并
- `consolidate(agent_ids) -> dict`:汇总各 `RunResult`(reply/artifacts/errors per-agent),返回给父一个合并观测(参考 OpenHands "consolidated observation");父只看合并结果,不看各自原始流。

## 13. 状态持久化磁盘布局(v0.2 新增)

```
$YANSHI_HOME (默认 ~/.yanshi)/
├── agents/<agent_id>/
│   ├── run.json          # RunRecord: spec 摘要, pid(子进程), owner_pid(监控宿主),
│   │                     #   cli_version, state, AgentStatus 快照, session_id, log 偏移
│   ├── run.lock          # filelock(原子写 + flock, mode 0600)
│   ├── stream.ndjson     # 原始事件流(ring buffer / 截断), 字节偏移切片读
│   └── result.json       # 终态 RunResult
├── sessions.json         # alias→native session id 映射(headless sessions.ts 等价)
└── pricing-cache.json    # models.dev 定价缓存
```
- 写 `run.json`/`AgentStatus` **MUST** 原子(临时文件 + rename + flock),mode 0600。
- `owner_pid` 为监控宿主(入口 A 长驻进程 / 入口 B 阻塞进程)pid;读者据其存活校验,陈旧 `running` 且 owner 已死 → 纠正为 `stalled`(无 detached 监控,故不依赖独立 heartbeat 线程)。
- 保留策略:终态 agent 目录默认保留 N 天后 GC(`yanshi gc`),`stream.ndjson` 超过上限即 ring-buffer 截断(不静默丢——计数 + 标注)。




