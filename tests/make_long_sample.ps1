# Generează o "ședință" sintetică cu 3 vorbitori (TTS Windows), pentru testarea segmentării.
#   powershell -File tests\make_long_sample.ps1 [-Minutes 120]
# Ieșire: tests\data\long_meeting_<N>min.mp3 (stereo 44.1 kHz, cu zgomot roz)
param([int]$Minutes = 120)
$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot
$tmp = Join-Path $env:TEMP "meeting-mom-tts"
New-Item -ItemType Directory -Force $tmp | Out-Null

# vorbitor | text   (A = David, B = Zira, C = David cu ton coborât)
$lines = @(
  "A|Good morning everyone. Let's start the weekly clinical meeting of the internal medicine department.",
  "B|Good morning.",
  "A|First item. The patient in room twelve, sixty seven years old, admitted with community acquired pneumonia. Temperature is down, C reactive protein dropped from one hundred and forty to sixty.",
  "C|Yes.",
  "A|We continue ceftriaxone for three more days and repeat the chest X-ray on Friday.",
  "B|I agree. Should we also check the procalcitonin before stopping antibiotics?",
  "A|OK.",
  "C|The pharmacy reported that we are running low on ceftriaxone and amoxicillin clavulanate and they asked all departments to review the prescriptions for the next two weeks because the new delivery is delayed until the end of the month and there is no alternative supplier approved yet so please be careful with prolonged courses and document the indication for every patient who receives a broad spectrum antibiotic in this period",
  "B|Understood.",
  "B|Second item. The MRI schedule. We have four patients waiting, two of them with suspected stroke. Radiology can take one patient tomorrow morning and two on Thursday.",
  "A|Right.",
  "A|Then the patient from room five goes first, he has the highest NIHSS score.",
  "C|No.",
  "C|Wait. The patient from room five has a pacemaker. We need to confirm the device is MRI conditional. Otherwise we do a CT angiography instead.",
  "B|Good point. I will call cardiology after the meeting.",
  "A|Third item. Discharges. Room three and room eight can go home today. The discharge letters are ready, they need only the signature of the head of department. Room fourteen stays, the hemoglobin is still eight point two and we planned a transfusion.",
  "B|Thank you.",
  "C|One more thing. The new electronic prescription system starts on Monday. Everybody must complete the online training before Sunday evening.",
  "A|Any questions? No? Then we meet again next week. Thank you all."
)

Add-Type -AssemblyName System.Speech
$i = 0
foreach ($l in $lines) {
  $spk, $text = $l.Split("|", 2)
  $syn = New-Object System.Speech.Synthesis.SpeechSynthesizer
  $syn.SelectVoice($(if ($spk -eq "B") { "Microsoft Zira Desktop" } else { "Microsoft David Desktop" }))
  $raw = Join-Path $tmp ("{0:d3}_{1}_raw.wav" -f $i, $spk)
  $syn.SetOutputToWaveFile($raw); $syn.Speak($text); $syn.Dispose()
  $out = Join-Path $tmp ("{0:d3}_{1}.wav" -f $i, $spk)
  # C: ton coborât (alt timbru); toți: 16 kHz mono
  $af = if ($spk -eq "C") { "asetrate=22050*0.82,aresample=16000,atempo=1.1" } else { "aresample=16000" }
  ffmpeg -hide_banner -loglevel error -y -i $raw -af $af -ac 1 $out
  Remove-Item $raw
  $i++
}

# asamblare cu pauze variate (0.15 - 2.0 s), apoi repetare până la durata cerută
& "$root\.venv\Scripts\python.exe" -c @"
import glob, numpy as np, soundfile as sf
rng = np.random.default_rng(0)
parts = []
for f in sorted(glob.glob(r'$tmp\*.wav')):
    x, sr = sf.read(f, dtype='float32')
    x = x[(np.abs(x) > 1e-3).argmax():]  # taie liniștea TTS de la început
    parts += [x, np.zeros(int(rng.choice([0.15, 0.25, 0.6, 1.0, 2.0]) * sr), 'float32')]
sf.write(r'$tmp\block.wav', np.concatenate(parts), sr)
print('bloc', round(sum(len(p) for p in parts) / sr, 1), 's')
"@

$dst = Join-Path $root "tests\data\long_meeting_${Minutes}min.mp3"
ffmpeg -hide_banner -loglevel error -y -stream_loop -1 -i "$tmp\block.wav" -f lavfi -i "anoisesrc=color=pink:amplitude=0.02:r=16000" `
  -filter_complex "[0:a]volume=0.5[v];[v][1:a]amix=inputs=2:duration=first:normalize=0,aresample=44100,pan=stereo|c0=c0|c1=c0" `
  -t ($Minutes * 60) -c:a libmp3lame -b:a 96k $dst
Remove-Item -Recurse $tmp
Write-Output $dst
