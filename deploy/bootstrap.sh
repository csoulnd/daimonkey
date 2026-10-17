#!/usr/bin/env bash
# InferTech 观察站安装交付件：全新 Ubuntu/Debian 服务器一条命令拉起。
#
# 用法（在 git clone 后的仓库根目录执行）:
#   sudo bash deploy/bootstrap.sh
#   sudo bash deploy/bootstrap.sh --host 0.0.0.0 --port 8080
#   sudo bash deploy/bootstrap.sh --restore daimonkey-backup-YYYYmmdd-HHMMSS.tar.gz --force
#
# 流程: 系统依赖 -> pi 运行时 -> venv/依赖 -> (可选)恢复备份 -> 迁移路径/建库/构建
#       -> 安装 userctl -> 安装并启动 systemd 服务。
# 幂等: 重复执行安全；已满足的步骤跳过。
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
ORIG_PWD="$PWD"
# 所有 python -m collector / scripts 调用都以安装目录为工作目录，
# 否则从其他目录调用时会误用当前目录的代码与数据。
cd "$ROOT"

HOST="0.0.0.0"
PORT="8080"
RUN_AS="${SUDO_USER:-$(id -un)}"
RESTORE=""
FORCE_RESTORE=0
INSTALL_SERVICE=1
INSTALL_DEPS=1
REBUILD=0
BIN_DIR="/usr/local/bin"

say()  { printf '\n== %s ==\n' "$*"; }
ok()   { printf '  [OK]   %s\n' "$*"; }
warn() { printf '  [WARN] %s\n' "$*"; }
die()  { printf '  [FAIL] %s\n' "$*" >&2; exit 1; }

usage() {
  cat <<'EOF'
用法: sudo bash deploy/bootstrap.sh [选项]

选项:
  --host HOST           监听地址（默认 0.0.0.0）
  --port PORT           监听端口（默认 8080）
  --run-as USER         服务运行用户（默认 sudo 发起者/当前用户）
  --restore FILE        从 deploy/backup.sh 生成的备份包恢复（新机部署用）
  --force               恢复时允许覆盖已存在的 data/（旧数据改名保留）
  --rebuild             强制重建站点（dist/ 存在也重建）
  --no-service          只安装，不写 systemd/不启动（演练与调试用）
  --bin-dir DIR         userctl 安装目录（默认 /usr/local/bin）
  --skip-system-deps    跳过 apt 安装（依赖已手工装好时）
  -h, --help            显示帮助
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --host) HOST="${2:?--host 需要参数}"; shift 2 ;;
    --port) PORT="${2:?--port 需要参数}"; shift 2 ;;
    --run-as) RUN_AS="${2:?--run-as 需要参数}"; shift 2 ;;
    --restore) RESTORE="${2:?--restore 需要参数}"; shift 2 ;;
    --force) FORCE_RESTORE=1; shift ;;
    --rebuild) REBUILD=1; shift ;;
    --no-service) INSTALL_SERVICE=0; shift ;;
    --bin-dir) BIN_DIR="${2:?--bin-dir 需要参数}"; shift 2 ;;
    --skip-system-deps) INSTALL_DEPS=0; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "未知参数: $1（--help 查看用法）" ;;
  esac
done

[ -f "$ROOT/requirements.txt" ] || die "未找到 requirements.txt，请在仓库内执行: $ROOT"
[ -f "$ROOT/scripts/serve.py" ] || die "未找到 scripts/serve.py，仓库不完整"
id "$RUN_AS" >/dev/null 2>&1 || die "运行用户不存在: $RUN_AS"
# 相对参数按调用者当前目录解析（脚本内部已 cd 到安装目录）
case "$RESTORE" in
  "") ;;
  /*) ;;
  *) RESTORE="$ORIG_PWD/$RESTORE" ;;
esac
case "$BIN_DIR" in
  /*) ;;
  *) BIN_DIR="$ORIG_PWD/$BIN_DIR" ;;
esac
[ "$(id -u)" -eq 0 ] || warn "非 root 运行：将跳过系统包安装与 systemd 安装"

# ---------------------------------------------------------------- 1. 系统依赖
say "1/7 系统依赖"
if [ "$INSTALL_DEPS" = 1 ] && [ "$(id -u)" -eq 0 ]; then
  APT_UPDATED=0
  apt_install() {
    if [ "$APT_UPDATED" = 0 ]; then
      apt-get update -qq
      APT_UPDATED=1
    fi
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "$@"
  }
  need=()
  command -v python3 >/dev/null || need+=(python3)
  python3 -m venv --help >/dev/null 2>&1 || need+=(python3-venv)
  python3 -c 'import ensurepip' >/dev/null 2>&1 || need+=(python3-venv)
  command -v git >/dev/null || need+=(git)
  command -v sqlite3 >/dev/null || need+=(sqlite3)
  command -v node >/dev/null || need+=(nodejs)
  command -v npm >/dev/null || need+=(npm)
  command -v curl >/dev/null || need+=(curl)
  if [ ${#need[@]} -gt 0 ]; then
    ok "安装系统包: ${need[*]}"
    apt_install "${need[@]}"
  else
    ok "系统包齐全"
  fi
elif [ "$INSTALL_DEPS" = 0 ]; then
  warn "已按 --skip-system-deps 跳过 apt"
fi

# 选一个 >=3.11 的解释器（apt 装不到时提示升级系统）
PYTHON_BIN=""
for cand in python3 python3.13 python3.12 python3.11; do
  if command -v "$cand" >/dev/null 2>&1 \
     && "$cand" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3,11) else 1)' 2>/dev/null; then
    PYTHON_BIN="$(command -v "$cand")"
    break
  fi
done
[ -n "$PYTHON_BIN" ] || die "需要 Python >= 3.11（Ubuntu 24.04 / Debian 12+ 自带；旧系统请先升级或手动安装）"
ok "Python: $("$PYTHON_BIN" --version 2>&1)"

command -v git >/dev/null || die "缺少 git"
command -v sqlite3 >/dev/null || die "缺少 sqlite3（备份/恢复依赖）"
ok "git: $(git --version | awk '{print $3}')   sqlite3: $(sqlite3 --version | awk '{print $1}')"

command -v node >/dev/null || die "缺少 Node.js（>=18，pi 运行时依赖）"
node -e 'process.exit(process.versions.node.split(".").map(Number)[0] >= 18 ? 0 : 1)' \
  || die "Node.js 版本过低: $(node -v)（需要 >=18；旧系统请先安装新版 Node）"
ok "Node.js: $(node -v)"

# ---------------------------------------------------------------- 2. pi 运行时
say "2/7 PIAgent 运行时（pi，版本锁定）"
PI_PKG="@earendil-works/pi-coding-agent@1.3.0"
PI_VERSION="1.3.0"
if command -v pi >/dev/null 2>&1 && pi --version 2>/dev/null | grep -qx "$PI_VERSION"; then
  ok "pi 已安装: $PI_VERSION"
else
  command -v npm >/dev/null || die "缺少 npm，无法安装 pi"
  npm install -g "$PI_PKG"
  command -v pi >/dev/null || die "pi 安装后不可执行（检查 npm 全局 bin 是否在 PATH）"
  pi --version | grep -qx "$PI_VERSION" \
    || die "pi 版本不是 $PI_VERSION: $(pi --version 2>&1)"
  ok "pi 已安装: $PI_VERSION"
fi

# ---------------------------------------------------------------- 3. venv + pip
say "3/7 Python 虚拟环境与依赖"
if [ ! -x "$ROOT/.venv/bin/python" ]; then
  "$PYTHON_BIN" -m venv "$ROOT/.venv"
  ok "已创建 .venv"
else
  ok ".venv 已存在"
fi
"$ROOT/.venv/bin/python" -m pip install --disable-pip-version-check -q \
  -r "$ROOT/requirements.txt"
ok "requirements.txt 依赖就绪"

# ---------------------------------------------------------------- 4. 恢复数据
say "4/7 数据恢复"
RESTORE_STAGE=""
cleanup() { [ -n "$RESTORE_STAGE" ] && rm -rf "$RESTORE_STAGE"; }
trap cleanup EXIT

if [ -n "$RESTORE" ]; then
  [ -f "$RESTORE" ] || die "备份文件不存在: $RESTORE"
  if tar -tzf "$RESTORE" | grep -Eq '(^|/)\.\.(/|$)|^/'; then
    die "备份包含不安全路径（绝对路径或 ..），拒绝恢复"
  fi
  RESTORE_STAGE="$(mktemp -d)"
  tar -xzf "$RESTORE" -C "$RESTORE_STAGE"
  [ -f "$RESTORE_STAGE/manifest.json" ] || die "备份缺少 manifest.json"
  "$PYTHON_BIN" - "$RESTORE_STAGE/manifest.json" <<'PY' || die "备份 manifest 不合法"
import json, sys
d = json.load(open(sys.argv[1], encoding="utf-8"))
assert d.get("format") == "daimonkey-backup", d.get("format")
assert int(d.get("version", 0)) >= 1
PY
  if [ -f "$RESTORE_STAGE/SHA256SUMS" ] && command -v sha256sum >/dev/null; then
    ( cd "$RESTORE_STAGE" && sha256sum -c --quiet SHA256SUMS ) \
      || die "备份校验失败（SHA256SUMS）"
    ok "备份完整性校验通过"
  else
    warn "备份无 SHA256SUMS 或系统缺少 sha256sum，跳过完整性校验"
  fi
  [ -d "$RESTORE_STAGE/data" ] || die "备份缺少 data/ 目录"

  if [ -d "$ROOT/data" ] && [ -n "$(find "$ROOT/data" -mindepth 1 -print -quit 2>/dev/null)" ]; then
    if [ "$FORCE_RESTORE" != 1 ]; then
      die "data/ 非空，拒绝覆盖。确认后加 --force（现有 data/ 会改名保留）"
    fi
    KEEP="$ROOT/data.pre-restore-$(date +%Y%m%d-%H%M%S)"
    mv "$ROOT/data" "$KEEP"
    ok "现有 data/ 已改名保留: $KEEP"
  fi
  mkdir -p "$ROOT/data"
  cp -a "$RESTORE_STAGE/data/." "$ROOT/data/"
  chmod 600 "$ROOT/data/assistant.env" "$ROOT/data/assistant_secret" 2>/dev/null || true
  ok "数据已恢复到 $ROOT/data"
else
  ok "未指定 --restore（全新安装）"
fi

if [ "$(id -u)" -eq 0 ]; then
  chown -R "$RUN_AS" "$ROOT/data" 2>/dev/null || true
  if [ -d "$ROOT/.venv" ]; then chown -R "$RUN_AS" "$ROOT/.venv"; fi
  if [ -d "$ROOT/dist" ]; then chown -R "$RUN_AS" "$ROOT/dist"; fi
fi

# ---------------------------------------------------------------- 5. 路径/建库/构建
say "5/7 数据库与站点"
if [ -f "$ROOT/data/heat_index.sqlite" ]; then
  "$ROOT/.venv/bin/python" "$ROOT/scripts/migrate_relpath.py"
fi
"$ROOT/.venv/bin/python" -m collector --init-db
ok "数据库就绪（含在线迁移）"

if [ "$REBUILD" = 1 ] || [ ! -f "$ROOT/dist/index.html" ]; then
  "$ROOT/.venv/bin/python" -m collector --build
  ok "站点已构建到 dist/"
else
  ok "dist/ 已存在（--rebuild 可强制重建）"
fi

if [ ! -f "$ROOT/data/assistant.env" ]; then
  warn "缺少 data/assistant.env（DEEPSEEK_API_KEY）：站点可用，AI 助手/评分降级"
fi

# ---------------------------------------------------------------- 6. userctl
say "6/7 账号管理命令 userctl"
if [ "$(id -u)" -ne 0 ] && [ ! -w "$BIN_DIR" ]; then
  BIN_DIR="$HOME/.local/bin"
  warn "/usr/local/bin 不可写，改用 $BIN_DIR（确认它在 PATH 中）"
fi
mkdir -p "$BIN_DIR"
cat > "$BIN_DIR/userctl.tmp" <<EOF
#!/usr/bin/env bash
exec "$ROOT/.venv/bin/python" "$ROOT/scripts/userctl.py" "\$@"
EOF
chmod 755 "$BIN_DIR/userctl.tmp"
mv "$BIN_DIR/userctl.tmp" "$BIN_DIR/userctl"
ok "已安装 $BIN_DIR/userctl"

# ---------------------------------------------------------------- 7. systemd
say "7/7 进程托管（systemd）"
if [ "$INSTALL_SERVICE" = 0 ]; then
  warn "已按 --no-service 跳过 systemd 安装；手动启动: $ROOT/scripts/serve.sh --host $HOST --port $PORT"
elif [ "$(id -u)" -ne 0 ]; then
  warn "非 root：跳过 systemd 安装；手动启动: $ROOT/scripts/serve.sh --host $HOST --port $PORT"
elif ! systemctl show-environment >/dev/null 2>&1; then
  warn "systemd 不可用（容器环境？）：跳过托管；手动启动: $ROOT/scripts/serve.sh --host $HOST --port $PORT"
else
  UNIT_SRC="$ROOT/deploy/daimonkey.service"
  UNIT_DST="/etc/systemd/system/daimonkey.service"
  [ -f "$UNIT_SRC" ] || die "缺少 $UNIT_SRC"
  TMP_UNIT="$(mktemp)"
  sed -e "s|__INSTALL_ROOT__|$ROOT|g" \
      -e "s|__RUN_AS__|$RUN_AS|g" \
      -e "s|__HOST__|$HOST|g" \
      -e "s|__PORT__|$PORT|g" "$UNIT_SRC" > "$TMP_UNIT"
  UNIT_CHANGED=1
  if [ -f "$UNIT_DST" ] && cmp -s "$TMP_UNIT" "$UNIT_DST"; then
    UNIT_CHANGED=0
  fi
  install -m 644 "$TMP_UNIT" "$UNIT_DST"
  rm -f "$TMP_UNIT"
  systemctl daemon-reload
  systemctl enable daimonkey >/dev/null
  ok "已安装 /etc/systemd/system/daimonkey.service（User=$RUN_AS）"

  port_in_use() {
    if command -v ss >/dev/null 2>&1; then
      ss -ltn 2>/dev/null | awk '{print $4}' | grep -Eq "[:.]$PORT$"
    else
      "$PYTHON_BIN" - "$PORT" <<'PY'
import socket, sys
s = socket.socket()
s.settimeout(0.3)
raise SystemExit(0 if s.connect_ex(("127.0.0.1", int(sys.argv[1]))) == 0 else 1)
PY
    fi
  }

  if systemctl is-active --quiet daimonkey; then
    if [ "$UNIT_CHANGED" = 1 ]; then
      systemctl restart daimonkey
      ok "单元有变更，服务已重启"
    else
      ok "服务已在运行（单元无变化，未重启）"
    fi
  elif port_in_use; then
    die "端口 $PORT 已被非 systemd 进程占用（常见为旧 nohup serve.py）。先停止旧进程后重跑:
         pkill -f 'scripts/serve.py'   # 或按 ss -ltnp 定位
         sudo bash deploy/bootstrap.sh"
  else
    systemctl start daimonkey
    ok "服务已启动并设为开机自启"
  fi

  health_host="$HOST"
  [ "$health_host" = "0.0.0.0" ] && health_host="127.0.0.1"
  HEALTH_OK=0
  for _ in $(seq 1 20); do
    if curl -fsS -o /dev/null --max-time 2 "http://$health_host:$PORT/" 2>/dev/null; then
      HEALTH_OK=1
      ok "健康检查通过: http://$health_host:$PORT/"
      break
    fi
    sleep 1
  done
  if ! systemctl is-active --quiet daimonkey; then
    journalctl -u daimonkey -n 20 --no-pager || true
    die "服务启动失败，见上方 journal"
  fi
  [ "$HEALTH_OK" = 1 ] || {
    journalctl -u daimonkey -n 20 --no-pager || true
    die "服务进程存活但 HTTP 健康检查未通过（端口 $PORT）"
  }
fi

# ---------------------------------------------------------------- 完成
say "安装完成"
cat <<EOF
  安装目录 : $ROOT
  运行用户 : $RUN_AS
  监听     : http://$HOST:$PORT/（云安全组放行 TCP $PORT）
  服务管理 : systemctl status|restart daimonkey   |  journalctl -u daimonkey -f
  建账号   : userctl add <用户名>
  手动更新 : $ROOT/scripts/update.sh
  备份     : $ROOT/deploy/backup.sh
  搬迁演练 : $ROOT/deploy/smoke.sh --all
  部署文档 : $ROOT/docs/deploy.md
EOF
