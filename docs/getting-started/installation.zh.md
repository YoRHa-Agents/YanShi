# 安装

YanShi 交付单个 CLI `yanshi`,外加一个可导入的 Python 库和一个可选的 MCP 垫片。
随附的 `install.sh` 是推荐的入口:它**以 uv 优先,并带有 pip + venv 回退**,默认采用只读、
无意外的配置,并且绝不会仅仅因为缺少某个厂商 CLI 就让安装失败。

## 前置条件

- **Python 3.12+**——安装器会探测 `python3`/`python`,并拒绝任何更旧的版本。
- **[uv](https://docs.astral.sh/uv/)**(推荐)——本地和全局安装都会用到它。没有它时,安装器会
  回退到 `pip`/`venv`(本地)或 `pipx`/`pip --user`(全局)。
- 四个厂商 CLI(`claude`、`codex`、`cursor-agent`、`gemini`)是**可选的**,YanShi 只会
  *检测*它们,从不安装。见 [厂商 CLI 只被检测,不被安装](#vendor-clis-are-detected-not-installed)。

## 用 `install.sh` 快速安装

一行命令(通过随附安装器进行全局安装):

```bash
curl -fsSL https://raw.githubusercontent.com/YoRHa-Agents/YanShi/main/install.sh | bash -s -- --global
```

从检出的仓库中,直接运行它:

```bash
./install.sh --local --dev
```

### 安装器选项

| 标志 | 作用 |
|---|---|
| `--local` | 以可编辑模式安装到项目的 `.venv`(未指定作用域时的默认值)。必须从检出的仓库中运行。 |
| `--global` | 通过 `uv tool install` 进行全局工具安装(回退到 `pipx`,再到 `pip install --user`)。 |
| `--with-mcp` | 同时打印 MCP 接线说明,并验证 `skill/mcp_server.py` 能否导入。 |
| `--dev` | 包含 `dev` 依赖组(pytest、ruff、mypy)。 |
| `--docs` | 包含 `docs` 依赖组(MkDocs Material + i18n)。 |
| `--dry-run` | 打印每一步操作而不改动系统。 |
| `--lang zh\|en` | 强制设定安装器的消息语言(否则从 `$LANG` 推断)。 |
| `--help` | 显示用法并退出。 |

!!! note "本地作用域 vs. 全局作用域"
    `--local` 会把项目以**可编辑**模式安装到 `<checkout>/.venv`,最适合开发。`--dev`/`--docs`
    组仅适用于本地安装;它们是开发专用的,对 `--global` 工具安装会被忽略。

## `uv` 路径

如果你有一份检出的仓库,并想要标准的开发环境:

```bash
uv sync --group dev      # core + dev tools (pytest, ruff, mypy)
uv run yanshi doctor     # verify which vendor CLIs are available
```

当你打算构建站点时,加入文档工具链:

```bash
uv sync --group docs
```

## `pip` 回退

当 `uv` 不可用时,一个普通的虚拟环境同样可行:

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev,docs]"   # extras are optional
yanshi doctor
```

## 厂商 CLI 只被检测,不被安装

YanShi 会把任务派发给厂商 CLI,但**不会**捆绑或安装它们。安装 YanShi 后,运行
[`yanshi doctor`](../cli/reference.md#doctor) 查看哪些适配器拥有可用的可执行文件与鉴权:

```bash
yanshi doctor
```

`doctor` 为每个适配器打印一行 JSON(`cli`、`status`、`executable`、`version`、`errors`、
`warnings`),并在任一适配器 preflight 失败时以非零退出。这只是信息性的:例如,缺少
`gemini` 并不妨碍你派发给 `claude`。请按各厂商自己的说明,安装并鉴权你打算使用的每个厂商 CLI。

## `$YANSHI_HOME`

YanShi 把所有运行状态保存在 `$YANSHI_HOME` 下,其默认值为 `~/.yanshi`。覆盖它即可迁移
按 agent 划分的运行记录、原始流与缓存:

```bash
export YANSHI_HOME="$HOME/.local/state/yanshi"
```

完整的磁盘布局见 [配置](../reference/configuration.md)。

## 下一步

前往 [快速开始](quickstart.md),派发你的第一个子智能体,并学习低上下文轮询规则。
