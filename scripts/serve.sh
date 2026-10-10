#!/usr/bin/env bash
# 启动本地服务（静态站点 + 手动更新接口）
set -euo pipefail
cd "$(dirname "$0")/.."
exec .venv/bin/python scripts/serve.py "$@"
