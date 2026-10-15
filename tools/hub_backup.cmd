@echo off
REM The hub's daily backup and its mirror [SPEC-STAR-086], for Task Scheduler.
REM
REM Loud on failure: a backup that stops is found by a message on screen, not
REM weeks later by a restore that has nothing to restore. The log is
REM data\backups\backup.log; `star_sync.py ... status` says the same at any time.
setlocal
cd /d "%~dp0.."
if not exist data\backups mkdir data\backups
echo ==== %DATE% %TIME% >> data\backups\backup.log
python tools\star_sync.py fleet\star-plan.json backup >> data\backups\backup.log 2>&1
set rc=%ERRORLEVEL%
if not "%rc%"=="0" msg %USERNAME% "Lempi: the hub's backup or its mirror FAILED or is stale (exit %rc%). See data\backups\backup.log"
REM Then every file of the library against its record [GDE-INT-115], read-only,
REM about two minutes. A damaged, missing or unreadable file is said the same way.
if not exist data\verify mkdir data\verify
echo ==== %DATE% %TIME% >> data\verify\verify.log
python tools\verify_library.py data\library.db >> data\verify\verify.log 2>&1
set vrc=%ERRORLEVEL%
if not "%vrc%"=="0" msg %USERNAME% "Lempi: the library's verification found damaged, missing or unreadable files, or could not run (exit %vrc%). See data\verify\verify.log"
if not "%rc%"=="0" exit /b %rc%
exit /b %vrc%
