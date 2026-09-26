# Local n8n for MedPark

This runs a self-hosted n8n instance at http://localhost:5678, a local audio
upload page at http://localhost:8080, and the local preprocessing service at
http://localhost:8001. On first visit, n8n asks you to create the instance owner
account.

## Start

```bash
cd n8n-local
docker compose up -d
```

This is the single command to start the frontend and n8n. The page has no CDN,
analytics, or other external browser calls; its upload is proxied locally to n8n.

### Enable the included upload workflow (once)

After creating the n8n owner account, import and activate the versioned local
workflow once. This stores it in the persistent n8n volume.

```bash
docker compose exec -T n8n n8n import:workflow --input=/workflows/audio-upload.json
docker compose exec -T n8n n8n publish:workflow --id=medpark-audio-upload
docker compose restart n8n
```

The upload page sends a multipart field named `audio` to the webhook. The
workflow sanitizes the filename, submits the binary to the local
`preprocessing` service, then saves the original upload under `runtime/inbox/`.
The service stages its private source and writes the normalized WAV, VAD result,
and status below `runtime/preprocess/<job_id>/`.

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

The ASR pipeline lives in `../AI` and currently does its own normalization and VAD from the original
recording; it does **not** read `result.json` yet (there is no `--preprocess-result` option). On the host:

```powershell
cd ..\AI
.\.venv\Scripts\python.exe run_pipeline.py ..\n8n-local\runtime\inbox\meeting.m4a --job-id meeting-20260926
```

The output for the LLM is `AI/jobs/meeting-20260926/llm_input.json` (format: `AI/docs/LLM_INPUT.md`).
Whether the pipeline should instead consume this service's WAV/VAD is an open team decision: both
components currently implement normalization and VAD.

Note on `denoise`: in `AI/`, denoising with `noisereduce` raised the word error rate on the reference
recording (59.5% → 63.0%). The `light` ffmpeg `afftdn` filter used here was not measured; compare it with
`AI/tests/evaluate.py` before relying on it for transcription input.

n8n currently owns submission and job polling. The ASR/diarization process
remains the existing host-side MedPark runtime; connecting its invocation to a
second, authenticated worker/service is the next deployment step.

This local configuration binds port 5678 to `127.0.0.1` only. It is not suitable
for external webhooks until it is deployed behind HTTPS with an externally
reachable `WEBHOOK_URL`. Port 8001 is likewise loopback-only; n8n reaches the
preprocessor over the private Docker network as `http://preprocess:8000`.
