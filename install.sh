#!/usr/bin/env bash
# ZJAI-Agent 一键安装启动脚本（Linux / macOS）
#
# 用法：
#   curl -fsSL https://raw.githubusercontent.com/ZSJ0000/ZJAI-Agent_MCPversion/main/install.sh | bash
#
# 可选环境变量：
#   ZJAI_DIR       安装目录（默认 ~/ZJAI-Agent）
#   ZJAI_PORT      监听端口（默认 5000）
#   ZJAI_BRANCH    分支（默认 main）
#   ZJAI_MIRROR    Git 代理前缀，如 https://ghfast.top/（默认空）
#   ZJAI_YES=1     跳过交互确认
set -euo pipefail

REPO="ZSJ0000/ZJAI-Agent_MCPversion"
INSTALL_DIR="${ZJAI_DIR:-$HOME/ZJAI-Agent}"
PORT="${ZJAI_PORT:-5000}"
BRANCH="${ZJAI_BRANCH:-main}"
MIRROR="${ZJAI_MIRROR:-}"

info() { printf '\033[32m[ZJAI]\033[0m %s\n' "$*"; }
warn() { printf '\033[33m[ZJAI]\033[0m %s\n' "$*"; }
die()  { printf '\033[31m[ZJAI]\033[0m %s\n' "$*" >&2; exit 1; }

# ---------- 检查依赖 ----------
command -v git >/dev/null 2>&1 || die "未找到 git，请先安装：apt install git / brew install git"

PY=""
for candidate in python3.12 python3.11 python3.10 python3.9 python3.8 python3; do
  if command -v "$candidate" >/dev/null 2>&1; then
    if "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3,8) else 1)' 2>/dev/null; then
      PY="$candidate"
      break
    fi
  fi
done
[ -n "$PY" ] || die "未找到 Python 3.8+，请先安装：apt install python3 / brew install python@3.11"
info "使用 $($PY --version 2>&1) ($PY)"

# ---------- 准备目录 ----------
if [ -d "$INSTALL_DIR/.git" ]; then
  info "检测到已有安装，更新代码"
  git -C "$INSTALL_DIR" fetch --depth 1 origin "$BRANCH"
  git -C "$INSTALL_DIR" reset --hard "origin/$BRANCH"
else
  mkdir -p "$(dirname "$INSTALL_DIR")"
  info "克隆到 $INSTALL_DIR"
  git clone --depth 1 --branch "$BRANCH" "${MIRROR}https://github.com/${REPO}.git" "$INSTALL_DIR"
fi

cd "$INSTALL_DIR"

# ---------- 虚拟环境 ----------
if [ ! -d .venv ]; then
  info "创建虚拟环境 .venv"
  if ! "$PY" -m venv .venv 2>/dev/null; then
    warn "venv 创建失败（系统缺 python3-venv），改用当前 Python 直接安装依赖"
    rm -rf .venv
  fi
fi
if [ -d .venv ]; then
  # shellcheck disable=SC1091
  . .venv/bin/activate
  RUNPY="python"
  PIP="python -m pip"
else
  # 无 venv 时系统 Python 可能受 PEP 668 保护（Debian/Ubuntu）
  RUNPY="$PY"
  PIP="$PY -m pip"
  if $PY -m pip help install 2>/dev/null | grep -q -- '--break-system-packages'; then
    if ! $PY -c 'import flask' >/dev/null 2>&1; then
      warn "系统 Python 受 PEP 668 保护，使用 --break-system-packages"
      PIP="$PY -m pip --break-system-packages"
    fi
  fi
fi

info "安装依赖"
$PIP install --quiet --upgrade pip 2>/dev/null || warn "pip 升级失败，继续使用现有版本"

# 先常规安装；遇到 PEP 668（externally-managed）时自动重试一次
install_deps() {
  if $PIP install --quiet -r requirements.txt 2>/tmp/zjai-pip-err; then
    return 0
  fi
  if grep -q 'externally-managed-environment\|break-system-packages' /tmp/zjai-pip-err 2>/dev/null; then
    warn "系统 Python 受 PEP 668 保护，改用 --break-system-packages 重试"
    $PY -m pip install --quiet --break-system-packages -r requirements.txt && return 0
  fi
  cat /tmp/zjai-pip-err >&2
  return 1
}
install_deps || die "依赖安装失败，请检查网络或换用镜像（ZJAI_MIRROR）"
rm -f /tmp/zjai-pip-err

$PIP install --quiet waitress 2>/dev/null || warn "waitress 未安装，将使用内置开发服务器"

# ---------- 生成密钥 ----------
ENV_FILE="$INSTALL_DIR/.env"
if [ ! -f "$ENV_FILE" ]; then
  info "生成 session 密钥并写入 .env"
  SECRET="$($RUNPY -c 'import secrets; print(secrets.token_hex(32))')"
  cat > "$ENV_FILE" <<EOF
# ZJAI-Agent 环境变量，由 install.sh 生成
ZJAI_SECRET_KEY=$SECRET
ZJAI_PORT=$PORT
EOF
  chmod 600 "$ENV_FILE"
else
  info "沿用已有 .env"
fi
# shellcheck disable=SC1090
. "$ENV_FILE"

# ---------- 提示配置 ----------
if [ ! -f config/models.json ] || ! grep -q '"base_url"' config/models.json 2>/dev/null; then
  warn "尚未配置模型 API。可以："
  warn "  1) 启动后在浏览器里用安装向导填写"
  warn "  2) 预置配置：cp config/models.example.json config/models.json 后编辑"
fi

info "安装完成"
echo
if [ -d .venv ]; then
  ACT=". .venv/bin/activate && "
else
  ACT=""
fi
info "启动命令："
echo "    cd $INSTALL_DIR && ${ACT}$RUNPY run.py"
echo
info "或使用生产服务器（需已装 waitress）："
echo "    cd $INSTALL_DIR && ${ACT}waitress-serve --host=0.0.0.0 --port=$PORT run:app"
echo
info "访问 http://127.0.0.1:$PORT"

# ---------- 非交互模式直接启动 ----------
if [ "${ZJAI_YES:-0}" = "1" ]; then
  info "ZJAI_YES=1，直接启动"
  exec $RUNPY run.py
fi
