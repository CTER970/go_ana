@echo off

powershell -ExecutionPolicy Bypass -NoProfile -Command "$ws=New-Object -ComObject WScript.Shell; $d=[Environment]::GetFolderPath('Desktop'); $l=$ws.CreateShortcut($d+'\GoAnalyzer.lnk'); $l.TargetPath='%~dp0run.bat'; $l.WorkingDirectory='%~dp0'; $l.WindowStyle=7; $l.Description='KataGo Analyzer'; $l.Save()"

echo.
echo Desktop shortcut created: GoAnalyzer
echo.
echo Double-click GoAnalyzer on your desktop to start the analyzer.
echo (Chinese guide: open the .txt file in this folder.)
echo.
pause
