#!/bin/bash
# Mac 雙擊啟動。若雙擊無反應，對著檔案按右鍵 → 打開。
cd "$(dirname "$0")"
echo "============================================"
echo "  vocus 全文搜尋 - 啟動中"
echo "============================================"
if command -v python3 >/dev/null 2>&1; then
  python3 run.py
else
  echo ""
  echo "[!] 找不到 python3。請先安裝 Python："
  echo "    https://www.python.org/downloads/macos/"
  echo ""
  read -p "按 Enter 結束…"
fi
