# Build the Turnloom Studio desktop app into dist\Turnloom Studio.
# Run from the repository root in an environment with the studio, full and desktop extras.
$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)

python -m pip install -e ".[studio,full,desktop]"
if ($LASTEXITCODE) { throw "Dependency installation failed." }

npm ci --prefix studio --ignore-scripts
if ($LASTEXITCODE) { throw "npm ci failed." }
npm run build --prefix studio
if ($LASTEXITCODE) { throw "Studio interface build failed." }

python -m PyInstaller packaging\turnloom-studio.spec --noconfirm --workpath build\pyinstaller
if ($LASTEXITCODE) { throw "PyInstaller build failed." }

Write-Host "Built dist\Turnloom Studio\Turnloom Studio.exe"
