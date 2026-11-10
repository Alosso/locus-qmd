<#
Start the Locus server on this PC and show the address to open on your phone.

    powershell -ExecutionPolicy Bypass -File scripts\start.ps1

Needs Tailscale installed and signed in on this PC and on the phone (same account), and
`tailscale serve --bg 8080` run once so the server is published inside your tailnet over
HTTPS. Nothing is exposed to the public internet.
#>
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

if (-not (Test-Path "corpus\wh40k\graph.jsonl")) {
    Write-Host "No corpus found. Run 'bash scripts/setup.sh' first." -ForegroundColor Red
    exit 1
}

$ts = (Get-Command tailscale -ErrorAction SilentlyContinue).Source
if (-not $ts -and (Test-Path "C:\Program Files\Tailscale\tailscale.exe")) { $ts = "C:\Program Files\Tailscale\tailscale.exe" }

if ($ts) {
    try {
        $status = & $ts status --json 2>$null | ConvertFrom-Json
        $name = $status.Self.DNSName.TrimEnd(".")
        if ($status.BackendState -ne "Running") {
            Write-Host "Tailscale is installed but not connected. Open Tailscale and sign in." -ForegroundColor Yellow
        } elseif ($name) {
            Write-Host ""
            Write-Host "On your phone (Tailscale app connected), open:" -ForegroundColor Green
            Write-Host "    https://$name" -ForegroundColor Green
            Write-Host "If that does not load, run this once and try again:  tailscale serve --bg 8080"
            Write-Host ""
        }
    } catch {
        Write-Host "Could not read Tailscale status: $($_.Exception.Message)" -ForegroundColor Yellow
    }
} else {
    Write-Host "Tailscale is not installed; the server will only be reachable from this PC (http://localhost:8080)." -ForegroundColor Yellow
}

Write-Host "Starting the server (Ctrl+C to stop)..."
npm --prefix server run serve
