# Alternative v15 deployment (when old Streamlit URL stays on v14)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

Write-Host "=== Grok Dashboard v15 — Alternative Deploy ===" -ForegroundColor Cyan
Write-Host ""
Write-Host "OPTION A — New Streamlit app (recommended)"
Write-Host "  1. Open https://share.streamlit.io"
Write-Host "  2. Create app -> Paste GitHub URL:"
Write-Host "     https://github.com/DaveOptionsAholic/grok-thematic-dashboard/blob/deploy-v15/streamlit_app.py"
Write-Host "  3. Branch: deploy-v15  |  Main file: streamlit_app.py"
Write-Host "  4. Custom subdomain (optional): grok-dashboard-v15"
Write-Host ""
Write-Host "OPTION B - Fix existing Streamlit app settings"
Write-Host "  1. share.streamlit.io -> your app -> Settings"
Write-Host "  2. Repository: DaveOptionsAholic/grok-thematic-dashboard"
Write-Host "  3. Branch: deploy-v15"
Write-Host "  4. Main file path: streamlit_app.py"
Write-Host "  5. Reboot app"
Write-Host ""
Write-Host "OPTION C — Hugging Face Space (free)"
Write-Host "  1. https://huggingface.co/new-space -> SDK: Streamlit"
Write-Host "  2. Clone the space repo, copy streamlit_app.py + requirements.txt + README.md"
Write-Host "  3. git push -> live at https://huggingface.co/spaces/YOUR_USER/YOUR_SPACE"
Write-Host ""

$env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path","User")

# Push clean deploy-v15 branch
git add streamlit_app.py app.py README.md requirements.txt .streamlit/config.toml
git add grok_build_thematic_dashboard_v15.py grok_build_thematic_dashboard_v14.py grok-thematic-dashboard-v14.py
git commit -m "v15.2: streamlit_app.py + deploy-v15 branch prep" 2>$null

$branchExists = git branch --list deploy-v15
if (-not $branchExists) {
    git branch deploy-v15
}
git push origin main 2>&1 | Out-Host

git checkout deploy-v15 2>$null
if ($LASTEXITCODE -ne 0) {
    git checkout -b deploy-v15
}
git reset --hard main
git push -u origin deploy-v15 --force 2>&1 | Out-Host
git checkout main

Write-Host ""
Write-Host "Pushed branch deploy-v15 (clean copy of v15.2)" -ForegroundColor Green
Write-Host "Verify sidebar shows: Dashboard v15.2" -ForegroundColor Green