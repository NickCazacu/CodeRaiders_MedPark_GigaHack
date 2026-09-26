# Local n8n for MedPark

This runs a self-hosted n8n instance at http://localhost:5678, a local audio
upload page at http://localhost:8080, and the local preprocessing service at
http://localhost:8001. On first visit, n8n asks you to create the instance owner
account.

## Full flow: upload → minutes

```
upload page (:8080) ──► n8n "audio-upload" ──► preprocessing (Docker) ──► save to runtime/inbox/
                                   └──► AI/service.py on the host (GPU): ASR + diarization → LLM (Ollama qwen3:8b) → mom.html
upload page ◄── minutes ◄── n8n "job-status" ◄── polled every 4 s ──┘
```

The ASR and the LLM run on the Windows host next to the GPU, not in Docker. n8n reaches the host service at
`http://host.docker.internal:8765`; the service listens on `127.0.0.1` only and accepts only file names
from `runtime/inbox/`. It processes one recording at a time (ASR and LLM do not fit in 12 GB of VRAM together).

## Start

Easiest: from the repository root, `powershell -File start_demo.ps1` (checks Ollama, starts the AI service
in its own window and the containers). Manually:

```powershell
# 1. Ollama with the model (once: ollama pull qwen3:8b); it usually starts with Windows
# 2. AI service on the host, in its own window:
cd AI
.\.venv\Scripts\python.exe service.py
# 3. containers:
cd ..\n8n-local
docker compose up -d
```

The page has no CDN, analytics, or other external browser calls; its requests are proxied locally to n8n.

### Enable the included workflows (once)

Import and publish the versioned local workflows once. This stores them in the persistent n8n volume.

```bash
docker compose exec -T n8n n8n import:workflow --input=/workflows/audio-upload.json
docker compose exec -T n8n n8n publish:workflow --id=medpark-audio-upload
docker compose exec -T n8n n8n import:workflow --input=/workflows/job-status.json
docker compose exec -T n8n n8n publish:workflow --id=medpark-job-status
docker compose restart n8n
```

After changing a workflow JSON, run the same import + publish + restart again.

The upload page sends a multipart field named `audio` to the webhook. The
workflow sanitizes the filename (prefixed with a unique job id), submits the binary to the local
`preprocessing` service, saves the original upload under `runtime/inbox/`, and starts the
AI job; it answers with `{"job_id", "state": "queued"}`. The page then polls `/api/jobs/<job_id>`
(→ n8n `job-status` → AI service) and shows the minutes from `/api/jobs/<job_id>/minutes` when done.
The preprocessing service writes the normalized WAV, VAD result, and status below `runtime/preprocess/<id>/`.

### End-to-end test

```powershell
powershell -File n8n-local\e2e_test.ps1 -Audio D:\path\to\meeting.m4a
```

Measured on an RTX 5070: a 12 min meeting (56 MB m4a) → minutes in 2:40 (ASR ~1:40, LLM ~40 s).

## Stop and inspect

```bash
docker compose down
docker compose logs -f n8n
docker compose logs -f frontend
docker compose logs -f preprocess
```

Its data is stored in the Docker volume `n8n-local_n8n_data`, so workflows and
credentials survive a container restart. The MedPark repository is mounted in
the container at `/workspace/medpark`; `runtime/` is mounted at
`/workspace/runtime` in n8n and `/shared` in the preprocessing service.

## Preprocessing workflow contract

Put an input recording in `runtime/inbox/`. In an n8n **HTTP Request** node,
send a `POST` request to `http://preprocess:8000/jobs` with JSON such as:

```json
{
  "job_id": "meeting-20260926",
  "source_path": "/shared/inbox/meeting.m4a",
  "options": {"denoise": "light"}
}
```

Poll `GET http://preprocess:8000/jobs/meeting-20260926` until `state` is
`done`; a `failed` state contains the error message. The completed contract is
`runtime/preprocess/meeting-20260926/result.json`, which includes the VAD
regions and a `wav_path` relative to that file. The relative path makes the
result usable both inside Docker and by the MedPark process on the host.

The ASR pipeline in `../AI` (started by `AI/service.py`) currently does its own normalization and VAD from
the original recording; it does **not** read `result.json`. Whether it should instead consume this
service's WAV/VAD is an open team decision: both components currently implement normalization and VAD.

Note on `denoise`: in `AI/`, denoising with `noisereduce` raised the word error rate on the reference
recording (59.5% → 63.0%). The `light` ffmpeg `afftdn` filter used here was not measured; compare it with
`AI/tests/evaluate.py` before relying on it for transcription input.

This local configuration binds port 5678 to `127.0.0.1` only. It is not suitable
for external webhooks until it is deployed behind HTTPS with an externally
reachable `WEBHOOK_URL`. Port 8001 is likewise loopback-only; n8n reaches the
preprocessor over the private Docker network as `http://preprocess:8000`.
