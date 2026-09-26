# SETUP: meeting-mom

Pipeline audio on-premise pentru ședințe de spital (RO/RU/EN + termeni medicali).
Inferența rulează **fără rețea**. Rețeaua e necesară o singură dată, la instalare și la descărcarea modelelor.

## 1. Cerințe

| Componentă | Versiune folosită | Note |
|---|---|---|
| Python | **3.11.9** | 3.11 sau 3.12. **Nu 3.13+** (lipsesc wheel-uri pentru o parte din dependențe). |
| ffmpeg / ffprobe | 9.0.2 (Gyan full build) | Trebuie să fie în `PATH`. |
| Driver NVIDIA | 591.86 (CUDA 13.1) | Doar pe Windows cu GPU. Driverul trebuie să suporte CUDA ≥ 12.8. CUDA Toolkit **nu** e necesar. |
| GPU testat | RTX 5070 12 GB (sm_120, Blackwell) | Are nevoie de torch cu cu128. Wheel-urile mai vechi (cu121/cu124) nu au sm_120. |

Instalare ffmpeg:
- Windows: `winget install --id Gyan.FFmpeg -e` (apoi redeschide terminalul)
- Mac: `brew install ffmpeg`

## 2. Mediul virtual

Windows (PowerShell):
```powershell
cd D:\Hackaton\CodeRaiders_MedPark_GigaHack
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
```

Mac:
```bash
python3.11 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
```

## 3. Instalarea pachetelor

`requirements.in` are doar dependențele directe. `requirements.txt` are versiunile exacte (`pip freeze`), ca toată echipa să aibă aceleași versiuni.

### Windows + NVIDIA (CUDA 12.8)
`requirements.txt` conține deja `--extra-index-url https://download.pytorch.org/whl/cu128`, așa că se instalează `torch==2.11.0+cu128`:
```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Instalare de la zero, fără versiuni fixate (de exemplu la un upgrade):
```powershell
.\.venv\Scripts\python.exe -m pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu128
.\.venv\Scripts\python.exe -m pip install -r requirements.in
```
Instalează torch **înainte** de restul. Altfel pip poate aduce varianta CPU de pe PyPI.

Verificarea GPU-ului:
```powershell
.\.venv\Scripts\python.exe tests\check_gpu.py
```
Trebuie să afișeze `cuda available: True`, `GPU: NVIDIA GeForce RTX 5070`, `sm_120` în `arch list` și `faster-whisper tiny pe cuda/float16 OK`.

### Mac (Apple Silicon)
torch-ul standard de pe PyPI, fără CUDA. faster-whisper (CTranslate2) rulează **doar pe CPU** pe Mac (nu are backend Metal/MPS). Folosește aceleași versiuni, fără sufixul `+cu128`:
```bash
grep -v '^--extra-index-url' requirements.txt | sed 's/+cu128//' > /tmp/requirements-mac.txt
.venv/bin/python -m pip install -r /tmp/requirements-mac.txt
```
În `config.yaml` pe Mac: `whisper.device: cpu`, `whisper.compute_type: int8`.

## 4. Particularități Windows (important)

- **CTranslate2 și cuBLAS:** faster-whisper pe CUDA dă `RuntimeError: Library cublas64_12.dll is not found` dacă `torch` nu e importat înainte. torch cu128 aduce `cublas64_12.dll` și `cudnn64_9.dll` și le încarcă în proces. Regula: **`import torch` înainte de `from faster_whisper import ...`**. Nu trebuie instalat CUDA Toolkit.
- **Telemetrie pyannote:** pyannote.audio 4.x trimite implicit metrici la `otel.pyannote.ai`. `pipeline/common.py` setează `PYANNOTE_METRICS_ENABLED=false` și `HF_HUB_OFFLINE=1`. Orice modul nou importă `pipeline.common` primul.
- **Symlink-uri HF:** fără Developer Mode, cache-ul Hugging Face copiază fișierele în loc să facă symlink-uri. Ocupă mai mult spațiu, dar e inofensiv. Descărcăm oricum în `models/` cu `--local-dir`.

## 5. Descărcarea unică a modelelor (singurul pas cu rețea)

### 5a. Whisper (faster-whisper)
```powershell
.\.venv\Scripts\hf.exe download Systran/faster-whisper-large-v3 --local-dir models\faster-whisper-large-v3
# opțional, mai rapid:
.\.venv\Scripts\hf.exe download mobiuslabsgmbh/faster-whisper-large-v3-turbo --local-dir models\faster-whisper-large-v3-turbo
```

### 5b. pyannote (diarizare: speaker-diarization-3.1), modele gated
Pipeline-ul 3.1 folosește două sub-modele: segmentation-3.0 și wespeaker-resnet34. Pe lângă ele, pyannote 4.x
cere un PLDA pe care îl ia implicit de pe HF din community-1 (clustering-ul 3.1 nu îl folosește, dar
constructorul îl încarcă), deci descărcăm și `plda/` din community-1.

1. Creează un cont pe https://huggingface.co.
2. Acceptă condițiile de utilizare (logat, butonul „Agree and access”) pe fiecare pagină:
   - https://huggingface.co/pyannote/speaker-diarization-3.1
   - https://huggingface.co/pyannote/segmentation-3.0
   - https://huggingface.co/pyannote/speaker-diarization-community-1
3. Creează un token *Read* la https://huggingface.co/settings/tokens și autentifică-te:
   `.\.venv\Scripts\hf.exe auth login`
4. Descarcă o singură dată în `models/pyannote/` (structura oglindește ID-urile HF):
   ```powershell
   $env:HF_HUB_OFFLINE = "0"
   $hf = ".\.venv\Scripts\hf.exe"
   & $hf download pyannote/speaker-diarization-3.1 --local-dir models\pyannote\speaker-diarization-3.1
   & $hf download pyannote/segmentation-3.0 --local-dir models\pyannote\segmentation-3.0
   & $hf download pyannote/wespeaker-voxceleb-resnet34-LM --local-dir models\pyannote\wespeaker-voxceleb-resnet34-LM
   & $hf download pyannote/speaker-diarization-community-1 --include "plda/*" --local-dir models\pyannote\speaker-diarization-community-1
   Remove-Item Env:HF_HUB_OFFLINE
   & $hf auth logout
   ```
5. De aici încolo totul rulează offline: `HF_HUB_OFFLINE=1` e setat automat de `pipeline/common.py`. `pipeline/segment.py` citește
   `config.yaml`-ul lui 3.1 și înlocuiește ID-urile HF ale sub-modelelor cu căile locale din `config.yaml` (secțiunea `diarization`).
   Tokenul nu mai e necesar.
6. Notă Windows: pyannote primește audio-ul ca tensor (`waveform`), nu ca fișier, pentru că decodorul lui (torchcodec)
   cere DLL-urile ffmpeg „shared”, iar build-ul ffmpeg instalat e static.

Pentru mașinile din spital fără internet, copiază folderul `models/` de pe o mașină care a făcut pasul de mai sus.

### 5c. Silero VAD
Nu trebuie descărcat nimic. Modelul e inclus în pachetul pip `silero-vad`.

## 6. Ce model Whisper pe ce mașină

| Mașină | device | compute_type | Model | Note |
|---|---|---|---|---|
| Windows, RTX 5070 12 GB | `cuda` | `float16` | `large-v3` | Implicit. Cea mai bună acuratețe pe RO/RU amestecat. ~4–5 GB VRAM, rămâne loc și pentru pyannote. |
| Windows, RTX 5070 (rapid) | `cuda` | `float16` | `large-v3-turbo` | De câteva ori mai rapid, puțin mai slab pe termeni rari. |
| GPU NVIDIA 6–8 GB | `cuda` | `int8_float16` | `large-v3` | Mai puțin VRAM. |
| Mac Apple Silicon, ≥ 16 GB RAM | `cpu` | `int8` | `large-v3-turbo` | Doar CPU. O ședință de 2 h durează semnificativ. |
| Mac / laptop CPU (dezvoltare) | `cpu` | `int8` | `small` | Pentru teste rapide, nu pentru producție. |

## 7. Rulare

Tot pipeline-ul (ingest → normalize → segment → asr → postprocess → pack), reluabil:
```powershell
.\.venv\Scripts\python.exe run_pipeline.py sedinta.m4a --model medium --device cuda [--no-diarization] [--denoise] [--language ro]
```
Ieșirea finală e `jobs/<job_id>/llm_input.json`: replici `[mm:ss] SPEAKER: text`, cu ` [?]` la încredere scăzută, grupate în
ferestre de ~3500 tokeni cu suprapunere de 3 replici. Fișiere intermediare: `asr.jsonl` (scris incremental,
reluat de la ultimul segment) și `transcript.json` (după postprocesare; segmentele eliminate rămân cu `dropped`).

Pentru a compara modele sau setări pe același fișier, dă fiecărei variante propriul job, de exemplu
`--job-id Medpark_large-v3_ro`. Același `asr.jsonl` nu poate conține rezultate de la modele diferite: pipeline-ul se oprește cu un mesaj.

Glossary (opțional): `glossary/prompt.txt` înlocuiește `whisper.initial_prompt` din config, iar `glossary/hotwords.txt`
se trimite ca `hotwords`, câte un termen pe linie. Liniile care încep cu `#` sunt ignorate.

Etapele pe rând:
```powershell
.\.venv\Scripts\python.exe -m pipeline.ingest cale\catre\sedinta.mp3        # afișează job_id
.\.venv\Scripts\python.exe -m pipeline.normalize <job_id> [--denoise]
.\.venv\Scripts\python.exe -m pipeline.segment <job_id> [--no-diarization] [--export-segments]
.\.venv\Scripts\python.exe -m pipeline.segment_stats <job_id>              # verificarea segmentării
.\.venv\Scripts\python.exe -m pipeline.asr <job_id> [--model medium]
.\.venv\Scripts\python.exe -m pipeline.postprocess <job_id>
.\.venv\Scripts\python.exe -m pipeline.pack_for_llm <job_id>
```
Rezultatele sunt în `jobs/<job_id>/`: `source.*`, `probe.json`, `audio_16k.wav`, `audio_16k_denoised.wav` (opțional),
`diarization.json` (turele pyannote, cache), `segments.json` (`id`, `speaker`, `start`, `end`), `segments/*.wav`
(doar cu `--export-segments`) și `status.json`, cu durata fiecărei etape. Dacă rulezi din nou, etapele terminate sunt sărite.
Ca să refaci segmentarea cu alți parametri, șterge `segments.json`. Diarizarea rămâne în cache în `diarization.json`.

Teste și unelte:
```powershell
.\.venv\Scripts\python.exe -m tests.test_segment_rules                     # regulile de segmentare
.\.venv\Scripts\python.exe -m tests.test_asr_mapping                       # rezultate batched -> segmentul corect
.\.venv\Scripts\python.exe -m tests.compare_jobs JOB_A JOB_B [--all]       # compară 2 modele/setări segment cu segment
powershell -File tests\make_long_sample.ps1 -Minutes 120                   # ședință sintetică de 2 h (3 vorbitori TTS)
powershell -File tests\measure.ps1 -m pipeline.segment <job_id>            # timp + RAM maxim
```

## 8. Adăugarea unui pachet nou

1. `pip install <pachet>` în `.venv`
2. Adaugă-l în `requirements.in`
3. Regenerează `requirements.txt` (păstrează header-ul cu `--extra-index-url`):
   `.\.venv\Scripts\python.exe -m pip freeze`
4. Actualizează acest fișier dacă pachetul are particularități de platformă.
