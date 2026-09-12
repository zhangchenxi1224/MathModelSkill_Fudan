param(
    [ValidateSet('development', 'confirmation', 'engineering', 'freeze')]
    [string]$Phase = 'development',
    [string]$Manifest = 'results/research_ready_final/manifest.json',
    [int]$Workers = 2,
    [switch]$Resume,
    [string]$Python = ''
)
$ErrorActionPreference = 'Stop'
if (-not $Python) {
    if (Test-Path -LiteralPath 'D:\st_python\python.exe') {
        $Python = 'D:\st_python\python.exe'
    } else {
        $Python = (Get-Command python -ErrorAction Stop).Source
    }
}
if (-not [System.IO.Path]::IsPathRooted($Manifest)) {
    $Manifest = Join-Path $PSScriptRoot $Manifest
}
if ($Phase -eq 'freeze') {
    & $Python (Join-Path $PSScriptRoot 'run.py') freeze-selection --manifest $Manifest
} else {
    $arguments = @((Join-Path $PSScriptRoot 'run.py'), 'run', '--manifest', $Manifest,
                   '--partition', $Phase, '--workers', $Workers)
    if ($Resume) { $arguments += '--resume' }
    if ($Phase -eq 'confirmation') {
        $arguments += @('--selection', (Join-Path (Split-Path -Parent $Manifest) 'selection.json'))
    }
    & $Python @arguments
}
exit $LASTEXITCODE
