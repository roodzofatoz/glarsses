#!/bin/bash
# Builds the plugin and packages release/decky-glarsses.zip (the asset install.sh downloads).
set -euo pipefail
cd "$(dirname "$0")/.."
npm ci --no-audit --no-fund >/dev/null 2>&1 || npm install --no-audit --no-fund >/dev/null
npm run build >/dev/null
VERSION="$(python3 -I -c 'import json; print(json.load(open("package.json"))["version"])')"
rm -rf release && mkdir -p release/stage/decky-glarsses/dist
cp dist/index.js release/stage/decky-glarsses/dist/
cp -r main.py plugin.json package.json LICENSE README.md driver release/stage/decky-glarsses/
find release/stage -name __pycache__ -prune -exec rm -rf {} +
(cd release/stage && python3 -I -m zipfile -c ../decky-glarsses.zip decky-glarsses)
rm -rf release/stage
echo "release/decky-glarsses.zip (v$VERSION)"
