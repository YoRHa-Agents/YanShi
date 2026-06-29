[English](QUICKSTART.md) | 简体中文

# YanShi 快速开始

> Last-Modified: 2026-06-25

几分钟内从零完成你的第一次受监控派发。完整文档位于 <https://yorha-agents.github.io/YanShi/>;本指南是
快速路径。

YanShi 把一个任务派发给某个 headless agent CLI(`claude` / `codex` / `cursor` / `gemini`),并让你
通过一个小巧、确定性的状态对象进行监控,而不是读原始日志。

## 1. 安装

通过自带安装器全局安装(无需克隆代码库):

```bash
curl -fsSL https://raw.githubusercontent.com/YoRHa-Agents/YanShi/main/install.sh | bash -s -- --global
```

或从克隆的代码库做本地开发安装:

```bash
git clone https://github.com/YoRHa-Agents/YanShi.git
cd YanShi
./install.sh --local --dev
```

安装器优先使用 `uv`,并以 `pip` + `venv` 兜底。你也可以直接用 `uv tool install .`、`uv sync` 或
`pip install .` 安装。

安装器还会把 skill(`SKILL.md`)**注册**到你的 agent skills 目录,以便上层 agent 发现 YanShi。
若你是直接安装的(未用 `install.sh`),请自行注册:

```bash
yanshi skill register        # 探测 ~/.cursor/skills、~/.claude/skills、~/.agents/skills
```

## 2. 初始化工作区配置 —— `yanshi init`

YanShi 会读取一个可选的仓库级 `.yanshi.toml`(从当前目录沿父目录向上 walk 发现,语义同 `.git`)。
scaffold 一个带注释的起始文件:

```bash
yanshi init                  # 写入 ./.yanshi.toml;未给 --force 时拒绝覆盖
```

编辑它以启用适配器子集、打开建议性摘要器,并定义命名 profiles:

```toml
[adapters]
enabled = ["claude", "codex"]      # 仅注册这些,并仅由 doctor 检查它们

[summarizer]
enabled = true                     # 启用超轻量 agent-CLI 摘要观察者
cli = "claude"
model = "claude-3-5-haiku-latest"  # 便宜档(haiku / flash / mini)

[profiles.cheap]
effort = "low"
cost_ceiling_usd = 0.5
timeout_s = 600

[profiles.thorough]
effort = "high"
timeout_s = 3600
```

随后核对解析后的分层,并带 profile 派发:

```bash
yanshi config                                        # 解析后的配置 + provenance(JSON)
yanshi dispatch --profile cheap "解释这个仓库"        # 套用 [profiles.cheap]
```

`--profile` 选用一个 `[profiles.<name>]` 预设;未知名字会被忽略并记 warning,且每次调用的参数
仍优先于 profile 与 `[defaults]`。

## 3. 检查环境 —— `yanshi doctor`

```bash
yanshi doctor
```

每个已注册适配器会以一行 JSON 报告其可执行文件、版本与鉴权状态:

```json
{"cli": "claude", "status": "ok", "executable": "/usr/local/bin/claude", "version": "…", "errors": [], "warnings": []}
```

只要任一适配器为 `failed`,`doctor` 即以非零码退出。YanShi 只**检测**厂商 CLI,绝不替你安装或鉴权——
在派发之前,先修复任何 `failed` 的适配器(安装二进制、登录)。

## 4. 你的第一次派发 —— `yanshi dispatch --wait`

```bash
yanshi dispatch --cli claude --effort high --wait \
  "总结这个仓库的架构"
```

CLI 派发是**阻塞**的:它内联运行监控内核,直到 agent 进入终态,然后打印一个 `RunResult` JSON 对象
(state、reply、usage、cost、`log_dir` 等)并在出错时以非零码退出。注意:`--wait` 是默认值,也是 CLI
唯一支持的模式;`--no-wait` 会被拒绝——后台派发请用 Python 库。

常用选项(与 `improve` 共享):

| 选项 | 含义 |
| --- | --- |
| `--cli` | 适配器:`claude` / `codex` / `cursor` / `gemini`(默认 `claude`)。 |
| `--model` | 透传给 CLI 的 model id。 |
| `--effort` | 推理强度:`low` / `medium` / `high` / `xhigh`。 |
| `--allow` | 权限模式:`read-only`(默认)或 `yolo`(必须显式)。 |
| `--workdir` | 子进程的工作目录。 |
| `--timeout` | 墙钟超时(秒)。 |

## 5. 低上下文监控 —— `yanshi status` / `yanshi summary`

在运行进行中(或结束后,因为读取均为纯磁盘读)用两次极小的拉取观测它。先找到 id:

```bash
yanshi list                  # -> ["ys-12345-...", ...]
```

再拉取确定性快照与建议性叙述:

```bash
yanshi status  <agent_id>    # 确定性 AgentStatus:state、计数、用量、花费、错误
yanshi summary <agent_id>    # 建议性的 1-3 句滚动摘要
```

> **唯一要紧的规则:** 只轮询 `status` 与 `summary`。原始流保留在
> `$YANSHI_HOME/agents/<agent_id>/stream.ndjson` 下,仅供审计/调试,除非有人明确索取原始日志,**绝不**
> 应粘贴进上层 agent 的上下文。

## 6. 阻塞或终止 —— `yanshi wait` / `yanshi cancel`

```bash
yanshi wait   <agent_id> --timeout 300    # 阻塞至终态(或超时);打印 AgentStatus
yanshi cancel <agent_id>                  # 优雅信号 -> SIGKILL,随后 finalize 为 cancelled
```

`wait` 只是轮询磁盘上的 `AgentStatus.state` 直到终态或超时,绝不重新解析流。处理完旧运行后,用
`yanshi gc --older-than 604800` 回收磁盘(此处为超过 7 天的运行)。

## 7. 迭代直到闸门通过 —— `yanshi improve`

`improve` 把单次派发变成一个有界的**派发 → 闸门 → 精修**循环:

```bash
yanshi improve --cli claude "修复失败的单元测试" \
  --check "uv run pytest -q" --max-iterations 3
```

- **闸门**(`--check`)是权威:退出码 `0` 即通过。该命令以 `shlex` 解析并仅以 argv 方式 spawn(绝不经过
  shell)。
- 若闸门失败,只有其输出的**截断尾部**会被喂回下一轮 prompt——绝不是原始子流——以保持上下文很小。
- 循环始终受 `--max-iterations`(默认 `3`)约束,每次闸门受 `--gate-timeout`(默认 `300` 秒)约束。
  加 `--critic` 可启用建议性的 LLM critic。

它会打印一个 `ImproveResult`,其 `stop_reason` 为 `gate_passed`、`critic_threshold`、
`max_iterations`、`fatal_error` 或 `no_evaluator` 之一,且除非成功否则以非零码退出。

## 下一步

- [README](./README.zh-CN.md) —— 概览、特性与 CLI 速查。
- [Skill 契约](./skill/SKILL.md) —— 上层 agent 应如何驱动 YanShi。
- 完整文档:[安装](https://yorha-agents.github.io/YanShi/getting-started/installation/) ·
  [架构](https://yorha-agents.github.io/YanShi/concepts/architecture/) ·
  [命令参考](https://yorha-agents.github.io/YanShi/cli/reference/) ·
  [Python API](https://yorha-agents.github.io/YanShi/library/python-api/)
- 设计规范(源头真相): [`.local/memory/specs/yanshi/spec.md`](./.local/memory/specs/yanshi/spec.md)
