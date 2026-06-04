#!/usr/bin/env bash
# 一次性构建后台 Tailwind CSS（使用 npx，不写入 package.json 依赖）
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
mkdir -p static/css
npx --yes tailwindcss@3.4.17 \
  -i static/css/admin-input.css \
  -o static/css/admin.css \
  --minify \
  --content "./templates/**/*.html" "./htmlpdf/templates/**/*.html"
echo "Wrote static/css/admin.css"
