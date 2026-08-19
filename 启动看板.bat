@echo off
chcp 65001 >nul
echo ==========================================
echo  A股大机构持仓跟踪系统 - Streamlit 看板
echo ==========================================
echo.

set PYTHON=D:\KimiData\daimon-share\daimon\runtime\python\.venv\Scripts\python.exe
set SCRIPT_DIR=%~dp0

if not exist "%PYTHON%" (
    echo [错误] 找不到 Python: %PYTHON%
    echo 请确认 Kimi 虚拟环境已正确安装依赖。
    pause
    exit /b 1
)

echo [1/2] 使用虚拟环境 Python: %PYTHON%
echo [2/2] 启动看板...
echo.

cd /d "%SCRIPT_DIR%"
"%PYTHON%" -m streamlit run dashboard\app.py

pause
