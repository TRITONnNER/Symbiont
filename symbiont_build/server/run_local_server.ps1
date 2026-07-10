# run_local_server.ps1 - EXPERIMENT: your PC as Symbiont VPN server (Windows, ASCII-only).
# Local sing-box SERVER on 127.0.0.1 (three protocols) + registers node 'local-pc' in backend.
# Requires: Python 3 and sing-box.exe (from tools\setup_windows.ps1, in bin\).
# Run: powershell -ExecutionPolicy Bypass -File run_local_server.ps1   (no admin needed)
$ErrorActionPreference = "Stop"
function Say($m){ Write-Host "[local-server] $m" -ForegroundColor Green }
function Warn($m){ Write-Host "[local-server] $m" -ForegroundColor Yellow }
function Die($m){ Write-Host "[local-server] ERROR: $m" -ForegroundColor Red; exit 1 }

# write text as UTF-8 WITHOUT BOM (sing-box rejects BOM in JSON)
function Save-NoBom($path, $text){
  $enc = New-Object System.Text.UTF8Encoding($false)
  [System.IO.File]::WriteAllText($path, $text, $enc)
}

$here    = Split-Path -Parent $MyInvocation.MyCommand.Path
$appdata = Join-Path $env:APPDATA "Symbiont"
$srvDir  = Join-Path $appdata "server-local"
New-Item -ItemType Directory -Force -Path $srvDir | Out-Null

# 1) find sing-box.exe
$singbox = Join-Path $appdata "bin\sing-box.exe"
if (-not (Test-Path $singbox)) {
  $alt = Get-Command sing-box.exe -ErrorAction SilentlyContinue
  if ($alt) { $singbox = $alt.Source } else {
    Die "sing-box.exe not found. Run tools\setup_windows.ps1 first."
  }
}
Say "sing-box: $singbox"

# 2) generate localhost config (always regenerate to avoid stale BOM file)
Say "generating server config (PC as server)..."
python "$here\gen_server.py" --localhost --out "$srvDir"

# 3) TLS cert for Hysteria2: openssl if present, else sing-box generate
$cert = Join-Path $srvDir "cert.pem"; $key = Join-Path $srvDir "key.pem"
$haveCert = $false
$openssl = Get-Command openssl -ErrorAction SilentlyContinue
if ($openssl) {
  Say "generating self-signed cert via openssl..."
  & openssl ecparam -genkey -name prime256v1 -out $key 2>$null
  & openssl req -new -x509 -days 3650 -key $key -out $cert -subj "/CN=www.bing.com" 2>$null
  if ((Test-Path $cert) -and (Test-Path $key)) { $haveCert = $true }
} else {
  Say "openssl not found - generating cert via sing-box..."
  try {
    $out = & $singbox generate tls-keypair www.bing.com 2>$null
    $txt = ($out -join "`n")
    $certBlock = [regex]::Match($txt, "-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----", "Singleline").Value
    $keyBlock  = [regex]::Match($txt, "-----BEGIN (EC )?PRIVATE KEY-----.*?-----END (EC )?PRIVATE KEY-----", "Singleline").Value
    if ($certBlock -and $keyBlock) {
      Save-NoBom $cert $certBlock
      Save-NoBom $key  $keyBlock
      $haveCert = $true
    }
  } catch { }
}

# 4) load config, set cert paths (or drop Hysteria2 if no cert), save WITHOUT BOM
$cfgPath = Join-Path $srvDir "config.json"
$cfg = Get-Content $cfgPath -Raw | ConvertFrom-Json
if ($haveCert) {
  foreach ($ib in $cfg.inbounds) {
    if ($ib.type -eq "hysteria2") { $ib.tls.certificate_path = $cert; $ib.tls.key_path = $key }
  }
  Say "Hysteria2 cert ready."
} else {
  Warn "no cert - removing Hysteria2 inbound for this run (Reality + SS2022 remain)."
  $cfg.inbounds = @($cfg.inbounds | Where-Object { $_.type -ne "hysteria2" })
}
Save-NoBom $cfgPath ($cfg | ConvertTo-Json -Depth 20)

# 5) validate
Say "checking server config..."
& $singbox check -c $cfgPath
if ($LASTEXITCODE -ne 0) { Die "server config invalid (see output above)" }
Say "config OK"

# 6) register node in backend
$backend = Join-Path (Split-Path $here -Parent) "backend"
if (Test-Path $backend) {
  Say "registering node local-pc in backend..."
  python "$here\publish_nodes.py" (Join-Path $srvDir "manifest_node.json") --out (Join-Path $backend "nodes.json")
  Warn "restart the backend (uvicorn) so it serves the node to the client."
} else {
  Warn "backend folder not found next to server/ - register node manually."
}

Say "STARTING LOCAL SERVER. Do not close this window."
Say "Next: 1) restart backend  2) refresh nodes in app  3) pick 'My PC' and connect."
Write-Host ""
& $singbox run -c $cfgPath -D $srvDir
