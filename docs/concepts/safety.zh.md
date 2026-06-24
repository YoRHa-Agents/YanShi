# 安全与策略

YanShi 不提供工作树或容器隔离。相反,它的安全模型立足于三根支柱:**显式的权限策略**、
**忠实且受强约束的执行**,以及**没有静默失败**。策略由调用方提供(通过 skill 层或
`RunSpec`),并在每次派发之前和期间被强制执行。

## 权限模式:`read-only` vs. `yolo`

权限模型刻意设计为二元的:

- **`read-only`(默认)**——适配器会注入各厂商的最小权限参数(例如 Claude 的
  `--allowedTools Read,Grep,Glob,LS,WebFetch,WebSearch`、Codex 的 `--sandbox read-only`、
  Cursor 的 `--mode plan`、Gemini 的 `--approval-mode plan`)。
- **`yolo`(仅显式)**——只有在此时,危险的厂商参数才会被注入
  (`--dangerously-skip-permissions`、`--dangerously-bypass-approvals-and-sandbox`、
  `--force`、`--approval-mode yolo`)。

策略校验会在 spawn 之前强制执行合理的不变量:

- 若 `RunSpec` 的 `allow` 模式不在适配器声明的 `permission_modes` 中,它会被拒绝。
- 一次 `read-only` 派发**不可**请求可写的 `add_dirs`——该组合会被拒绝。

!!! danger "`yolo` 会移除厂商的安全护栏"
    `yolo` 绝不会被隐式启用。请仅在可信赖的工作中显式请求它;它会绕过厂商的审批与沙箱保护。
    每个 CLI 的确切参数列在 [适配器](../adapters/index.md) 中。

## 可写边界与过滤后的环境

调用方控制工作目录(`workdir`)以及任何额外的可写目录(`add_dirs`);这些会被解析和校验
(它们必须存在,并且可能被约束在一个可信根目录之内)。子进程以**过滤后的环境**被 spawn
出来——只有一份变量白名单(例如 `PATH`、`HOME`、`USER`、区域设置变量)加上任何显式的
`RunSpec.env` 覆盖会被透传,而不是泄漏上层的整个环境。

## 仅 argv 方式 spawn,绝不 `shell=True`

每个子进程都以 `shell=False`(或异步的 `create_subprocess_exec` 等价物)从一个 **argv 列表**
被 spawn 出来。prompt 通过 stdin 或作为单个 argv 值传递——它**绝不会**被插值进 shell 命令行。
同样的规则适用于改进循环的闸门命令,它用 `shlex` 解析并仅以 argv 方式执行。

!!! note "为什么这很重要"
    Shell 插值是 agent prompt 的经典注入途径。禁止 `shell=True` 从结构上消除了它:一个包含
    `$(...)`、反引号或 `;` 的 prompt,对子进程而言只是文本。

## 花费上限与缺失定价的回退

supervisor 强制执行**每次运行的花费上限**(以及舰队层面的全局上限)。当一次运行累计的
`cost_usd` 超过上限时,supervisor 会逐步升级终止 `SIGINT → SIGTERM → SIGKILL`——这是防止
失控循环悄悄烧掉预算的护栏。

只有在已知定价时,花费才能以美元强制执行。`UsageMeter` 按以下顺序解析花费:

1. **native**——CLI 报告它自己的花费。
2. **priced**——一个缓存的/内置的定价表匹配到该模型。
3. **missing**——两者都不可用;`cost_usd` 为 `null`。

!!! warning "当定价为 `missing` 时降级花费护栏"
    当 `pricing_status == missing` 时,美元上限无法被可靠地强制执行。YanShi 会**降级**为基于
    token 的上限,并在状态上记录一条警告,使这种降级显式化。它**绝不能**假装美元上限仍在
    生效。见 [故障排查](../troubleshooting.md#cost-guard-degrades-when-pricing-is-missing)。

## 在落盘与 summarizer 之前进行密钥脱敏

常见的密钥形态——`api_key`/`token`/`password`/`secret` 赋值、`Bearer` token,以及 `sk-…`
密钥——会在原始行被写入 `stream.ndjson` **之前**,以及在任何文本被交给 summarizer **之前**,
被脱敏为 `[REDACTED]`。密钥被同时挡在磁盘上的可见性平面和上层读取的上下文平面之外。

## 能力不匹配会被暴露,而非伪装

如果 `RunSpec` 请求了某个适配器无法表达的东西——在没有 effort 控制的 CLI 上请求
`reasoning_effort`、在没有 `output_schema` 的 CLI 上请求它,或任何上下文窗口控制(没有任何
CLI 暴露它)——YanShi 会记录一个结构化的 `WarningRecord` 并降级。它绝不会静默地假装某个
不受支持的控制生效了。

## 没有静默失败

依据项目的治理准则,错误**始终**会被暴露:

- spawn/preflight 失败会在任何子进程运行之前抛出已分类的错误。
- 运行时错误会被追加到 `AgentStatus.errors`,带有类别和原始消息。
- 在改进循环中,闸门/critic/派发的失败会出现在 `GateOutcome.error`、
  `ImproveResult.warnings` 或终态的 `fatal_error` 中——绝不会被吞掉。

## 相关阅读

- [适配器](../adapters/index.md)——每个 CLI 的确切权限参数。
- [监控](monitoring.md)——错误和警告如何到达状态对象。
- [配置](../reference/configuration.md)——`$YANSHI_HOME`、保留策略与定价缓存。
