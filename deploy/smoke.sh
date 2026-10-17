#!/usr/bin/env bash
# 部署自测：在线检查 + 搬迁演练 + systemd 重启演练。
#
# 用法:
#   deploy/smoke.sh                # 仅在线检查（服务/账号/溯源/知识库/测试/verify）
#   deploy/smoke.sh --relocation   # 备份 -> 新目录 bootstrap --restore -> 临时实例全检
#   deploy/smoke.sh --restart      # systemctl restart 后服务自愈
#   deploy/smoke.sh --all          # 在线 + 搬迁 + 重启（D6 验收）
# 退出码 0 = 全部通过。
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
BASE="http://127.0.0.1:8080"
DRILL_PORT="${SMOKE_DRILL_PORT:-18080}"
DO_RELOCATION=0
DO_RESTART=0
KEEP=0

PASS=0
FAIL=0
TMPDIR_SMOKE=""
SERVER_PID=""

say()  { printf '\n== %s ==\n' "$*"; }
pass() { PASS=$((PASS+1)); printf '  [PASS] %s\n' "$*"; }
fail() { FAIL=$((FAIL+1)); printf '  [FAIL] %s\n' "$*"; }
warn() { printf '  [WARN] %s\n' "$*"; }

usage() { sed -n '2,10p' "$0"; }

while [ $# -gt 0 ]; do
  case "$1" in
    --base) BASE="${2:?--base 需要 URL}"; shift 2 ;;
    --relocation) DO_RELOCATION=1; shift ;;
    --restart) DO_RESTART=1; shift ;;
    --all) DO_RELOCATION=1; DO_RESTART=1; shift ;;
    --keep) KEEP=1; shift ;;
    --drill-port) DRILL_PORT="${2:?--drill-port 需要端口}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "未知参数: $1" >&2; exit 2 ;;
  esac
done
BASE="${BASE%/}"

cleanup() {
  if [ -n "$SERVER_PID" ] && kill -0 "$SERVER_PID" 2>/dev/null; then
    kill "$SERVER_PID" 2>/dev/null || true
    wait "$SERVER_PID" 2>/dev/null || true
  fi
  if [ -n "$TMPDIR_SMOKE" ] && [ "$KEEP" = 0 ]; then
    rm -rf "$TMPDIR_SMOKE"
  elif [ -n "$TMPDIR_SMOKE" ]; then
    echo "  演练目录保留: $TMPDIR_SMOKE"
  fi
}
trap cleanup EXIT

http_ok() {
  curl -fsS -o /dev/null --max-time 5 "$1" 2>/dev/null
}

run_tests() {
  local root="$1"
  ( cd "$root" && .venv/bin/python -m unittest discover -s tests ) \
    >/tmp/daimonkey-smoke-unittest.log 2>&1
}

run_node_tests() {
  local root="$1"
  if command -v node >/dev/null 2>&1; then
    ( cd "$root" && node tests/test_knowledge_search.cjs \
      && node tests/test_knowledge_lazy.cjs ) \
      >/tmp/daimonkey-smoke-node.log 2>&1
  else
    return 0
  fi
}

run_verify() {
  local root="$1" base="$2"
  ( cd "$root" && .venv/bin/python scripts/verify.py --base "$base" ) \
    >/tmp/daimonkey-smoke-verify.log 2>&1
}

user_count() {
  "$1/.venv/bin/python" - "$1/data/heat_index.sqlite" <<'PY'
import sqlite3, sys
c = sqlite3.connect(sys.argv[1])
print(c.execute("SELECT COUNT(*) FROM users").fetchone()[0])
c.close()
PY
}

trace_check() {
  local root="$1" base="$2"
  local rid
  rid="$( cd "$root" && .venv/bin/python - "$base" <<'PY'
import gzip, hashlib, json, sqlite3, sys, urllib.request
from collector.config import DATA_DIR, DB_PATH, resolve_data_path
c = sqlite3.connect(DB_PATH)
c.row_factory = sqlite3.Row
row = c.execute(
    "SELECT rp.id, rp.body_path, rp.body_sha256 FROM raw_payloads rp"
    " JOIN metrics m ON m.raw_payload_id=rp.id"
    " WHERE rp.body_path IS NOT NULL AND rp.body_evicted_at IS NULL"
    " ORDER BY rp.id DESC LIMIT 1").fetchone()
if not row:
    raise SystemExit("没有可用的非驱逐溯源样本")
data = gzip.decompress(resolve_data_path(row["body_path"], DATA_DIR).read_bytes())
if hashlib.sha256(data).hexdigest() != row["body_sha256"]:
    raise SystemExit(f"raw#{row['id']} SHA256 不匹配")
with urllib.request.urlopen(f"{sys.argv[1]}/api/v1/raw/{row['id']}.json",
                            timeout=10) as r:
    d = json.load(r)
if d.get("raw_body") is None or not d.get("extracted"):
    raise SystemExit(f"raw#{row['id']} API 溯源链不完整")
c.close()
print(row["id"])
PY
)" || { fail "溯源链（原始体+SHA256+API）"; return; }
  pass "溯源链完整（raw#$rid）"
}

online_checks() {
  local base="$1" root="$2"
  say "在线检查 ($base)"
  if http_ok "$base/"; then pass "首页 200"; else fail "首页不可达"; fi
  if curl -fsS --max-time 5 "$base/api/v1/assistant/session" 2>/dev/null \
       | grep -q '"authed"'; then
    pass "登录状态接口可用"
  else
    fail "登录状态接口异常"
  fi

  local n
  n="$(user_count "$root")" || n=""
  if [ -n "$n" ] && [ "$n" -ge 1 ]; then pass "账号表可读（$n 个账号）"
  elif [ -n "$n" ]; then warn "账号表可读但为空（旧密码过渡模式）"
  else fail "账号表不可读"; fi

  trace_check "$root" "$base"

  if [ -f "$root/data/knowledge_search.json" ] \
     || ls "$root"/data/knowledge_search-*.json >/dev/null 2>&1; then
    pass "知识库搜索快照存在"
  else
    fail "知识库搜索快照缺失"
  fi
  if [ -f "$root/dist/knowledge/index.html" ] \
     || ls "$root"/dist/knowledge-*/index.html >/dev/null 2>&1; then
    pass "知识库页面已构建"
  else
    fail "知识库页面缺失"
  fi

  if run_tests "$root"; then pass "Python 单元测试"; else fail "Python 单元测试（/tmp/daimonkey-smoke-unittest.log）"; fi
  if run_node_tests "$root"; then pass "Node 测试"; else fail "Node 测试（/tmp/daimonkey-smoke-node.log）"; fi
  if run_verify "$root" "$base"; then pass "verify.py 全量验收"; else fail "verify.py 未全绿（/tmp/daimonkey-smoke-verify.log）"; fi
}

wait_http() {
  local url="$1"
  for _ in $(seq 1 40); do
    http_ok "$url" && return 0
    sleep 0.5
  done
  return 1
}

relocation_drill() {
  say "搬迁演练（备份 -> 新目录 bootstrap --restore -> 临时实例）"
  [ -x "$ROOT/deploy/bootstrap.sh" ] || { fail "bootstrap.sh 不可执行"; return; }
  [ -x "$ROOT/deploy/backup.sh" ] || { fail "backup.sh 不可执行"; return; }

  TMPDIR_SMOKE="$(mktemp -d)"
  local target="$TMPDIR_SMOKE/daimonkey"
  local backups="$TMPDIR_SMOKE/backups"
  mkdir -p "$backups"

  if ! "$ROOT/deploy/backup.sh" --out "$backups" >"$TMPDIR_SMOKE/backup.log" 2>&1; then
    fail "备份失败（$TMPDIR_SMOKE/backup.log）"
    return
  fi
  local backup
  backup="$(ls -t "$backups"/daimonkey-backup-*.tar.gz 2>/dev/null | head -1)"
  [ -n "$backup" ] || { fail "未生成备份文件"; return; }
  pass "备份生成（$(du -h "$backup" | awk '{print $1}')）"

  # 干净工作区优先用 git clone 模拟换机；有未提交改动时复制工作树（覆盖当前代码）
  if git -C "$ROOT" rev-parse --git-dir >/dev/null 2>&1 \
     && [ -z "$(git -C "$ROOT" status --porcelain 2>/dev/null)" ]; then
    git clone -q "$ROOT" "$target" || { fail "git clone 失败"; return; }
    pass "新目录 git clone"
  else
    mkdir -p "$target"
    tar -C "$ROOT" --exclude=.git --exclude=.venv --exclude=data \
        --exclude=dist --exclude=__pycache__ --exclude='*.pyc' -cf - . \
      | tar -C "$target" -xf - || { fail "工作树复制失败"; return; }
    warn "工作区有未提交改动，演练改用工作树快照"
  fi

  local src_users
  src_users="$(user_count "$ROOT")"

  if ! "$target/deploy/bootstrap.sh" --restore "$backup" --no-service \
        --skip-system-deps --host 127.0.0.1 --port "$DRILL_PORT" \
        --bin-dir "$target/.local/bin" >"$TMPDIR_SMOKE/bootstrap.log" 2>&1; then
    fail "新目录 bootstrap --restore 失败（$TMPDIR_SMOKE/bootstrap.log）"
    tail -20 "$TMPDIR_SMOKE/bootstrap.log" | sed 's/^/     /'
    return
  fi
  pass "新目录 bootstrap --restore 完成"

  local db_abs
  db_abs="$( "$target/.venv/bin/python" - "$target/data/heat_index.sqlite" <<'PY'
import sqlite3, sys
c = sqlite3.connect(sys.argv[1])
print(c.execute("SELECT COUNT(*) FROM raw_payloads WHERE body_path LIKE '/%'").fetchone()[0])
c.close()
PY
)"
  [ "$db_abs" = "0" ] && pass "恢复后 body_path 无绝对路径" || fail "恢复后仍有 $db_abs 条绝对路径"

  local tgt_users
  tgt_users="$(user_count "$target")"
  [ "$tgt_users" = "$src_users" ] \
    && pass "账号恢复一致（$tgt_users 个）" \
    || fail "账号数量不一致（源 $src_users / 目标 $tgt_users）"

  ( cd "$target" && exec .venv/bin/python scripts/serve.py --host 127.0.0.1 \
      --port "$DRILL_PORT" ) >"$TMPDIR_SMOKE/serve.log" 2>&1 &
  SERVER_PID=$!
  local drill_base="http://127.0.0.1:$DRILL_PORT"
  if ! wait_http "$drill_base/"; then
    fail "临时实例启动失败（$TMPDIR_SMOKE/serve.log）"
    tail -20 "$TMPDIR_SMOKE/serve.log" | sed 's/^/     /'
    return
  fi
  pass "临时实例首页 200"

  # 登录可用性：临时建一个演练账号，登录成功后删除（只动演练副本）
  local smoke_user="smoke_$(date +%s)_$RANDOM"
  if ( cd "$target" && .venv/bin/python - "$smoke_user" <<'PY'
import sys
from collector import users
raise SystemExit(0 if users.create_user(sys.argv[1], "smoke-pass-123") else 1)
PY
  ); then
    local code
    code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 -X POST \
      -H 'Content-Type: application/json' \
      -d "{\"username\":\"$smoke_user\",\"password\":\"smoke-pass-123\"}" \
      "$drill_base/api/v1/assistant/login")"
    [ "$code" = "200" ] && pass "登录可用（临时账号）" || fail "登录失败（HTTP $code）"
    ( cd "$target" && .venv/bin/python - "$smoke_user" <<'PY'
import sys
from collector import db
with db.conn() as c:
    c.execute("DELETE FROM users WHERE username=?", (sys.argv[1],))
PY
    ) || true
  else
    fail "演练账号创建失败"
  fi

  trace_check "$target" "$drill_base"
  if [ -f "$target/data/knowledge_search.json" ]; then
    pass "知识库快照已恢复"
  else
    fail "知识库快照未恢复"
  fi
  if run_tests "$target"; then pass "恢复副本单元测试"; else fail "恢复副本单元测试"; fi
  if run_verify "$target" "$drill_base"; then pass "恢复副本 verify.py"; else fail "恢复副本 verify.py"; fi

  kill "$SERVER_PID" 2>/dev/null || true
  wait "$SERVER_PID" 2>/dev/null || true
  SERVER_PID=""
}

restart_drill() {
  say "重启演练（systemd restart 后自愈）"
  if ! command -v systemctl >/dev/null 2>&1 \
     || ! systemctl is-active --quiet daimonkey 2>/dev/null; then
    fail "daimonkey.service 未由 systemd 运行（先执行 bootstrap.sh）"
    return
  fi
  local before after
  before="$(systemctl show -p MainPID --value daimonkey)"
  if ! systemctl restart daimonkey; then
    fail "systemctl restart 失败"
    return
  fi
  if wait_http "$BASE/"; then pass "重启后首页 200"; else fail "重启后服务不可达"; fi
  after="$(systemctl show -p MainPID --value daimonkey)"
  if [ "$after" != "0" ] && [ "$after" != "$before" ]; then
    pass "主进程已更换（$before -> $after）"
  else
    fail "主进程未变化（$before -> $after）"
  fi
  if systemctl is-active --quiet daimonkey; then pass "systemd 状态 active"; else fail "systemd 状态异常"; fi
  if journalctl -u daimonkey --since "-3 min" --no-pager 2>/dev/null \
       | grep -Eq "服务已启动|Started daimonkey"; then
    pass "journal 可查启动日志"
  else
    warn "journal 未匹配到启动日志（不影响服务可用性）"
  fi
}

# ---------------------------------------------------------------- 主流程
if [ "$DO_RELOCATION" = 0 ] && [ "$DO_RESTART" = 0 ]; then
  online_checks "$BASE" "$ROOT"
else
  online_checks "$BASE" "$ROOT"
  [ "$DO_RELOCATION" = 1 ] && relocation_drill
  [ "$DO_RESTART" = 1 ] && restart_drill
fi

echo
TOTAL=$((PASS+FAIL))
if [ "$FAIL" -eq 0 ]; then
  echo "部署自测结果: $PASS/$TOTAL 全绿"
  exit 0
else
  echo "部署自测结果: $PASS/$TOTAL 通过，$FAIL 项失败"
  exit 1
fi
