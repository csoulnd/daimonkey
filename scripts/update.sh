#!/usr/bin/env bash
# 手动全流程更新：采集 -> 评分 -> LRU -> 导出 API -> 构建站点
set -euo pipefail
cd "$(dirname "$0")/.."
exec .venv/bin/python -m collector --update "$@"
