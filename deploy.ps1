# Deploy Grok Thematic Dashboard v15 to Streamlit Community Cloud
# Prerequisites: GitHub account, one-time `gh auth login`

$ErrorActionPreference = "Stop"
$RepoRoot = $PSScriptRoot
$RepoName = "grok-thematic-dashboard"

Set-Location $RepoRoot

$gh = Get-Command gh -ErrorAction SilentlyContinue
if (-not $gh) {
    Write-Host "Installing GitHub CLI..."
    winget install --id GitHub.cli -e --accept-source-agreements --accept-package-agreements
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path", "User")
}

$prevEAP = $ErrorActionPreference
$ErrorActionPreference = "Continue"
gh auth status *> $null
$authed = ($LASTEXITCODE -eq 0)
$ErrorActionPreference = $prevEAP
if (-not $authed) {
    Write-Host "Log in to GitHub (browser will open)..."
    gh auth login -h github.com -p https -w
}

if (-not (Test-Path ".git")) {
    git init -b main
}

git add .gitignore app.py requirements.txt .streamlit/config.toml deploy.ps1
git add grok_build_thematic_dashboard_v15.py 2>$null

$status = git status --porcelain
if ($status) {
    git commit -m "Deploy v15: sidebar controls and configurable charts"
}

$remotes = git remote 2>$null
if ($remotes -notcontains "origin") {
    gh repo create $RepoName --public --source=. --remote=origin --push
} else {
    git push -u origin main
}

$user = (gh api user -q .login)
Write-Host ""
Write-Host "Repository: https://github.com/$user/$RepoName"
Write-Host ""
Write-Host "Streamlit Cloud:"
Write-Host "  1. Open https://share.streamlit.io"
Write-Host "  2. Create app -> Paste GitHub URL:"
Write-Host "     https://github.com/$user/$RepoName/blob/main/app.py"
Write-Host "  3. Main file path: app.py  |  Branch: main"
Write-Host ""