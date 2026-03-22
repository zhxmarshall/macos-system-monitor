#!/bin/bash
# macOS System Monitor 开发模式启动脚本
# 用法: ./run.sh

DIR="$(cd "$(dirname "$0")" && pwd)"
PYTHON="$DIR/venv/bin/python3"

if [ ! -f "$PYTHON" ]; then
    echo "首次运行，创建虚拟环境..."
    python3 -m venv "$DIR/venv"
    "$PYTHON" -m pip install -q -r "$DIR/requirements.txt"
fi

"$PYTHON" "$DIR/app.py"
