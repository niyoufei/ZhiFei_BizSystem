@echo off
chcp 65001 >nul
title 青天评标系统
cd /d "%~dp0.."

:: 检测 Python
where python >nul 2>&1
if %errorlevel% neq 0 (
    echo [错误] 未找到 Python。请先安装 Python 3.10+ 并勾选 "Add to PATH"。
    echo 下载: https://www.python.org/downloads/
    pause
    exit /b 1
)

echo.
echo ========================================
echo   青天评标系统 - 一键启动
echo ========================================
echo.

:: 首次运行建议先手动执行一次: python -m pip install -r requirements.txt
:: 若希望每次启动前自动安装/更新依赖，可取消下面三行注释：
:: echo 正在检查依赖...
:: python -m pip install -r requirements.txt -q
:: if errorlevel 1 ( echo 依赖安装失败，请以管理员身份运行或手动: pip install -r requirements.txt & pause & exit /b 1 )

echo 启动服务中，请稍候...
if not defined PORT set PORT=8000
echo 启动成功后将自动打开浏览器；未打开请手动访问: http://localhost:%PORT%/
echo 关闭本窗口即停止服务。
echo.

python -m app.main
pause
