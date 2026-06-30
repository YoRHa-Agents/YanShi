# 故障排查

大多数问题都落入少数几类:某个尚未就绪的适配器、某个需要分类的厂商错误,或者某次看似卡住的运行。YanShi 对这三者都是确定性的,因此 status 对象通常会准确地告诉你发生了什么。

## 从 `yanshi doctor` 开始

`doctor` 会运行每个适配器的预检,并为每个 CLI 打印一行 JSON:

```bash
yanshi doctor
```

```text
{"cli": "claude", "status": "ok", "executable": "/usr/local/bin/claude", "version": "…", "errors": [], "warnings": []}
{"cli": "codex", "status": "failed", "executable": null, "version": null, "errors": ["missing CLI executable: codex"], "warnings": []}
```

如果任何适配器失败,它会以非零退出码退出。这里的失败只是信息性的——其它适配器仍然可用。

## 预检失败

预检在任何子进程被 spawn **之前**运行,并快速失败,因此一个配置有误的 CLI 绝不会产生一次"半截"的运行。

- **缺少二进制** —— `missing CLI executable: <cli>`。该可执行文件不在 `PATH` 上。安装厂商 CLI(YanShi 不会安装它)并重新运行 `doctor`。记住 Cursor 会先解析 `cursor-agent`、然后是 `agent` 别名——安装其中任一即可。
- **鉴权** —— 例如 `claude authentication seed not found`。通过 `CLAUDE_CODE_OAUTH_TOKEN` / `ANTHROPIC_API_KEY`,或一个包含 `.credentials.json` / `auth.json` 的 `CLAUDE_CONFIG_DIR`(或 `~/.claude*`)来提供凭据。鉴权失败被归类为 `auth`。
- **未检测到版本** —— 一个非致命警告(`could not detect version for <cli>`);派发仍会继续。

安装细节见 [安装](getting-started/installation.md)。

## 错误类别

当一次运行失败时,`error_category`(以及每个 `errors[].category`)会把厂商错误文本归类到一个治理类别:

| 类别 | 典型触发词 | 可重试? |
|---|---|---|
| `rate_limit` | `rate limit`、`429`、`too many requests` | 是 |
| `overloaded` | `overloaded`、`capacity`、`busy` | 是 |
| `server_error` | `server error`、`5xx`、`500`–`504` | 是 |
| `auth` | `unauthorized`、`not logged in`、`login` | 否 |
| `billing` | `billing`、`quota`、`payment`、`credit` | 否 |
| `invalid_request` | `invalid request`、`bad request`、`schema` | 否 |
| `max_output_tokens` | `max output`、`output tokens` | 否 |
| `unknown` | 任何未分类的内容(原始消息保留) | 否 |

监督器只重试可重试的类别,并采用有界的指数退避;不可重试的类别快速失败。原始消息总是与类别一并保留——分类绝不丢弃信息。

## `stalled` vs. `waiting_*`

一次不产生输出的运行未必就是卡住了。监督器区分三种情形:

- **`waiting_rate_limit`** —— 子进程因速率限制被有意暂停。监督器会**等待**;它不会杀掉它。
- **`waiting_tool`** —— 一个工具调用正在进行。监督器最多等待一个长工具超时(≈900s),之后才将其视为卡住。
- **`stalled`** —— 超过停滞超时(≈300s)仍无输出,于是监督器进行中断;或者该运行因其监控宿主死亡而被修正为 `stalled`(见下文)。`wall_timeout`(≈1800s)也会触发终止。

如果你看到过早的 `stalled`,很可能是子进程在停滞窗口内没有产生任何可解析的事件;请在 `RunSpec` 上调高相关超时,或检查 `stream.ndjson` 以查看那段静默期。

## 陈旧的 running 被修正为 stalled { #stale-running-corrected-to-stalled }

每次运行都记录一个 `owner_pid`(监控宿主)和一个 `child_pid`。如果某个读取器观察到一个非终态的 `running` 状态,但 `owner_pid` 已经**不再存活**,它会确定性地把状态改写为 `stalled`,并追加一个致命错误,说明 owner pid 已经消失。这正是一个孤儿子进程(在其监控宿主崩溃时被遗留下来)如何被诚实地暴露出来、而不是看起来永远在运行的方式。没有单独的心跳线程需要信任——存活性是在读取时从 owner pid 推导出来的。

## 当定价缺失时成本守卫会降级 { #cost-guard-degrades-when-pricing-is-missing }

只有当成本已知时(`pricing_status` 为 `native` 或 `priced`),每次运行的花费上限才能以美元强制执行。当 `pricing_status` 为 `missing` 时,YanShi **不会**假装美元上限仍在生效。相反,它会降级为一个 **token 上限**,并记录一个警告以明确这种降级。如果你依赖一个硬性的美元上限,请确保该模型被原生成本报告覆盖,或被 `pricing-cache.json` 中的某个条目覆盖;否则请设置一个与你的风险承受度相匹配的 token 上限。参见 [安全与策略](concepts/safety.md) 和 [配置](reference/configuration.md)。

## 该去哪里查看

- [监控](concepts/monitoring.md) —— 每个 status 字段的含义。
- [适配器](adapters/index.md) —— 各 CLI 的退出码与终态事件。
- [配置](reference/configuration.md) —— `stream.ndjson` 与运行记录存放在何处。
