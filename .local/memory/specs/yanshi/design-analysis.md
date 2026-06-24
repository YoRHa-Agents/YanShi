# YanShi 设计完备性分析 + 参考库调研

> 来源: `.local/human/input/what_i_want.md` 的设计评审。
> 最后更新: 2026-06-17 21:09 (UTC+8)
> 研究依据: 本机 agent-CLI `--help` 实测 + 2026 官方文档 + 8 个已 clone 的参考仓库 (`/home/agent/reference/`)。
>
> **本文为论证/缺口分析的历史依据**(rationale archive),设计现状以 `spec.md`/`governance.md`/`implementation-path.md` 为准。
> **2026-06-18 决议更新(收敛评审)**:① 运行时收敛为"一内核 + 两入口(A 库/MCP 默认 / B CLI `--wait`)+ 纯磁盘读",**去除** fire-and-forget detached 监控;② 范围维持 4 CLI + 全特性 + 4 文档(不砍能力,收敛聚焦于精简表述/去重/补一致性);③ cursor 命令名解析 `cursor-agent`→`agent` 回退、effort 与用户显式 model 冲突以用户 model 为准、cost ceiling 无原生定价时降级为 token 兜底。本文 §2/§3 的缺口分析仍有效,文件/隔离一项已按"无 worktree+显式策略"在 spec §1.3/§6 定案。

## 0. 总体结论

**愿景成立、方向正确,但当前文档处于"目标陈述"层级,远未达到"设计完备"。**

核心命题 —— *厂商中立的 sub-agent 派发 + 用超轻量模型低上下文监控* —— 被强力的先例验证:Claude Code Dynamic Workflows(2026-05)、`harness`、`headless-cli`、`agentbridge` 几乎在做同一件事。所以这不是空想。
但 11 行需求里缺少 ~8 个必须定义的设计维度(任务/结果契约、文件隔离、并发、能力差异适配、失败/重启、安全沙箱、状态数据模型、交付形态),且其中一条需求("控制上下文长度")在当前主流 CLI 上**基本不可实现**,需要重新表述。

## 1. 需求覆盖矩阵(原文 7 条 → 可行性 + 参考)

| # | 原始需求 | 可行性 | 关键发现 / 参考 |
|---|---------|--------|----------------|
| 1 | 调用 agent-cli 派发任务 | ✅ 可行 | 四个 CLI 都有 headless 模式:`claude -p` / `codex exec` / `cursor-agent -p` / `gemini -p`。参考 `harness` 的 `RunSpec→RunResult`。 |
| 2 | 兼容主流各种 agent-cli | ✅ 可行,需适配层 | 需 adapter-per-CLI 抽象。`harness`(13 个 CLI)、`headless-cli`、`agentbridge` 是直接模板。 |
| 3 | 控制模型 | ✅ 可行 | 全部支持 `--model`。需做"规范 model id → 各家方言"的归一化(`harness/model_normalization.py`)。 |
| 3 | 控制上下文长度 | ⚠️ **基本不可行** | **没有一个 CLI 暴露 context-window 控制 flag。** 只能间接:控制喂入的 prompt/instructions 大小、选模型、靠各家自动 compaction。此需求需重写为"控制输入上下文体量"。 |
| 3 | 控制 effort | ⚠️ 差异大 | claude `--effort`;codex `-c model_reasoning_effort=`;cursor **无**(靠 `-thinking` 模型变体);gemini `--model-thinking-level/budget`。需 per-adapter 翻译,且要接受"cursor 无法精确控制 effort"。 |
| 4 | 监控状态/输出/错误 | ✅ 可行 | 全部支持 `--output-format stream-json`/`--json` 的 NDJSON 事件流。成功判定要分层:exit code → 终止事件 `is_error`/`turn.failed` → 错误字符串分类。 |
| 5 | 不占大量上下文的轻量监控 | ✅ 可行,**但需修正方法** | 见 §3。核心修正:**90% 监控应是确定性的(解析事件,无 LLM)**,超轻量模型只在需要时做"叙述性总结",且 parent 是 **pull** 状态而非被 push 原始流。 |

## 2. 设计完备性缺口(按严重度)

### 🔴 Blocker 级(不补就无法落地)
1. **任务输入契约未定义** — 一个"任务"包含什么?prompt、工作目录、可读/可写文件集、前序产物摘要、验收标准?(参考 `harness/SPEC.md` 的 `RunSpec`。)
2. **结果/产物回收契约未定义** — sub-agent 的最终答案、改动文件、结构化输出如何回传 parent?用 `--json-schema`/`--output-schema` 约束输出。
3. **文件/工作区隔离未定义** — sub-agent 会真实写文件、跑 shell。并行多个 agent 必须隔离(git worktree / 容器)。`claude-squad`、`uzi`(worktree)、`container-use`(容器+分支)都在解决这个。**这是并行派发的前提,现在完全缺失。**
4. **安全/沙箱/权限模型缺失** — 这些 CLI 默认要 `--dangerously-skip-permissions`/`--yolo` 才能无人值守,等于让 sub-agent 任意执行代码。必须定义:沙箱级别、允许工具白名单、密钥脱敏、可写目录边界。(违反 workspace 规则 "No Silent Failures" 的反面 —— 这里是"无约束执行"风险。)

### 🟠 Critical 级
5. **能力差异适配策略缺失** — 不同 CLI 对 effort/context/事件 schema 差异巨大(见矩阵)。需明确"规范能力集 + per-adapter 翻译 + 不支持时的降级行为"。
6. **失败/重启/成本护栏缺失** — 错误分类(可重试 `rate_limit/server_error` vs 不可重试 `auth/billing`)、指数退避、`--resume` 续跑、熔断、**总花费上限**(防"$47K 周末死循环")。
7. **归一化状态数据模型缺失** — parent 读的统一 status 对象 schema(state FSM、progress、tokens、cost、errors、artifacts、rolling_summary)。§4 已给出建议 schema。
8. **并发/调度模型未定义** — 并行 sub-agent 数量、资源上限、调度策略、fan-out/聚合。

### 🟡 Major 级
9. **交付形态不清晰** — "YanShi is a skill" 到底是:Cursor skill?CLI?库?MCP server?常驻 daemon?它被谁调用(被上层 agent 当工具调用来 spawn sub-agent)?需明确集成面。
10. **CLI 发现与鉴权** — 检测已装 CLI、版本兼容、各家独立 auth 管理。
11. **超时/停滞检测的语义歧义** — "长时间无输出"不等于卡死(可能在跑 `pytest` 或在等 rate limit)。需用事件语义区分。
12. **测试策略缺失** — workspace 规则要求强制验证;需对每个 adapter 做 per-CLI 版本集成测试 + 事件归一化测试(参考 `agentbridge/driver_event_normalization_test.go`)。

## 3. 关键修正:监控应"确定性优先,LLM 只做叙述"

原文"用超轻量模型对 sub-agent 执行过程进行监控和总结"——方向对,但**过度依赖模型**。研究一致结论:

```
sub-agent NDJSON 流(几千条事件)
      ↓
(a) 确定性 StatusReducer —— 无模型,纯函数 (status,event)→status
    处理 ~90% 监控:计数器、FSM、last tool、错误分类、token/cost。≈0 成本。
      ↓ (只把"显著事件摘要窗口"喂下去)
(b) 超轻量 Summarizer(haiku/flash/mini)—— 节流触发,非逐条
    输入=紧凑事件摘要(非原始日志),输出=1–3 句滚动状态叙述。
      ↓
parent agent —— pull 一个小 status 对象 + 短摘要(几十 token);**永不读原始流**
```

要点:① parent **pull** 而非被 push;② 原始 NDJSON 落盘(SQLite/ring buffer),不进 parent 上下文;③ LLM 只产出 `rolling_summary` 这一个自由文本字段,所有"可据以决策"的字段都来自确定性 reducer(防幻觉);④ summarizer 不可用时降级为"拼接最近显著事件"。被 Claude Dynamic Workflows / OpenHands / GitHub Actions 日志流共同验证(可见性平面与上下文平面分离)。

## 4. 建议归一化状态 Schema(供 parent pull)

```jsonc
{
  "agent_id": "ys-7f3a", "cli": "claude-code", "session_id": "abc123",
  "model": "claude-haiku-4.x",
  "state": "running",            // pending|starting|running|waiting_rate_limit|waiting_tool|succeeded|failed|stalled|cancelled|killed
  "progress_pct": 60,            // best-effort,无则 null,绝不让 LLM 编
  "last_event": {"kind":"tool_call","summary":"ran `pytest -q` (exit 1)","ts":"..."},
  "liveness": {"idle_seconds":2,"stalled":false,"waiting_reason":null},
  "counters": {"events":1840,"tool_calls":12,"files_changed":5,"retries":1},
  "tokens": {"input":24763,"cached_input":24448,"output":122},
  "cost_usd": 0.0184,
  "errors": [{"category":"rate_limit","status":429,"fatal":false}],
  "artifacts": [{"type":"file_edit","path":"src/auth.ts"}],
  "rolling_summary": "正在排查 auth 测试失败;改了 auth.ts 重跑 pytest 仍 1 处失败……",
  "exit": {"code":null,"is_error":null,"duration_ms":null}
}
```

## 5. 已 clone 的参考库(`/home/agent/reference/`)及借鉴点

| 仓库 | 借鉴维度 | 重点文件 |
|------|---------|---------|
| **harness** (Py/TS) | **抽象核心** `RunSpec/RunResult` + adapter registry + 模型归一化 | `SPEC.md`, `src/harness/base.py`, `adapters/*.py`, `model_normalization.py`, `registry.py` |
| **headless-cli** (TS) | **控制面 + 运行监控**(model/effort/session/output + run-status) | `src/types.ts`(`reasoningEffort`/`sessionMode`), `run-status.ts`, `run-metrics.ts`, `native-transcripts.ts`, `skills/headless-swarm/` |
| **agentbridge** (Go) | **事件归一化 + token 计量**(轻量监控的喂料) | `backend.go`(`RawEvent`/`Usage`), `multi_backend.go`, `*_stream_driver.go`, `driver_event_normalization_test.go` |
| **mco** (Py/JS) | 并行 fan-out + 结构化结果聚合(JSON/SARIF/MD) | self-describing CLI |
| **claude-squad** (Go) | 会话生命周期:tmux + git worktree 隔离 | session/worktree 管理 |
| **uzi** (Go) | worktree 并行派发 | — |
| **container-use** (Go) | 容器隔离 + 全命令日志(事后监控) | MCP server 模式 |
| **humanlayer** (Go/TS) | 并行会话编排 + 审批 + 12-factor-agents | 最大(27M) |

未 clone(网络失败但已评估,与上述功能冗余):`opencode`, `goose`, `vibe-kanban`;另 `viamin/agent-harness`(Ruby,熔断/限流/健康监控)、`matt82198/conductor`(流解析/停滞检测)值得读源码不必 clone。

## 6. 建议的最小可行架构 & 下一步

**合成路线**:以 `harness` 的 `RunSpec/RunResult`+adapter registry 为核心 → 叠加 `headless-cli` 的控制面(model/effort/session/output + run-status)→ 用 `agentbridge` 的归一化 `RawEvent` 流 + `Usage` 作为超轻量 supervisor 的喂料。得到:厂商中立派发 + 低上下文监控。

**组件**:CLIAdapter(每 CLI 一个,含 PTY 降级)→ StreamPump(异步双管道、按行缓冲、容错解析)→ StatusReducer(确定性)+ Supervisor/Watchdog(超时/停滞/分类重启/熔断/花费上限)+ RawLogSink(落盘 ring buffer)→ Summarizer(节流+降级)→ StatusStore(+可选 OTEL `gen_ai.*` 导出)。

**给用户的待澄清问题**:见聊天回复 §"需要你拍板的开放问题"。
