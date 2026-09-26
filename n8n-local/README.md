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

Use the result in the existing MedPark pipeline, for example on the host:

```bash
cd ../CodeRaiders_MedPark_GigaHack
python run_pipeline.py /absolute/path/to/meeting.m4a \
  --preprocess-result ../n8n-local/runtime/preprocess/meeting-20260926/result.json
```

n8n currently owns submission and job polling. The ASR/diarization process
remains the existing host-side MedPark runtime; connecting its invocation to a
second, authenticated worker/service is the next deployment step.

This local configuration binds port 5678 to `127.0.0.1` only. It is not suitable
for external webhooks until it is deployed behind HTTPS with an externally
reachable `WEBHOOK_URL`. Port 8001 is likewise loopback-only; n8n reaches the
preprocessor over the private Docker network as `http://preprocess:8000`.
