@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo ========================================
echo   安装/重建 开发环境
echo ========================================
echo.

:: 检查Python
python --version >nul 2>&1
if errorlevel 1 (
    echo 错误: 未找到Python，请先安装Python
    pause
    exit /b 1
)

:: 删除旧环境
if exist "venv" (
    echo 删除旧的虚拟环境...
    rmdir /s /q venv
)

echo [1/2] 创建虚拟环境...
python -m venv venv

if errorlevel 1 (
    echo 创建虚拟环境失败!
    pause
    exit /b 1
)

echo [2/2] 安装依赖...
venv\Scripts\pip.exe install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple --trusted-host pypi.tuna.tsinghua.edu.cn

echo.
echo ========================================
echo   环境安装完成!
echo ========================================
echo.
echo 已安装的包:
venv\Scripts\pip.exe list
echo.
echo 使用方法:
echo   run_web.bat          - 运行本地网页
echo   build_release_web.bat - 生成 release zip（网页版）
echo.
pause
