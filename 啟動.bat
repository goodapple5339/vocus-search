@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ============================================
echo   vocus 全文搜尋 - 啟動中
echo ============================================
where python >nul 2>nul
if %errorlevel%==0 (
    python run.py
    goto end
)
where py >nul 2>nul
if %errorlevel%==0 (
    py run.py
    goto end
)
echo.
echo [!] 找不到 Python。請先安裝 Python：
echo     https://www.python.org/downloads/
echo     安裝時務必勾選 "Add Python to PATH"
echo.
pause
:end
pause
