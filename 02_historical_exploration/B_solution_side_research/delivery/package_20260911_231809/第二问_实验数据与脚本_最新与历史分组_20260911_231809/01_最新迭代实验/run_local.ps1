param(
    [string]$Python = '',
    [ValidateRange(20,2000)][int]$Budget = 80,
    [ValidateRange(0,20)][int]$OfficialCases = 2,
    [ValidateRange(1,16)][int]$Workers = 2,
    [string]$Output = ''
)
$ErrorActionPreference = 'Stop'
if (-not $Python) {
    if (Test-Path -LiteralPath 'D:\st_python\python.exe') { $Python = 'D:\st_python\python.exe' }
    else { $Python = (Get-Command python -ErrorAction Stop).Source }
}
if (-not $Output) {
    $Output = Join-Path $PSScriptRoot ('results/local_' + (Get-Date -Format 'yyyyMMdd_HHmmss_fff'))
} elseif (-not [IO.Path]::IsPathRooted($Output)) { $Output = Join-Path $PSScriptRoot $Output }
& $Python -B (Join-Path $PSScriptRoot 'experiment.py') --output $Output --budget $Budget --official-cases $OfficialCases --workers $Workers
if ($LASTEXITCODE -ne 0) { throw "Experiment failed: exit $LASTEXITCODE" }
Write-Output "Results: $Output"
