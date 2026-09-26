# Test cap-coadă prin pagina locală, ca un browser: upload -> n8n -> preprocess -> AI (ASR + LLM) -> minute.
#   powershell -File n8n-local\e2e_test.ps1 -Audio <fisier> [-TimeoutMin 30]
# Cere: containerele pornite (docker compose up -d), workflow-urile publicate, AI\service.py pornit pe host.
param([Parameter(Mandatory)] [string]$Audio, [int]$TimeoutMin = 30, [string]$Base = "http://localhost:8080")
$ErrorActionPreference = "Stop"
$t0 = Get-Date
function Elapsed { "{0,5:N0}s" -f ((Get-Date) - $t0).TotalSeconds }

Write-Output "upload: $Audio ($([math]::Round((Get-Item $Audio).Length / 1MB, 1)) MB)"
$resp = curl.exe -s -S -X POST -F "audio=@$Audio" "$Base/api/upload"
Write-Output "$(Elapsed) raspuns upload: $resp"
$job = $resp | ConvertFrom-Json
if (-not $job.job_id) { throw "upload fara job_id" }

$last = ""
do {
  Start-Sleep 5
  $s = curl.exe -s "$Base/api/jobs/$($job.job_id)" | ConvertFrom-Json
  $line = "$($s.state) $($s.stage) $($s.progress)"
  if ($line -ne $last) { Write-Output "$(Elapsed) $line"; $last = $line }
} while ($s.state -in "queued", "running" -and ((Get-Date) - $t0).TotalMinutes -lt $TimeoutMin)

if ($s.state -ne "done") { throw "job $($job.job_id): state=$($s.state) error=$($s.error)" }
$html = curl.exe -s -D - "$Base/api/jobs/$($job.job_id)/minutes"
$csp = ($html -split "`n" | Select-String "Content-Security-Policy").Line
$title = [regex]::Match(($html -join "`n"), "<h1>(.*?)</h1>").Groups[1].Value
Write-Output "$(Elapsed) GATA: job=$($job.job_id) audio=$($s.audio_duration_s)s procesare=$($s.seconds)s"
Write-Output "minute: <h1>$title</h1> | $([math]::Round(($html -join "`n").Length / 1KB)) KB | $csp"
Write-Output "pagina: $Base  (minutele: $Base/api/jobs/$($job.job_id)/minutes)"
