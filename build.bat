@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo ========================================
echo   AI Word Detector v2.7.3 - Build Script
echo ========================================
echo.

:: Check virtual environment
if not exist "venv\Scripts\pyinstaller.exe" (
    echo Error: Virtual environment not found or pyinstaller not installed
    echo Please run setup_env.bat to install the environment
    pause
    exit /b 1
)

echo [1/4] Starting build...
echo      - Bundling locales (en.json, zh_CN.json)
echo      - Bundling word_lists
echo      NOTE: No vocabulary_data.json bundled - users build their own corpus
echo.

:: Optimized build - only essential hidden imports
venv\Scripts\pyinstaller.exe --noconfirm --onefile --windowed ^
    --name "AIWordDetector" ^
    --add-data "locales;locales" ^
    --add-data "word_lists;word_lists" ^
    --hidden-import "jieba" ^
    --hidden-import "numpy" ^
    --hidden-import "onnxruntime" ^
    --hidden-import "tokenizers" ^
    --hidden-import "ufal.udpipe" ^
    --collect-all numpy ^
    --collect-all onnxruntime ^
    --collect-all tokenizers ^
    --collect-all ufal ^
    ai_word_detector.py

if errorlevel 1 (
    echo Build failed!
    if "%AIWORDDETECTOR_NO_PAUSE%"=="1" exit /b 1
    pause
    exit /b 1
)

echo.
echo [2/4] Organizing files...

:: Copy to root directory with version
copy /y "dist\AIWordDetector.exe" "AIWordDetector.exe" >nul

:: Clean up temporary files
rmdir /s /q build 2>nul
rmdir /s /q dist 2>nul
del AIWordDetector.spec 2>nul

echo.
echo [3/4] Build verification...

:: Check file size
for %%A in ("AIWordDetector.exe") do (
    set SIZE=%%~zA
    set /a SIZE_MB=%%~zA/1048576
)
echo      Executable size: %SIZE_MB% MB

echo.
echo ========================================
echo   Build Complete!
echo ========================================
echo.
echo Generated file: AIWordDetector.exe
echo.
echo IMPORTANT: No vocabulary is bundled.
echo Users must load their own PDF corpus to build vocabulary.
echo.

echo [4/4] Building offline package (exe + models)...

for /f "delims=" %%V in ('venv\Scripts\python.exe -c "from version import VERSION; print(VERSION)"') do set APP_VER=%%V
set PKG_DIR=release\AIWordDetector_%APP_VER%_offline
if exist "%PKG_DIR%" rmdir /s /q "%PKG_DIR%"
mkdir "%PKG_DIR%" 2>nul

copy /y "AIWordDetector.exe" "%PKG_DIR%\AIWordDetector.exe" >nul
copy /y "README.md" "%PKG_DIR%\README.md" >nul
copy /y "LICENSE" "%PKG_DIR%\LICENSE" >nul
copy /y "download_models.bat" "%PKG_DIR%\download_models.bat" >nul
copy /y "download_semantic_model.py" "%PKG_DIR%\download_semantic_model.py" >nul

if exist "models" (
    xcopy /e /i /y "models" "%PKG_DIR%\models" >nul
) else (
    echo Warning: models folder not found. Semantic similarity will be unavailable in the package.
)

powershell -NoProfile -Command "Compress-Archive -Path '%PKG_DIR%\\*' -DestinationPath 'release\\AIWordDetector_%APP_VER%_offline.zip' -Force" >nul
echo      Package: release\AIWordDetector_%APP_VER%_offline.zip
echo.
if "%AIWORDDETECTOR_NO_PAUSE%"=="1" exit /b 0
pause
