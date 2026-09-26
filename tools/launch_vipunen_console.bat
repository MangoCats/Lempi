@echo off
REM Launches the Vipunen console (tools/console.py) against the desktop's own
REM working library, and opens it in the default browser.
REM
REM This is NOT the same database lempi02w plays from -- see
REM docs/spec/SPEC006-data-flow-and-portability.md. Edits here reach the
REM appliance only via the bundle exporter/importer, never automatically.
REM
REM First, whether the hub's backup is still running [SPEC-STAR-086]: the
REM primary was lost once, and whoever opens the console is who should see it.
REM data\lempi_new.db, named here until 2026-09-26, was deleted on 2026-09-11;
REM the pair is data\library.db and data\listener.db, either path finding both.
setlocal
cd /d "%~dp0.."
python tools\star_sync.py fleet\star-plan.json status
if errorlevel 1 (
    echo.
    echo *** The hub's backup is STALE or its mirror is behind: see above. ***
    pause
)
start "Vipunen Console" python tools\console.py data\library.db --root "C:\Users\Mango Cat\Music"
timeout /t 2 /nobreak >nul
start "" http://127.0.0.1:5730/
