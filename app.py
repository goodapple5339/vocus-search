#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
vocus 多創作者 全文搜尋 — 本機網頁介面
啟動:  python3 app.py    然後瀏覽器開 http://127.0.0.1:5000
"""
import os, re, sqlite3, html, sys, subprocess, threading
from flask import Flask, request, render_template_string, abort
from sources import SOURCES

HERE = os.path.dirname(os.path.abspath(__file__))
DB   = os.path.join(HERE, "vocus.db")
UPDATE_LOG  = os.path.join(HERE, "update_run.log")
COOKIE_FILE = os.path.join(HERE, "cookie.txt")
app  = Flask(__name__)

_proc = None                 # 目前更新中的子程序
_lock = threading.Lock()

def src_name(key):
    return SOURCES.get(key, {}).get("name", key or "")

def conn():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c

def stats():
    try:
        with conn() as c:
            a = c.execute("SELECT COUNT(*) FROM articles WHERE body!=''").fetchone()[0]
            m = c.execute("SELECT COUNT(*) FROM comments").fetchone()[0]
            return a, m
    except Exception:
        return 0, 0

# --------- 取出含關鍵字的上下文片段並標亮（純文字輸入，安全處理） ---------
def snippets(text, q, ctx=40, max_hits=3):
    if not text or not q:
        return [], 0
    low, ql = text.lower(), q.lower()
    out, start, n = [], 0, 0
    while True:
        i = low.find(ql, start)
        if i < 0:
            break
        n += 1
        if len(out) < max_hits:
            a = max(0, i - ctx); b = min(len(text), i + len(q) + ctx)
            seg = text[a:b]
            seg = html.escape(seg)
            # 在已 escape 的字串上標亮（q 也 escape 後比對）
            seg = re.sub("(" + re.escape(html.escape(q)) + ")",
                         r"<mark>\1</mark>", seg, flags=re.I)
            out.append(("…" if a > 0 else "") + seg + ("…" if b < len(text) else ""))
        start = i + len(q)
    return out, n

def highlight_full(text, q):
    esc = html.escape(text or "")
    if q:
        esc = re.sub("(" + re.escape(html.escape(q)) + ")",
                     r"<mark>\1</mark>", esc, flags=re.I)
    return esc

# ----------------------------------------------------- routes
@app.route("/")
def index():
    q = (request.args.get("q") or "").strip()
    scope = request.args.get("scope", "all")   # all / article / comment / author
    source = request.args.get("source", "")    # "" = 全部創作者，否則某 source key
    if source not in SOURCES:
        source = ""
    art_hits, com_hits = [], []
    if q:
        like = f"%{q}%"
        with conn() as c:
            if scope in ("all", "article"):
                sql = ("SELECT id,source,title,publish_at,tags,body FROM articles "
                       "WHERE body!='' AND (title LIKE ? OR body LIKE ?)")
                params = [like, like]
                if source:
                    sql += " AND source=?"; params.append(source)
                sql += " ORDER BY publish_at DESC"
                for r in c.execute(sql, params).fetchall():
                    segs, n = snippets(r["body"], q)
                    tn = r["title"].lower().count(q.lower())
                    art_hits.append(dict(id=r["id"], src=src_name(r["source"]),
                                         title=highlight_full(r["title"], q),
                                         date=(r["publish_at"] or "")[:10],
                                         tags=r["tags"] or "", segs=segs, hits=n + tn))
            if scope in ("all", "comment", "author"):
                sql = ("SELECT cm.id,cm.source,cm.article_id,cm.author,cm.is_author,"
                       "cm.body,cm.created_at,a.title "
                       "FROM comments cm LEFT JOIN articles a ON a.id=cm.article_id "
                       "WHERE cm.body LIKE ?")
                params = [like]
                if scope == "author":            # 只搜作者本人留言
                    sql += " AND cm.is_author=1"
                if source:
                    sql += " AND cm.source=?"; params.append(source)
                sql += " ORDER BY cm.created_at DESC"
                for r in c.execute(sql, params).fetchall():
                    segs, n = snippets(r["body"], q, ctx=40, max_hits=1)
                    com_hits.append(dict(article_id=r["article_id"], src=src_name(r["source"]),
                                         title=r["title"] or "(文章)",
                                         author=r["author"] or "讀者",
                                         is_author=bool(r["is_author"]),
                                         date=(r["created_at"] or "")[:10],
                                         seg=segs[0] if segs else html.escape(r["body"][:80])))
    a, m = stats()
    creators = [(k, v["name"]) for k, v in SOURCES.items()]
    return render_template_string(TPL_INDEX, q=q, scope=scope, sel=source,
                                  creators=creators, art_hits=art_hits, com_hits=com_hits,
                                  na=a, nm=m)

@app.route("/article/<aid>")
def article(aid):
    q = (request.args.get("q") or "").strip()
    with conn() as c:
        a = c.execute("SELECT * FROM articles WHERE id=?", (aid,)).fetchone()
        if not a:
            abort(404)
        coms = c.execute(
            "SELECT * FROM comments WHERE article_id=? ORDER BY "
            "CASE WHEN parent_id IS NULL THEN id ELSE parent_id END, created_at",
            (aid,)).fetchall()
    body = highlight_full(a["body"], q).replace("\n", "<br>")
    clist = [dict(author=cm["author"] or "讀者", date=(cm["created_at"] or "")[:10],
                  body=highlight_full(cm["body"], q).replace("\n", "<br>"),
                  reply=bool(cm["parent_id"]),
                  is_author=bool(cm["is_author"])) for cm in coms]
    return render_template_string(TPL_ARTICLE, a=a, body=body, coms=clist,
                                  q=q, date=(a["publish_at"] or "")[:10],
                                  srcname=src_name(a["source"]))

# ----------------------------------------------------- 資料更新（背景跑 crawl.py）
@app.route("/update", methods=["POST"])
def update():
    global _proc
    with _lock:
        if _proc and _proc.poll() is None:
            return {"ok": False, "msg": "更新已在進行中"}
        logf = open(UPDATE_LOG, "w", encoding="utf-8")
        _proc = subprocess.Popen([sys.executable, "crawl.py"], cwd=HERE,
                                 stdout=logf, stderr=subprocess.STDOUT)
    return {"ok": True, "msg": "已開始更新"}

@app.route("/update/stop", methods=["POST"])
def update_stop():
    global _proc
    killed = False
    with _lock:
        if _proc and _proc.poll() is None:
            _proc.terminate()
            try:
                _proc.wait(timeout=5)
            except Exception:
                _proc.kill()
            killed = True
    # 保險：清掉任何殘留的 crawl.py 子程序
    try:
        subprocess.run(["pkill", "-f", "crawl.py"], timeout=5)
    except Exception:
        pass
    return {"ok": True, "killed": killed}

@app.route("/update/status")
def update_status():
    running = bool(_proc and _proc.poll() is None)
    lines = []
    try:
        with open(UPDATE_LOG, encoding="utf-8") as fh:
            lines = fh.read().splitlines()
    except FileNotFoundError:
        pass
    done = (not running) and any("完成統計" in l for l in lines)
    # cookie 可能過期：大量內文失敗 / 403
    warn = any(("✗ 內文失敗" in l) or ("HTTP 403" in l) for l in lines)
    return {"running": running, "started": _proc is not None,
            "done": done, "warn": warn, "tail": "\n".join(lines[-14:])}

@app.route("/settings", methods=["GET", "POST"])
def settings():
    msg = ""
    if request.method == "POST":
        ck = (request.form.get("cookie") or "").strip()
        if ck:
            with open(COOKIE_FILE, "w", encoding="utf-8") as f:
                f.write(ck + "\n")
            msg = "✅ 已儲存！回首頁按「🔄 更新資料」就能抓最新內容了。"
        else:
            msg = "⚠️ 沒有貼上內容。"
    has = False
    try:
        has = bool(open(COOKIE_FILE, encoding="utf-8").read().strip())
    except FileNotFoundError:
        pass
    return render_template_string(TPL_SETTINGS, msg=msg, has=has)

# ----------------------------------------------------- templates
BASE_CSS = """
<style>
*{box-sizing:border-box} body{font-family:"PingFang TC","Microsoft JhengHei",
 "Noto Sans CJK TC",sans-serif;margin:0;background:#f4f5f7;color:#222;line-height:1.7}
.wrap{max-width:860px;margin:0 auto;padding:20px}
header{background:#0b8a99;color:#fff;padding:16px 0}
header .wrap{padding:0 20px;display:flex;align-items:center;gap:14px}
header h1{font-size:18px;margin:0} header .meta{font-size:12px;opacity:.85;margin-left:auto}
a{color:#0b8a99;text-decoration:none} a:hover{text-decoration:underline}
form.search{display:flex;gap:8px;margin:18px 0}
form.search input[type=text]{flex:1;padding:11px 14px;font-size:16px;border:1px solid #ccc;border-radius:8px}
form.search button{padding:11px 20px;font-size:16px;background:#0b8a99;color:#fff;border:0;border-radius:8px;cursor:pointer}
.scope{margin:-8px 0 14px;font-size:13px;color:#666}
.scope label{margin-right:14px;cursor:pointer}
.card{background:#fff;border:1px solid #e6e6e6;border-radius:10px;padding:14px 16px;margin:12px 0}
.card .t{font-size:17px;font-weight:700;margin-bottom:4px}
.card .d{font-size:12px;color:#888;margin-bottom:8px}
.seg{font-size:14px;color:#444;background:#fafafa;border-left:3px solid #d9ecef;padding:5px 10px;margin:5px 0;border-radius:4px}
mark{background:#ffe58a;padding:0 1px;border-radius:2px}
.section-title{font-size:14px;color:#888;margin:24px 0 6px;border-bottom:1px solid #ddd;padding-bottom:4px}
.tag{display:inline-block;background:#eef4f5;color:#0b8a99;font-size:11px;padding:1px 7px;border-radius:10px;margin-right:5px}
.cm{font-size:14px} .cm.reply{margin-left:24px;border-left:2px solid #eee;padding-left:10px}
.cm .who{font-size:12px;color:#999}
.author{color:#c0392b;font-weight:700}
.cm.byauthor{border-left:3px solid #c0392b;background:#fff8f5}
.src{display:inline-block;background:#0b8a99;color:#fff;font-size:11px;padding:1px 8px;border-radius:10px;margin-right:6px;vertical-align:middle}
select{padding:6px 10px;font-size:14px;border:1px solid #ccc;border-radius:8px}
#upbtn{background:#fff;color:#0b8a99;border:0;padding:5px 12px;border-radius:8px;font-size:13px;cursor:pointer;font-weight:700}
#upbtn:disabled{opacity:.6;cursor:default}
#upbox{display:none;background:#fff;border:1px solid #e6e6e6;border-radius:10px;padding:12px 16px;margin:14px 0}
#upbox .st{font-size:14px}
#stopbtn{background:#c0392b;color:#fff;border:0;padding:4px 12px;border-radius:8px;font-size:13px;cursor:pointer;margin-left:10px}
#stopbtn:disabled{opacity:.6;cursor:default}
#upbox .lg{font-size:12px;color:#666;background:#fafafa;border-radius:6px;padding:8px;margin:8px 0 0;max-height:160px;overflow:auto;white-space:pre-wrap}
.body{background:#fff;border:1px solid #e6e6e6;border-radius:10px;padding:22px 26px;font-size:16px}
.hint{color:#999;font-size:13px;text-align:center;margin:40px 0}
.count{font-size:13px;color:#666;margin:6px 0}
.backlink{font-size:13px}
</style>
"""

TPL_INDEX = """<!doctype html><html lang="zh-TW"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>vocus 全文搜尋</title>""" + BASE_CSS + """</head><body>
<header><div class="wrap"><h1>🔍 vocus 創作者全文搜尋</h1>
<span class="meta">{{na}} 篇文章 · {{nm}} 則留言 &nbsp;
<button id="upbtn" onclick="startUpdate()">🔄 更新資料</button>
<a href="/settings" style="color:#fff;margin-left:8px">⚙️ 設定</a></span></div></header>
<div class="wrap">
<div id="upbox"><b class="st"></b>
  <button id="stopbtn" onclick="stopUpdate()" style="display:none">⏹ 停止更新</button>
  <pre class="lg"></pre></div>
<script>
function startUpdate(){
  if(!confirm('開始抓取最新文章與留言？\\n會在背景執行，需要幾分鐘，完成後重整頁面即可搜到新內容。')) return;
  var box=document.getElementById('upbox'); box.style.display='block';
  box.querySelector('.st').textContent='⏳ 啟動更新中…';
  document.getElementById('upbtn').disabled=true;
  fetch('/update',{method:'POST'}).then(r=>r.json()).then(d=>{
     if(!d.ok){ box.querySelector('.st').textContent='⚠️ '+d.msg; }
     poll();
  }).catch(e=>{ box.querySelector('.st').textContent='⚠️ 無法啟動：'+e; });
}
function stopUpdate(){
  if(!confirm('確定要停止這次更新嗎？\\n已抓到的會保留，下次更新會接續未完成的部分。')) return;
  var sb=document.getElementById('stopbtn'); sb.disabled=true; sb.textContent='停止中…';
  fetch('/update/stop',{method:'POST'}).then(r=>r.json()).then(d=>{ poll(); });
}
function poll(){
  fetch('/update/status').then(r=>r.json()).then(d=>{
    var box=document.getElementById('upbox');
    var sb=document.getElementById('stopbtn');
    var msg = d.running ? '⏳ 更新中…（可離開，背景會繼續跑）'
            : (d.done ? '✅ 更新完成！重整頁面即可搜到新內容' : '⏹ 已停止（已抓到的有保留，下次更新會接續）');
    if(d.warn) msg += '　⚠️ 偵測到抓取失敗，cookie 可能已過期，請更新 cookie.txt';
    box.querySelector('.st').textContent = msg;
    box.querySelector('.lg').textContent = d.tail || '';
    sb.style.display = d.running ? 'inline-block' : 'none';
    sb.disabled=false; sb.textContent='⏹ 停止更新';
    if(d.running){ setTimeout(poll, 2500); }
    else { document.getElementById('upbtn').disabled=false; }
  });
}
// 進站時若仍有更新在跑，自動接續顯示進度
window.addEventListener('load', function(){
  fetch('/update/status').then(r=>r.json()).then(d=>{ if(d.running){ document.getElementById('upbox').style.display='block'; document.getElementById('upbtn').disabled=true; poll(); } });
});
</script>
<form class="search" method="get" action="/">
  <input type="text" name="q" value="{{q}}" placeholder="輸入關鍵字，例如：升息、油價、NVDA…" autofocus>
  <input type="hidden" name="scope" value="{{scope}}">
  <input type="hidden" name="source" value="{{sel}}">
  <button>搜尋</button>
</form>
<div class="scope">創作者：
  <select onchange="set('source',this.value)">
    <option value="" {{'selected' if not sel}}>全部創作者</option>
    {% for k,name in creators %}<option value="{{k}}" {{'selected' if sel==k}}>{{name}}</option>{% endfor %}
  </select>
  &nbsp;&nbsp;範圍：
  <label><input type="radio" name="sc" onclick="set('scope','all')" {{'checked' if scope=='all'}}> 全部</label>
  <label><input type="radio" name="sc" onclick="set('scope','article')" {{'checked' if scope=='article'}}> 只搜文章</label>
  <label><input type="radio" name="sc" onclick="set('scope','comment')" {{'checked' if scope=='comment'}}> 只搜留言</label>
  <label><input type="radio" name="sc" onclick="set('scope','author')" {{'checked' if scope=='author'}}> ⭐ 只搜作者留言</label>
</div>
<script>function set(k,v){var u=new URL(location);u.searchParams.set(k,v);location=u}</script>

{% if q %}
  {% set who = (creators|selectattr('0','equalto',sel)|map(attribute='1')|first) if sel else '全部創作者' %}
  {% if scope=='author' %}
  <div class="count">在「{{who}} 本人留言」中搜尋「{{q}}」：命中 {{com_hits|length}} 則</div>
  {% else %}
  <div class="count">{{who}}｜關鍵字「{{q}}」：文章 {{art_hits|length}} 篇、留言 {{com_hits|length}} 則命中</div>
  {% endif %}
  {% if art_hits %}<div class="section-title">📄 文章 ({{art_hits|length}})</div>{% endif %}
  {% for h in art_hits %}
    <div class="card">
      <div class="t">{% if not sel %}<span class="src">{{h.src}}</span>{% endif %}<a href="/article/{{h.id}}?q={{q}}">{{h.title|safe}}</a></div>
      <div class="d">{{h.date}} · 命中 {{h.hits}} 處
        {% for t in h.tags.split(',') if t %} <span class="tag">{{t}}</span>{% endfor %}</div>
      {% for s in h.segs %}<div class="seg">{{s|safe}}</div>{% endfor %}
    </div>
  {% endfor %}
  {% if com_hits %}<div class="section-title">{{'⭐ 作者留言' if scope=='author' else '💬 留言'}} ({{com_hits|length}})</div>{% endif %}
  {% for h in com_hits %}
    <div class="card cm {{'byauthor' if h.is_author}}">
      <div class="who">{% if not sel %}<span class="src">{{h.src}}</span>{% endif %}<span class="{{'author' if h.is_author}}">{{h.author}}</span> · {{h.date}} · 於〈<a href="/article/{{h.article_id}}?q={{q}}">{{h.title}}</a>〉</div>
      <div class="seg">{{h.seg|safe}}</div>
    </div>
  {% endfor %}
  {% if not art_hits and not com_hits %}<div class="hint">沒有找到「{{q}}」</div>{% endif %}
{% else %}
  <div class="hint">輸入關鍵字開始搜尋全部文章與留言</div>
{% endif %}
</div></body></html>"""

TPL_ARTICLE = """<!doctype html><html lang="zh-TW"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{{a['title']}}</title>""" + BASE_CSS + """</head><body>
<header><div class="wrap"><h1>📄 {{srcname}}</h1>
<span class="meta"><a href="/?q={{q}}&scope=all" style="color:#fff">← 回搜尋</a></span></div></header>
<div class="wrap">
  <div class="body">
    <h2 style="margin-top:0">{{a['title']}}</h2>
    <div class="d" style="color:#888;font-size:13px;margin-bottom:14px">
      {{date}} · {{a['word_count'] or '?'}} 字 ·
      <a href="{{a['url']}}" target="_blank">原文連結 ↗</a></div>
    <div>{{body|safe}}</div>
  </div>
  {% if coms %}
  <div class="section-title">💬 留言 ({{coms|length}})</div>
  {% for c in coms %}
    <div class="card cm {{'reply' if c.reply}} {{'byauthor' if c.is_author}}">
      <div class="who"><span class="{{'author' if c.is_author}}">{{c.author}}</span> · {{c.date}}{{' · 回覆' if c.reply}}</div>
      <div>{{c.body|safe}}</div>
    </div>
  {% endfor %}
  {% endif %}
</div></body></html>"""

TPL_SETTINGS = """<!doctype html><html lang="zh-TW"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>設定 - vocus 全文搜尋</title>""" + BASE_CSS + """</head><body>
<header><div class="wrap"><h1>⚙️ 設定</h1>
<span class="meta"><a href="/" style="color:#fff">← 回搜尋</a></span></div></header>
<div class="wrap">
  <div class="body">
    <h2 style="margin-top:0">登入 Cookie（更新資料時才需要）</h2>
    <p style="font-size:14px;color:#555">
      目前狀態：{{ '✅ 已設定（要換新的就貼上覆蓋）' if has else '❌ 尚未設定' }}
    </p>
    {% if msg %}<p style="font-size:15px;font-weight:700">{{msg}}</p>{% endif %}
    <form method="post">
      <textarea name="cookie" placeholder="把整段 cookie 貼在這裡…"
        style="width:100%;height:120px;font-size:13px;padding:10px;border:1px solid #ccc;border-radius:8px"></textarea>
      <div style="margin-top:10px"><button style="background:#0b8a99;color:#fff;border:0;padding:10px 22px;border-radius:8px;font-size:15px;cursor:pointer">儲存</button></div>
    </form>
    <hr style="margin:22px 0;border:none;border-top:1px solid #eee">
    <h3>怎麼拿到 cookie？（更新才需要，只想搜尋可略過）</h3>
    <ol style="font-size:14px;color:#444;line-height:2">
      <li>用電腦瀏覽器登入 <b>vocus.cc</b>（要有訂閱該創作者才抓得到付費內容）</li>
      <li>按鍵盤 <b>F12</b> 打開開發者工具 → 上方切到 <b>Network（網路）</b> 分頁</li>
      <li>重新整理頁面，點任一個請求 → 找到 <b>Request Headers</b> 裡的 <b>cookie</b> 那一整行</li>
      <li>整行複製，貼到上面框框，按「儲存」</li>
    </ol>
    <p style="font-size:13px;color:#888">
      註：cookie 大約一天會過期，更新時若提示過期，就重做一次以上步驟。<br>
      若只是要搜尋現有資料，<b>完全不需要設定 cookie</b>。
    </p>
  </div>
</div></body></html>"""

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)
