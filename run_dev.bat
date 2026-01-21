@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo ========================================
echo   开发调试 - 运行程序
echo ========================================
echo.

:: 检查虚拟环境
if not exist "venv\Scripts\python.exe" (
    echo 错误: 虚拟环境不存在
    echo 请先运行 setup_env.bat 安装环境
    pause
    exit /b 1
)

echo 使用虚拟环境运行...
venv\Scripts\python.exe ai_word_detector.py

pause
