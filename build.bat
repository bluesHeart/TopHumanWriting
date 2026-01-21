@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo ========================================
echo   AI Word Detector v2.1 - Build Script
echo ========================================
echo.

:: Check virtual environment
if not exist "venv\Scripts\pyinstaller.exe" (
    echo Error: Virtual environment not found or pyinstaller not installed
    echo Please run setup_env.bat to install the environment
    pause
    exit /b 1
)

echo [1/3] Starting build...
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
    ai_word_detector.py

if errorlevel 1 (
    echo Build failed!
    pause
    exit /b 1
)

echo.
echo [2/3] Organizing files...

:: Copy to root directory with version
copy /y "dist\AIWordDetector.exe" "AIWordDetector.exe" >nul

:: Clean up temporary files
rmdir /s /q build 2>nul
rmdir /s /q dist 2>nul
del AIWordDetector.spec 2>nul

echo.
echo [3/3] Build verification...

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
pause
