# Register the Machinery News Scraper scheduled task.
# Runs the scraper every 6 hours.
#
# Usage from PowerShell (Admin):
#     cd "e:\OneDrive - 华兴科技公司\code space\trae\machinery-news-scraper"
#     .\register-task.ps1
#
# Or with -Force to overwrite an existing task:
#     .\register-task.ps1 -Force

param(
    [switch]$Force
)

$batPath = Join-Path $PSScriptRoot "run-scheduled.bat"
if (-not (Test-Path $batPath)) {
    Write-Error "run-scheduled.bat not found at: $batPath"
    exit 1
}

Write-Host "Registering scheduled task 'MachineryNewsScraper'..."
Write-Host "  Trigger: every 6 hours"
Write-Host "  Action:  $batPath"

$action    = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c `"$batPath`""
$trigger   = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Hours 6)
$settings  = New-ScheduledTaskSettingsSet -StartWhenAvailable -DontStopOnIdleEnd -ExecutionTimeLimit (New-TimeSpan -Hours 1)
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Highest

if ($Force) {
    Unregister-ScheduledTask -TaskName "MachineryNewsScraper" -Confirm:$false -ErrorAction SilentlyContinue
}

Register-ScheduledTask `
    -TaskName "MachineryNewsScraper" `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Principal $principal `
    -Force:$Force

Write-Host ""
Write-Host "Done. Verify:"
Write-Host "  Get-ScheduledTask -TaskName 'MachineryNewsScraper' | Get-ScheduledTaskInfo"
Write-Host ""
Write-Host "Trigger manually now (optional):"
Write-Host "  Start-ScheduledTask -TaskName 'MachineryNewsScraper'"
