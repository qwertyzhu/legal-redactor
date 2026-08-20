# Install skill junctions for Claude Code / agents (Windows)

$ErrorActionPreference = "Stop"
$src = Join-Path $PSScriptRoot "..\skills\legal-document-redactor" | Resolve-Path
$targets = @(
  "$env:USERPROFILE\.agents\skills\legal-document-redactor",
  "$env:USERPROFILE\.claude\skills\legal-document-redactor"
)
foreach ($t in $targets) {
  if (Test-Path $t) { Remove-Item $t -Force -Recurse }
  $parent = Split-Path $t -Parent
  if (-not (Test-Path $parent)) { New-Item -ItemType Directory -Path $parent -Force | Out-Null }
  New-Item -ItemType Junction -Path $t -Target $src | Out-Null
  Write-Host "Linked $t -> $src"
}
Write-Host "Done. Restart Claude Code if it was already open."
