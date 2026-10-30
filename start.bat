@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

rem ============================================================
rem  ZJAI-Agent 安装 / 启动脚本（Windows）
rem  首次运行会自动装依赖，之后每次运行直接启动。
rem  可选：先写好 config\models.json 可跳过安装向导的模型配置步骤。
rem ============================================================

rem 注意：不要 chcp 65001。CP65001 在 Windows 7 + Python 3.8 下不稳定。
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"

echo [ZJAI] 检查 Python...
set "PY="
for %%P in (python.exe) do (
    if exist "%%~$PATH:P" set "PY=python"
)
if not defined PY (
    echo [ZJAI] 未找到 python，请先安装 Python 3.8 及以上版本
    echo        下载地址: https://www.python.org/downloads/windows/
    echo        安装时务必勾选 "Add Python to PATH"
    pause
    exit /b 1
)

for /f "tokens=2 delims= " %%V in ('python -V 2^>^&1') do set "PYVER=%%V"
echo [ZJAI] 使用 Python !PYVER!

rem ---------- 虚拟环境 ----------
if not exist ".venv\Scripts\python.exe" (
    echo [ZJAI] 创建虚拟环境 .venv
    python -m venv .venv
    if errorlevel 1 (
        echo [ZJAI] 虚拟环境创建失败，改用系统 Python
        set "PYRUN=python"
    ) else (
        set "PYRUN=.venv\Scripts\python.exe"
    )
) else (
    set "PYRUN=.venv\Scripts\python.exe"
)

rem ---------- 依赖 ----------
if not exist ".venv\.deps-ok" (
    echo [ZJAI] 安装依赖，请稍候...
    "%PYRUN%" -m pip install --upgrade pip >nul 2>&1
    "%PYRUN%" -m pip install -r requirements.txt
    if errorlevel 1 (
        echo.
        echo [ZJAI] 依赖安装失败。
        echo        国内网络可换用镜像后重试:
        echo        "%PYRUN%" -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
        pause
        exit /b 1
    )
    echo ok > ".venv\.deps-ok"
    echo [ZJAI] 依赖安装完成
)

rem ---------- 密钥 ----------
if not exist ".env" (
    echo [ZJAI] 生成 session 密钥
    for /f "delims=" %%K in ('"%PYRUN%" -c "import secrets; print('ZJAI_SECRET_KEY='+secrets.token_hex(32))"') do set "SECRETLINE=%%K"
    if defined SECRETLINE (
        echo # ZJAI-Agent 环境变量，由 start.bat 生成> .env
        echo !SECRETLINE!>> .env
    )
)
if exist ".env" (
    for /f "usebackq tokens=1,* delims==" %%A in (".env") do (
        if /i "%%A"=="ZJAI_SECRET_KEY" set "ZJAI_SECRET_KEY=%%B"
    )
)
if not defined ZJAI_SECRET_KEY (
    echo [ZJAI] 警告: 未设置 ZJAI_SECRET_KEY，使用默认值（不建议用于对外服务）
)

rem ---------- 配置提示 ----------
if not exist "config\models.json" (
    echo.
    echo [ZJAI] 尚未配置模型 API。可以：
    echo        1^) 启动后在浏览器里用安装向导填写
    echo        2^) 预置配置: copy config\models.example.json config\models.json 后编辑
    echo.
)

rem ---------- 启动 ----------
echo [ZJAI] 启动服务，浏览器访问 http://127.0.0.1:5000
echo [ZJAI] 按 Ctrl+C 停止
echo.
"%PYRUN%" -u run.py

echo.
echo [ZJAI] 服务已停止。
pause
endlocal
