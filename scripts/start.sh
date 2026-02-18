#!/usr/bin/env bash
# 一键启动青天评标系统，浏览器打开 http://localhost:8000/ 即可使用

cd "$(dirname "$0")/.."
echo "启动青天评标系统..."
echo "浏览器打开: http://localhost:8000/"
echo "按 Ctrl+C 停止"
if [ -x ".venv/bin/python" ]; then
  exec .venv/bin/python -m app.main
else
  exec python3 -m app.main
fi
