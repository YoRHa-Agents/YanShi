# 配置

YanShi 把所有运行状态都保存在磁盘上的单一根目录下,并且只读取一小组明确的环境变量。没有需要管理的配置文件:行为由派发时的 `RunSpec`/策略以及磁盘上的 `$YANSHI_HOME` 驱动。

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
