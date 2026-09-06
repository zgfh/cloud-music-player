#!/bin/bash
set -euo pipefail

cd "$(dirname "$0")/.."

TARSIER_PYTHON="${TARSIER_PYTHON:-/Users/zzg/.local/share/uv/tools/tarsier-ai/bin/python}"
WORKSPACE="build/flutter/ios/Runner.xcworkspace"

if [ ! -x "$TARSIER_PYTHON" ]; then
    echo "❌ 未找到 Tarsier-AI Python: $TARSIER_PYTHON" >&2
    echo "安装命令: uv tool install 'tarsier-ai==0.6.0'" >&2
    exit 1
fi

if [ ! -d "$WORKSPACE" ]; then
    echo "❌ 未找到 Xcode workspace: $WORKSPACE" >&2
    echo "请先运行: flet build ipa --yes --no-rich-output" >&2
    exit 1
fi

exec "$TARSIER_PYTHON" scripts/tarsier_xcode_deploy.py \
    --workspace "$WORKSPACE" \
    "$@"
