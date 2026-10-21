#!/usr/bin/env bash
# 安装并启用 systemd 定时任务（与 bootstrap.sh 解耦，默认安装不启用自动调度）。
#   每日 06:30  新闻采集（轻量）      daimonkey-news.timer
#   每周一 06:00 数据备份              daimonkey-backup.timer
#   每周一 07:30 全量更新（采集/建站）  daimonkey-update.timer
#
# 用法:
#   sudo deploy/install-timers.sh [--dry-run]
#
# 说明:
#   - 仓库可搬迁：单元里的 __INSTALL_ROOT__ 在安装时渲染为当前仓库绝对路径；
#   - 所有任务以 User=daimonkey 运行，需由 bootstrap.sh 先建好用户与目录权限；
#   - --dry-run 只渲染到临时目录并打印将执行的命令，不写 /etc、不碰 systemd。
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
UNIT_DIR="${DAIMONKEY_SYSTEMD_DIR:-/etc/systemd/system}"
DRY_RUN=0

SERVICES=(daimonkey-news.service daimonkey-update.service daimonkey-backup.service)
TIMERS=(daimonkey-news.timer daimonkey-update.timer daimonkey-backup.timer)

say()  { printf '\n== %s ==\n' "$*"; }
ok()   { printf '  [OK]   %s\n' "$*"; }
warn() { printf '  [WARN] %s\n' "$*"; }
die()  { printf '  [FAIL] %s\n' "$*" >&2; exit 1; }

while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run|-n) DRY_RUN=1; shift ;;
    -h|--help) sed -n '2,16p' "$0"; exit 0 ;;
    *) die "未知参数: $1（可用 --dry-run）" ;;
  esac
done

if [ "$DRY_RUN" -ne 1 ] && [ "$(id -u)" -ne 0 ]; then
  die "安装 systemd 单元需要 root：sudo $0"
fi
[ -x "$ROOT/.venv/bin/python" ] || warn "未找到 $ROOT/.venv/bin/python（先跑 deploy/bootstrap.sh，否则任务会失败）"
[ -f "$ROOT/data/heat_index.sqlite" ] || warn "未找到 data/heat_index.sqlite（先跑 bootstrap.sh 建库）"
command -v systemctl >/dev/null || [ "$DRY_RUN" -eq 1 ] || die "缺少 systemctl"

STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT

# 渲染占位符。ROOT 中若含 sed 特殊字符先转义（路径一般不会，但换机目录不可控）。
esc_root="${ROOT//\\/\\\\}"
esc_root="${esc_root//&/\\&}"
esc_root="${esc_root//|/\\|}"

say "1/3 渲染单元（__INSTALL_ROOT__ -> $ROOT）"
for f in "${SERVICES[@]}" "${TIMERS[@]}"; do
  src="$ROOT/deploy/$f"
  [ -f "$src" ] || die "缺少单元模板: $src"
  sed "s|__INSTALL_ROOT__|$esc_root|g" "$src" > "$STAGE/$f"
  if grep -q '__INSTALL_ROOT__' "$STAGE/$f"; then
    die "渲染后仍残留占位符: $f"
  fi
  ok "$f"
done
# 防回归：定时任务必须降权运行，不允许 root
for f in "${SERVICES[@]}"; do
  grep -qx 'User=daimonkey' "$STAGE/$f" || die "$f 缺少 User=daimonkey"
  grep -qx 'Group=daimonkey' "$STAGE/$f" || die "$f 缺少 Group=daimonkey"
  grep -qx 'Restart=no' "$STAGE/$f" || die "$f 必须显式 Restart=no（失败不重启风暴）"
done

if [ "$DRY_RUN" -eq 1 ]; then
  say "2/3 dry-run：将安装以下单元"
  for f in "${SERVICES[@]}" "${TIMERS[@]}"; do
    printf '  install -m 0644 %s/%s %s/%s\n' "$STAGE" "$f" "$UNIT_DIR" "$f"
  done
  say "3/3 dry-run：将执行的 systemd 命令"
  printf '  systemctl daemon-reload\n'
  printf '  systemctl enable --now %s\n' "${TIMERS[*]}"
  printf "  systemctl list-timers --all 'daimonkey-*'\n"
  echo
  ok "dry-run 完成（未改 /etc，未调用 systemctl）"
  exit 0
fi

mkdir -p "$UNIT_DIR"
say "2/3 安装单元到 $UNIT_DIR"
for f in "${SERVICES[@]}" "${TIMERS[@]}"; do
  install -m 0644 "$STAGE/$f" "$UNIT_DIR/$f"
  ok "$UNIT_DIR/$f"
done

say "3/3 启用定时器"
systemctl daemon-reload
systemctl enable --now "${TIMERS[@]}"
systemctl list-timers --all 'daimonkey-*' || true
echo
ok "已启用：${TIMERS[*]}"
echo "  查看日志：journalctl -u daimonkey-news -u daimonkey-update -u daimonkey-backup"
echo "  停止调度：sudo systemctl disable --now ${TIMERS[*]}"
