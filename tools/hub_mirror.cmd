@echo off
REM The hub backup's mirror, brought up to date whenever it can be reached
REM [SPEC-STAR-088], for Task Scheduler every quarter hour ("Lempi hub mirror").
REM
REM The mirror (teacherslounge) is usually off at 03:30, when the backup is
REM made, so it is written when it is next on. A run that cannot reach it says
REM so in one line and is not a failure; star_sync's status, run with the 03:30
REM backup, is what says loudly when the mirror has missed a day. Nothing here
REM puts a message on screen: every quarter hour would be too often.
setlocal
cd /d "%~dp0.."
if not exist data\backups mkdir data\backups
REM Kept small: about two lines a quarter hour.
for %%F in (data\backups\mirror.log) do if %%~zF GTR 1000000 move /y data\backups\mirror.log data\backups\mirror.log.old >nul
echo ==== %DATE% %TIME% >> data\backups\mirror.log
python tools\star_sync.py fleet\star-plan.json mirror >> data\backups\mirror.log 2>&1
exit /b %ERRORLEVEL%
