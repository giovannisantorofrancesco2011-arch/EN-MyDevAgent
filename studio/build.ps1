# Builds MyDevAgent Studio (English edition) for Windows: VSCodium + the MyDevAgent extension + Vio's theme and icons,
# then the installer with Inno Setup. Run by .github/workflows/studio.yml after it has created extension\mydevagent.vsix.
# Needs: gh (with GH_TOKEN), node, Inno Setup 6 (installed with choco if missing).
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
$root = Split-Path $PSScriptRoot -Parent
$branding = Join-Path $PSScriptRoot "branding"
$build = Join-Path $root "build"
$app = Join-Path $build "app"
$out = Join-Path $build "out"
$extras = Join-Path $app "extras"
function Assert-Success($what) { if ($LASTEXITCODE) { throw "$what failed (exit code $LASTEXITCODE)" } }

Remove-Item $build -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory $build, $out | Out-Null
$version = (Get-Content (Join-Path $root "extension\package.json") -Raw | ConvertFrom-Json).version
$vsix = Join-Path $root "extension\mydevagent.vsix"
if (-not (Test-Path $vsix)) { throw "${vsix} is missing: build the extension first (npm run package)" }

Write-Host "==> 1/5 VSCodium"
$release = gh release view --repo VSCodium/vscodium --json tagName,assets | ConvertFrom-Json
Assert-Success "gh release view"
$zip = $release.assets | Where-Object { $_.name -match '^VSCodium-win32-x64-[\d.]+\.zip$' } | Select-Object -First 1
if (-not $zip) { throw "the VSCodium release $($release.tagName) has no Windows x64 zip" }
gh release download $release.tagName --repo VSCodium/vscodium --pattern $zip.name --dir $build
Assert-Success "gh release download"
Expand-Archive (Join-Path $build $zip.name) $app
Write-Host "    VSCodium $($release.tagName)"

Write-Host "==> 2/5 Name, folders and no VSCodium updates"
$product = Get-ChildItem $app -Recurse -Filter product.json | Where-Object { $_.FullName -match '[\\/]resources[\\/]app[\\/]product\.json$' } |
  Select-Object -First 1
if (-not $product) { throw "VSCodium's product.json not found" }
node (Join-Path $PSScriptRoot "patch-product.js") $product.FullName
Assert-Success "patch-product.js"
$resources = $product.DirectoryName
$exe = Get-ChildItem $app -Filter *.exe | Where-Object { $_.Name -notmatch '^unins' } | Select-Object -First 1
$cli = Get-ChildItem (Join-Path $app "bin") -Filter *.cmd | Select-Object -First 1
if (-not $exe -or -not $cli) { throw "cannot find the VSCodium executable or CLI in $app" }
Write-Host "    executable: $($exe.Name), CLI: bin\$($cli.Name)"

Write-Host "==> 3/5 MyDevAgent extension and Studio settings"
$extensions = Join-Path $resources "extensions"
$unpacked = Join-Path $build "vsix"
Copy-Item $vsix (Join-Path $build "mydevagent.zip")
Expand-Archive (Join-Path $build "mydevagent.zip") $unpacked
Move-Item (Join-Path $unpacked "extension") (Join-Path $extensions "mydevagent")
Copy-Item (Join-Path $PSScriptRoot "defaults") (Join-Path $extensions "mydevagent-studio-defaults") -Recurse
New-Item -ItemType Directory $extras | Out-Null
Copy-Item (Join-Path $root "extension\setup\install-mydevagent.ps1") $extras

Write-Host "==> 4/5 Vio's icons"
$rcedit = Join-Path $build "rcedit.exe"
Invoke-WebRequest "https://github.com/electron/rcedit/releases/download/v2.0.0/rcedit-x64.exe" -OutFile $rcedit -UseBasicParsing
& $rcedit $exe.FullName --set-icon (Join-Path $branding "vio.ico") `
  --set-version-string FileDescription "MyDevAgent Studio" --set-version-string ProductName "MyDevAgent Studio" `
  --set-version-string CompanyName "MyDevAgent" --set-version-string LegalCopyright "(c) 2026 gio - MyDevAgent Studio. All rights reserved. Based on VSCodium (MIT)"
Assert-Success "rcedit"
$replaced = 0
foreach ($file in Get-ChildItem $app -Recurse -File -Include "letterpress-*.svg", "code-icon.svg", "code_150x150.png", "code_70x70.png") {
  $ours = @{ "code-icon.svg" = "vio.svg"; "code_150x150.png" = "tile-150.png"; "code_70x70.png" = "tile-70.png" }[$file.Name]
  if (-not $ours) { $ours = $file.Name }
  $source = Join-Path $branding $ours
  if (Test-Path $source) { Copy-Item $source $file.FullName -Force; $replaced++ }
}
Write-Host "    $replaced VSCodium images replaced with Vio"

Write-Host "==> 5/5 Installer"
$findIscc = { Get-ChildItem "${env:ProgramFiles(x86)}\Inno Setup *\ISCC.exe", "$env:ProgramFiles\Inno Setup *\ISCC.exe" -ErrorAction SilentlyContinue |
  Select-Object -First 1 }
$iscc = & $findIscc
if (-not $iscc) {
  choco install innosetup -y --no-progress | Out-Host
  Assert-Success "choco install innosetup"
  $iscc = & $findIscc
}
Write-Host "    $($iscc.FullName)"
& $iscc.FullName "/DStudioVersion=$version" "/DStudioSource=$app" "/DExe=$($exe.Name)" "/DBranding=$branding" "/O$out" `
  (Join-Path $PSScriptRoot "installer.iss")
Assert-Success "Inno Setup"
Copy-Item $vsix (Join-Path $out "mydevagent-en.vsix")
Get-ChildItem $out | ForEach-Object { Write-Host ("    {0}  {1:N1} MB" -f $_.Name, ($_.Length / 1MB)) }
