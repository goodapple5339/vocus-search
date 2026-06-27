#!/usr/bin/env bash
# 產生一個可以安全分享給朋友的 zip：
# 會「排除你自己的 cookie.txt 與暫存檔」，但保留資料庫(vocus.db)讓朋友能直接搜尋。
cd "$(dirname "$0")"
OUT="vocus-search-分享版.zip"
rm -f "$OUT"

# 需要 zip 指令
if ! command -v zip >/dev/null 2>&1; then
  echo "需要 zip 指令，請先安裝：sudo apt install zip"
  exit 1
fi

zip -r "$OUT" . \
  -x "cookie.txt" \
  -x "*.log" \
  -x "update_run.log" \
  -x "__pycache__/*" \
  -x "*.db-wal" -x "*.db-shm" \
  -x "$OUT" \
  -x "打包給朋友.sh" >/dev/null

echo "✅ 已產生：$(pwd)/$OUT"
echo "   裡面不含你的 cookie，可以安全傳給朋友。"
echo "   朋友解壓縮後，照「給朋友看的說明.txt」操作即可。"
