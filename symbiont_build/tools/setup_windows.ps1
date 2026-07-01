# tools/setup_windows.ps1  (ASCII only - avoids PowerShell 5.x codepage issues)
# Installs the Symbiont engine on Windows into %APPDATA%\Symbiont:
#   * sing-box.exe  -> full VPN mode (needs a real server in config.json)
#   * GoodbyeDPI    -> transparent bypass (NO server; needs Administrator / WinDivert)
#   * ByeDPI (ciadpi)-> bypass WITHOUT admin (local proxy; app sets system proxy)
# Restart the Symbiont app afterwards.
#
# Run (normal PowerShell is fine for downloading):
#   powershell -ExecutionPolicy Bypass -File tools\setup_windows.ps1

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

# Kill any running engine processes first: a running winws.exe locks WinDivert64.sys
# and makes the download fail with "file in use" / "access denied" (seen in logs).
foreach ($p in @("winws","ciadpi","goodbyedpi","symbiont")) {
  try { taskkill /F /IM ("$p.exe") 2>$null | Out-Null } catch {}
}
Start-Sleep -Milliseconds 600

$dest = Join-Path $env:APPDATA "Symbiont"
$bin  = Join-Path $dest "bin"
New-Item -ItemType Directory -Force -Path $bin | Out-Null
$tmp = Join-Path $env:TEMP ("symb_" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Force -Path $tmp | Out-Null

function Get-Latest($repo) {
  return Invoke-RestMethod ("https://api.github.com/repos/" + $repo + "/releases/latest") -Headers @{ "User-Agent" = "symbiont-setup" }
}

Write-Host "== Symbiont engine setup ==" -ForegroundColor Cyan
Write-Host ("Target folder: " + $dest)

try {
  # 1) sing-box (full VPN)
  Write-Host ""
  Write-Host "[1/4] Downloading sing-box (VPN engine)..." -ForegroundColor Yellow
  $rel = Get-Latest "SagerNet/sing-box"
  $a = $rel.assets | Where-Object { $_.name -match "windows-amd64.*\.zip$" } | Select-Object -First 1
  if (-not $a) { throw "sing-box windows-amd64 asset not found" }
  Write-Host ("  version " + $rel.tag_name + " (" + $a.name + ")")
  $z = Join-Path $tmp "sb.zip"
  Invoke-WebRequest $a.browser_download_url -OutFile $z
  Expand-Archive -Path $z -DestinationPath $tmp -Force
  $exe = Get-ChildItem -Path $tmp -Recurse -Filter "sing-box.exe" | Select-Object -First 1
  if (-not $exe) { throw "sing-box.exe not found in archive" }
  Copy-Item $exe.FullName (Join-Path $bin "sing-box.exe") -Force
  Write-Host "  OK: sing-box.exe" -ForegroundColor Green

  # 2) GoodbyeDPI (transparent bypass, needs admin)
  Write-Host ""
  Write-Host "[2/4] Downloading GoodbyeDPI (transparent bypass, admin)..." -ForegroundColor Yellow
  try {
    $rel2 = Get-Latest "ValdikSS/GoodbyeDPI"
    $a2 = $rel2.assets | Where-Object { $_.name -match "\.zip$" } | Select-Object -First 1
    if ($a2) {
      $z2 = Join-Path $tmp "gd.zip"
      Invoke-WebRequest $a2.browser_download_url -OutFile $z2
      $gdEx = Join-Path $tmp "gd"
      Expand-Archive -Path $z2 -DestinationPath $gdEx -Force
      $gexe = Get-ChildItem -Path $gdEx -Recurse -Filter "goodbyedpi.exe" | Where-Object { $_.FullName -match "x86_64" } | Select-Object -First 1
      if (-not $gexe) { $gexe = Get-ChildItem -Path $gdEx -Recurse -Filter "goodbyedpi.exe" | Select-Object -First 1 }
      if ($gexe) {
        $gdir = Join-Path $dest "goodbyedpi"
        New-Item -ItemType Directory -Force -Path $gdir | Out-Null
        Copy-Item (Join-Path $gexe.Directory.FullName "*") $gdir -Recurse -Force
        Write-Host "  OK: goodbyedpi" -ForegroundColor Green
      } else { Write-Host "  WARNING: goodbyedpi.exe not found (admin bypass unavailable)" -ForegroundColor DarkYellow }
    } else { Write-Host "  WARNING: GoodbyeDPI asset not found" -ForegroundColor DarkYellow }
  } catch { Write-Host ("  WARNING: GoodbyeDPI download failed: " + $_.Exception.Message) -ForegroundColor DarkYellow }

  # 3) ByeDPI / ciadpi (bypass WITHOUT admin)
  Write-Host ""
  Write-Host "[3/4] Downloading ByeDPI (bypass without admin)..." -ForegroundColor Yellow
  try {
    $rel3 = Get-Latest "hufrea/byedpi"
    $a3 = $rel3.assets | Where-Object { $_.name -match "(win|windows)" -and $_.name -match "\.zip$" } | Select-Object -First 1
    if (-not $a3) { $a3 = $rel3.assets | Where-Object { $_.name -match "\.zip$" } | Select-Object -First 1 }
    if ($a3) {
      $z3 = Join-Path $tmp "bd.zip"
      Invoke-WebRequest $a3.browser_download_url -OutFile $z3
      $bdEx = Join-Path $tmp "bd"
      Expand-Archive -Path $z3 -DestinationPath $bdEx -Force
      $bexe = Get-ChildItem -Path $bdEx -Recurse -Filter "ciadpi.exe" | Select-Object -First 1
      if ($bexe) {
        $bdir = Join-Path $dest "byedpi"
        New-Item -ItemType Directory -Force -Path $bdir | Out-Null
        Copy-Item (Join-Path $bexe.Directory.FullName "*") $bdir -Recurse -Force
        Write-Host "  OK: byedpi (ciadpi.exe)" -ForegroundColor Green
      } else { Write-Host "  WARNING: ciadpi.exe not found in ByeDPI archive; put it manually into $dest\byedpi\" -ForegroundColor DarkYellow }
    } else { Write-Host "  WARNING: ByeDPI Windows asset not found; no-admin bypass unavailable until ciadpi.exe is placed in $dest\byedpi\" -ForegroundColor DarkYellow }
  } catch { Write-Host ("  WARNING: ByeDPI download failed: " + $_.Exception.Message) -ForegroundColor DarkYellow }

  Write-Host "[4/4] Downloading zapret (winws) - strongest bypass for RU (YouTube/Discord), admin..." -ForegroundColor Yellow
  try {
    $zUrl = "https://github.com/Flowseal/zapret-discord-youtube/archive/refs/heads/main.zip"
    $zZip = Join-Path $tmp "zapret.zip"
    $zEx  = Join-Path $tmp "zapret_ex"
    Invoke-WebRequest -Uri $zUrl -OutFile $zZip -UseBasicParsing
    Expand-Archive -Path $zZip -DestinationPath $zEx -Force
    $zInner = Get-ChildItem -Path $zEx -Directory | Select-Object -First 1
    if ($zInner) {
      $zdir = Join-Path $dest "zapret"
      if (Test-Path $zdir) { Remove-Item -Recurse -Force $zdir }
      New-Item -ItemType Directory -Force -Path $zdir | Out-Null
      Copy-Item (Join-Path $zInner.FullName "*") $zdir -Recurse -Force
      if (Test-Path (Join-Path $zdir "bin\winws.exe")) {
        Write-Host "  OK: zapret (general.bat + bin\winws.exe + lists)" -ForegroundColor Green
      } else {
        Write-Host "  WARNING: winws.exe not found after extraction" -ForegroundColor DarkYellow
      }
    } else { Write-Host "  WARNING: zapret archive structure unexpected" -ForegroundColor DarkYellow }
  } catch { Write-Host ("  WARNING: zapret download failed: " + $_.Exception.Message) -ForegroundColor DarkYellow }

  Write-Host ""
  Write-Host ("Installed to: " + $dest) -ForegroundColor Cyan
  Write-Host ""
  Write-Host "How it picks a mode automatically:"
  Write-Host "  - If you run Symbiont as Administrator -> zapret/winws if installed (best for RU),"
  Write-Host "    otherwise GoodbyeDPI (transparent bypass)."
  Write-Host "  - Otherwise -> ByeDPI (local proxy); the app sets the per-user system proxy"
  Write-Host "    and restores it on disconnect. Works for apps that honor the system proxy"
  Write-Host "    (Chrome/Edge); may take a few seconds or reopening the page."
  Write-Host "  - Full VPN: put a real sing-box config at:"
  Write-Host ("      " + (Join-Path $dest "config.json"))
  Write-Host ""
  Write-Host "DPI bypass is network-specific. If a site stays blocked, tune the strategy:"
  Write-Host ("  GoodbyeDPI args: " + (Join-Path $dest "bypass_args.txt") + "  (default: -p -r -s -f 2 -k 2 -n -e 2)")
  Write-Host ("  ByeDPI args:     " + (Join-Path $dest "byedpi_args.txt") + "  (default: -p 1080 --disorder 1 --auto=torst --tlsrec 1+s)")
  Write-Host ("  zapret strategy: pick another "+(Join-Path $dest "zapret")+"\\general*.bat and set its name in "+(Join-Path $dest "zapret_bat.txt"))
  Write-Host "See RUNBOOK_NATIVE.md for details."
}
finally {
  Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
}
