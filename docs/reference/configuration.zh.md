# 配置

YanShi 把所有运行状态都保存在磁盘上的单一根目录下,并且只读取一小组明确的环境变量。运行时行为由派发时的 `RunSpec`/策略以及磁盘上的 `$YANSHI_HOME` 驱动;此外,一个**可选的**仓库级 `.yanshi.toml`(见下文)声明某个工作区启用哪些适配器、摘要器如何运行,以及每次派发的默认值/天花板范围。它只塑形**派发之前**所解析出的内容,绝不改变 `RunSpec` 契约本身,也不改变任何运行时不变量。

## 仓库级配置(`.yanshi.toml`)

工作区可以附带一个可选的 TOML 配置,使同一台机器上的不同仓库获得不同的能力:启用哪些适配器、摘要器如何运行,以及每次派发的默认值/天花板范围。若未找到任何配置,则套用内置默认值,YanShi 的行为与完全没有该文件时完全一致。

### 发现(discovery)

两个配置层共享同一套**完全相同**的 schema:

- **本地(工作区)**——`.yanshi.toml`,从当前工作目录沿父目录**向上** walk 至文件系统根,取**首个**遇到的 `.yanshi.toml`(与 Git 发现 `.git` 的方式相同)。最近的文件胜出;若一个都没找到,则没有 local 层。
- **全局**——`$YANSHI_HOME/config.toml`(默认 `~/.yanshi/config.toml`,与下文的磁盘状态根同位)。

两份文件都是 TOML,且每个 section 都禁止未知 key。格式错误的文件——语法错误、类型不符或出现未知 key——会抛出明确的错误并指明出错的路径;它**绝不会被静默忽略**。

### 优先级(precedence)

有效配置由各层**逐 section deep-merge** 合成,低→高:

```text
built-in defaults
  └< global   $YANSHI_HOME/config.toml
       └< local   ./.yanshi.toml         (nearest walk-up hit)
            └< --profile <name>          (a [profiles.<name>] preset)
                 └< per-call flags        (CLI options / RunSpec fields)   ← highest
```

在一个 section 内,高层逐 key 覆盖低层,`[profiles.*]` 按名字合并。文件各层(内置/全局/本地)合并整个 section;`--profile` 与每次调用的 flag 随后在派发时解析出 `[defaults]` 的各个字段。`[limits]` **最后**生效——在上述一切解析完成后,每个被请求的值都会被夹取到其上限(见下方 `[limits]` 夹取规则)。

### `.yanshi.toml` 示例

`yanshi init` 会原样写入下面这份带注释的起始模板。每个 key 都是可选的;省略某个 key 即保留其内置默认值。

```toml
# YanShi repository configuration (.yanshi.toml)
# Every value below is optional; omitted keys fall back to builtin defaults.

[adapters]
# Restrict which agent CLIs YanShi may dispatch to. Remove this key (or the
# whole section) to leave every installed adapter enabled. Names are validated
# against the real adapter registry at dispatch time.
enabled = ["claude", "codex", "cursor", "gemini"]

[summarizer]
# Advisory rolling summaries are OFF by default and never alter status fields.
enabled = false
# CLI used to produce summaries when enabled.
cli = "claude"
# Model for the summarizer CLI; omit to use the CLI's own default.
model = "claude-3-5-haiku-latest"
# Minimum seconds between summary refreshes (debounce).
debounce_s = 5.0
# Minimum number of new significant events before re-summarizing.
min_new_events = 2
# Hard cap on summary length, in tokens.
max_tokens = 150
# Total watcher token budget before falling back to deterministic text.
watcher_token_ceiling = 1000
# Per-summary CLI timeout, in seconds.
timeout_s = 60

[defaults]
# Default reasoning effort for every dispatch: low | medium | high | xhigh.
effort = "medium"
# Default permission model for every dispatch: read-only | yolo.
allow = "read-only"
# Default overall timeout per dispatch, in seconds.
timeout_s = 1800
# Default stall (no-progress) timeout per dispatch, in seconds.
stall_timeout_s = 300
# Optionally pin a default CLI / model / cost ceiling for every dispatch.
# cli = "claude"
# model = "claude-3-7-sonnet-latest"
# cost_ceiling_usd = 5.0

[limits]
# Hard caps enforced on every dispatch regardless of profile or per-call
# overrides. Uncomment to activate; requests above a cap are clamped + warned.
# max_allow = "read-only"
# max_cost_usd = 10.0
# max_timeout_s = 3600

[profiles.cheap]
# A fast, low-cost profile: minimal effort and tight budgets.
effort = "low"
cost_ceiling_usd = 0.5
timeout_s = 600

[profiles.thorough]
# A high-effort profile for hard, long-running tasks.
effort = "high"
timeout_s = 3600
stall_timeout_s = 600
```

### 各 section 的作用

| Section | 作用 |
|---|---|
| `[adapters]` | `enabled` 是 `claude`、`codex`、`cursor`、`gemini` 中允许被派发的子集——也是 `doctor` 唯一会检查的适配器。省略该 key(或整个 section)即让所有已安装的适配器都启用。请求一个被禁用或未知的适配器会 fail-fast。 |
| `[summarizer]` | 建议性滚动摘要器的设置,它作为一次轻量的 one-shot agent-CLI 调用运行。默认关闭(`enabled = false`),此时摘要保持确定性兜底。参见 [监控](../concepts/monitoring.md)。 |
| `[defaults]` | 套用到每次调用的最低优先级派发值:`cli`、`model`、`effort`(映射到 `RunSpec.reasoning_effort`)、`allow`、`timeout_s`、`stall_timeout_s` 和 `cost_ceiling_usd`。 |
| `[profiles.<name>]` | 一个命名预设,形状与 `[defaults]` 完全相同,通过每次调用的 `--profile <name>` 选用。 |
| `[limits]` | 无论 profile 或每次调用的覆盖如何,都会夹取到每次派发上的硬上限:`max_allow`、`max_cost_usd` 和 `max_timeout_s`。 |

**摘要器字段:** `enabled`(默认 `false`);`cli` 与 `model`(用于写摘要的 agent CLI——`cli` 必须在 `[adapters].enabled` 内);`debounce_s` 与 `min_new_events`(一次刷新可被触发的频率);`max_tokens`(摘要长度上限);`watcher_token_ceiling`(摘要器的累计 token 预算);以及 `timeout_s`(单次摘要的墙钟超时)。任何错误或预算耗尽都会降级为确定性兜底,且不阻塞被监控的 run。

### `[limits]` 如何夹取(必 warn)

`[limits]` 是最后一道闸门,在 `[defaults]`、选中的 profile 和每次调用的 flag 解析之后生效:

- `max_allow` 夹取 `allow`(排序为 `read-only` < `yolo`):在 `max_allow = "read-only"` 下,一个 `yolo` 请求会被夹回 `read-only`。
- `max_cost_usd` 夹取 `cost_ceiling_usd`。
- `max_timeout_s` 夹取 `timeout_s`。

每当一个值**确实**被夹取时,YanShi 会追加一条结构化的 `capability_clamped` `WarningRecord`(含 `code`、`message` 和 `detail`)——夹取绝不静默(No Silent Failures)。CLI 会把这些 warning 以 JSON 形式打印到 **stderr**。若设置了 `max_cost_usd`/`max_timeout_s` 上限而对应的值未设置,则该上限会被直接采用为生效值(没有任何东西被降级,因此不产生 warning)。

### 查看解析后的配置(`yanshi config`)

`yanshi config` 把合并后的配置以 JSON 打印,并附带 provenance,便于你追溯每个 section 来自哪一层:

```json
{
  "config": { "...": "the fully resolved document" },
  "sources": ["/home/you/.yanshi/config.toml", "/path/to/repo/.yanshi.toml"],
  "provenance": {
    "adapters": "builtin",
    "summarizer": "/path/to/repo/.yanshi.toml",
    "defaults": "/home/you/.yanshi/config.toml",
    "limits": "builtin",
    "profiles": "/path/to/repo/.yanshi.toml"
  },
  "enabled_adapters": ["claude", "codex", "cursor", "gemini"]
}
```

- `sources` 按优先级顺序(低→高)列出有贡献的文件。
- `provenance` 把每个顶层 section 映射到最后设置它的那一层:`builtin`,或全局/本地文件的路径。
- `enabled_adapters` 回显 `[adapters].enabled`(`null` 表示所有适配器都启用)。

参见 CLI 参考中的 [`yanshi init`](../cli/reference.md#init) 与 [`yanshi config`](../cli/reference.md#config)。

## `$YANSHI_HOME`

所有持久化状态都位于 `$YANSHI_HOME` 下,其默认值为 `~/.yanshi`。设置它即可重新指定运行记录、原始流和缓存的存放位置:

```bash
export YANSHI_HOME="$HOME/.local/state/yanshi"
```

## 磁盘布局

```text
$YANSHI_HOME/                     # default ~/.yanshi
├── agents/
│   └── <agent_id>/
│       ├── run.json              # run record + AgentStatus snapshot (atomic write, mode 0600)
│       ├── run.lock              # file lock guarding the atomic write
│       ├── stream.ndjson         # raw event stream (ring-buffered, secret-redacted)
│       └── result.json           # terminal RunResult
├── sessions.json                 # alias -> native session id map
└── pricing-cache.json            # cached model pricing
```

- **`run.json`** 保存实时运行记录和确定性的 `AgentStatus` 快照,随运行进展镜像到磁盘。写入是**原子的**(临时文件 + 重命名,由文件锁守护),并以 `0600` 模式创建。
- **`run.lock`** 是按记录的锁,用于串行化那些原子写入。
- **`stream.ndjson`** 是原始事件流(可见性平面)。它通过一个有界环形缓冲区(默认 8 MiB)写入,并在落盘前进行密钥脱敏。当窗口被超出时,最旧的字节会被丢弃——这种截断是**被计数的,而非静默的**。
- **`result.json`** 是终态的 `RunResult`,在运行结束后写入一次。
- **`sessions.json`** 把友好的别名映射到原生 CLI 会话 id(用于恢复)。
- **`pricing-cache.json`** 缓存成本计量器所用的模型定价。

!!! note "读取器是纯磁盘读取"
    `status`、`summary`、`wait`、`list` 和 `fleet_status` 只读取这棵目录树——没有子进程交互,也没有 LLM 调用。参见 [架构](../concepts/architecture.md)。

## 环境变量

| 变量 | 使用者 | 用途 |
|---|---|---|
| `YANSHI_HOME` | 存储 | 所有运行状态的根目录(默认 `~/.yanshi`)。 |
| `YANSHI_LIVE` | 测试 | 门控会 spawn 真实 CLI 的 live 测试(见 [贡献指南](../contributing.md))。 |
| `CLAUDE_CODE_OAUTH_TOKEN` / `ANTHROPIC_API_KEY` | Claude 预检 | 二者任一即可满足 Claude 的鉴权检查。 |
| `CLAUDE_CONFIG_DIR` | Claude 预检 | 用于检查 `.credentials.json` / `auth.json` 的目录。 |
| `LANG` | `install.sh` | 当省略 `--lang` 时推断安装器的消息语言。 |

### 子进程环境被过滤

子 CLI **不会**拿到上层进程的完整环境。只有一个允许列表会被透传——`PATH`、`HOME`、`USER`、`USERPROFILE`、`TMPDIR`、`TEMP`、`TMP`、`LANG`、`LC_ALL`,以及对应的 Windows 等价项——再加上调用方提供的任何显式 `RunSpec.env` 覆盖项。这能把杂散的凭据和配置挡在被派发的进程之外。

## 定价与成本溯源

成本计量器按以下顺序解析一次运行的 `cost_usd`:CLI 报告的**原生(native)**成本,否则来自模型定价表的**定价(priced)**估算,否则**缺失(missing)**(成本为 `null`)。该表把一个小巧的内置默认值与从 `pricing-cache.json` 加载的任何条目结合起来,其中每个条目把一个模型前缀映射到以美元计的 `[input_per_million, output_per_million]`。当定价为 `missing` 时,美元花费上限会降级为 token 上限——参见
[安全与策略](../concepts/safety.md) 和
[故障排查](../troubleshooting.md#cost-guard-degrades-when-pricing-is-missing)。

## 保留与垃圾回收

终态运行目录会一直保留,直到你回收它们。`yanshi gc` 会移除记录早于某阈值(默认一天)的终态运行,并返回被移除的 id:

```bash
yanshi gc --older-than 604800     # remove terminal runs older than 7 days
```

只有终态运行才符合条件;活跃运行绝不会被垃圾回收。参见
[CLI 参考](../cli/reference.md#gc)。
