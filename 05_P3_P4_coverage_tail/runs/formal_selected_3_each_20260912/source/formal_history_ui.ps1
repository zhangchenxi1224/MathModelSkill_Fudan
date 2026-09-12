param([ValidateSet('snapshot','invoke')][string]$Action='snapshot',[string]$Name='',[string]$OutputPath='',[switch]$Server)
# Read the visible application's accessibility tree only. Never reads simulator storage.
$ErrorActionPreference='Stop'
[Console]::OutputEncoding=New-Object System.Text.UTF8Encoding($false)
[Console]::InputEncoding=New-Object System.Text.UTF8Encoding($false)
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
Add-Type @'
using System; using System.Runtime.InteropServices;
public class RoundAccessibleWindow {
 [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h,int n);
 [DllImport("user32.dll")] public static extern bool IsIconic(IntPtr h);
}
'@
function Invoke-RoundUi([string]$Action,[string]$Name,[string]$OutputPath) {
$window=(Get-Process jammers-simulator | Where-Object {$_.MainWindowHandle -ne 0} | Select-Object -First 1).MainWindowHandle
if (-not $window) {throw 'Simulator window not found'}
if ([RoundAccessibleWindow]::IsIconic($window)) {
    [RoundAccessibleWindow]::ShowWindow($window,9) | Out-Null
    Start-Sleep -Milliseconds 200
}
$root=[System.Windows.Automation.AutomationElement]::FromHandle($window)
$types=@([System.Windows.Automation.ControlType]::Text,[System.Windows.Automation.ControlType]::Button,[System.Windows.Automation.ControlType]::Group,[System.Windows.Automation.ControlType]::Window)
$conditions=[System.Windows.Automation.Condition[]]@($types | ForEach-Object {New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ControlTypeProperty,$_ )})
$condition=New-Object System.Windows.Automation.OrCondition -ArgumentList (,$conditions)
$nodes=$root.FindAll([System.Windows.Automation.TreeScope]::Descendants,[System.Windows.Automation.Condition]::TrueCondition)
if ($Action -eq 'invoke') {
    $forbidden=[regex]::Unescape('\u6b63\u5f0f|\u4e2d\u6b62|\u9000\u51fa\u767b\u5f55')
    if ($Name -notin @('问题3正式测试','问题4正式测试','开始问题3正式测试','开始问题4正式测试','确认开始','继续','确认','返回问题3正式测试','返回问题4正式测试')) {throw 'Control has not been selected for this formal run'}
    $matches=@($nodes | Where-Object {$_.Current.Name -eq $Name -and $_.Current.ControlType -eq [System.Windows.Automation.ControlType]::Button -and $_.Current.IsEnabled -and -not $_.Current.IsOffscreen})
    if ($matches.Count -ne 1) {throw "Expected one enabled visible button named $Name; found $($matches.Count)"}
    $pattern=$matches[0].GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern)
    $pattern.Invoke()
    @{invoked=$Name} | ConvertTo-Json -Compress
    return
}
$items=@($nodes | Where-Object {$_.Current.Name -and -not $_.Current.IsOffscreen} | ForEach-Object {
    $c=$_.Current
    @{name=$c.Name;class=$c.ClassName;type=$c.ControlType.ProgrammaticName;enabled=$c.IsEnabled}
})
$payload=@{captured_utc=[DateTime]::UtcNow.ToString('o');items=$items} | ConvertTo-Json -Depth 4 -Compress
if ($OutputPath) {[System.IO.File]::WriteAllText([System.IO.Path]::GetFullPath($OutputPath),$payload,(New-Object System.Text.UTF8Encoding($false)))}
$payload
}
if ($Server) {
    while ($null -ne ($line=[Console]::ReadLine())) {
        try {
            $command=$line | ConvertFrom-Json
            Invoke-RoundUi $command.action $command.name $command.output
        } catch {
            @{error=$_.Exception.Message} | ConvertTo-Json -Compress
        }
    }
} else {
    Invoke-RoundUi $Action $Name $OutputPath
}
