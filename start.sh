#!/bin/sh
# 嘉旅供应链 · 货物跟踪 — 启动脚本
# 用法: ./start.sh [端口]   默认 8765
cd "$(dirname "$0")"
exec "/Users/davis/.workbuddy/binaries/python/versions/3.13.12/bin/python3" app.py "$@"
