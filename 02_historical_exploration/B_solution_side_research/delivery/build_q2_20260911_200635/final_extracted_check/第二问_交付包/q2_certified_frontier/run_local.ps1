param(
    [string]$Python = '',
    [ValidateRange(1,64)][int]$Workers = 2,
    [ValidateRange(1,30)][double]$AngleStep = 5,
    [string]$Output = ''
)
$ErrorActionPreference = 'Stop'
if (-not $Python) {
    if (Test-Path -LiteralPath 'D:\st_python\python.exe') {
        $Python = 'D:\st_python\python.exe'
    } else {
        $Python = (Get-Command python -ErrorAction Stop).Source
    }
}
if (-not $Output) {
    $Output = Join-Path $PSScriptRoot ('results/local_' + (Get-Date -Format 'yyyyMMdd_HHmmss_fff'))
} elseif (-not [IO.Path]::IsPathRooted($Output)) {
    $Output = Join-Path $PSScriptRoot $Output
}
$q2AngleArgument = $AngleStep.ToString([Globalization.CultureInfo]::InvariantCulture)
& $Python (Join-Path $PSScriptRoot 'study.py') --output $Output --official-contexts (Join-Path $PSScriptRoot 'docs/official_q2_contexts.json') --angle-step $q2AngleArgument --workers $Workers
if ($LASTEXITCODE -ne 0) { throw "Q2 experiment failed: exit $LASTEXITCODE" }
Write-Output "Completed Q2 results: $Output"
