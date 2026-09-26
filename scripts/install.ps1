# MyDevAgent — quick install (Windows PowerShell)
# Usage:  powershell -ExecutionPolicy Bypass -File scripts\install.ps1 -HwProfile gpu8
param([ValidateSet("cpu","gpu8","gpu16","gpu24")][string]$HwProfile = "")
if (-not $HwProfile) {  # same logic as `mydevagent doctor`: NVIDIA GPU VRAM
  $gb = 0
  if (Get-Command nvidia-smi -ErrorAction SilentlyContinue) {
    $mib = nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits | ForEach-Object { [int]$_ } | Sort-Object | Select-Object -Last 1
    $gb = [math]::Floor($mib / 1024)
  }
  $HwProfile = if ($gb -ge 22) { "gpu24" } elseif ($gb -ge 14) { "gpu16" } elseif ($gb -ge 7) { "gpu8" } else { "cpu" }
  Write-Host "==> Profile detected from hardware: $HwProfile (to choose it yourself: -HwProfile gpu8)"
}
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

$main = @{ cpu = "qwen2.5-coder:3b"; gpu8 = "qwen2.5-coder:7b"; gpu16 = "qwen2.5-coder:14b"; gpu24 = "qwen3-coder:30b" }[$HwProfile]

Write-Host "==> 1/4 Ollama"
if (-not (Get-Command ollama -ErrorAction SilentlyContinue)) {
  Write-Host "Installing Ollama with winget..."
  winget install -e --id Ollama.Ollama
  $env:Path += ";$env:LOCALAPPDATA\Programs\Ollama"
}
[Environment]::SetEnvironmentVariable("OLLAMA_FLASH_ATTENTION", "1", "User")
[Environment]::SetEnvironmentVariable("OLLAMA_KV_CACHE_TYPE", "q8_0", "User")

Write-Host "==> 2/4 Models ($HwProfile)"
$models = @($main, "qwen2.5-coder:1.5b", "nomic-embed-text")
if ($HwProfile -eq "cpu") { $models += "qwen2.5-coder:7b" }
foreach ($m in $models) { ollama pull $m }
ollama create mydevagent -f "modelfiles/Modelfile.$HwProfile"

Write-Host "==> 3/4 Python environment"
python -m venv .venv
& .\.venv\Scripts\Activate.ps1
python -m pip install -q --upgrade pip
pip install -q -e ".[server,search]"

Write-Host "==> 4/4 Configuration"
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
(Get-Content .env) -replace '^MYDEVAGENT_PROFILE=.*', "MYDEVAGENT_PROFILE=$HwProfile" | Set-Content .env

mydevagent doctor
Write-Host "`nDone!  Start it with:  .\run.bat   (or .\.venv\Scripts\Activate.ps1 ; mydevagent)"
