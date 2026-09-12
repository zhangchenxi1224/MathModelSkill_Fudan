$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$pipelinePython = 'D:/st_python/python.exe'
if (-not (Test-Path -LiteralPath 'data/development960.json')) {
    & $pipelinePython -X utf8 -u run_pipeline.py prepare
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
foreach ($pipelineStage in @('protocol_checks', 'protocol_report', 'development')) {
    & $pipelinePython -X utf8 -u run_pipeline.py $pipelineStage --workers 6
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
if (-not (Test-Path -LiteralPath 'selection.json')) {
    & $pipelinePython -X utf8 -u select_fidelity.py select
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
& $pipelinePython -X utf8 -u run_pipeline.py confirmation --workers 6
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $pipelinePython -X utf8 -u select_fidelity.py finalize
exit $LASTEXITCODE
