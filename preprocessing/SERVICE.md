# MedPark preprocessing service

`app.preprocess_service` exposes this repository's audio cleanup, 16 kHz WAV
creation, and VAD as an asynchronous local service. It accepts a path in a
shared volume rather than an uploaded file, so recordings do not become n8n
execution data.

The recommended local deployment is `../n8n-local`:

```bash
cd ../n8n-local
docker compose up -d --build
```

The service image is CPU-only and uses `onnxruntime` with the Silero ONNX model
bundled in the `silero-vad` package. It intentionally does not install Torch or
CUDA packages.

The service is available on the host at `http://localhost:8001` and from n8n
at `http://preprocess:8000`.

## API

`POST /jobs` accepts `source_path`, an optional safe `job_id`, and optional
`PreprocessConfig` overrides. `source_path` must be an existing file inside
`MOM_SOURCE_ROOT`. `POST /jobs/upload` accepts the same job metadata plus a
multipart `audio` field; it stores that upload in the private job directory
before enqueuing the normal path-based job. This route is for the local n8n
workflow only. The service itself owns `work_dir` and `cache`; callers cannot
override them.

`GET /jobs/{job_id}` returns `queued`, `running`, `done`, or `failed`.
`GET /jobs/{job_id}/result` returns the completed result and otherwise responds
with HTTP 409. `GET /healthz` responds with `{"status":"ok"}`.

Each job writes `status.json` and `result.json` under
`MOM_PREPROCESS_WORK_DIR/<job_id>/`. In `result.json`, `wav_path` is a filename
relative to the result file. This is intentional: the host and the service
container mount the shared directory at different absolute paths.
