#!/usr/bin/env bash
# 运维 agent 降权回滚：把 daimonkey.service 改回 root 并重启。
# 仅用于降权后站点不可用（权限/属主/SSH 问题）时的快速恢复。
# 用法: sudo bash deploy/ops-rollback.sh [--keep-owner]
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UNIT_SRC="$ROOT/deploy/daimonkey.service"
UNIT_DST="/etc/systemd/system/daimonkey.service"
KEEP_OWNER=0
[ "${1:-}" = "--keep-owner" ] && KEEP_OWNER=1

[ "$(id -u)" -eq 0 ] || { echo "需要 root: sudo bash deploy/ops-rollback.sh" >&2; exit 1; }
[ -f "$UNIT_SRC" ] || { echo "缺少 $UNIT_SRC" >&2; exit 1; }

TMP="$(mktemp)"
sed -e "s|__INSTALL_ROOT__|$ROOT|g" -e "s|__RUN_AS__|root|g" \
    -e "s|__HOST__|0.0.0.0|g" -e "s|__PORT__|8080|g" "$UNIT_SRC" > "$TMP"
install -m 644 "$TMP" "$UNIT_DST"
rm -f "$TMP"
systemctl daemon-reload
systemctl restart daimonkey
sleep 1
systemctl is-active --quiet daimonkey \
  && echo "[OK] 已回滚为 User=root，服务已重启" \
  || { journalctl -u daimonkey -n 30 --no-pager; exit 1; }

if [ "$KEEP_OWNER" = 0 ]; then
  echo "提示: 仓库/data/dist 仍属 daimonkey 用户；root 服务可读写，通常无需改回。"
  echo "      如需完全还原: chown -R root:root '$ROOT'"
fi
