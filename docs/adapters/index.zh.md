# 适配器

**适配器**是 YanShi 中唯一与厂商相关的代码。它把一个与厂商无关的 `RunSpec` 翻译成某个 CLI 的 argv,把该 CLI 的事件流归一化为 `YanShiEvent`,并解析出一个终态的 `RunResult`。新增一个 CLI 意味着编写一个适配器;内核中的其它部分都不需要改动。

YanShi 内置四个适配器:`claude`、`codex`、`cursor` 和 `gemini`。

## 各 CLI 的映射

下表基于已内置的适配器。除 Cursor 把 prompt 作为最后一个参数外,其余 prompt 都通过 stdin 传递。

| 维度 | `claude` | `codex` | `cursor` | `gemini` |
|---|---|---|---|---|
| 可执行文件 | `claude` | `codex` | `cursor-agent` → `agent` | `gemini` |
| Prompt 模式 | stdin | stdin | 参数 | stdin |
| 无头基础命令 | `claude -p …` | `codex … exec … -` | `cursor-agent -p --trust …` | `gemini -p …` |
| 结构化输出标志 | `--output-format stream-json --verbose` | `--json` | `--output-format stream-json` | `--output-format stream-json` |
| 模型标志 | `--model` | `--model` | `--model`(已并入 effort) | `--model` |
| Effort 转换 | `--effort <level>`(标志) | `-c model_reasoning_effort="<level>"`(配置) | 并入模型名,例如 `gpt-5.5-high`(模型后缀) | `--model-thinking-level <level>` |
| 只读权限 | `--allowedTools Read,Grep,Glob,LS,WebFetch,WebSearch` | `--sandbox read-only --ask-for-approval never --search` | `--mode plan` | `--approval-mode plan` |
| Yolo 权限 | `--dangerously-skip-permissions` | `--dangerously-bypass-approvals-and-sandbox` | `--force` | `--approval-mode yolo` |
| 会话恢复 | `--resume <id>`(通过 `--session-id` 指定新 id) | `exec resume <id>` | `--resume <id>` | `--resume <id>`(通过 `--session-id` 指定新 id) |
| 终态事件 | `result`(`is_error`) | `turn.completed` / `turn.failed` | `result`(`is_error`) | `result`(外加 exit `1` / `42` / `53`) |
| 事件词汇表 | system / assistant / user / result / stream_event | thread.* / turn.* / item.* | system / assistant / tool_call / result | init / message / tool_use / result |

!!! note "Codex 的沙箱标志位于 `exec` 之前"
    Codex 的权限标志会在 `exec` 子命令*之前*发出,例如
    `codex --sandbox read-only --ask-for-approval never --search exec --json --skip-git-repo-check -`。

## 声明的能力

每个适配器都声明自己的能力(通过每个适配器各自的 TOML 文件数据驱动)。派发策略在 spawn **之前**读取这些能力,用以校验 `RunSpec` 并为该 CLI 无法表达的任何东西发出降级警告。

| 能力 | `claude` | `codex` | `cursor` | `gemini` |
|---|---|---|---|---|
| `effort` 模式 | `flag` | `config` | `model_suffix` | `thinking_level` |
| `context_window_flag` | `false` | `false` | `false` | `false` |
| `session_resume` | `true` | `true` | `true` | `true` |
| `preassign_session_id` | `true` | `false` | `false` | `true` |
| `output_schema` | `true` | `true` | `false` | `true` |
| `stream_json` | `true` | `true` | `true` | `true` |
| `permission_modes` | read-only, yolo | read-only, yolo | read-only, yolo | read-only, yolo |

!!! note "声明的能力 vs. 实际接线的标志"
    能力描述的是某个 CLI *能够*表达什么,并驱动预检校验。实际的标志由适配器的命令构建器发出——例如,当提供了
    `output_schema` 时,`claude` 会接线 `--json-schema`。没有任何 CLI 暴露上下文窗口控制,因此任何依赖它的请求
    都总会产生一个结构化警告。

## `cursor-agent` → `agent` 回退

Cursor 的安装器会同时放置 `cursor-agent` 和一个简短的 `agent` 别名(两个指向同一二进制的软链接)。Cursor 适配器**必须**按 `cursor-agent`、然后 `agent` 的顺序解析可执行文件,用其中存在的那个,并且**绝不能**硬编码单一名称——否则在只安装了二者之一的机器上,预检会错误地报告 Cursor 缺失。

## Effort 与显式用户模型

有些 CLI(Cursor,以及任何把推理 effort 折叠进模型名的 CLI)无法用一个独立的标志来表达 effort。当调用方**同时**提供了显式的 `model` 和 `reasoning_effort` 时:

- **显式 `model` 胜出。** YanShi 绝不能改写用户指定的模型。
- 无法表达的那个 effort 会被记录为一个结构化警告(`cursor_effort_model_conflict`)。

只有当调用方**没有**提供模型时,适配器才可以根据 effort 合成一个带后缀的模型名(对 Cursor 而言,基础名默认为 `gpt-5.5`,且后缀只应用于 `gpt-` 系列模型)。

## 成功判定是分层的

由于各厂商对如何报告失败意见不一,成功是分层判定的:

1. **进程退出码**(Gemini 最丰富:`0` 正常、`1` 通用、`42` 鉴权、`53` 服务端)。
2. **终态事件**的标志(`result.is_error`、`turn.failed`)。
3. 把剩余文本进行**错误字符串分类**,归入某个治理类别(`rate_limit`、`auth`、`billing`、`server_error`、……)。

## 延伸阅读

- [安全与策略](../concepts/safety.md) —— 每个 CLI 的 `read-only` 与 `yolo` 各自注入了什么。
- [监控](../concepts/monitoring.md) —— 归一化事件如何驱动 FSM。
- [贡献指南](../contributing.md) —— 编写并测试一个新适配器。
