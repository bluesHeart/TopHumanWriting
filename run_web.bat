@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0"

echo ========================================
echo   TopHumanWriting (Offline) - 运行网页
echo ========================================
echo.

set "PY="
set "PIP="
set "MODE="

if exist "python\\python.exe" (
  set "PY=python\\python.exe"
  set "MODE=portable"
) else if exist "venv\\Scripts\\python.exe" (
  set "PY=venv\\Scripts\\python.exe"
  set "PIP=venv\\Scripts\\pip.exe"
  set "MODE=venv"
) else (
  python --version >nul 2>&1
  if errorlevel 1 (
    echo 错误: 未找到 Python
    echo 请选择一种方式：
    echo  1. 使用离线发布包（自带 python/），直接双击 run_web.bat
    echo  2. 安装 Python 后运行 setup_env.bat
    echo.
    pause
    exit /b 1
  )
  set "PY=python"
  set "PIP=python -m pip"
  set "MODE=system"
)

REM If deps are missing:
REM - portable: fail fast (offline package should already include deps)
REM - venv/system: attempt to install (may require pip access)
%PY% -c "import fastapi,uvicorn" >nul 2>&1
if errorlevel 1 (
  if "%MODE%"=="portable" (
    echo 错误: 离线包依赖不完整（fastapi/uvicorn 缺失）
    echo 请重新解压完整离线包，或联系维护者重新构建 release。
    echo.
    pause
    exit /b 1
  )
  echo 检测到缺少 FastAPI/Uvicorn，正在安装依赖...
  %PIP% install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple --trusted-host pypi.tuna.tsinghua.edu.cn
)

echo.
echo 启动本地网页: http://127.0.0.1:7860  （默认端口；若被占用会自动换端口）
echo 若未自动打开浏览器，请手动访问上面的地址。
echo 关闭窗口即可停止服务。
echo.

%PY% -m webapp.launch

if errorlevel 1 (
  echo.
  echo 启动失败：请把本窗口里的报错截图发我，或查看 webapp 目录是否被杀软拦截。
  echo.
  pause
  exit /b 1
)
