@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo ========================================
echo   Download offline semantic model
echo ========================================
echo.

if not exist "venv\Scripts\python.exe" (
  python --version >nul 2>&1
  if errorlevel 1 (
    echo Error: Python not found. Install Python or run setup_env.bat first.
    pause
    exit /b 1
  )
  set "PY=python"
) else (
  set "PY=venv\Scripts\python.exe"
)

%PY% download_semantic_model.py models\semantic

echo.
echo Done.
pause
