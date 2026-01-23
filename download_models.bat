@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo ========================================
echo   Download offline semantic model
echo ========================================
echo.

if not exist "venv\Scripts\python.exe" (
  echo Error: venv not found. Run setup_env.bat first.
  pause
  exit /b 1
)

venv\Scripts\python.exe download_semantic_model.py models\semantic

echo.
echo Done.
pause

