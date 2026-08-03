@echo off
rem Launcher for the PIWIS VAL Viewer.
rem Uses pythonw (no console window) when available, else falls back
rem to python. Any arguments are passed through to the script.
where pythonw >nul 2>nul
if %errorlevel%==0 (
    start "" pythonw "%~dp0piwis_val_viewer.py" %*
) else (
    python "%~dp0piwis_val_viewer.py" %*
)
