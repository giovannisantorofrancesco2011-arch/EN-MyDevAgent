# MyDevAgent Studio: installs what Vio needs to work, only if it is missing.
#   1. Python 3.10 or newer        2. Ollama (runs the models on your PC)
#   3. MyDevAgent, English edition (with its models, via the project's scripts\install.ps1)
# Usage: powershell -ExecutionPolicy Bypass -File install-mydevagent.ps1 [-Folder <where to install it>] [-Pause]
param(
  [string]$Folder = (Join-Path $env:LOCALAPPDATA "MyDevAgent"),
  [switch]$Pause
)
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"  # Invoke-WebRequest is much faster without the progress bar
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$Repo = "https://github.com/giovannisantorofrancesco2011-arch/MyDevAgent"

function Step($text) { Write-Host "`n==> $text" -ForegroundColor Magenta }
function Ok($text) { Write-Host "    $text" -ForegroundColor Green }
function Add-ToPath($dir) {
  if ($dir -and (Test-Path $dir) -and (($env:Path -split ";") -notcontains $dir)) { $env:Path = "$dir;$env:Path" }
}
function Winget([string[]]$arguments) {
  if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
    throw "winget is missing (App Installer from the Microsoft Store): install it, or install $($arguments[0]) by hand, and try again."
  }
  winget install -e --accept-package-agreements --accept-source-agreements --id @arguments
  if ($LASTEXITCODE -ne 0) { throw "winget could not install $($arguments[0]) (exit code $LASTEXITCODE)" }
}

function Test-GoodPython($exe, [string[]]$before) {
  try {
    $out = & $exe @before -c "import sys; print(sys.executable if sys.version_info >= (3, 10) else '')" 2>$null
    if ($LASTEXITCODE -eq 0 -and $out) { return ($out | Select-Object -Last 1).Trim() }
  } catch { }
  return $null
}
function Find-Python {
  if (Get-Command py -ErrorAction SilentlyContinue) { $p = Test-GoodPython "py" @("-3"); if ($p) { return $p } }
  if (Get-Command python -ErrorAction SilentlyContinue) { $p = Test-GoodPython "python" @(); if ($p) { return $p } }
  $folders = Get-ChildItem (Join-Path $env:LOCALAPPDATA "Programs\Python") -Directory -ErrorAction SilentlyContinue
  foreach ($dir in ($folders | Sort-Object Name -Descending)) {
    $p = Test-GoodPython (Join-Path $dir.FullName "python.exe") @(); if ($p) { return $p }
  }
  return $null
}
function Find-Ollama {
  $cmd = Get-Command ollama -ErrorAction SilentlyContinue
  if ($cmd) { return $cmd.Source }
  $exe = Join-Path $env:LOCALAPPDATA "Programs\Ollama\ollama.exe"
  if (Test-Path $exe) { return $exe }
  return $null
}
function Test-OllamaRunning {
  try { Invoke-RestMethod "http://127.0.0.1:11434/api/version" -TimeoutSec 2 | Out-Null; return $true } catch { return $false }
}
function Find-MyDevAgent {  # the same places the extension looks in
  $candidates = @($env:MYDEVAGENT_HOME, $Folder, (Join-Path $env:USERPROFILE "MyDevAgent"))
  foreach ($base in @("Desktop", "OneDrive\Desktop", "Documents", "OneDrive\Documents", "")) {
    $dir = Join-Path $env:USERPROFILE $base
    if (Test-Path $dir) { $candidates += @(Get-ChildItem $dir -Directory -ErrorAction SilentlyContinue | ForEach-Object { $_.FullName }) }
  }
  foreach ($c in $candidates) { if ($c -and (Test-Path (Join-Path $c "mydevagent\__init__.py"))) { return $c } }
  return $null
}

$exitCode = 0
try {
  Write-Host "MyDevAgent Studio: checking what Vio needs (an Internet connection is required)." -ForegroundColor Magenta

  Step "1/3 Python"
  $python = Find-Python
  if (-not $python) {
    Write-Host "    Installing Python 3.12..."
    Winget @("Python.Python.3.12", "--scope", "user")
    $python = Find-Python
    if (-not $python) { throw "Python was installed, but I cannot find it: restart your PC and try again." }
  }
  Add-ToPath (Split-Path $python)
  Add-ToPath (Join-Path (Split-Path $python) "Scripts")
  Ok "Python: $python"

  Step "2/3 Ollama"
  $ollama = Find-Ollama
  if (-not $ollama) {
    Write-Host "    Installing Ollama..."
    Winget @("Ollama.Ollama")
    $ollama = Find-Ollama
    if (-not $ollama) { throw "Ollama was installed, but I cannot find it: restart your PC and try again." }
  }
  Add-ToPath (Split-Path $ollama)
  if (-not (Test-OllamaRunning)) {
    $app = Join-Path (Split-Path $ollama) "ollama app.exe"
    if (Test-Path $app) { Start-Process $app } else { Start-Process $ollama -ArgumentList "serve" -WindowStyle Hidden }
    for ($i = 0; $i -lt 30 -and -not (Test-OllamaRunning); $i++) { Start-Sleep -Seconds 1 }
    if (-not (Test-OllamaRunning)) { throw "Ollama does not start: open it from the Start menu and try again." }
  }
  Ok "Ollama: $ollama"

  Step "3/3 MyDevAgent"
  $installDir = Find-MyDevAgent
  if ($installDir -and (Test-Path (Join-Path $installDir ".venv\Scripts\python.exe"))) {
    Ok "MyDevAgent is already installed in $installDir"
  } else {
    if (-not $installDir) {
      $installDir = $Folder
      if (Test-Path $installDir) { throw "The folder $installDir already exists but does not contain MyDevAgent: move it or choose another one." }
      if (Get-Command git -ErrorAction SilentlyContinue) {
        Write-Host "    Downloading MyDevAgent (English edition) with git into $installDir..."
        git clone --depth 1 -b EN "$Repo.git" $installDir
        if ($LASTEXITCODE -ne 0) { throw "git clone failed" }
      } else {
        Write-Host "    Downloading MyDevAgent (English edition) into $installDir..."
        $zip = Join-Path $env:TEMP "mydevagent.zip"
        $tmp = Join-Path $env:TEMP "mydevagent-zip"
        Invoke-WebRequest "$Repo/archive/refs/heads/EN.zip" -OutFile $zip -UseBasicParsing
        Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
        Expand-Archive $zip $tmp
        Move-Item (Get-ChildItem $tmp -Directory | Select-Object -First 1).FullName $installDir
        Remove-Item $zip, $tmp -Recurse -Force -ErrorAction SilentlyContinue
      }
    }
    Write-Host "    Setting up MyDevAgent and downloading the models (the first time this can take a while)..."
    & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $installDir "scripts\install.ps1")
    if ($LASTEXITCODE -ne 0) { throw "the MyDevAgent installation failed (exit code $LASTEXITCODE)" }
    Ok "MyDevAgent installed in $installDir"
  }
  Write-Host "`nAll set! Open MyDevAgent Studio: Vio is waiting for you in the left sidebar." -ForegroundColor Green
} catch {
  Write-Host "`nI couldn't finish: $($_.Exception.Message)" -ForegroundColor Red
  Write-Host "You can try again from MyDevAgent Studio: Vio shows you the 'Install MyDevAgent' button."
  $exitCode = 1
}
if ($Pause) { Read-Host "`nPress Enter to close" | Out-Null }
exit $exitCode
