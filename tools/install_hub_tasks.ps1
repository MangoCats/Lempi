# SPDX-License-Identifier: MIT
#
# The hub's two scheduled tasks, created or brought up to date [SPEC-STAR-086],
# [SPEC-STAR-088]. Until 2026-10-15 the first existed only as made by hand,
# recorded by no script.
#
#   Lempi hub backup  03:30 daily: tools\hub_backup.cmd -- the backup, the
#                     re-read of its objects, the library's verification; a
#                     message on screen if any fails or is stale
#   Lempi hub mirror  every quarter hour: tools\hub_mirror.cmd -- the mirror
#                     brought up to date whenever it can be reached; quiet,
#                     and run without a window
#
# Both run as the signed-in user, start late if a run was missed, ignore a
# second start while one runs, and are stopped after three hours.
#
#     powershell -ExecutionPolicy Bypass -File tools\install_hub_tasks.ps1

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$user = "$env:USERDOMAIN\$env:USERNAME"
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 3) -MultipleInstances IgnoreNew

$backup = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c `"$repo\tools\hub_backup.cmd`""
Register-ScheduledTask -TaskName "Lempi hub backup" -Force -Principal $principal -Settings $settings `
    -Trigger (New-ScheduledTaskTrigger -Daily -At "03:30") -Action $backup `
    -Description "The Lempi hub's daily backup [SPEC-STAR-086]; tools\hub_backup.cmd" | Out-Null
Write-Output "Lempi hub backup: daily at 03:30"

# Headless: a console every quarter hour would flash on screen.
$mirror = New-ScheduledTaskAction -Execute "conhost.exe" `
    -Argument "--headless cmd.exe /c `"$repo\tools\hub_mirror.cmd`""
$every = New-ScheduledTaskTrigger -Once -At (Get-Date).Date -RepetitionInterval (New-TimeSpan -Minutes 15)
Register-ScheduledTask -TaskName "Lempi hub mirror" -Force -Principal $principal -Settings $settings `
    -Trigger $every -Action $mirror `
    -Description "The Lempi hub backup's mirror, whenever it can be reached [SPEC-STAR-088]; tools\hub_mirror.cmd" | Out-Null
Write-Output "Lempi hub mirror: every 15 minutes"
