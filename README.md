# CodeRaiders MedPark: procese-verbale automate din ședințe de spital

Sistem on-premise: înregistrarea ședinței → transcriere (ro/ru/en) cu vorbitori → procesul-verbal generat de LLM.

| Folder | Ce conține | Documentație |
|---|---|---|
| `AI/` | pipeline-ul audio: normalizare, diarizare, ASR (Whisper), pregătirea textului pentru LLM; `service.py` = serviciul local apelat de n8n | [AI/SETUP.md](AI/SETUP.md), [AI/docs/LLM_INPUT.md](AI/docs/LLM_INPUT.md) |
| `LLM/` | procesul-verbal din transcriere (Ollama `qwen3:8b`, local) | [LLM/README.md](LLM/README.md) |
| `preprocessing/` | serviciu de preprocesare audio (Docker, CPU); **opțional**, nu e în fluxul de upload | [preprocessing/SERVICE.md](preprocessing/SERVICE.md) |
| `docs/` | arhitectura propusă a sistemului | [docs/architecture.md](docs/architecture.md) |
| `n8n-local/` | n8n + pagina de upload, rulează local în Docker | [n8n-local/README.md](n8n-local/README.md) |

## Pornire (demo)

```powershell
powershell -ExecutionPolicy Bypass -File start_demo.ps1
```
Pornește Ollama, serviciul AI (în fereastra lui) și containerele, apoi deschideți **http://localhost:8080**, încărcați o
înregistrare și așteptați procesul-verbal. Timpi măsurați pe RTX 5070 (12 GB), 27.09:

| Înregistrare | Audio → transcriere | LLM | Total |
|---|---|---|---|
| Medpark, 12 min (prin pagina web) | ~2:50 | ~1:15 | **4:08** |
| 2 h sintetic (cel mai rău caz: o ședință de 5 min repetată de 24 de ori, ~100 de fragmente) | 21:00 | 26:00 | **47:00** |

Pe 2 h: normalizare 4:10, diarizare + VAD 3:40, ASR 13:00, LLM 97 de fragmente × ~14,5 s. O ședință reală de 2 h
(~20–40 de puncte) are de 3–4 ori mai puține fragmente pentru LLM: estimat ~30 min (nemăsurat, nu avem o astfel de
înregistrare). Măsurat cu alte aplicații deschise (inclusiv un joc pe GPU): pe calculatorul de demo, închideți
aplicațiile grele. Detalii: [AI/evaluation/RESULTS.md](AI/evaluation/RESULTS.md).

Test automat cap-coadă:
`powershell -File n8n-local\e2e_test.ps1 -Audio <fișier>`. Detalii despre flux: [n8n-local/README.md](n8n-local/README.md).

## Ce face și ce limite are (măsurat, detalii în [AI/evaluation/RESULTS.md](AI/evaluation/RESULTS.md))

- **Orice ședință din spital**: raport de gardă, consiliu, ședință de organizare. Procesul-verbal e pe **puncte
  discutate**: pacienți (diagnostic și istoric, stare clinică, paraclinic, tratament, plan/decizii, în așteptare) sau
  subiecte (gărzi, echipamente, protocoale, incidente), doar cu informația de bază. Fiecare afirmație are momentul
  `[mm:ss]` din înregistrare, iar fiecare decizie replica exactă, ca să poată fi verificată.
- **Limbi**: română (inclusiv dialectul moldovenesc), rusă, engleză, amestecate în aceeași ședință; procesul-verbal e
  în română.
- **Formate**: orice citește ffmpeg: m4a, mp3, wav, ogg/opus, flac, amr/3gp de telefon, video mp4/mov/webm.
- **Acuratețea transcrierii** (Medpark, referință manuală): WER 54.7%, CER 31.7%: textul e inteligibil, dar
  termenii medicali și numerele ies adesea deformate, iar vorbirea suprapusă se pierde. O înregistrare de telefon
  (8 kHz) e cu ~10 puncte WER mai slabă decât una de laptop/reportofon.
- **Procesul-verbal**: pe Medpark, cei 3 pacienți corecți în toate rulările, fără fapte puse la alt pacient; pe o
  ședință de test de 30 min (8 pacienți + 4 subiecte), 12/12 puncte și 95% din informația esențială. Valorile greșit
  transcrise („Pacientul 48” în loc de „patul 8”) trec în procesul-verbal: se verifică după `[mm:ss]`.
- **100% local la procesare**: modelele se încarcă de pe disc (`HF_HUB_OFFLINE=1`), LLM-ul refuză orice adresă în
  afară de localhost, serviciul AI ascultă doar pe 127.0.0.1, n8n are telemetria oprită. Internetul e necesar doar la
  instalare. **Verificare**: după instalare, opriți Wi-Fi-ul și rulați o înregistrare; procesul-verbal trebuie să apară.

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
5. LLM: `winget install --id Ollama.Ollama -e`, apoi o singură dată `ollama pull qwen3:8b` (~5 GB).
6. Sistemul complet: Docker Desktop instalat, apoi `start_demo.ps1` din rădăcina repo-ului. Scriptul creează
   `n8n-local\.env` (cheie nouă), reconstruiește pagina de upload și importă workflow-urile n8n dacă lipsesc sau
   s-au schimbat în repo. Păstrați cheia veche doar dacă vreți să mutați și datele n8n.
7. Verificarea finală, cu sistemul pornit (din rădăcina repo-ului):
   ```powershell
   AI\.venv\Scripts\python.exe -m LLM.tests                                   # trebuie: TOTUL OK
   powershell -File n8n-local\e2e_test.ps1 -Audio AI\tests\data\sample_meeting.mp3   # trebuie: GATA
   ```
   apoi aceeași înregistrare cu Wi-Fi-ul oprit (dovada că totul e local).
8. **După un `git pull` pe un PC deja instalat**: rulați din nou `start_demo.ps1` (reconstruiește pagina și
   reimportă workflow-urile schimbate). Dacă serviciul AI rula deja, închideți fereastra „MedPark AI service”
   înainte, ca să pornească cu codul nou.

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
