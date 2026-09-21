# SPDX-License-Identifier: MIT
#
# Phase 1b: patch a freshly Raspberry-Pi-Imager-written card's boot partition,
# before it ever reaches bose. Runs on the development host (Windows).
#
# Idempotent: each edit checks its own current state first and is skipped if
# already applied, so re-running this after a partial failure -- or just to
# confirm everything landed -- does not double-apply anything.
#
#   powershell -File BosePi/patch-boot-image.ps1 -DriveLetter D
#
# Refuses anything that doesn't look like a Raspberry Pi OS boot partition
# (missing cmdline.txt/config.txt) or that looks like a system disk.
#
# Status: the edits this script makes were done once, by hand, successfully,
# against a card written by Raspberry Pi Imager v2.0.10 on 2026-09-06 -- this
# file re-expresses them as regex-driven edits, which have not themselves
# been run against a real card. Raspberry Pi Imager's cloud-init output
# format is exactly the kind of thing that changes across versions; a future
# Imager could restructure user-data enough that the "sudo: null" pattern
# below no longer matches anything, and this script will say so (each edit
# reports "already present" or "not found" rather than assuming), but it
# cannot know it needs a new pattern. Read what it reports for each file
# before trusting the card is actually patched.

param(
    [string]$DriveLetter = "D"
)

$ErrorActionPreference = "Stop"
$LogDir = Join-Path $PSScriptRoot "logs"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$LogFile = Join-Path $LogDir ("patch-boot-image-{0}.log" -f (Get-Date -Format "yyyyMMddTHHmmssZ"))

function Say($msg) { $msg | Tee-Object -FilePath $LogFile -Append }
function Step($msg) { Say ""; Say "== $msg" }
function Die($msg) { Say "patch-boot-image: $msg"; exit 1 }

Say "-----------------------------------------------------------------"
Say "This script has not itself been run against a real card -- see the"
Say "header. Read what each step below reports rather than only the exit"
Say "code; 'not found' on any edit means look at the file by hand."
Say "-----------------------------------------------------------------"

Step "Checking the target"
$root = "${DriveLetter}:\"
if (-not (Test-Path $root)) { Die "no drive $root" }

$partition = Get-Partition -DriveLetter $DriveLetter -ErrorAction SilentlyContinue
if (-not $partition) { Die "$root is not a partition on a real disk" }
$disk = Get-Disk -Number $partition.DiskNumber
if ($disk.BusType -ne "USB") { Die "$root is on a $($disk.BusType) disk, not USB -- refusing" }
if ($disk.IsBoot -or $disk.IsSystem) { Die "$root is on a boot/system disk -- refusing" }
Say "  $root -> Disk $($disk.Number), $([math]::Round($disk.Size/1GB,1)) GB, USB, not boot/system"

$cmdline = Join-Path $root "cmdline.txt"
$config  = Join-Path $root "config.txt"
$userdata = Join-Path $root "user-data"
if (-not (Test-Path $cmdline)) { Die "$cmdline not found -- is this actually a freshly-imaged card?" }
if (-not (Test-Path $config))  { Die "$config not found -- is this actually a freshly-imaged card?" }
Say "  cmdline.txt and config.txt present"

Step "cmdline.txt: disable first-boot root auto-expand [BOSE003 step 3]"
$cmd = Get-Content -Raw -LiteralPath $cmdline
if ($cmd -match "\bresize\b") {
    $cmd = $cmd -replace "\s*\bresize\b", ""
    [System.IO.File]::WriteAllText($cmdline, $cmd)
    Say "  removed 'resize' token"
} else {
    Say "  already absent -- skipped"
}

Step "config.txt: HiFiBerry DAC+ Pro, HDMI audio off [PI-BOS-020]"
$cfg = Get-Content -Raw -LiteralPath $config
$changed = $false
if ($cfg -match "dtoverlay=vc4-kms-v3d\s*$" -and $cfg -notmatch "audio=off") {
    $cfg = $cfg -replace "dtoverlay=vc4-kms-v3d\s*\r?\n", "dtoverlay=vc4-kms-v3d,audio=off`n"
    $changed = $true
    Say "  set audio=off on the vc4-kms-v3d overlay"
} elseif ($cfg -match "audio=off") {
    Say "  audio=off already present -- skipped"
}
if ($cfg -notmatch "dtoverlay=hifiberry-dacplus") {
    $cfg = $cfg.TrimEnd() + "`n`n# HiFiBerry DAC+ Pro on I2S [PI-BOS-020]`ndtoverlay=hifiberry-dacplus`n"
    $changed = $true
    Say "  added dtoverlay=hifiberry-dacplus"
} else {
    Say "  hifiberry-dacplus already present -- skipped"
}
if ($changed) { [System.IO.File]::WriteAllText($config, $cfg) }

Step "user-data: NOPASSWD sudo at write time [IMPL-BOS-090b -- avoids the manual bridge this build needed]"
if (Test-Path $userdata) {
    $ud = Get-Content -Raw -LiteralPath $userdata
    if ($ud -match "sudo:\s*null") {
        $ud = $ud -replace "sudo:\s*null", "sudo: ['ALL=(ALL) NOPASSWD:ALL']"
        [System.IO.File]::WriteAllText($userdata, $ud)
        Say "  sudo: null -> NOPASSWD:ALL"
    } elseif ($ud -match "NOPASSWD") {
        Say "  NOPASSWD already present -- skipped"
    } else {
        Say "  WARNING: no 'sudo: null' found to replace -- check user-data by hand"
    }
} else {
    Say "  no user-data on this card (not cloud-init?) -- skipped, nothing to do"
}

Say ""
Say "Done. Log: $LogFile"
