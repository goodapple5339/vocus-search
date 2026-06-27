#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
一鍵啟動器（跨平台）。
朋友只要雙擊對應的啟動檔（Windows: 啟動.bat / Mac: 啟動.command / Linux: start.sh），
它會自動：
  1) 檢查並安裝必要元件 (flask)
  2) 啟動搜尋網頁
  3) 自動打開瀏覽器到 http://127.0.0.1:5000
"""
import sys, os, time, subprocess, threading, webbrowser

HERE = os.path.dirname(os.path.abspath(__file__))
os.chdir(HERE)
PORT = 5000
URL = f"http://127.0.0.1:{PORT}"

def ensure_flask():
    try:
        import flask  # noqa
        return
    except ImportError:
        pass
    print("首次啟動，正在安裝必要元件 (flask)，請稍候…", flush=True)
    for args in (["-m", "pip", "install", "flask"],
                 ["-m", "pip", "install", "--user", "flask"]):
        try:
            subprocess.run([sys.executable] + args, check=True)
            import flask  # noqa
            return
        except Exception:
            continue
    print("\n⚠️ 自動安裝 flask 失敗。請手動執行：")
    print(f"   {sys.executable} -m pip install flask")
    input("按 Enter 結束…")
    sys.exit(1)

def open_browser():
    time.sleep(1.8)
    try:
        webbrowser.open(URL)
    except Exception:
        pass

def main():
    ensure_flask()
    print(f"\n搜尋網頁啟動中… 瀏覽器會自動打開：{URL}")
    print("（若沒自動打開，手動在瀏覽器輸入上面那行網址）")
    print("要關閉時：直接關掉這個視窗即可。\n")
    threading.Thread(target=open_browser, daemon=True).start()
    import app
    app.app.run(host="127.0.0.1", port=PORT, debug=False)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
    except Exception as e:
        print("\n發生錯誤：", e)
        input("按 Enter 結束…")
