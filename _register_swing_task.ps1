# Register swing PAPER-trading tasks (market-split, weekdays). ASCII-only. Run once by the user.
#   Swing-KR : 09:05 KST  -> KR names, enter at today's open (decision on prev-day candle) + review + briefs
#   Swing-US : 06:00 KST  -> US names on fresh US-close data
#   Scalp-V5 : 15:00 KST  -> ultra-short v5 overnight breakout (intraday scan, close buy) via run_scalp_v5.bat
# Paper mode only - live orders stay triple-locked via .env.
$dir = "C:\Users\xect2\swing-short-trading"
$hidden = Join-Path $dir "hidden.vbs"

# Remove old single task if present (replaced by market-split tasks)
try { Unregister-ScheduledTask -TaskName "Swing-PaperTrading" -Confirm:$false -ErrorAction Stop } catch {}

$settings = New-ScheduledTaskSettingsSet -WakeToRun -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 30)
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
$weekdays = @("Monday","Tuesday","Wednesday","Thursday","Friday")

function Register-Swing($name, $bat, $at, $desc) {
    $action = New-ScheduledTaskAction -Execute "wscript.exe" -Argument ('"' + $hidden + '" ' + $bat) -WorkingDirectory $dir
    $trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek $weekdays -At $at
    Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Description $desc -Force | Out-Null
    Write-Host ("OK: '" + $name + "' @ " + $at) -ForegroundColor Green
}

Register-Swing "Swing-KR" "run_swing_kr.bat" "9:05AM"  "KR swing paper (open entry) + review + briefs. Weekdays 09:05 KST."
Register-Swing "Swing-US" "run_swing_us.bat" "6:00AM"  "US swing paper on fresh US-close data. Weekdays 06:00 KST."
Register-Swing "Scalp-V5" "run_scalp_v5.bat" "3:00PM" "Ultra-short v5 overnight breakout paper (intraday scan + close buy). Weekdays 15:00 KST."
Register-Swing "Evolve-Daily" "run_evolve.bat" "8:00PM" "Self-improving tuning loop: AI proposes -> harness OOS A/B -> Discord for human `swing adopt`. Weekdays 20:00 KST."

Get-ScheduledTask -TaskName "Swing-*","Scalp-*","Evolve-*" | Get-ScheduledTaskInfo | Select-Object TaskName, NextRunTime, State | Format-List
