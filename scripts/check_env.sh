#!/usr/bin/env bash
# 智能体热度指数 - 前置环境检查（开发规范第 11 章的实现）
# 用法: bash scripts/check_env.sh [报告文件路径]
set -uo pipefail
cd "$(dirname "$0")/.."
REPORT="${1:-env_report.md}"
PASS=0; WARN=0; FAIL=0

log()  { printf '%s\n' "$*" | tee -a "$REPORT"; }
ok()   { PASS=$((PASS+1)); log "  [PASS] $*"; }
warn() { WARN=$((WARN+1)); log "  [WARN] $*"; }
fail() { FAIL=$((FAIL+1)); log "  [FAIL] $*"; }

: > "$REPORT"
log "# 环境检查报告"
log ""
log "- 生成时间: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
log "- 主机名: $(hostname)  用户: $(whoami)"
log ""

# ---------- 1. 系统资源 ----------
log "## 1. 系统资源"
CPU=$(nproc 2>/dev/null || echo 0)
[ "$CPU" -ge 2 ] && ok "CPU 核数: $CPU" || warn "CPU 核数: $CPU（建议 >= 2）"

MEM_MB=$(awk '/MemTotal/{printf "%d", $2/1024}' /proc/meminfo 2>/dev/null || echo 0)
if [ "$MEM_MB" -ge 3800 ]; then ok "内存: ${MEM_MB}MB"
elif [ "$MEM_MB" -ge 1800 ]; then warn "内存: ${MEM_MB}MB（2G 档，须严格控制并发）"
else fail "内存: ${MEM_MB}MB（不足，需 >= 2GB）"; fi

SWAP_MB=$(awk '/SwapTotal/{printf "%d", $2/1024}' /proc/meminfo 2>/dev/null || echo 0)
[ "$SWAP_MB" -ge 1800 ] && ok "Swap: ${SWAP_MB}MB" || warn "Swap: ${SWAP_MB}MB（建议 >= 2GB）"

DISK_AVAIL=$(df -BG --output=avail . 2>/dev/null | tail -1 | tr -dc '0-9')
[ "${DISK_AVAIL:-0}" -ge 10 ] && ok "项目分区可用: ${DISK_AVAIL}GB" || fail "项目分区可用: ${DISK_AVAIL:-?}GB（需 >= 10GB）"

# ---------- 2. 时间 ----------
log ""
log "## 2. 时间与时区"
log "  - 当前时间(UTC): $(date -u +%Y-%m-%dT%H:%M:%SZ)"
if timedatectl show -p NTPSynchronized --value 2>/dev/null | grep -q yes; then
  ok "NTP 已同步"
else
  warn "NTP 未确认同步（时间偏差会导致时序数据错乱）"
fi

# ---------- 3. 运行时 ----------
log ""
log "## 3. 运行时环境"
PYV=$(python3 -c 'import sys;print("%d.%d"%sys.version_info[:2])' 2>/dev/null || true)
if [ -n "$PYV" ]; then
  PMAJ=${PYV%%.*}; PMIN=${PYV##*.}
  if [ "$PMAJ" -gt 3 ] || { [ "$PMAJ" -eq 3 ] && [ "$PMIN" -ge 11 ]; }; then
    ok "Python: $PYV"
  else
    fail "Python: $PYV（需 >= 3.11）"
  fi
else
  fail "Python3 未安装"
fi

if [ -x .venv/bin/python ]; then
  ok "venv: $(.venv/bin/python --version 2>&1 | cut -d' ' -f2)（依赖已就绪）"
  VENV_OK=1
else
  warn ".venv 不存在（运行 python3 -m venv .venv && .venv/bin/pip install -r requirements.txt）"
  VENV_OK=0
fi

SQLV=$(python3 -c 'import sqlite3;print(sqlite3.sqlite_version)' 2>/dev/null || echo none)
[ "$SQLV" != "none" ] && ok "SQLite(内建): $SQLV" || fail "sqlite3 不可用"
python3 -c "import sqlite3;sqlite3.connect(':memory:').execute('CREATE VIRTUAL TABLE t USING fts5(x)')" 2>/dev/null \
  && ok "SQLite FTS5 可用" || warn "SQLite FTS5 不可用（影响后续站内搜索，不影响当前功能）"

command -v git >/dev/null && ok "git: $(git --version | awk '{print $3}')" || warn "git 未安装"

if command -v node >/dev/null 2>&1; then
  NODEV=$(node -v)
  NMAJ=$(printf '%s' "$NODEV" | tr -d 'v' | cut -d. -f1)
  if [ "${NMAJ:-0}" -ge 18 ]; then
    ok "Node.js: $NODEV（pi 运行时）"
  else
    fail "Node.js: $NODEV（需 >=18，pi 依赖）"
  fi
else
  fail "Node.js 未安装（需 >=18，pi 依赖；sudo apt install nodejs npm）"
fi

if command -v pi >/dev/null 2>&1; then
  PIV=$(pi --version 2>/dev/null | tail -1)
  if [ "$PIV" = "1.3.0" ]; then
    ok "pi: $PIV（AI 助手运行时，版本已锁定）"
  else
    warn "pi: ${PIV:-未知}（期望 1.3.0；重跑 deploy/bootstrap.sh 会重装锁定版本）"
  fi
else
  warn "pi 未安装（AI 助手不可用；sudo npm i -g @earendil-works/pi-coding-agent@1.3.0）"
fi

command -v sqlite3 >/dev/null && ok "sqlite3 CLI: $(sqlite3 --version | awk '{print $1}')" \
  || warn "sqlite3 CLI 未安装（备份脚本会用 Python API 兜底）"

# ---------- 3b. 知识库同步（GitHub SSH，可选） ----------
log ""
log "## 3b. 知识库同步（GitHub SSH，可选）"
if command -v ssh >/dev/null 2>&1; then
  SSH_OPTS=(-o BatchMode=yes -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10)
  if command -v timeout >/dev/null 2>&1; then
    SSHG=$(timeout 15 ssh "${SSH_OPTS[@]}" -T git@github.com 2>&1) || true
  else
    SSHG=$(ssh "${SSH_OPTS[@]}" -T git@github.com 2>&1) || true
  fi
  if printf '%s' "$SSHG" | grep -q "successfully authenticated"; then
    ok "GitHub SSH 可用（知识库同步）"
  else
    warn "GitHub SSH 不可用（知识库同步降级；检查 ~/.ssh 私钥与网络，见 docs/deploy.md §4.3）"
  fi
else
  warn "ssh 未安装（知识库同步不可用）"
fi

# ---------- 4. 数据源可达性 ----------
log ""
log "## 4. 数据源可达性"
probe() {
  local name="$1" url="$2" method="${3:-GET}"
  local code
  if [ "$method" = "POST" ]; then
    code=$(curl -s -o /dev/null -w '%{http_code}' -m 20 -X POST "$url" \
      -H 'Accept: application/json;api-version=7.2-preview.1' \
      -H 'Content-Type: application/json' \
      --data '{"filters":[{"criteria":[{"filterType":7,"value":"GitHub.copilot"}]}],"flags":914}' 2>/dev/null)
  else
    code=$(curl -s -o /dev/null -w '%{http_code}' -m 20 "$url" 2>/dev/null)
  fi
  if [ "$code" = "200" ]; then ok "$name (HTTP $code)"
  elif [ "$code" = "000" ]; then
    if [ "$4" = "optional" ]; then warn "$name 不可达（可选项，registry 中已禁用）"
    else fail "$name 不可达（超时/DNS 失败）"; fi
  else
    warn "$name 返回 HTTP $code（429 为限流，采集器有退避重试）"
  fi
}
probe "GitHub API          " "https://api.github.com/repos/openai/codex"
probe "npm Downloads API   " "https://api.npmjs.org/downloads/point/last-month/@openai/codex"
probe "PyPI (pypistats)    " "https://pypistats.org/api/packages/crewai/recent"
probe "Apple 榜单 RSS       " "https://itunes.apple.com/us/rss/topfreeapplications/limit=5/json"
probe "Apple lookup API    " "https://itunes.apple.com/lookup?bundleId=com.openai.chat&country=us"
probe "VS Code Marketplace " "https://marketplace.visualstudio.com/_apis/public/gallery/extensionquery" POST
probe "HuggingFace (可选)   " "https://huggingface.co/api/models/gpt2" GET optional
probe "Docker Hub (可选)    " "https://hub.docker.com/v2/repositories/library/nginx/" GET optional

# ---------- 5. 权限与进程管理 ----------
log ""
log "## 5. 权限与进程管理"
[ "$(whoami)" != "root" ] && ok "以非 root 用户运行" || warn "以 root 运行（本地开发可接受；对外部署建议专用用户）"
[ -w . ] && ok "项目目录可写" || fail "项目目录不可写"
if command -v systemctl >/dev/null 2>&1 && systemctl show-environment >/dev/null 2>&1; then
  ok "systemd 可用"
  if systemctl list-unit-files daimonkey.service 2>/dev/null | grep -q daimonkey; then
    systemctl is-enabled --quiet daimonkey \
      && ok "daimonkey.service 已设为开机自启" \
      || warn "daimonkey.service 未 enable（sudo systemctl enable --now daimonkey）"
    systemctl is-active --quiet daimonkey \
      && ok "daimonkey.service 运行中" \
      || fail "daimonkey.service 未运行（journalctl -u daimonkey -n 50）"
  else
    warn "daimonkey.service 未安装（sudo bash deploy/bootstrap.sh 一步完成）"
  fi
else
  log "  [跳过] systemd 不可用（容器环境；用 scripts/serve.sh 前台运行）"
fi

# ---------- 6. 站点服务状态 ----------
log ""
log "## 6. 站点服务"
code=$(curl -s -o /dev/null -w '%{http_code}' -m 5 http://127.0.0.1:8080/ 2>/dev/null)
[ "$code" = "200" ] && ok "本地服务运行中 (127.0.0.1:8080)" \
  || warn "本地服务未运行（systemctl start daimonkey 或 scripts/serve.sh）"

log ""
log "## 6b. 部署交付件"
for f in deploy/bootstrap.sh deploy/backup.sh deploy/smoke.sh \
         deploy/daimonkey.service docs/deploy.md scripts/migrate_relpath.py; do
  if [ -f "$f" ]; then
    ok "存在: $f"
  else
    fail "缺失: $f"
  fi
done

# ---------- 汇总 ----------
log ""
log "## 检查汇总"
log ""
log "- PASS: $PASS"
log "- WARN: $WARN"
log "- FAIL: $FAIL"
log ""
if [ "$FAIL" -gt 0 ]; then
  log "**结论：存在 FAIL 项，先修复再继续。**"
else
  log "**结论：无阻塞项。**"
fi
echo ""
echo "报告已写入: $REPORT"
echo "PASS=$PASS WARN=$WARN FAIL=$FAIL"
[ "$FAIL" -gt 0 ] && exit 1 || exit 0
