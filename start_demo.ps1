# Pornește tot sistemul local: Ollama (LLM) -> serviciul AI (ASR + LLM, pe GPU) -> containerele n8n.
#   powershell -ExecutionPolicy Bypass -File start_demo.ps1
# Apoi deschideți http://localhost:8080 și încărcați o înregistrare.
# "Continue": docker scrie progresul pe stderr, pe care Windows PowerShell 5.1 l-ar trata ca eroare fatală.
# Fiecare pas e verificat explicit mai jos (throw la eșec).
$ErrorActionPreference = "Continue"
$root = $PSScriptRoot
$env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [Environment]::GetEnvironmentVariable("Path", "User")

function Up($url) { try { Invoke-WebRequest -UseBasicParsing -TimeoutSec 3 $url | Out-Null; $true } catch { $false } }
function WaitFor($url, $what, $seconds) {
  $deadline = (Get-Date).AddSeconds($seconds)
  while (-not (Up $url)) { if ((Get-Date) -gt $deadline) { throw "$what nu răspunde la $url" }; Start-Sleep 3 }
}

# 1. Ollama + modelul LLM
if (-not (Up "http://localhost:11434/api/version")) {
  $ollama = (Get-Command ollama -ErrorAction SilentlyContinue).Source
  if (-not $ollama) { $ollama = "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe" }
  if (-not (Test-Path $ollama)) { throw "Ollama nu e instalat: winget install --id Ollama.Ollama -e" }
  Start-Process $ollama -ArgumentList "serve" -WindowStyle Hidden
  WaitFor "http://localhost:11434/api/version" "Ollama" 60
}
$model = "qwen3:8b"
$tags = (Invoke-RestMethod http://localhost:11434/api/tags).models.name
if ($tags -notcontains $model) { throw "Lipsește modelul LLM. O singură dată: ollama pull $model" }
Write-Output "[ok] Ollama cu $model"

# 2. Serviciul AI pe host, în fereastra lui (acolo se văd log-urile)
if (-not (Up "http://127.0.0.1:8765/healthz")) {
  $py = Join-Path $root "AI\.venv\Scripts\python.exe"
  if (-not (Test-Path $py)) { throw "Lipsește AI\.venv: vezi README.md, «Pe calculatorul nou»" }
  Start-Process powershell -WorkingDirectory (Join-Path $root "AI") -ArgumentList "-NoExit", "-Command",
    "`$host.UI.RawUI.WindowTitle = 'MedPark AI service'; `$env:PYTHONIOENCODING = 'utf-8'; & '$py' service.py"
  WaitFor "http://127.0.0.1:8765/healthz" "Serviciul AI" 60
}
Write-Output "[ok] serviciul AI: http://127.0.0.1:8765"

# 3. Docker + containerele
if (-not (docker info 2>$null)) {
  $dd = "C:\Program Files\Docker\Docker\Docker Desktop.exe"
  if (-not (Test-Path $dd)) { throw "Docker Desktop nu e instalat" }
  Start-Process $dd
  Write-Output "aștept Docker Desktop..."
  $deadline = (Get-Date).AddMinutes(3)
  while (-not (docker info 2>$null)) { if ((Get-Date) -gt $deadline) { throw "Docker nu a pornit" }; Start-Sleep 5 }
}
Set-Location (Join-Path $root "n8n-local")
if (-not (Test-Path .env)) {
  $bytes = New-Object byte[] 32; [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
  "N8N_ENCRYPTION_KEY=" + (($bytes | ForEach-Object { $_.ToString("x2") }) -join "") | Out-File -Encoding ascii .env
  Write-Output "[ok] n8n-local\.env creat (cheie nouă)"
}
$log = docker compose up -d --build 2>&1
if ($LASTEXITCODE -ne 0) { $log | Select-Object -Last 15; throw "docker compose up a eșuat" }
WaitFor "http://localhost:5678/healthz" "n8n" 120

# 4. Workflow-urile (o singură dată pe un PC nou)
$have = docker compose exec -T n8n n8n list:workflow 2>$null
$missing = @("audio-upload", "job-status") | Where-Object { -not ($have -match "medpark-$_\|") }
if ($missing) {
  foreach ($w in $missing) {
    docker compose exec -T n8n n8n import:workflow --input=/workflows/$w.json 2>&1 | Out-Null
    docker compose exec -T n8n n8n publish:workflow --id=medpark-$w 2>&1 | Out-Null
  }
  docker compose restart n8n 2>&1 | Out-Null
  WaitFor "http://localhost:5678/healthz" "n8n" 120
  Write-Output "[ok] workflow-uri importate: $($missing -join ', ')"
}
WaitFor "http://localhost:8080" "Pagina de upload" 60
Write-Output "[ok] totul pornit. Deschideți http://localhost:8080"
