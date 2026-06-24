#!/usr/bin/env bash
#
# YanShi installer — installs the `yanshi` CLI (and optionally wires the MCP server).
#
# Usage:
#   ./install.sh [--local|--global] [--with-mcp] [--dev] [--docs]
#                [--dry-run] [--lang zh|en] [--help]
#
# Design rules (honoring the repo's "No Silent Failures" workspace rule):
#   - uv-first, with a pip + venv fallback.
#   - read-only by default; the default scope is --local.
#   - Real failures print a clear message to stderr and exit non-zero.
#   - Missing vendor CLIs (claude/codex/cursor/gemini) are WARNINGS, never failures.
#   - `--dry-run` is 100% side-effect-free: no venv, no install, no network.
#
set -Eeuo pipefail

# --------------------------------------------------------------------------- #
# Configuration / defaults
# --------------------------------------------------------------------------- #
SCOPE=""                 # local | global  (default: local)
WITH_MCP=0
WITH_DEV=0
WITH_DOCS=0
DRY_RUN=0
LANG_CHOICE=""           # zh | en  (default: inferred from $LANG)
REPO_URL="https://github.com/YoRHa-Agents/YanShi.git"
MIN_PY_MINOR=12          # require Python 3.12+
PYTHON_BIN=""

# --------------------------------------------------------------------------- #
# No Silent Failures: surface any *unexpected* error (something we did not
# explicitly handle with `die`) instead of exiting quietly.
# --------------------------------------------------------------------------- #
_on_error() {
  local rc="$1" line="$2"
  if [ "${LANG_CHOICE:-en}" = "zh" ]; then
    printf '错误: 安装脚本在第 %s 行意外失败(退出码 %s)。\n' "${line}" "${rc}" >&2
  else
    printf 'ERROR: installer failed unexpectedly at line %s (exit code %s).\n' "${line}" "${rc}" >&2
  fi
  exit "${rc}"
}
trap '_on_error "$?" "${LINENO}"' ERR

# --------------------------------------------------------------------------- #
# Resolve the repo dir using bash builtins only (no external commands), so the
# prerequisite check still runs even with a stripped-down PATH.
# --------------------------------------------------------------------------- #
REPO_DIR=""
_src="${BASH_SOURCE[0]:-}"
if [ -n "${_src}" ] && [ -f "${_src}" ]; then
  _dir="${_src%/*}"
  if [ "${_dir}" = "${_src}" ]; then
    _dir="${PWD}"
  fi
  case "${_dir}" in
    /*) REPO_DIR="${_dir}" ;;
    *)  REPO_DIR="${PWD}/${_dir}" ;;
  esac
  # Only treat it as a checkout if it actually contains the project.
  if [ ! -f "${REPO_DIR}/pyproject.toml" ]; then
    REPO_DIR=""
  fi
fi

# --------------------------------------------------------------------------- #
# Bilingual messaging (EN + 简体中文). Every user-facing string is provided in
# both languages and routed through msg()/info()/warn()/die().
# --------------------------------------------------------------------------- #
detect_lang() {
  if [ -n "${LANG_CHOICE}" ]; then
    return 0
  fi
  case "${LANG:-}" in
    zh | zh_* | zh-* | *zh_CN* | *zh_TW* | *zh_HK*) LANG_CHOICE="zh" ;;
    *) LANG_CHOICE="en" ;;
  esac
}

# msg <en-text> <zh-text>  ->  prints the right one for the active language.
msg() {
  if [ "${LANG_CHOICE}" = "zh" ]; then
    printf '%s\n' "$2"
  else
    printf '%s\n' "$1"
  fi
}

info() { msg "$1" "$2"; }
warn() { msg "WARN: $1" "警告: $2" >&2; }
die()  { msg "ERROR: $1" "错误: $2" >&2; exit "${3:-1}"; }

usage() {
  # The brand line and a canonical "Usage:" line are emitted in both languages
  # so tooling can grep for them regardless of locale.
  printf '%s\n\n' "YanShi installer (install.sh)"
  printf '%s\n\n' "Usage: ./install.sh [OPTIONS]"
  if [ "${LANG_CHOICE}" = "zh" ]; then
    cat <<'EOF'
将 `yanshi` 命令行安装到本地(可编辑模式)或全局(加入 PATH)。
若未指定 --local 或 --global,则默认使用 --local。

选项 (Options):
  --local        在项目 .venv 中进行可编辑/开发安装(默认)。
  --global       将 `yanshi` 安装到 PATH(uv tool / pipx / pip --user)。
  --with-mcp     校验 yanshi.dispatch 导入与 skill/mcp_server.py,并打印
                 将 skill.mcp_server 接入 MCP host 的指引(与具体 host 无关)。
  --dev          安装开发依赖(配合 --local)。
  --docs         安装文档依赖。
  --dry-run      仅打印将要执行的操作,然后退出且不做任何更改。
  --lang zh|en   消息语言(默认:根据 $LANG 自动检测)。
  -h, --help     显示本帮助并退出。

示例 (Examples):
  ./install.sh --local --dev
  ./install.sh --global --with-mcp
  ./install.sh --dry-run --local
EOF
  else
    cat <<'EOF'
Install the `yanshi` CLI locally (editable) or globally (on PATH).
If neither --local nor --global is given, --local is the default.

Options:
  --local        Editable/dev install into the project .venv (default).
  --global       Install `yanshi` onto your PATH (uv tool / pipx / pip --user).
  --with-mcp     Verify the yanshi.dispatch import and skill/mcp_server.py, then
                 print host-agnostic guidance for wiring skill.mcp_server into an MCP host.
  --dev          Include dev dependencies (with --local).
  --docs         Include docs dependencies.
  --dry-run      Print every action that WOULD run, then exit without changes.
  --lang zh|en   Message language (default: auto-detect from $LANG).
  -h, --help     Show this help and exit.

Examples:
  ./install.sh --local --dev
  ./install.sh --global --with-mcp
  ./install.sh --dry-run --local
EOF
  fi
}

# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #
have() { command -v "$1" >/dev/null 2>&1; }

# run <cmd...> : execute a command, or (under --dry-run) print what WOULD run.
run() {
  if [ "${DRY_RUN}" -eq 1 ]; then
    printf '  [dry-run] %s\n' "$*"
    return 0
  fi
  "$@"
}

extras_suffix() {
  # Build a PEP 508 extras suffix like "[dev,docs]" for pip fallback installs.
  local groups=()
  if [ "${WITH_DEV}" -eq 1 ]; then
    groups+=("dev")
  fi
  if [ "${WITH_DOCS}" -eq 1 ]; then
    groups+=("docs")
  fi
  if [ "${#groups[@]}" -eq 0 ]; then
    printf ''
    return 0
  fi
  local joined
  joined="$(IFS=,; printf '%s' "${groups[*]}")"
  printf '[%s]' "${joined}"
}

warn_if_bindir_missing() {
  local bindir="${HOME:-}/.local/bin"
  case ":${PATH:-}:" in
    *":${bindir}:"*) return 0 ;;
    *)
      warn "${bindir} is not on your PATH; add it so the 'yanshi' command is found" \
           "${bindir} 不在 PATH 中;请将其加入 PATH 以便找到 'yanshi' 命令"
      ;;
  esac
}

# --------------------------------------------------------------------------- #
# Argument parsing
# --------------------------------------------------------------------------- #
while [ "$#" -gt 0 ]; do
  case "$1" in
    --local)    SCOPE="local" ;;
    --global)   SCOPE="global" ;;
    --with-mcp) WITH_MCP=1 ;;
    --dev)      WITH_DEV=1 ;;
    --docs)     WITH_DOCS=1 ;;
    --dry-run)  DRY_RUN=1 ;;
    --lang)
      shift || { detect_lang; die "--lang requires an argument (zh|en)" "--lang 需要一个参数 (zh|en)" 2; }
      case "${1:-}" in
        zh|en) LANG_CHOICE="$1" ;;
        *) detect_lang; die "--lang must be 'zh' or 'en'" "--lang 必须是 'zh' 或 'en'" 2 ;;
      esac
      ;;
    --lang=*)
      _v="${1#--lang=}"
      case "${_v}" in
        zh|en) LANG_CHOICE="${_v}" ;;
        *) detect_lang; die "--lang must be 'zh' or 'en'" "--lang 必须是 'zh' 或 'en'" 2 ;;
      esac
      ;;
    -h|--help)  detect_lang; usage; exit 0 ;;
    *)          detect_lang; die "unknown option: $1" "未知选项: $1" 2 ;;
  esac
  shift
done

detect_lang
[ -n "${SCOPE}" ] || SCOPE="local"

# --------------------------------------------------------------------------- #
# Prerequisite checks
# --------------------------------------------------------------------------- #
find_python() {
  local candidate
  for candidate in python3 python3.12 python; do
    if have "${candidate}"; then
      if "${candidate}" -c "import sys; raise SystemExit(0 if sys.version_info[:2] >= (3, ${MIN_PY_MINOR}) else 1)" >/dev/null 2>&1; then
        PYTHON_BIN="${candidate}"
        return 0
      fi
    fi
  done
  return 1
}

check_prereqs() {
  info "Checking prerequisites..." "正在检查前置条件..."
  if ! find_python; then
    die "Python 3.${MIN_PY_MINOR}+ is required but no suitable python3 was found on PATH" \
        "需要 Python 3.${MIN_PY_MINOR}+,但在 PATH 中未找到合适的 python3" 1
  fi
  info "  Python: $(${PYTHON_BIN} --version 2>&1)" "  Python: $(${PYTHON_BIN} --version 2>&1)"
  if have uv; then
    info "  uv: $(uv --version 2>&1)" "  uv: $(uv --version 2>&1)"
  else
    warn "uv not found; using the pip + venv fallback (this is OK, not an error)" \
         "未找到 uv;将使用 pip + venv 兜底(这没关系,并非错误)"
  fi
}

# --------------------------------------------------------------------------- #
# Install paths
# --------------------------------------------------------------------------- #
install_local() {
  info "Installing YanShi locally (editable install into .venv)..." \
       "正在本地安装 YanShi(可编辑模式,装入 .venv)..."
  if [ -z "${REPO_DIR}" ]; then
    die "--local must be run from a YanShi checkout (pyproject.toml not found next to install.sh)" \
        "--local 需在 YanShi 代码库中运行(install.sh 旁未找到 pyproject.toml)" 1
  fi
  if have uv; then
    local uv_args=(uv sync)
    if [ "${WITH_DEV}" -eq 1 ]; then
      uv_args+=(--group dev)
    fi
    if [ "${WITH_DOCS}" -eq 1 ]; then
      uv_args+=(--group docs)
    fi
    uv_args+=(--project "${REPO_DIR}")
    run "${uv_args[@]}"
    if [ "${DRY_RUN}" -ne 1 ]; then
      info "Installed (editable) via 'uv sync'. Activate: . ${REPO_DIR}/.venv/bin/activate" \
           "已通过 'uv sync' 完成可编辑安装。激活:. ${REPO_DIR}/.venv/bin/activate"
    fi
  else
    local extras
    extras="$(extras_suffix)"
    run "${PYTHON_BIN}" -m venv "${REPO_DIR}/.venv"
    run "${REPO_DIR}/.venv/bin/python" -m pip install --upgrade pip
    run "${REPO_DIR}/.venv/bin/python" -m pip install -e "${REPO_DIR}${extras}"
    if [ "${DRY_RUN}" -ne 1 ]; then
      info "Installed (editable) via pip. Activate: . ${REPO_DIR}/.venv/bin/activate" \
           "已通过 pip 完成可编辑安装。激活:. ${REPO_DIR}/.venv/bin/activate"
    fi
  fi
}

install_global() {
  info "Installing YanShi globally (onto your PATH)..." \
       "正在全局安装 YanShi(加入 PATH)..."
  local source
  if [ -n "${REPO_DIR}" ]; then
    source="${REPO_DIR}"
  else
    source="git+${REPO_URL}"
  fi
  if have uv; then
    if [ "${WITH_DEV}" -eq 1 ] || [ "${WITH_DOCS}" -eq 1 ]; then
      warn "--dev/--docs are dev-only groups; ignored for a global tool install" \
           "--dev/--docs 为开发分组,全局工具安装时已忽略"
    fi
    run uv tool install "${source}"
    if [ "${DRY_RUN}" -ne 1 ]; then
      info "Installed via 'uv tool install'." "已通过 'uv tool install' 安装。"
    fi
  elif have pipx; then
    run pipx install "${source}"
    if [ "${DRY_RUN}" -ne 1 ]; then
      info "Installed via 'pipx install'." "已通过 'pipx install' 安装。"
    fi
  else
    warn "Neither uv nor pipx found; falling back to 'pip install --user'" \
         "未找到 uv 或 pipx;回退到 'pip install --user'"
    local extras
    extras="$(extras_suffix)"
    if [ -n "${REPO_DIR}" ]; then
      run "${PYTHON_BIN}" -m pip install --user "${REPO_DIR}${extras}"
    else
      run "${PYTHON_BIN}" -m pip install --user "yanshi @ git+${REPO_URL}"
    fi
    if [ "${DRY_RUN}" -ne 1 ]; then
      info "Installed via 'pip install --user'." "已通过 'pip install --user' 安装。"
    fi
  fi
  warn_if_bindir_missing
}

# --------------------------------------------------------------------------- #
# Optional: MCP wiring (--with-mcp)
# --------------------------------------------------------------------------- #
wire_mcp() {
  if [ "${WITH_MCP}" -ne 1 ]; then
    return 0
  fi
  info "MCP wiring guidance (skill/mcp_server.py)..." \
       "MCP 接入指引 (skill/mcp_server.py)..."
  cat <<'EOF'
  Host-agnostic MCP wiring:
    Expose the importable wrappers from skill/mcp_server.py as MCP tools, e.g.:
      from skill.mcp_server import dispatch, get_status, get_summary, wait_for, cancel_agent
    Run your MCP host from the repo root so the `skill` package resolves, and make sure
    `python -c "import yanshi.dispatch"` succeeds in that interpreter.
EOF
  if [ "${DRY_RUN}" -eq 1 ]; then
    printf '  [dry-run] verify: python -c "import yanshi.dispatch" and "import skill.mcp_server"\n'
    return 0
  fi

  if [ -z "${REPO_DIR}" ]; then
    warn "Not running from a checkout; cannot verify skill/mcp_server.py here" \
         "未在代码库中运行;此处无法校验 skill/mcp_server.py"
    return 0
  fi
  if [ ! -f "${REPO_DIR}/skill/mcp_server.py" ]; then
    die "expected ${REPO_DIR}/skill/mcp_server.py but it is missing" \
        "缺少文件 ${REPO_DIR}/skill/mcp_server.py" 1
  fi

  local verify_py
  if [ -x "${REPO_DIR}/.venv/bin/python" ]; then
    verify_py="${REPO_DIR}/.venv/bin/python"
  else
    verify_py="${PYTHON_BIN}"
  fi

  if ( cd "${REPO_DIR}" && "${verify_py}" -c "import yanshi.dispatch" ) >/dev/null 2>&1; then
    info "  Verified: 'import yanshi.dispatch' works." "  校验通过:'import yanshi.dispatch' 正常。"
  elif [ "${SCOPE}" = "global" ]; then
    warn "Could not import yanshi.dispatch with ${verify_py}; global tool envs are isolated — use your MCP host's interpreter" \
         "无法用 ${verify_py} 导入 yanshi.dispatch;全局工具环境彼此隔离 —— 请使用 MCP host 的解释器" 
  else
    die "'import yanshi.dispatch' failed with ${verify_py}; the install looks broken" \
        "用 ${verify_py} 执行 'import yanshi.dispatch' 失败;安装可能已损坏" 1
  fi

  if ( cd "${REPO_DIR}" && "${verify_py}" -c "import skill.mcp_server" ) >/dev/null 2>&1; then
    info "  Verified: 'import skill.mcp_server' works." "  校验通过:'import skill.mcp_server' 正常。"
  else
    warn "Could not import skill.mcp_server with ${verify_py}; bind it from your MCP host's interpreter" \
         "无法用 ${verify_py} 导入 skill.mcp_server;请在 MCP host 的解释器中绑定"
  fi
}

# --------------------------------------------------------------------------- #
# Post-install: `yanshi doctor` (informational only; never fails the install)
# --------------------------------------------------------------------------- #
run_doctor() {
  local yanshi_cmd
  if [ "${SCOPE}" = "local" ]; then
    yanshi_cmd="${REPO_DIR:-.}/.venv/bin/yanshi"
  else
    yanshi_cmd="yanshi"
  fi
  info "Running 'yanshi doctor' to detect vendor CLIs (missing ones are OK)..." \
       "正在运行 'yanshi doctor' 检测厂商 CLI(缺失也没关系)..."
  if [ "${DRY_RUN}" -eq 1 ]; then
    printf '  [dry-run] %s doctor\n' "${yanshi_cmd}"
    return 0
  fi

  if [ "${SCOPE}" = "local" ] && [ ! -x "${yanshi_cmd}" ]; then
    if have yanshi; then
      yanshi_cmd="yanshi"
    fi
  fi
  if [ "${yanshi_cmd}" = "yanshi" ] && ! have yanshi; then
    warn "yanshi is not on PATH yet; open a new shell and run 'yanshi doctor'" \
         "PATH 中尚未找到 yanshi;请新开终端运行 'yanshi doctor'"
    return 0
  fi
  if [ "${yanshi_cmd}" != "yanshi" ] && [ ! -x "${yanshi_cmd}" ]; then
    warn "yanshi binary not found at ${yanshi_cmd}; skipping doctor" \
         "未在 ${yanshi_cmd} 找到 yanshi;跳过 doctor"
    return 0
  fi

  # doctor exits non-zero when vendor CLIs are missing; that is expected and
  # must NOT fail the install (the `if` keeps set -e / ERR from tripping).
  if "${yanshi_cmd}" doctor; then
    info "All registered adapters look healthy." "所有已注册适配器状态正常。"
  else
    warn "Some vendor CLIs are missing or unauthenticated (expected; install/auth them as needed)" \
         "部分厂商 CLI 缺失或未认证(属正常;按需安装/认证)"
  fi
}

# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
main() {
  info "YanShi installer — scope=${SCOPE}, dry-run=${DRY_RUN}, with-mcp=${WITH_MCP}" \
       "YanShi 安装器 — 范围=${SCOPE}, 演练=${DRY_RUN}, 接入MCP=${WITH_MCP}"
  check_prereqs
  case "${SCOPE}" in
    local)  install_local ;;
    global) install_global ;;
    *)      die "invalid scope: ${SCOPE}" "无效的安装范围: ${SCOPE}" 2 ;;
  esac
  wire_mcp
  run_doctor
  info "Done. Docs: https://yorha-agents.github.io/YanShi/" \
       "完成。文档:https://yorha-agents.github.io/YanShi/"
}

main
