# Rulează o comandă python din .venv și raportează timpul și memoria maximă (peak working set).
#   powershell -File tests\measure.ps1 -m pipeline.segment JOB_ID --no-diarization
# Pe Windows, .venv\Scripts\python.exe e un launcher care pornește interpretorul real
# ca proces copil, deci măsurăm memoria copiilor, nu a launcher-ului.
$py = Join-Path (Split-Path $PSScriptRoot) ".venv\Scripts\python.exe"
$sw = [Diagnostics.Stopwatch]::StartNew()
$p = Start-Process -FilePath $py -ArgumentList $args -NoNewWindow -PassThru
$null = $p.Handle  # necesar ca ExitCode să fie disponibil după ieșire
$peak = @{}
while (-not $p.HasExited) {
  foreach ($c in Get-CimInstance Win32_Process -Filter "ParentProcessId=$($p.Id)" -ErrorAction SilentlyContinue) {
    try { $peak[$c.ProcessId] = [Math]::Max([long]$peak[$c.ProcessId], (Get-Process -Id $c.ProcessId).PeakWorkingSet64) } catch {}
  }
  Start-Sleep -Milliseconds 300
}
$p.WaitForExit()
$max = ($peak.Values | Measure-Object -Maximum).Maximum
Write-Output ("==> {0}: {1:N1} s, RAM max {2:N0} MB, exit {3}" -f ($args -join " "), $sw.Elapsed.TotalSeconds, ($max / 1MB), $p.ExitCode)
