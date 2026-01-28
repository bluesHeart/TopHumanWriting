@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo ========================================
echo   Download offline LLM assets (llama.cpp + 3B GGUF)
echo ========================================
echo.

if not exist "venv\\Scripts\\python.exe" (
  python --version >nul 2>&1
  if errorlevel 1 (
    echo Error: Python not found. Install Python or run setup_env.bat first.
    pause
    exit /b 1
  )
  set "PY=python"
) else (
  set "PY=venv\\Scripts\\python.exe"
)

%PY% download_llama_server.py models\\llm
if errorlevel 1 (
  echo.
  echo Failed to download llama-server.
  pause
  exit /b 1
)

%PY% download_llm_model.py models\\llm
if errorlevel 1 (
  echo.
  echo Failed to download the 3B GGUF model.
  pause
  exit /b 1
)

echo.
echo Done.
pause
