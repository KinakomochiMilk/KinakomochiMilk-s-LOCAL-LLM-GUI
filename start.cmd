@echo off
setlocal
cd /d "%~dp0"
if exist "%LocalAppData%\Programs\Python\Python314\pythonw.exe" (
    "%LocalAppData%\Programs\Python\Python314\pythonw.exe" launcher.py
) else (
    where pythonw >nul 2>&1
    if not errorlevel 1 (
        pythonw launcher.py
    ) else (
        python launcher.py
    )
)
endlocal
