# vocus 多創作者 全文搜尋

把 vocus 上多位創作者的付費專欄全部文章內文 + 留言抓下來，
存進本機 SQLite，提供網頁關鍵字搜尋（中英文、任意長度、關鍵字高亮、可依創作者篩選）。

目前收錄：美股韭菜王、Pentimetrics。

## 檔案
- `sources.py` — **創作者清單設定（要加新創作者改這裡）**
- `crawl.py`  — 爬蟲（抓文章+留言進 `vocus.db`）
- `app.py`    — 搜尋網頁（Flask）
- `cookie.txt`— 你的登入 cookie（**會過期，過期就更新這個檔**）
- `vocus.db`  — 資料庫（SQLite，每列有 source 欄標記創作者）
- `start.sh`  — 啟動網頁

## 使用
```bash
# 1) 抓資料（第一次必跑；可重複執行，已抓過的會跳過）
python3 crawl.py

# 2) 開搜尋網頁
./start.sh           # 或 python3 app.py
# 瀏覽器開 http://127.0.0.1:5000
```

## 日常更新（之後有新文章/新留言時）
**最簡單：開網頁點右上角「🔄 更新資料」按鈕**，背景自動抓、即時顯示進度，
完成後重整頁面就能搜到新內容。（若提示 cookie 過期，更新 cookie.txt 再按一次。）

更新是**聰明增量**：只抓新文章、以及留言數有變化（有人新留言）的文章，
沒變的瞬間略過，所以通常 1～2 分鐘就跑完（不是每次全部重抓）。
判斷依據是「列表的 commentCount vs 上次存的」，不是時間戳。

或用指令：
```bash
python3 crawl.py                       # 抓全部創作者（補新文章 + 更新留言）
python3 crawl.py --source pentimetrics # 只抓某一位
python3 crawl.py --comments-only       # 只更新留言
python3 crawl.py --refresh             # 強制全部重抓
```

## 新增一位創作者
1. 打開創作者的 vocus 頁面，找出其付費 salon 的 `salonId` 與本人 `userId`
   （可看頁面 __NEXT_DATA__，或請 Claude 幫忙抓）。
2. 在 `sources.py` 的 `SOURCES` 加一筆：key / name / salon_id / owner_id。
3. 執行 `python3 crawl.py --source <新key>`。
4. 重整網頁，創作者下拉選單就會多一位。

## Cookie 過期怎麼辦
網頁登入 vocus 後，從瀏覽器開發者工具複製 `cookie` 標頭（至少要有
`cf_clearance`、`id_token`、`userId`），貼進 `cookie.txt` 覆蓋即可。
cf_clearance 約幾小時～一天會失效，id_token 也有期限。

## 搜尋說明
- 子字串比對，2 個字（如「升息」「油價」）也能搜
- 創作者下拉選單：全部創作者 / 各別創作者
- 範圍切換：全部 / 只搜文章 / 只搜留言 / ⭐ 只搜作者留言（用 is_author 欄）
- 結果有來源標籤，作者本人留言標紅色
- 點文章標題進詳情頁看全文與留言，關鍵字會標亮
