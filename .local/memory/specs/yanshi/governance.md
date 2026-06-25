# YanShi 核心管控规范 (Governance Spec) v1.3 — 定稿

> 本文是 YanShi 对 sub-agent 的**规范性管控契约**(normative)。关键词遵循 RFC2119:
> **MUST / MUST NOT / SHOULD / MAY**。配套 `spec.md`、`implementation-path.md`。
> 最后更新: 2026-06-25 (UTC+8)
> v1.1 review 补足: G2.9/G2.10(舰队汇总/单读者)、G3.6/G3.7(preflight/凭证)、G8(命令构造与进程安全)、G9(标识与会话)、G10(运行时存活)。
> v1.2 收敛: G10 重写为 owner_pid 存活模型(去 detached/heartbeat);新增 G1.4(effort/用户 model 冲突)、G5.6(无原生定价时 cost 护栏降级)。
> v1.3 新增: G11(配置与初始化管控,配套 spec §14)——发现/优先级确定性、`[limits]` 天花板夹取必 warn、`[adapters].enabled` fail-fast、摘要器=超轻量 agent-CLI 调用 + 强制降级 + watcher 预算、配置层不擅改用户 model、配置错误 fail-loud、配置驱动动作可审计。

YanShi 是 sub-agent 的**调度入口**。所有管控维度通过 skill 层暴露给调度方,YanShi 负责**如实执行 + 强约束 + 不可越权**。共十一个管控域(G1–G11)。

---

## G1. 控制管控(Control)
对每次派发可控的维度及其规范默认值/校验/降级。

| 维度 | 取值 | 默认 | 不支持时(降级) |
|---|---|---|---|
| `model` | 规范 id 字符串 | 各 CLI 内置默认 | 适配器翻译失败 MUST 报错,不得静默改模型 |
| `reasoning_effort` | low/medium/high/xhigh | medium | cursor 无 flag→编入模型名;gemini→thinking-level;**无法表达时 MUST 记 warning 并用最接近档** |
| 输入上下文体量 | prompt/instructions 字节 | 不限 | **无 CLI 暴露 context-window**;只控输入大小,MUST 在文档/返回里明示该限制 |
| `allow` | read-only / yolo | **read-only** | 见 G3 |
| `timeout_s` / `stall_timeout_s` | 秒 | 见 G4 | — |
| `workdir` / `add_dirs` | 路径 | 调用方传入 | 见 G3 |
| `cost_ceiling_usd` | 浮点 | 调用方传入或全局默认 | 见 G5 |
| `output_schema` | JSON schema | 无 | per-CLI 映射(`--json-schema`/`--output-schema`/`--schema-file`) |

**规则**
- G1.1 控制项 MUST 经 `policy.py` 校验后才下发;非法组合(如 read-only + 要求写文件)MUST 拒绝并返回明确原因。
- G1.2 厂商方言翻译 MUST 隔离在单个适配器 + TOML 内;一处漂移不得扩散。
- G1.3 任何"目标能力无法在该 CLI 上表达"的情形 MUST 以结构化 warning 出现在 `AgentStatus`,MUST NOT 假装成功。
- G1.4 对"effort 编进 model 名"的 CLI(cursor 等):调用方**同时**显式给 `model` 与 `reasoning_effort` 时,**MUST** 以用户 `model` 为准,effort 无法表达即按 G1.3 记 warning,**MUST NOT** 擅自改写用户指定 model;仅当用户**未**显式给 model 时,适配器 MAY 用 effort 合成带后缀的 model 名。

---

## G2. 监控管控(Observability / 低上下文契约)
核心差异化:监控**确定性优先**,父 agent **零原始流**。

- G2.1 父 agent **MUST** 通过 `status()` / `summary()` **pull**;YanShi **MUST NOT** 把原始事件流 push 进父上下文。
- G2.2 原始 NDJSON + transcript **MUST** 落盘(SQLite/ring buffer),**MUST NOT** 进入返回给父的对象。
- G2.3 `AgentStatus` 的**全部可决策字段**(state/progress_pct/tokens/cost/errors/last_event)**MUST** 由确定性 `StatusReducer` 产生,**MUST NOT** 来自 LLM。
- G2.4 `rolling_summary` 是**唯一** LLM 产物,且 **MUST** 标注为 advisory(仅叙述,不可据以做控制决策)。
- G2.5 `progress_pct` 不可确定时 **MUST** 为 `null`,**MUST NOT** 让 LLM 估算填充。
- G2.6 Summarizer **MUST** 节流(最小间隔 ≥5s 且最小新事件数 ≥N),输入 **MUST** 是结构化事件摘要而非原始日志,输出 **MUST** ≤150 token。
- G2.7 Summarizer 不可用/超预算时 **MUST** 降级为"拼接最近显著事件"的确定性兜底,**MUST NOT** 阻塞或报错中断主流程。
- G2.8 状态机(FSM)合法转移:`pending→starting→running→{waiting_rate_limit|waiting_tool}→running→(succeeded|failed|stalled|cancelled|killed)`;非法转移 MUST 记错误。
- G2.9 **舰队级**(多 agent)汇总 `fleet_status` MUST 为确定性聚合(状态计数/总 token/总 cost/阻塞项);`fleet_summary` 的 LLM reduce 为可选且 advisory。父 agent 监控 N 个 sub-agent 时 MUST 仍只 pull 聚合对象,不读各自原始流。
- G2.10 监控者唯一性:每个子进程的 stdout/stderr **MUST** 只有一个读者(监控进程/任务),禁止多读者竞争导致丢事件。

---

## G3. 安全 / 隔离管控(Safety)
无 worktree,边界靠显式策略 + 强约束。

- G3.1 默认 `allow=read-only`;升 `yolo`(注入各家 `--dangerously-*`/`--force`/`--yolo`)**MUST** 由调用方显式声明。
- G3.2 写操作 **MUST** 限制在 `workdir` ∪ `add_dirs`;Runner **MUST** 以该 cwd spawn 并**过滤** agent 环境变量(白名单 + 显式传入),**MUST NOT** 全量透传父环境。
- G3.3 落盘与喂 summarizer 前 **MUST** 经 `secrets.py` 脱敏(key/token/密码正则);**MUST NOT** 把原始密钥写入日志或发给 watcher 模型。
- G3.4 审批默认 auto-approve(无人值守);若策略声明需人工审批点,YanShi **MUST** 将审批请求透传给调用方而非自行决定。
- G3.5 PTY-only CLI(如 aider)**MUST** 标注"低保真监控",并 **MUST NOT** 谎报为完整结构化监控。
- G3.6 **派发前 preflight**:dispatch 前 **MUST** 校验目标 CLI 已安装、已鉴权、版本可识别(`doctor`/preflight)。未鉴权/缺失 **MUST** fail-fast 返回明确原因,**MUST NOT** spawn 一个注定失败的子进程。
- G3.7 **凭证管理**:各 CLI 使用其自身鉴权(`seed_paths`);YanShi **MUST NOT** 在日志/状态/run.json 中持久化原始凭证内容。

---

## G4. 生命周期管控(Lifecycle)

- G4.1 每次 spawn **MUST** 设置墙钟 `timeout_s` 与停滞 `stall_timeout_s`(类型默认: dispatch/impl=1800s、停滞=300s,可被策略覆盖)。
- G4.2 停滞判定 **MUST** 区分三类,**MUST NOT** 一刀切误杀:
  - 上个事件是 `api_retry`/带 `retry_delay` → **等待 rate limit**(不算停滞);
  - 有未完成的 `command_execution`/长工具 → **长工具**(用更长阈值);
  - 长时间无任何事件且无在跑工具 → **真停滞**(候选 kill)。
- G4.3 取消/超时 **MUST** 走 graceful→hard: SIGINT→SIGTERM→(grace)→SIGKILL;kill 后 **MUST** drain 已缓冲输出再 finalize,避免丢最终结果。
- G4.4 **MUST NOT** 产生孤儿子进程/任务:用 `asyncio.TaskGroup` 作用域管理 pump/watchdog,父取消即级联清理。
- G4.5 每个内部循环 **MUST** 有 `max_iterations`;每个等待 **MUST** 包 `asyncio.wait_for`。

---

## G5. 成本管控(Cost)

- G5.1 每次派发 **MUST** 有 per-run cost ceiling;系统 **MUST** 有全局聚合上限。
- G5.2 超限 **MUST** 触发熔断,按 G4.3 终止子 agent,并在 status 标注 `cost_exceeded`。
- G5.3 cost 计量 **MUST** 原生 `usage`/`total_cost_usd` 优先,缺失时用 models.dev 兜底定价并标注 `pricing_status: native|priced|missing`。
- G5.4 watcher(summarizer)模型花费 **MUST** 单独计量、单独设上限,**MUST** 远小于被监控子 agent(选 haiku/flash/mini tier)。
- G5.5 重启 **MUST** 受熔断器约束(最大重启次数 + 退避),**MUST NOT** 无限重启(防"$47K 死循环")。
- G5.6 当 `pricing_status=missing`(无原生 cost 且无 models.dev 命中)时,USD ceiling 不可靠,**MUST** 降级为 token 上限兜底并在 `AgentStatus` 记 warning 明示"成本护栏以 token 估算执行",**MUST NOT** 假装 USD 护栏生效。

---

## G6. 失败管控(Failure / No Silent Failures)
对齐 workspace 规则 **No Silent Failures**。

- G6.1 成功判定 **MUST** 分层: 进程 exit code → 终止事件 `is_error`/`turn.failed` → 错误字符串分类。exit 0 但 `is_error=true` **MUST** 判为失败。
- G6.2 错误 **MUST** 分类为枚举 `ErrorCategory`: `rate_limit / server_error / overloaded`(可重试)、`auth / billing / invalid_request / max_output_tokens`(不可重试)、`unknown`。
- G6.3 可重试错误 **MAY** 指数退避重试(优先 `--resume` 续跑),受 G5.5 约束;不可重试错误 **MUST** fail-fast。
- G6.4 任何错误 **MUST** 进 `AgentStatus.errors` 且向调用方上报;**MUST NOT** try/except 后静默吞掉。仅当调用方显式声明 "best-effort" 的非关键任务方 MAY 忽略,且 **MUST** 仍记 log。
- G6.5 解析错误(非 JSON 行 / 未知事件)**MUST** 容错(落 log / Unknown 桶)而非崩溃,但 **MUST NOT** 静默丢弃——计入 `counters` 并可审计。

---

## G7. 一致性与可审计
- G7.1 同一 `RunSpec` 的命令构建 **MUST** 确定可复现(便于审计/测试快照)。
- G7.2 所有管控动作(降级、重启、熔断、kill、脱敏命中)**MUST** 产生可审计事件(落盘 + 可选 OTEL `gen_ai.*` 导出)。
- G7.3 版本: 本规范随 `spec.md` 同步演进;破坏性变更 **MUST** 升版本并记 CHANGELOG。

---

## G8. 命令构造与进程安全(v0.2 新增)
防止注入与越权,是"如实执行 + 强约束"的底线。

- G8.1 子进程 **MUST** 以 argv 列表(`create_subprocess_exec`)启动,**MUST NOT** 用 `shell=True` 或字符串拼接 shell 命令(杜绝命令注入)。
- G8.2 prompt **MUST** 经 stdin 或单个 argv 参数传入,**MUST NOT** 插值进 shell 字符串。
- G8.3 `workdir` / `add_dirs` **MUST** 规范化(`realpath`)并校验:必须是已存在目录;若策略声明了根边界,MUST 校验其位于边界内(防 `../` 逃逸)。非法路径 **MUST** 拒绝派发。
- G8.4 子进程环境 **MUST** 按白名单过滤(默认不全量透传父环境);`RunSpec.env` 显式注入项才下发。
- G8.5 厂商危险开关(`--dangerously-*`/`--force`/`--yolo`)**MUST** 仅在 `allow=yolo` 时注入,且该值 MUST 来自调用方显式策略,不得有代码默认开启。

## G9. 标识与会话(v0.2 新增)
- G9.1 `agent_id` 由 YanShi 生成(UUID),全局唯一,作为磁盘布局与所有 API 的主键。
- G9.2 对支持预指定 session id 的 CLI(claude/gemini)**SHOULD** 预生成 session id 并下发(`--session-id`),使 YanShi 从一开始掌控会话标识;不支持的(codex/cursor)MUST 从首个 init/`thread.started` 事件抓取并存入 `sessions.json`。
- G9.3 resume **MUST** 用存储的原生 session id;别名(`session_alias`)仅为 YanShi 侧友好名,不直接下发给 CLI。

## G10. 运行时存活(v0.3 收敛,配合 §11/§13)
**无 detached 监控**:监控者 = `dispatch` 调用方(入口 A 的长驻宿主 / 入口 B 的阻塞进程),其存活即监控存活。
- G10.1 `run.json` **MUST** 记录子进程 `pid` 与监控宿主 `owner_pid`;读取者 **MUST** 据 `owner_pid` 存活校验,读到陈旧 `running` 而 owner 已死时 **MUST** 纠正为 `stalled`,**MUST NOT** 误报为 `running`(无须独立 heartbeat 线程)。
- G10.2 `cancel` **MUST** 终止子进程(入口 A 经进程内 API;入口 B 经前台中断或据 `run.json` 子 `pid` 发信号),并在 drain 后 finalize 为 `cancelled`,**MUST NOT** 留下孤儿。
- G10.3 监控宿主崩溃/宿主重启后,子进程可能成孤儿;读取者据 G10.1 置 `stalled`,孤儿子进程 **MUST** 按记录的 `pid` 回收。

## G11. 配置与初始化管控(Configuration)(v1.3 新增,配套 spec §14)
仓库级配置让"不同工作区有不同可用配置"成立;管控底线:**分层确定、收紧必显、错误不静默**。

- G11.1 **发现与优先级确定性**:本地配置 **MUST** 由 `cwd` 向上 walk 至文件系统根、取**首个** `.yanshi.toml`;全局配置 **MUST** 为 `$YANSHI_HOME/config.toml`(默认 `~/.yanshi/config.toml`)。分层优先级 **MUST** 恒为 `内置默认 < 全局 < 本地 < 每次调用覆盖`,逐 section deep-merge,本地同键覆盖全局。解析 **MUST** 确定可复现(同输入→同结果,呼应 G7.1)。
- G11.2 **天花板夹取必 warn**:merge 后 `[limits]` **MUST** 对生效请求执行夹取——`max_allow` 夹 `allow`(权限偏序 `read-only < yolo`;`yolo` 请求在 `read-only` 上限工作区 **MUST** 被夹回 `read-only`)、`max_cost_usd` 夹 `cost_ceiling_usd`、`max_timeout_s` 夹 `timeout_s`。**每次实际发生夹取 MUST 产生结构化 `WarningRecord`**(No Silent Failures,呼应 G1.3/G3.1),**MUST NOT** 静默收紧请求。
- G11.3 **enabled-set fail-fast**:`[adapters].enabled` **MUST** 为 `{claude, codex, cursor, gemini}` 的子集(默认全部);**仅** enabled 的适配器 **MAY** 被注册、被 `doctor`/preflight(G3.6)校验。请求一个被禁用或未知的适配器 **MUST** fail-fast 报错并列出当前 enabled 集合,**MUST NOT** 静默回退到其它 CLI。
- G11.4 **摘要器=轻量 agent-call + 强制降级**:摘要器 **MUST** 实现为一次超轻量 one-shot agent-CLI 调用(`build_command` + 阻塞执行 + 解析 reply),**MUST NOT** 作为被递归监控的 dispatch 运行(不落 `agents/<id>/`、不递归自摘要)。`[summarizer].enabled` 默认 `false`(向后兼容)。摘要 **MUST** 受 G2.6 节流(`debounce_s`/`min_new_events`,输出 ≤150 token)与 G5.4 watcher 预算(`watcher_token_ceiling` + `timeout_s`,选 haiku/flash/mini 便宜档)双重约束。**任何**错误或预算/超时耗尽 **MUST** 降级为确定性"拼接显著事件"兜底(G2.7),**MUST NOT** 阻塞、中断或拖慢被监控的主 run。
- G11.5 **不擅改用户 model**:配置层(`[defaults]`/`[profiles]`)**只填充缺省**;当调用方已显式给定 `model` 时,配置 **MUST NOT** 覆盖或静默改写之(重申 G1.4)。TOML `effort` 键 **MUST** 映射到 `reasoning_effort`,该 CLI 无法表达时按 G1.3 记 warning。
- G11.6 **配置错误 fail-loud**:格式错误的 TOML(语法错误/类型不符)**MUST** 抛出明确错误,**MUST NOT** 静默忽略或 try/except 吞掉(No Silent Failures)。未知 `--profile` 名 **MUST** 记 warning 并忽略(继续按无 profile 解析),**MUST NOT** crash。
- G11.7 **配置驱动动作可审计**:所有由配置触发的动作(`[limits]` 夹取、适配器禁用拒绝、profile 忽略、摘要器降级)**MUST** 产生可审计记录(`WarningRecord` + 落盘,可选 OTEL `gen_ai.*`,呼应 G7.2);`yanshi config` **MUST** 能回放有效值及其 provenance(来源层:built-in/global/local/override)。
- G11.8 **init 不静默覆盖**:`yanshi init` 在目标配置文件已存在且未给 `--force` 时 **MUST** 拒绝覆盖并报明确错误,**MUST NOT** 静默改写用户既有配置(No Silent Failures)。
