# CodeRaiders MedPark: procese-verbale automate din ședințe de spital

Sistem on-premise: înregistrarea ședinței → transcriere (ro/ru/en) cu vorbitori → procesul-verbal generat de LLM.

| Folder | Ce conține | Documentație |
|---|---|---|
| `AI/` | pipeline-ul audio: normalizare, diarizare, ASR (Whisper), pregătirea textului pentru LLM | [AI/SETUP.md](AI/SETUP.md), [AI/docs/LLM_INPUT.md](AI/docs/LLM_INPUT.md) |
| `preprocessing/` | serviciu de preprocesare audio (Docker, CPU) | [preprocessing/SERVICE.md](preprocessing/SERVICE.md) |
| `n8n-local/` | n8n + pagina de upload, rulează local în Docker | [n8n-local/README.md](n8n-local/README.md) |

## Mutarea proiectului pe alt calculator

Git aduce **doar codul și documentația**. Restul se mută separat sau se recreează:

| Ce | Mărime | Cum ajunge pe noul PC |
|---|---|---|
| cod, config, documentație | < 1 MB | `git clone` |
| `AI/.venv` (pachetele Python) | ~5 GB | **nu se copiază** (nu merge mutat): se recreează din `AI/requirements.txt` |
| `AI/models/` (Whisper, pyannote) | ~4.5 GB | copiat pe stick/drive privat, **sau** descărcat din nou (AI/SETUP.md §5) |
| `n8n-local/.env` (cheia n8n) | 1 linie | secret: se copiază privat, sau se generează una nouă |
| înregistrări, `AI/jobs/`, `AI/tests/reference/*.txt` | variabil | **date medicale**: doar dacă e nevoie, doar pe canal privat, apoi șterse |

### Pe calculatorul vechi (cel care are proiectul funcțional)
1. `git push`: toate modificările trebuie să fie pe GitHub.
2. Copiați `AI/models/` pe un stick sau într-un drive **privat** al echipei. Pentru `large-v3` + pyannote e suficient
   `faster-whisper-large-v3/` și `pyannote/`; `faster-whisper-medium/` e opțional.
3. Doar dacă e nevoie: `n8n-local/.env` și, pe canal privat, înregistrările de test și referințele.

### Pe calculatorul nou
1. Instalați: **Git**, **Python 3.11** (sau 3.12; **nu** 3.13+), **ffmpeg** (`winget install --id Gyan.FFmpeg -e`),
   driver NVIDIA recent (suport CUDA ≥ 12.8; CUDA Toolkit nu e necesar). Pentru n8n: **Docker Desktop**. Spațiu: ~15 GB.
2. Codul și mediul Python:
   ```powershell
   git clone https://github.com/NickCazacu/CodeRaiders_MedPark_GigaHack.git
   cd CodeRaiders_MedPark_GigaHack\AI
   py -3.11 -m venv .venv
   .\.venv\Scripts\python.exe -m pip install -r requirements.txt
   ```
3. Modelele: copiați folderul `models\` în `AI\models\`, sau descărcați-le după AI/SETUP.md §5. Pentru pyannote,
   fiecare utilizator acceptă condițiile pe Hugging Face, chiar dacă modelele sunt copiate.
4. Verificare:
   ```powershell
   .\.venv\Scripts\python.exe tests\check_gpu.py            # trebuie: cuda available: True
   .\.venv\Scripts\python.exe -m tests.test_segment_rules
   .\.venv\Scripts\python.exe run_pipeline.py tests\data\sample_meeting.mp3 --job-id test_nou_pc
   ```
5. n8n (opțional): `cd ..\n8n-local`, copiați `.env.example` în `.env` și puneți o cheie nouă (sau pe cea veche, ca să
   păstrați datele n8n), apoi urmați n8n-local/README.md. Contul n8n și workflow-ul se refac (import din `workflows/`).

**Fără placă NVIDIA:** în `AI/config.yaml` puneți `whisper.device: cpu`, `whisper.compute_type: int8`, `whisper.model: medium`
și `diarization.device: cpu`. Merge, dar de multe ori mai lent. **Mac:** vezi AI/SETUP.md §3.

### Cine ce face
| Cine | Ce |
|---|---|
| cel cu proiectul funcțional | push; copiază `AI/models/` pe drive privat; dă înregistrările de test doar pe canal privat |
| cel care instalează pe PC nou | pașii de mai sus; la final `check_gpu.py` și un `run_pipeline.py` de test |
| echipa LLM | nu are nevoie de modele sau GPU: lucrează pe `AI/docs/llm_input.example.json` și pe un `llm_input.json` real primit privat |
| cine rulează n8n/preprocessing | Docker Desktop, `n8n-local/.env`, importul workflow-ului |
| oricine rulează diarizarea | cont Hugging Face și acceptarea condițiilor pyannote (AI/SETUP.md §5b) |

**Nu urcați niciodată pe GitHub** înregistrări, `AI/jobs/`, `AI/models/`, `.env` sau tokenuri. `.gitignore` le exclude,
dar verificați cu `git status` înainte de commit.
