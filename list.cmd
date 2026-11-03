<# :
@echo off
setlocal
set "SCRIPT_PATH=%~f0"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$code = '$PSCommandPath = $env:SCRIPT_PATH; $PSScriptRoot = Split-Path -Parent $env:SCRIPT_PATH; ' + (Get-Content -LiteralPath $env:SCRIPT_PATH -Raw); $input | & ([ScriptBlock]::Create($code))"
pause
exit /b
: #>


$currentDateTime = Get-Date
$dateTimeString = $currentDateTime.ToString("yyyyMMddHHmmss")
Write-Output $dateTimeString

Get-ChildItem *.jpeg | Rename-Item -NewName { $_.Name -replace '.jpeg','.ts' }
Get-ChildItem *.ts | Rename-Item -NewName { $_.Name -replace 'video([0-9]{1}).ts', 'video000$1.ts' }
Get-ChildItem *.ts | Rename-Item -NewName { $_.Name -replace 'video([0-9]{2}).ts', 'video00$1.ts' }
Get-ChildItem *.ts | Rename-Item -NewName { $_.Name -replace 'video([0-9]{3}).ts', 'video0$1.ts' }
Get-ChildItem -Recurse -File | Sort-Object -Property Name | foreach { if ($_.Extension -eq ".ts") {Write-Output "file '$($_.FullName)'" } } | Out-File list.txt -Encoding ascii
.\ffmpeg.exe -safe 0 -f concat -i list.txt -c copy "$dateTimeString.mp4"
del *.ts