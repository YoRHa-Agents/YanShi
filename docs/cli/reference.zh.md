# 命令参考

`yanshi` 命令是库之上的一层薄封装。每个动词都打印机器可读的输出(JSON,`summary` 除外,它
打印摘要文本),并用退出码来表示失败,因此它能在脚本和编排器中干净地组合。

!!! note "约定"
    - `AGENT_ID` 是在 `RunResult` / `AgentStatus` 中打印的 id(也可通过 `list` 发现)。
    - `status`、`summary`、`wait` 和 `list` 是对 `$YANSHI_HOME` 的**纯磁盘读取**。
    - 读取 agent 意味着只拉取 `status` + `summary`——绝不读取原始流
      (见 [监控](../concepts/monitoring.md))。

## doctor

检查每个已注册适配器的可执行文件与鉴权状态。

```text
yanshi doctor
```

为每个适配器打印一个 JSON 对象(`cli`、`status`、`executable`、`version`、`errors`、
`warnings`),并在**任一**适配器 preflight 失败时以非零退出。

```bash
yanshi doctor
```

## init

写入一份带注释的起始配置(模板见 [配置](../reference/configuration.md))。默认写本地的 `./.yanshi.toml`,或用 `--global` 写全局的 `$YANSHI_HOME/config.toml`。

```text
yanshi init [--global | --local] [--force]
```

| 选项 | 默认 | 说明 |
|---|---|---|
| `--global` / `--local` | `--local` | 写全局的 `$YANSHI_HOME/config.toml` 而非 `./.yanshi.toml`。 |
| `--force` | 关 | 覆盖已存在的配置文件。 |

**拒绝覆盖**已存在的目标,除非传入 `--force`:它会把 `refusing to overwrite existing config: <path> (use --force)` 打印到 stderr 并以 `1` 退出。成功时写入模板并把解析后的路径打印到 stdout。

```bash
yanshi init                 # write ./.yanshi.toml
yanshi init --global        # write $YANSHI_HOME/config.toml
yanshi init --force         # overwrite an existing file
```

## config

把生效的分层配置及其 provenance 以 JSON 打印——内置默认 < 全局 `$YANSHI_HOME/config.toml` < 本地 `./.yanshi.toml`。便于回答"这个默认到底从哪来?"。

```text
yanshi config
```

该 JSON 有四个 key:`config`(完整解析后的文档)、`sources`(有贡献的文件,低→高)、`provenance`(每个顶层 section 映射到 `builtin` 或最后设置它的文件)以及 `enabled_adapters`(`[adapters].enabled` 列表,或 `null` 表示全部)。schema 与优先级规则见 [配置](../reference/configuration.md)。

```bash
yanshi config
```

## dispatch

通过监控内核运行一次阻塞式派发,并打印终态的 `RunResult`。

```text
yanshi dispatch [OPTIONS] [PROMPT]
```

| 选项 | 默认 | 说明 |
|---|---|---|
| `PROMPT` | `""` | 传给 agent CLI 的位置参数 prompt(经 stdin 发送)。 |
| `--cli` | `claude` | 适配器名:`claude`、`codex`、`cursor` 或 `gemini`。 |
| `--model` | — | 透传给适配器的 model id。 |
| `--effort` | — | 推理 effort:`low`、`medium`、`high` 或 `xhigh`。 |
| `--allow` | `read-only` | 权限模式:`read-only` 或 `yolo`。 |
| `--profile` | — | 选用的命名配置 profile(`[profiles.NAME]`);未知名字会 warn 并被忽略。 |
| `--workdir` | — | 子进程工作目录。 |
| `--timeout` | — | 墙钟超时秒数。 |
| `--wait` / `--no-wait` | `--wait` | CLI 派发是阻塞的;`--no-wait` 会被拒绝(后台运行请使用库)。 |

当结果为错误时以 `1` 退出,非法调用(例如 `--no-wait` 或非法的 `--effort`)时以 `2` 退出。

未设置的 flag 会从解析后的配置填充:先 `[defaults]`,再任何 `--profile` 预设,然后是你显式给的 flag,最后由 `[limits]` 夹取。解析期间的 warning——未知的 `--profile`,或被夹取的能力——会以 JSON `WarningRecord` 形式打印到 **stderr**,且绝不中断运行。参见 [配置](../reference/configuration.md)。

```bash
yanshi dispatch --cli claude --model sonnet --effort high "Inspect the failing tests"
```

## improve

运行一个有界的**派发 → 闸门 → 精修**循环,并打印 `ImproveResult`。完整模型见
[改进循环](improve-loop.md)。

```text
yanshi improve [OPTIONS] [PROMPT]
```

| 选项 | 默认 | 说明 |
|---|---|---|
| `PROMPT` | `""` | 要迭代的任务 prompt。 |
| `--cli` | `claude` | 适配器名。 |
| `--model` | — | 透传的 model id。 |
| `--effort` | — | 推理 effort:`low`、`medium`、`high`、`xhigh`。 |
| `--allow` | `read-only` | 权限模式。 |
| `--profile` | — | 选用的命名配置 profile(`[profiles.NAME]`);未知名字会 warn 并被忽略。 |
| `--workdir` | — | 子进程工作目录。 |
| `--timeout` | — | 每次派发的墙钟超时秒数。 |
| `--check` | — | 确定性闸门命令(退出 `0` = 通过)。用 `shlex` 解析,仅以 argv 方式运行。 |
| `--max-iterations` | `3` | 派发 → 闸门 → 精修 循环的最大次数(须 ≥ 1)。 |
| `--gate-timeout` | `300` | 闸门命令超时秒数。 |
| `--critic` / `--no-critic` | `--no-critic` | 启用建议性的 LLM critic。 |

当循环未成功时以 `1` 退出,当 `--max-iterations` 小于 1 时以 `2` 退出。

`--profile` 与由配置驱动的默认值/天花板解析,与 [`dispatch`](#dispatch) 完全一致;夹取与未知 profile 的 warning 都走 **stderr**。

```bash
yanshi improve --cli claude "fix failing tests" --check "uv run pytest -q" --max-iterations 3
```

## list

确定性地列出已知的 agent id。

```text
yanshi list
```

```bash
yanshi list
```

## status

从磁盘读取一个确定性的 `AgentStatus` 快照。

```text
yanshi status AGENT_ID
```

```bash
yanshi status ys-12345-1700000000000000000
```

## summary

从磁盘读取建议性的滚动摘要(当尚不存在滚动摘要时,回退到最后一个事件的摘要)。

```text
yanshi summary AGENT_ID
```

```bash
yanshi summary ys-12345-1700000000000000000
```

## wait

轮询磁盘状态,直到 agent 到达终态或超时,然后打印 `AgentStatus`。

```text
yanshi wait AGENT_ID [--timeout SECONDS]
```

| 选项 | 默认 | 说明 |
|---|---|---|
| `--timeout` | — | 等待的最大秒数(省略则无限等待)。 |

```bash
yanshi wait ys-12345-1700000000000000000 --timeout 300
```

## cancel

取消一次运行:向记录在案的子进程发出信号(若存在进程内任务则一并取消),然后把状态最终
确定为 `cancelled`。

```text
yanshi cancel AGENT_ID
```

```bash
yanshi cancel ys-12345-1700000000000000000
```

## gc

对超过阈值的终态运行目录进行垃圾回收,返回被移除的 agent id 列表。只有终态运行会被移除。

```text
yanshi gc [--older-than SECONDS]
```

| 选项 | 默认 | 说明 |
|---|---|---|
| `--older-than` | `86400` | 以秒计的年龄阈值(默认 1 天)。 |

```bash
yanshi gc --older-than 604800   # remove terminal runs older than 7 days
```

## record

一个用于适配器开发的维护辅助命令:运行一次 CLI,并把它保留的原始流复制到一个 fixture
文件中(用于构建离线解析器测试)。

```text
yanshi record [OPTIONS] [PROMPT]
```

| 选项 | 默认 | 说明 |
|---|---|---|
| `PROMPT` | `hello` | 要录制的 prompt。 |
| `--cli` | `claude` | 适配器名。 |
| `--output` | `tests/fixtures/recorded.ndjson` | 目标 fixture 路径。 |

```bash
yanshi record --cli claude "hello" --output tests/fixtures/claude_hello.ndjson
```

## 另见

- [改进循环](improve-loop.md)——深入讲解迭代循环。
- [适配器](../adapters/index.md)——`--cli`、`--model`、`--effort` 和 `--allow` 如何映射到厂商参数。
- [配置](../reference/configuration.md)——`gc`、`status` 和 `wait` 在磁盘上读取什么。
