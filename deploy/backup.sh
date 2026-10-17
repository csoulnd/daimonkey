#!/usr/bin/env bash
# 生成可搬迁备份：SQLite 热备 + data/ 关键内容打包。
# 排除 dist/（可重建）、data/cache（可再生）、data/logs（诊断信息）。
#
# 用法:
#   deploy/backup.sh [--out DIR]
# 默认输出到 $HOME/daimonkey-backups/daimonkey-backup-YYYYmmdd-HHMMSS.tar.gz
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
OUT_DIR="${DAIMONKEY_BACKUP_DIR:-$HOME/daimonkey-backups}"

say()  { printf '\n== %s ==\n' "$*"; }
ok()   { printf '  [OK]   %s\n' "$*"; }
warn() { printf '  [WARN] %s\n' "$*"; }
die()  { printf '  [FAIL] %s\n' "$*" >&2; exit 1; }

while [ $# -gt 0 ]; do
  case "$1" in
    --out) OUT_DIR="${2:?--out 需要目录}"; shift 2 ;;
    -h|--help) sed -n '2,7p' "$0"; exit 0 ;;
    *) die "未知参数: $1" ;;
  esac
done

[ -f "$ROOT/data/heat_index.sqlite" ] || die "数据库不存在: $ROOT/data/heat_index.sqlite"
command -v tar >/dev/null || die "缺少 tar"
mkdir -p "$OUT_DIR"
OUT_DIR="$(cd "$OUT_DIR" && pwd)"
TS="$(date +%Y%m%d-%H%M%S)"
OUT="$OUT_DIR/daimonkey-backup-$TS.tar.gz"
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
mkdir -p "$STAGE/data"

say "1/3 SQLite 热备"
DB="$ROOT/data/heat_index.sqlite"
if command -v sqlite3 >/dev/null 2>&1; then
  sqlite3 "$DB" ".backup '$STAGE/data/heat_index.sqlite'"
else
  warn "sqlite3 CLI 不可用，改用 Python 备份 API"
  "$ROOT/.venv/bin/python" - "$DB" "$STAGE/data/heat_index.sqlite" <<'PY'
import sqlite3, sys
src = sqlite3.connect(sys.argv[1])
dst = sqlite3.connect(sys.argv[2])
with dst:
    src.backup(dst)
dst.close()
src.close()
PY
fi
"$ROOT/.venv/bin/python" - "$STAGE/data/heat_index.sqlite" <<'PY' || die "热备数据库校验失败"
import sqlite3, sys
c = sqlite3.connect(sys.argv[1])
assert c.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
assert c.execute("SELECT COUNT(*) FROM users").fetchone()[0] >= 0
c.close()
PY
ok "已热备 $DB（integrity_check ok）"

say "2/3 打包 data/ 关键内容"
copy() {
  if [ -e "$ROOT/data/$1" ]; then
    cp -a "$ROOT/data/$1" "$STAGE/data/"
  else
    warn "跳过缺失项: data/$1"
  fi
}
for item in assistant.env assistant_secret update_password.sha256 \
            update_status.json snapshots raw knowledge_search.json; do
  copy "$item"
done
shopt -s nullglob
for d in "$ROOT"/data/knowledge-repo "$ROOT"/data/knowledge-*-repo; do
  [ -d "$d" ] || continue
  cp -a "$d" "$STAGE/data/"
done
for f in "$ROOT"/data/knowledge_search-*.json; do
  cp -a "$f" "$STAGE/data/"
done
shopt -u nullglob

"$ROOT/.venv/bin/python" - "$STAGE/manifest.json" "$ROOT" "$DB" <<'PY'
import json, sqlite3, sys, time
path, root, db = sys.argv[1], sys.argv[2], sys.argv[3]
try:
    c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    counts = {t: c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
              for t in ("raw_payloads", "metrics", "news_items", "users")}
    c.close()
except Exception as e:
    counts = {"error": str(e)}
try:
    import subprocess
    commit = subprocess.check_output(["git", "-C", root, "rev-parse", "HEAD"],
                                     text=True, stderr=subprocess.DEVNULL).strip()
except Exception:
    commit = ""
json.dump({
    "format": "daimonkey-backup",
    "version": 1,
    "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    "host": __import__("socket").gethostname(),
    "install_root": root,
    "git_commit": commit,
    "counts": counts,
    "excludes": ["dist/", "data/cache/", "data/logs/"],
}, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
PY

( cd "$STAGE" && find data -type f -print0 | sort -z \
    | xargs -0 sha256sum > SHA256SUMS )
ok "已生成 manifest.json 与 SHA256SUMS（$(find "$STAGE/data" -type f | wc -l) 个文件）"

tar -czf "$OUT" -C "$STAGE" manifest.json SHA256SUMS data
chmod 600 "$OUT"

if tar -tzf "$OUT" | grep -Eq '^data/(cache|logs|dist)/'; then
  rm -f "$OUT"
  die "备份误含 cache/logs/dist，已中止"
fi

say "3/3 完成"
SIZE="$(du -h "$OUT" | awk '{print $1}')"
ok "备份文件: $OUT（$SIZE）"
echo
echo "恢复到新机:"
echo "  git clone <仓库地址> daimonkey && cd daimonkey"
echo "  sudo bash deploy/bootstrap.sh --restore $OUT --force"
echo
warn "备份含账号哈希与密钥，请按敏感文件保管（权限 600，勿入 git/公开目录）"
