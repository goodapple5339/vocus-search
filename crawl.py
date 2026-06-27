#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
vocus 多創作者全文爬蟲
- 抓取各創作者全部文章內文 + 全部留言(含回覆) 存進 SQLite,每列標記 source(創作者)
- 可重複執行 (resumable)：已抓過且成功的文章會自動跳過
- 創作者清單在 sources.py 設定
用法:
    python3 crawl.py                    # 抓全部創作者 (跳過已完成)
    python3 crawl.py --source pentimetrics   # 只抓某一位
    python3 crawl.py --refresh          # 強制重抓全部
    python3 crawl.py --comments-only    # 只更新留言(文章內文不動)
"""
import os, re, sys, json, time, sqlite3, html, urllib.request, urllib.error
from html.parser import HTMLParser
from sources import SOURCES

HERE = os.path.dirname(os.path.abspath(__file__))
DB   = os.path.join(HERE, "vocus.db")
COOKIE_FILE = os.path.join(HERE, "cookie.txt")

API   = "https://api.vocus.cc"
WEB   = "https://vocus.cc"
UA    = ("Mozilla/5.0 (X11; Linux aarch64) AppleWebKit/537.36 "
         "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")
SLEEP = 1.2          # 每篇之間的禮貌延遲(秒)
TIMEOUT = 30

# ---------------------------------------------------------------- HTTP
def cookie():
    with open(COOKIE_FILE, encoding="utf-8") as f:
        return f.read().strip()

def fetch(url, tries=3):
    for i in range(tries):
        req = urllib.request.Request(url, headers={
            "user-agent": UA,
            "cookie": cookie(),
            "accept": "text/html,application/json,*/*",
        })
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                return r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            if e.code in (403, 429, 503):
                wait = 5 * (i + 1)
                print(f"  ! HTTP {e.code}, 等待 {wait}s 重試…", flush=True)
                time.sleep(wait)
                continue
            raise
        except Exception as e:
            print(f"  ! {e}, 重試…", flush=True)
            time.sleep(3 * (i + 1))
    raise RuntimeError(f"fetch failed: {url}")

# ---------------------------------------------------------------- HTML -> text
class _Text(HTMLParser):
    BLOCK = {"p","div","h1","h2","h3","h4","h5","h6","br","li","tr",
             "figure","figcaption","blockquote","hr"}
    SKIP  = {"style","script","noscript"}
    def __init__(self):
        super().__init__(); self.out = []; self.skip = 0
    def handle_starttag(self, t, attrs):
        if t in self.SKIP:
            self.skip += 1
            return
        d = dict(attrs)
        if t == "img" and d.get("alt"):
            self.out.append(f"\n［圖：{d['alt']}］\n")
        if t in self.BLOCK:
            self.out.append("\n")
        if t == "li":
            self.out.append("• ")
    def handle_endtag(self, t):
        if t in self.SKIP and self.skip > 0:
            self.skip -= 1
    def handle_data(self, data):
        if self.skip == 0:
            self.out.append(data)

def html_to_text(content_html):
    p = _Text(); p.feed(content_html or "")
    t = html.unescape("".join(p.out))
    t = re.sub(r"[ \t]+\n", "\n", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()

def parse_article_page(page_html):
    m = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
                  page_html, re.S)
    if not m:
        return None
    j = json.loads(m.group(1))
    fb = j["props"]["pageProps"].get("fallback", {})
    # fallback 內任一個含 "article" 子物件的值即文章主體
    for v in fb.values():
        if isinstance(v, dict) and isinstance(v.get("article"), dict):
            return v["article"]
    # 保險：深度搜尋同時具備 content/wordsCount/title 的物件
    def find(o):
        if isinstance(o, dict):
            if "content" in o and "wordsCount" in o and "title" in o:
                return o
            for v in o.values():
                r = find(v)
                if r: return r
        elif isinstance(o, list):
            for v in o:
                r = find(v)
                if r: return r
    return find(j)

# ---------------------------------------------------------------- DB
def _add_col(con, table, col, decl):
    cols = {r[1] for r in con.execute(f"PRAGMA table_info({table})")}
    if col not in cols:
        con.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")

def db():
    con = sqlite3.connect(DB)
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript("""
    CREATE TABLE IF NOT EXISTS articles(
        id TEXT PRIMARY KEY,
        source TEXT, title TEXT, publish_at TEXT, abstract TEXT, tags TEXT,
        word_count INTEGER, comment_count INTEGER, is_pay INTEGER,
        url TEXT, body TEXT, fetched_at TEXT);
    CREATE TABLE IF NOT EXISTS comments(
        id TEXT PRIMARY KEY,
        source TEXT, article_id TEXT, parent_id TEXT, user_id TEXT, author TEXT,
        is_author INTEGER, body TEXT, like_count INTEGER, created_at TEXT);
    CREATE INDEX IF NOT EXISTS idx_c_article ON comments(article_id);
    """)
    # 舊資料庫補欄位
    _add_col(con, "comments", "user_id", "TEXT")
    _add_col(con, "articles", "source", "TEXT")
    _add_col(con, "comments", "source", "TEXT")
    _add_col(con, "comments", "is_author", "INTEGER")
    con.execute("CREATE INDEX IF NOT EXISTS idx_c_user ON comments(user_id)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_a_source ON articles(source)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_c_source ON comments(source)")
    # 舊資料(韭菜王)回填 source 與 is_author
    con.execute("UPDATE articles SET source='chivesking' WHERE source IS NULL")
    con.execute("UPDATE comments SET source='chivesking' WHERE source IS NULL")
    owner = SOURCES.get("chivesking", {}).get("owner_id")
    if owner:
        con.execute("UPDATE comments SET is_author=(user_id=?) WHERE is_author IS NULL", (owner,))
    con.commit()
    return con

# ---------------------------------------------------------------- crawl steps
def list_all_contents(salon_id):
    items, page, num = [], 1, 50
    while True:
        url = f"{API}/api/contents?num={num}&page={page}&salonId={salon_id}&sort=publishTime"
        j = json.loads(fetch(url))
        batch = j.get("contents", [])
        items += batch
        total = j.get("count", 0)
        print(f"  清單 page {page}: +{len(batch)}  累計 {len(items)}/{total}", flush=True)
        if len(items) >= total or not batch:
            break
        page += 1
        time.sleep(0.5)
    return items

def _uname(u):
    if isinstance(u, list):
        u = u[0] if u else {}
    u = u or {}
    return u.get("fullname") or u.get("username") or ""

def _uid(u):
    if isinstance(u, list):
        u = u[0] if u else {}
    return (u or {}).get("_id")

def _display(user_id, user_obj, owner_id, owner_name):
    """顯示名稱：作者本人統一標成創作者名，讀者用其暱稱"""
    if user_id == owner_id:
        return owner_name
    return _uname(user_obj) or "讀者"

def fetch_comments(con, article_id, expected_top, source, owner_id, owner_name):
    page, num, got = 1, 50, 0
    while True:
        url = (f"{API}/api/comments/v2/article/{article_id}"
               f"?num={num}&page={page}&sort=createdAt")
        try:
            j = json.loads(fetch(url))
        except Exception as e:
            print(f"    留言抓取失敗: {e}", flush=True); break
        coms = j.get("comments", [])
        if not coms:
            break
        rows = []
        for c in coms:
            cuid = _uid(c.get("user"))
            rows.append((c["_id"], source, article_id, None, cuid,
                         _display(cuid, c.get("user"), owner_id, owner_name),
                         1 if cuid == owner_id else 0,
                         c.get("msg") or "", c.get("likeCount") or 0, c.get("createdAt")))
            for r in (c.get("msgs") or []):
                ruid = r.get("userId") or _uid(r.get("user"))
                rows.append((r["_id"], source, article_id, c["_id"], ruid,
                             _display(ruid, r.get("user"), owner_id, owner_name),
                             1 if ruid == owner_id else 0,
                             r.get("msg") or "", r.get("likeCount") or 0, r.get("createdAt")))
        con.executemany(
            "INSERT OR REPLACE INTO comments "
            "(id, source, article_id, parent_id, user_id, author, is_author, body, like_count, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)", rows)
        got += len(coms)
        total_top = j.get("count", expected_top or 0)
        if got >= total_top or len(coms) < num:
            break
        page += 1
        time.sleep(0.4)
    return got

def crawl_source(con, key, src, refresh, comments_only):
    name     = src["name"]
    salon_id = src["salon_id"]
    owner_id = src["owner_id"]
    print(f"\n========== 創作者：{name} ({key}) ==========", flush=True)
    contents = list_all_contents(salon_id)
    print(f"共 {len(contents)} 筆\n", flush=True)

    done = {r[0] for r in con.execute(
        "SELECT id FROM articles WHERE body IS NOT NULL AND body!='' AND source=?",
        (key,)).fetchall()}
    # 每篇上次記錄的留言數，用來判斷是否有新留言
    stored_cc = {r[0]: r[1] for r in con.execute(
        "SELECT id, comment_count FROM articles WHERE source=?", (key,)).fetchall()}

    n_new, n_cupd, n_cskip = 0, 0, 0
    for i, it in enumerate(contents, 1):
        cid   = it.get("contentId") or it.get("_id")
        title = it.get("title", "")
        ccount= it.get("commentCount", 0)
        meta  = it.get("article", {}) or {}
        if not cid:
            continue
        is_new = cid not in done
        prefix = f"[{name} {i}/{len(contents)}] {title[:26]}"

        if not comments_only and (refresh or is_new):
            try:
                page = fetch(f"{WEB}/article/{cid}")
                art  = parse_article_page(page)
                body = html_to_text(art.get("content","")) if art else ""
                tags = ",".join(t.get("name","") if isinstance(t,dict) else str(t)
                                for t in (art.get("tags") or [])) if art else ""
                con.execute(
                    "INSERT OR REPLACE INTO articles "
                    "(id, source, title, publish_at, abstract, tags, word_count, "
                    " comment_count, is_pay, url, body, fetched_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,datetime('now'))",
                    (cid, key, title,
                     (art or it).get("publishAt") or it.get("publishAt"),
                     (art or {}).get("abstract",""), tags,
                     (art or {}).get("wordsCount", meta.get("wordsCount")),
                     ccount, 1 if it.get("isPay") else 0,
                     f"{WEB}/article/{cid}", body))
                n_new += 1
                print(f"{prefix}  ✓ 內文 {len(body)} 字", flush=True)
            except Exception as e:
                print(f"{prefix}  ✗ 內文失敗: {e}", flush=True)
        else:
            print(f"{prefix}  · 內文已存在", flush=True)

        # 留言：只有新文章、留言數有變、或 --refresh 時才重抓
        if ccount and ccount > 0:
            changed = refresh or is_new or (stored_cc.get(cid) != ccount)
            if changed:
                n = fetch_comments(con, cid, ccount, key, owner_id, name)
                con.execute("UPDATE articles SET comment_count=? WHERE id=?", (ccount, cid))
                n_cupd += 1
                old = stored_cc.get(cid)
                tag = "新增留言" if (old is not None and not is_new) else "留言"
                print(f"          {tag} {n} 主則(含回覆)  [{old}→{ccount}]", flush=True)
            else:
                n_cskip += 1

        con.commit()
        time.sleep(SLEEP if (is_new or (ccount and stored_cc.get(cid) != ccount)) else 0.05)

    print(f"  ── {name} 完成：新文章 {n_new}、留言更新 {n_cupd} 篇、留言無變化略過 {n_cskip} 篇",
          flush=True)

def main():
    refresh = "--refresh" in sys.argv
    comments_only = "--comments-only" in sys.argv
    only = None
    if "--source" in sys.argv:
        only = sys.argv[sys.argv.index("--source") + 1]
    con = db()

    keys = [only] if only else list(SOURCES.keys())
    for key in keys:
        if key not in SOURCES:
            print(f"!! 未知創作者 '{key}'，可用：{', '.join(SOURCES)}", flush=True); continue
        crawl_source(con, key, SOURCES[key], refresh, comments_only)

    print("\n========== 完成統計 ==========", flush=True)
    for key in SOURCES:
        na = con.execute("SELECT COUNT(*) FROM articles WHERE body!='' AND source=?", (key,)).fetchone()[0]
        nc = con.execute("SELECT COUNT(*) FROM comments WHERE source=?", (key,)).fetchone()[0]
        print(f"  {SOURCES[key]['name']}: 文章 {na} 篇、留言 {nc} 則", flush=True)
    print(f"  → {DB}", flush=True)

if __name__ == "__main__":
    main()
