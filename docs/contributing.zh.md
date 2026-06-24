# 贡献指南

YanShi 是一个小巧而严格的代码库:类型化契约、确定性监控,以及无静默失败。贡献需要保持测试、lint 和类型门槛全绿,并遵循项目的治理规范。

## 开发环境搭建

用 [uv](https://docs.astral.sh/uv/) 安装核心包以及 `dev` 依赖组:

```bash
uv sync --group dev
```

这会提供 `pytest`、`ruff` 和 `mypy`。`pip`/`venv` 方式也可以(见 [安装](getting-started/installation.md))。

## 质量门槛

在发起 pull request 之前运行这些;CI 运行的是同一套。

```bash
uv run pytest -m "not live" --cov     # tests with coverage (offline)
uv run ruff check .                   # lint
uv run mypy --strict src tests        # strict type checking
```

- **测试**使用 `pytest`(配合 `pytest-asyncio`)。覆盖率被配置为低于其阈值即失败,因此要在新增逻辑的同时添加测试。
- **Lint** 使用 `ruff`(行长 100;规则集 `E`、`F`、`I`、`UP`、`B`、`SIM`)。
- **类型**必须让 `src` 和 `tests` 都通过 `mypy --strict`。

!!! note "强制验证"
    绝不要跳过验证,也不要把它标记为 TODO 来绕过它。新增逻辑随测试一同交付,且三道门槛都必须通过。

## Live 测试由 `YANSHI_LIVE` 门控

会 spawn 真实厂商 CLI 的端到端测试被标记为 `live`,并且**默认被排除**(`-m "not live"`)。它们需要已鉴权的 CLI 和一次显式的选择加入:

```bash
YANSHI_LIVE=1 uv run pytest -m live
```

为离线解析器测试录制夹具(fixtures)是用维护命令 `yanshi record` 完成的(见 [CLI 参考](cli/reference.md#record))。

## 新增一个适配器

一个新 CLI 就是一个适配器加上它的能力元数据——内核不会改变:

1. 实现 `Adapter` 协议(`build_command`、`parse_event`、`parse_result`、
   `session_id_from_event`)。
2. 在适配器的 TOML 数据文件中声明能力,并在默认注册表中注册它。
3. 把所有厂商方言(标志、模型后缀、事件词汇表)都保留在那一个适配器内部。
4. 遵守安全不变量:仅 argv 的 spawn、默认 `read-only`、对不受支持的控制发出结构化警告,以及不吞掉任何错误。

每个适配器都必须提供的映射见 [适配器](adapters/index.md)。

## 文档工作流

文档站点是 MkDocs Material 配合 `mkdocs-static-i18n`。安装文档工具链,然后预览或构建:

```bash
uv sync --group docs
mkdocs serve                 # live preview at http://127.0.0.1:8000
mkdocs build --strict        # the build CI enforces: any warning fails
```

!!! warning "`--strict` 在任何警告时都会失败"
    断裂的内部链接、缺失的页面以及其它问题都会中止 `mkdocs build --strict`。请在页面之间使用相对链接,为代码围栏添加语言标签,并把 Mermaid 图写成带有效节点 id 的 ```mermaid 围栏块。

### 双语 `*.zh.md` 约定

站点采用 i18n 的**后缀(suffix)**布局实现双语。英文页面位于 `path/page.md`;其简体中文翻译就放在它旁边,命名为 `path/page.zh.md`。导航在 `mkdocs.yml` 中只定义一次(中文标签通过 `nav_translations` 提供),并由两种语言共享——不要为每种语言重复一份导航。当某个翻译缺失时,i18n 插件会静默地回退到英文页面。

## 治理提醒

- **无静默失败** —— 记录日志、重新抛出,或返回一个显式的错误/警告;绝不吞掉错误。
- **受保护分支** —— 绝不直接推送到受保护分支;从特性分支发起 merge/pull request。
- **真相之源** —— 设计存放在 `.local/memory/specs/yanshi/`;实现那些决策,而不是修改它们。
