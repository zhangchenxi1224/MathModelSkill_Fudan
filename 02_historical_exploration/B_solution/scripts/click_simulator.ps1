param([int]$X,[int]$Y)
# Native window input only. Coordinates come from a previously inspected screenshot.
Add-Type @'
using System; using System.Text; using System.Runtime.InteropServices;
public class MsgB {
 public delegate bool CB(IntPtr h, IntPtr p);
 [StructLayout(LayoutKind.Sequential)] public struct RECT {public int L,T,R,B;}
 [DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
 [DllImport("user32.dll")] public static extern bool EnumChildWindows(IntPtr h,CB c,IntPtr p);
 [DllImport("user32.dll")] public static extern int GetClassName(IntPtr h,StringBuilder s,int n);
 [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h,out RECT r);
 [DllImport("user32.dll")] public static extern bool PostMessage(IntPtr h,uint m,IntPtr w,IntPtr l);
}
'@
[MsgB]::SetProcessDPIAware() | Out-Null
$window=(Get-Process jammers-simulator | Where-Object {$_.MainWindowHandle -ne 0} | Select-Object -First 1).MainWindowHandle
$script:render=[IntPtr]::Zero
$callback=[MsgB+CB]{param($h,$p) $s=New-Object Text.StringBuilder 256; [MsgB]::GetClassName($h,$s,256) | Out-Null; if ($s.ToString() -eq 'Chrome_RenderWidgetHostHWND') {$script:render=$h}; return $true}
[MsgB]::EnumChildWindows($window,$callback,[IntPtr]::Zero) | Out-Null
if ($script:render -eq [IntPtr]::Zero) {throw 'visible render window not found'}
$wr=New-Object MsgB+RECT
$rr=New-Object MsgB+RECT
[MsgB]::GetWindowRect($window,[ref]$wr)|Out-Null
[MsgB]::GetWindowRect($script:render,[ref]$rr)|Out-Null
$px=$wr.L+$X-$rr.L
$py=$wr.T+$Y-$rr.T
$lp=[IntPtr](($py -shl 16) -bor $px)
[MsgB]::PostMessage($script:render,512,[IntPtr]0,$lp)|Out-Null
[MsgB]::PostMessage($script:render,513,[IntPtr]1,$lp)|Out-Null
[MsgB]::PostMessage($script:render,514,[IntPtr]0,$lp)|Out-Null
