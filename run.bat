@echo off
cd /d "%~dp0analyzer"

rem Prefer pythonw (no console window), then py launcher, then python
where pythonw >nul 2>nul && (start "" pythonw app.py & exit /b)
where py >nul 2>nul && (start "" py app.py & exit /b)
where python >nul 2>nul && (start "" python app.py & exit /b)

echo.
echo ============================================
echo   Python not found - cannot start
echo ============================================
echo.
echo Please install Python 3 from:
echo   https://www.python.org/downloads/
echo During install, CHECK this box:
echo   Add python.exe to PATH
echo Then double-click this file again.
echo.
echo (Chinese guide: open the .txt file in this folder)
echo.
pause
