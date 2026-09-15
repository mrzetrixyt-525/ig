$ErrorActionPreference = 'Stop'
$Root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$Venv = Join-Path $Root '.venv'
$Python = Join-Path $Venv 'Scripts\python.exe'

if (-not (Get-Command py -ErrorAction SilentlyContinue)) { throw 'Python 3 is required. Install Python 3 first.' }

py -3 -m venv $Venv
& $Python -m pip install --upgrade pip
& (Join-Path $Venv 'Scripts\pip.exe') install -r (Join-Path $Root 'requirements.txt')

# Create a Windows Defender Firewall group/rule allowing the status page only on port 5665.
New-NetFirewallRule -DisplayName 'RGNodes VPS Protect Dashboard 5665' -Direction Inbound -Protocol TCP -LocalPort 5665 -Action Allow -Profile Any -Group 'RGNodes VPS Protect' -ErrorAction SilentlyContinue | Out-Null

$Action = New-ScheduledTaskAction -Execute $Python -Argument ('"' + (Join-Path $Root 'app\main.py') + '"') -WorkingDirectory $Root
$Trigger = New-ScheduledTaskTrigger -AtStartup
$Principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
Register-ScheduledTask -TaskName 'RGNodes-VPS-Protect' -Action $Action -Trigger $Trigger -Principal $Principal -Force | Out-Null
Start-ScheduledTask -TaskName 'RGNodes-VPS-Protect'
Write-Host 'RG Nodes VPS Protect is installed and scheduled for 24/7 startup.'
Write-Host 'Dashboard: http://SERVER-IP:5665'
