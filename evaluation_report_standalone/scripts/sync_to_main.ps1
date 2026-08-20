# Sync standalone evaluation_report / converter / templates back to main django project.
# Does NOT copy apps/core shim or standalone migrations.
# Usage (from repo root or any cwd):
#   powershell -File evaluation_report_standalone/scripts/sync_to_main.ps1

$ErrorActionPreference = "Stop"
$standalone = Split-Path $PSScriptRoot -Parent
$repo = Split-Path $standalone -Parent
$src = $standalone
$dst = Join-Path $repo "django"

if (-not (Test-Path (Join-Path $dst "manage.py"))) {
    throw "Main django project not found at $dst"
}

Write-Host "Repo: $repo"
Write-Host "From: $src"
Write-Host "To:   $dst"

robocopy "$src\apps\evaluation_report" "$dst\apps\evaluation_report" /E `
  /XD migrations __pycache__ /XF *.pyc /NFL /NDL /NJH /NJS /nc /ns /np
if ($LASTEXITCODE -ge 8) { throw "robocopy evaluation_report failed: $LASTEXITCODE" }

robocopy "$src\converter" "$dst\converter" /E /XD __pycache__ /XF *.pyc /NFL /NDL /NJH /NJS /nc /ns /np
if ($LASTEXITCODE -ge 8) { throw "robocopy converter failed: $LASTEXITCODE" }

robocopy "$src\templates\evaluation_report" "$dst\templates\evaluation_report" /E /NFL /NDL /NJH /NJS /nc /ns /np
if ($LASTEXITCODE -ge 8) { throw "robocopy templates failed: $LASTEXITCODE" }

$mdInc = "$src\templates\includes\markdown_render_block.html"
if (Test-Path $mdInc) {
    New-Item -ItemType Directory -Force -Path "$dst\templates\includes" | Out-Null
    Copy-Item $mdInc "$dst\templates\includes\markdown_render_block.html" -Force
}

Write-Host "Synced. Next in django/: python manage.py makemigrations evaluation_report && python manage.py migrate"
Write-Host "See evaluation_report_standalone/docs/EMBEDDING.md"
