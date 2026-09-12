$ErrorActionPreference='Stop'
[Console]::OutputEncoding=New-Object System.Text.UTF8Encoding($false)
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
$simWindow=(Get-Process jammers-simulator | Where-Object {$_.MainWindowHandle -ne 0} | Select-Object -First 1).MainWindowHandle
$simRoot=[System.Windows.Automation.AutomationElement]::FromHandle($simWindow)
$simNodes=$simRoot.FindAll([System.Windows.Automation.TreeScope]::Descendants,[System.Windows.Automation.Condition]::TrueCondition)
@{count=$simNodes.Count;nodes=@($simNodes | ForEach-Object {@{name=$_.Current.Name;control=$_.Current.ControlType.ProgrammaticName;id=$_.Current.AutomationId;isPassword=$_.Current.IsPassword;patterns=@($_.GetSupportedPatterns() | ForEach-Object {$_.ProgrammaticName})}})} | ConvertTo-Json -Depth 4 -Compress
