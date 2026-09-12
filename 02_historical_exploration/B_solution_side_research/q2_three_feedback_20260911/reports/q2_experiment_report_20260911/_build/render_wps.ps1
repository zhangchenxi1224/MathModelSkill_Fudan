$ErrorActionPreference = 'Stop'
$reportFolder = Split-Path -Parent $PSScriptRoot
$reportFinalPath = Join-Path $reportFolder '第二问实验报告_最终稿.docx'
$reportFinalPdf = Join-Path $PSScriptRoot 'render\word_final.pdf'
$reportWpsApp = New-Object -ComObject KWPS.Application
$reportWpsApp.Visible = $false
$reportWpsApp.DisplayAlerts = 0
$reportWpsDoc = $null
try {
    $reportWpsDoc = $reportWpsApp.Documents.Open($reportFinalPath, $false, $true, $false)
    $reportWpsDoc.ExportAsFixedFormat($reportFinalPdf, 17)
    Write-Output $reportFinalPdf
} finally {
    if ($null -ne $reportWpsDoc) { $reportWpsDoc.Close(0); [void][System.Runtime.InteropServices.Marshal]::ReleaseComObject($reportWpsDoc) }
    $reportWpsApp.Quit(0)
    [void][System.Runtime.InteropServices.Marshal]::ReleaseComObject($reportWpsApp)
}
