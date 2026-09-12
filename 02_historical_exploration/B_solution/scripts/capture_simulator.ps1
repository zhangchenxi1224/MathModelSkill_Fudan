param([string]$OutputPath = "results/simulator_window.png")
Add-Type -AssemblyName System.Drawing
Add-Type @'
using System; using System.Runtime.InteropServices;
public class CapB {
 [StructLayout(LayoutKind.Sequential)] public struct RECT {public int L,T,R,B;}
 [DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
 [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h,out RECT r);
 [DllImport("user32.dll")] public static extern bool PrintWindow(IntPtr h,IntPtr dc,uint f);
}
'@
[CapB]::SetProcessDPIAware() | Out-Null
$window=(Get-Process jammers-simulator | Where-Object {$_.MainWindowHandle -ne 0} | Select-Object -First 1).MainWindowHandle
$r=New-Object CapB+RECT
[CapB]::GetWindowRect($window,[ref]$r)|Out-Null
$b=New-Object System.Drawing.Bitmap(($r.R-$r.L),($r.B-$r.T))
$g=[System.Drawing.Graphics]::FromImage($b)
$dc=$g.GetHdc()
[CapB]::PrintWindow($window,$dc,2)|Out-Null
$g.ReleaseHdc($dc)
$b.Save([System.IO.Path]::GetFullPath($OutputPath))
$g.Dispose()
$b.Dispose()
