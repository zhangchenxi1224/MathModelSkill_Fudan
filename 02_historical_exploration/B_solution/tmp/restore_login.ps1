$ErrorActionPreference='Stop'
[Console]::OutputEncoding=New-Object System.Text.UTF8Encoding($false)
$loginData=[Console]::ReadLine() | ConvertFrom-Json
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
$simWindow=(Get-Process jammers-simulator | Where-Object {$_.MainWindowHandle -ne 0} | Select-Object -First 1).MainWindowHandle
$simRoot=[System.Windows.Automation.AutomationElement]::FromHandle($simWindow)
$simNodes=$simRoot.FindAll([System.Windows.Automation.TreeScope]::Descendants,[System.Windows.Automation.Condition]::TrueCondition)
$edits=@($simNodes | Where-Object {$_.Current.ControlType -eq [System.Windows.Automation.ControlType]::Edit -and -not $_.Current.IsOffscreen})
$team=@($edits | Where-Object {-not $_.Current.IsPassword})
$secret=@($edits | Where-Object {$_.Current.IsPassword})
$login=@($simNodes | Where-Object {$_.Current.ControlType -eq [System.Windows.Automation.ControlType]::Button -and $_.Current.Name -eq ([regex]::Unescape('\u767b\u5f55')) -and -not $_.Current.IsOffscreen})
if ($team.Count -ne 1 -or $secret.Count -ne 1 -or $login.Count -ne 1) {throw 'Expected login controls are not available'}
$team[0].GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern).SetValue([string]$loginData.team)
$secret[0].GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern).SetValue([string]$loginData.password)
$loginData=$null
$login[0].GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern).Invoke()
Write-Output 'Submitted login through visible controls.'
