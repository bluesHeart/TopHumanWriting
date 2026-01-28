@echo off
setlocal EnableExtensions EnableDelayedExpansion
chcp 65001 >nul
cd /d "%~dp0"

echo ========================================
echo   Build Release ^(Web, no desktop EXE^)
echo ========================================
echo.

REM NOTE:
REM This script generates a fully offline "unzip & run" web release:
REM - release/<version_folder>/  (includes python runtime + deps + models)
REM - release/<version_folder>.zip

REM Resolve python
if exist "venv\\Scripts\\python.exe" (
  set "PY=venv\\Scripts\\python.exe"
) else (
  python --version >nul 2>&1
  if errorlevel 1 (
    echo Error: Python not found. Install Python first.
    pause
    exit /b 1
  )
  set "PY=python"
)

for /f "usebackq delims=" %%V in (`%PY% -c "import version; print(getattr(version,'VERSION','0.0.0'))"`) do set "VER=%%V"

for /f "usebackq delims=" %%P in (`%PY% -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}')"`) do set "PYVER=%%P"
for /f "usebackq delims=" %%T in (`%PY% -c "import sys; print(f'python{sys.version_info.major}{sys.version_info.minor}')"`) do set "PYTAG=%%T"
set "PYZIP=%PYTAG%.zip"
set "PYPTH=%PYTAG%._pth"
set "EMBED_ZIP=python-%PYVER%-embed-amd64.zip"

set "PKG_NAME=TopHumanWriting_%VER%_offline"
set "OUT_DIR=release\\%PKG_NAME%"
set "OUT_ZIP=release\\%PKG_NAME%.zip"

if not exist "release" mkdir "release" >nul 2>&1

echo Output folder: %OUT_DIR%
echo Output zip:    %OUT_ZIP%
echo.

if exist "%OUT_DIR%" (
  echo Removing old folder...
  rmdir /s /q "%OUT_DIR%"
)
if exist "%OUT_ZIP%" (
  echo Removing old zip...
  del /f /q "%OUT_ZIP%"
)

mkdir "%OUT_DIR%" >nul 2>&1

echo Copying files...
copy /y "LICENSE" "%OUT_DIR%\\LICENSE" >nul
copy /y "README.md" "%OUT_DIR%\\README.md" >nul
copy /y "requirements.txt" "%OUT_DIR%\\requirements.txt" >nul
copy /y "run_web.bat" "%OUT_DIR%\\run_web.bat" >nul
copy /y "setup_env.bat" "%OUT_DIR%\\setup_env.bat" >nul
if exist "TopHumanWriting.vbs" copy /y "TopHumanWriting.vbs" "%OUT_DIR%\\TopHumanWriting.vbs" >nul
copy /y "ai_word_detector.py" "%OUT_DIR%\\ai_word_detector.py" >nul
copy /y "i18n.py" "%OUT_DIR%\\i18n.py" >nul
copy /y "version.py" "%OUT_DIR%\\version.py" >nul
if exist "UX_SPEC_WEB.md" copy /y "UX_SPEC_WEB.md" "%OUT_DIR%\\UX_SPEC_WEB.md" >nul

robocopy "webapp" "%OUT_DIR%\\webapp" /e /ndl /nfl /njh /njs >nul
robocopy "aiwd" "%OUT_DIR%\\aiwd" /e /ndl /nfl /njh /njs >nul
robocopy "locales" "%OUT_DIR%\\locales" /e /ndl /nfl /njh /njs >nul
robocopy "word_lists" "%OUT_DIR%\\word_lists" /e /ndl /nfl /njh /njs >nul

if "%AIWORDS_RELEASE_NO_PYTHON%"=="1" (
  echo Skipping python runtime ^(AIWORDS_RELEASE_NO_PYTHON=1^)
) else (
  if not exist "venv\\Lib\\site-packages" (
    echo Error: venv\\Lib\\site-packages not found.
    echo Please run setup_env.bat first to install dependencies.
    echo.
    pause
    exit /b 1
  )

  echo.
  echo Preparing portable Python runtime: !PYVER! ^(embed^)
  set "CACHE_DIR=trash\\python_embed_cache"
  set "EMBED_PATH=!CACHE_DIR!\\!EMBED_ZIP!"
  if not exist "!CACHE_DIR!" mkdir "!CACHE_DIR!" >nul 2>&1

  if not exist "!EMBED_PATH!" (
    echo Downloading !EMBED_ZIP! ...
    curl -L --fail --silent --show-error -o "!EMBED_PATH!" "https://www.python.org/ftp/python/!PYVER!/!EMBED_ZIP!"
    if errorlevel 1 (
      echo Primary download failed, trying mirror...
      curl -L --fail --silent --show-error -o "!EMBED_PATH!" "https://npmmirror.com/mirrors/python/!PYVER!/!EMBED_ZIP!"
    )
    if errorlevel 1 (
      echo Mirror download failed, trying TUNA mirror...
      curl -L --fail --silent --show-error -o "!EMBED_PATH!" "https://mirrors.tuna.tsinghua.edu.cn/python/!PYVER!/!EMBED_ZIP!"
    )
    if errorlevel 1 (
      echo Failed to download portable Python embed package.
      echo.
      pause
      exit /b 1
    )
  ) else (
    echo Using cached: !EMBED_PATH!
  )

  if exist "!OUT_DIR!\\python" rmdir /s /q "!OUT_DIR!\\python"
  mkdir "!OUT_DIR!\\python" >nul 2>&1
  powershell -NoProfile -Command "Expand-Archive -LiteralPath '!EMBED_PATH!' -DestinationPath '!OUT_DIR!\\python' -Force"

  echo Configuring !PYPTH! ...
  powershell -NoProfile -Command ^
    "$pth = Join-Path '!OUT_DIR!\\python' '!PYPTH!';" ^
    "$lines = @('!PYZIP!','.', '..', 'Lib\\site-packages', 'import site');" ^
    "Set-Content -Path $pth -Value $lines -Encoding ASCII"

  echo Copying Python deps ^(site-packages^)...
  if not exist "!OUT_DIR!\\python\\Lib\\site-packages" mkdir "!OUT_DIR!\\python\\Lib\\site-packages" >nul 2>&1
  robocopy "venv\\Lib\\site-packages" "!OUT_DIR!\\python\\Lib\\site-packages" /e /ndl /nfl /njh /njs >nul
)

if "%AIWORDS_RELEASE_NO_MODELS%"=="1" (
  echo Skipping models/ ^(AIWORDS_RELEASE_NO_MODELS=1^)
) else (
  if exist "models" (
    echo Copying models/ ^(may take a while^)...
    robocopy "models" "%OUT_DIR%\\models" /e /ndl /nfl /njh /njs >nul
  )
)

echo.
echo Creating zip ^(may take a while^)...
powershell -NoProfile -Command "Compress-Archive -Path '%OUT_DIR%\\*' -DestinationPath '%OUT_ZIP%' -Force"

echo.
echo Done.
echo - Folder: %OUT_DIR%
echo - Zip:    %OUT_ZIP%
echo.
if "%AIWORDS_NO_PAUSE%"=="1" exit /b 0
pause
