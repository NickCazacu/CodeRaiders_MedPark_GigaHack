"""Serviciu local pentru n8n: înregistrare -> ASR (AI/) -> LLM (LLM/) -> proces-verbal (mom.html).

    .\\.venv\\Scripts\\python.exe service.py [--host 127.0.0.1] [--port 8765]

Rulează pe Windows, lângă placa video (ASR-ul și Ollama au nevoie de GPU); n8n îl apelează din Docker
la http://host.docker.internal:8765. Doar biblioteca standard. Un singur job odată (ASR și LLM
nu încap simultan în VRAM); celelalte așteaptă în coadă.

API (JSON):
  POST /jobs              {"file": "<nume în n8n-local/runtime/inbox>", "job_id"?: "...", "meeting_date"?: "YYYY-MM-DD"}
                          -> 202 {"job_id", "state": "queued"}
  GET  /jobs/<id>         -> {"job_id", "state": queued|running|done|failed, "stage", "error", ...};
                             cu "mom_html" (procesul-verbal) când state == done
  GET  /jobs/<id>/mom.html, GET /jobs/<id>/minutes.json
  GET  /healthz
"""
import argparse
import json
import os
import queue
import re
import subprocess
import sys
import threading
import time
from datetime import date, datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

AI_DIR = Path(__file__).resolve().parent
REPO = AI_DIR.parent
JOBS = AI_DIR / "jobs"
INBOX = REPO / "n8n-local" / "runtime" / "inbox"
PY = sys.executable
JOB_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$")
STAGES = ["ingest", "normalize", "segment", "asr", "postprocess", "pack", "llm", "mom"]
# sub-etapele din status.json -> etapa afișată
SUBSTAGE = {"probe": "ingest", "denoise": "normalize", "diarize": "segment"}

jobs = {}              # job_id -> starea din memorie (persistată și în jobs/<id>/service.json)
lock = threading.Lock()
work = queue.Queue()


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def save(job):
    d = JOBS / job["job_id"]
    d.mkdir(parents=True, exist_ok=True)
    (d / "service.json").write_text(json.dumps(job, ensure_ascii=False, indent=1), encoding="utf-8")


def update(job_id, **kw):
    with lock:
        jobs[job_id].update(kw)
        save(jobs[job_id])


def current_stage(job_id):
    """Etapa pipeline-ului în lucru, din status.json scris de AI/run_pipeline.py."""
    try:
        st = json.loads((JOBS / job_id / "status.json").read_text(encoding="utf-8"))["stages"]
    except (OSError, ValueError, KeyError):
        return None
    running = [k for k, v in st.items() if v.get("state") == "running"]
    return SUBSTAGE.get(running[-1], running[-1]) if running else None


def run(job_id, cmd, cwd, log, env=None):
    with open(log, "a", encoding="utf-8") as f:
        f.write(f"\n$ {' '.join(map(str, cmd))}\n")
        f.flush()
        p = subprocess.Popen(cmd, cwd=cwd, stdout=f, stderr=subprocess.STDOUT, env=env)
        while p.poll() is None:
            if (s := current_stage(job_id)) and jobs[job_id].get("stage") not in ("llm", "mom"):
                update(job_id, stage=s)
            time.sleep(2)
    if p.returncode != 0:
        lines = log.read_text(encoding="utf-8", errors="replace").strip().splitlines()
        # pentru utilizator: ultima linie „XxxError: ...” din traceback; detaliile rămân în service.log
        errors = [l.strip() for l in lines if re.match(r"^\s*\w*(Error|Exception|SystemExit)\b.*:", l)]
        detail = re.sub(r"^\w+(Error|Exception): ", "", errors[-1]) if errors else " | ".join(lines[-3:])
        name = cmd[2] if cmd[1] == "-m" else cmd[1]
        raise RuntimeError(f"{name} a eșuat: {detail} (detalii: AI/jobs/{job_id}/service.log)")


def process(job):
    job_id, d = job["job_id"], JOBS / job["job_id"]
    log = d / "service.log"
    update(job_id, state="running", stage="ingest", started_at=now())
    t0 = time.perf_counter()
    env = {**os.environ, "PYTHONIOENCODING": "utf-8",
           "LLM_KEEP_ALIVE": "0"}  # eliberează VRAM-ul după LLM, ca următorul ASR să încapă
    try:
        run(job_id, [PY, "run_pipeline.py", str(INBOX / job["file"]), "--job-id", job_id], AI_DIR, log, env)
        update(job_id, stage="llm")
        run(job_id, [PY, "-m", "LLM.extract", str(d / "llm_input.json"), "--date", job["meeting_date"]],
            REPO, log, env)
        update(job_id, stage="mom")
        run(job_id, [PY, "-m", "LLM.mom", str(d)], REPO, log, env)
        update(job_id, state="done", stage="done", finished_at=now(), seconds=round(time.perf_counter() - t0, 1))
    except Exception as e:  # noqa: BLE001 - orice eroare ajunge la utilizator prin status
        update(job_id, state="failed", error=str(e)[-1500:], finished_at=now(),
               seconds=round(time.perf_counter() - t0, 1))


def worker():
    while True:
        job = work.get()
        process(job)
        work.task_done()


def status(job_id):
    with lock:
        job = dict(jobs.get(job_id) or {})
    if not job:
        p = JOBS / job_id / "service.json"  # job dintr-o rulare anterioară a serviciului
        if not p.exists():
            return None
        job = json.loads(p.read_text(encoding="utf-8"))
    d = JOBS / job_id
    try:
        st = json.loads((d / "status.json").read_text(encoding="utf-8"))
        job["audio_duration_s"] = st.get("duration_s")
        job["stage_seconds"] = {k: v.get("seconds") for k, v in st.get("stages", {}).items()}
    except (OSError, ValueError):
        pass
    if job.get("stage") in STAGES:
        job["progress"] = f"{STAGES.index(job['stage']) + 1}/{len(STAGES)}"
    if job.get("state") == "done" and (d / "mom.html").exists():
        job["mom_html"] = (d / "mom.html").read_text(encoding="utf-8")
    return job


class Handler(BaseHTTPRequestHandler):
    server_version = "MedParkAI/1"

    def send(self, code, body, ctype="application/json; charset=utf-8"):
        data = body if isinstance(body, bytes) else (
            body.encode("utf-8") if isinstance(body, str) else json.dumps(body, ensure_ascii=False).encode("utf-8"))
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt, *args):  # fără corpuri de cereri în log (pot conține date medicale)
        sys.stderr.write(f"[service] {self.command} {self.path.split('?')[0]} {args[1] if len(args) > 1 else ''}\n")

    def do_GET(self):
        parts = [p for p in self.path.split("?")[0].split("/") if p]
        if parts == ["healthz"]:
            return self.send(200, {"status": "ok", "queued": work.qsize()})
        if len(parts) >= 2 and parts[0] == "jobs" and JOB_ID.match(parts[1]):
            job_id, d = parts[1], JOBS / parts[1]
            if len(parts) == 2:
                s = status(job_id)
                return self.send(200, s) if s else self.send(404, {"error": "job necunoscut"})
            if parts[2:] == ["mom.html"] and (d / "mom.html").exists():
                return self.send(200, (d / "mom.html").read_bytes(), "text/html; charset=utf-8")
            if parts[2:] == ["minutes.json"] and (d / "minutes.json").exists():
                return self.send(200, (d / "minutes.json").read_bytes())
        self.send(404, {"error": "negăsit"})

    def do_POST(self):
        if self.path.split("?")[0].rstrip("/") != "/jobs":
            return self.send(404, {"error": "negăsit"})
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            name = str(body.get("file") or "")
            src = (INBOX / name).resolve()
            if not name or src.parent != INBOX.resolve() or not src.is_file():
                raise ValueError(f"fișierul trebuie să existe direct în n8n-local/runtime/inbox: {name!r}")
            job_id = str(body.get("job_id") or f"mom-{datetime.now():%Y%m%d-%H%M%S}")
            if not JOB_ID.match(job_id):
                raise ValueError(f"job_id invalid: {job_id!r}")
            meeting_date = str(body.get("meeting_date") or date.today().isoformat())
            date.fromisoformat(meeting_date)
        except (ValueError, TypeError) as e:
            return self.send(400, {"error": str(e)})
        with lock:
            if job_id in jobs and jobs[job_id]["state"] in ("queued", "running"):
                return self.send(409, {"error": "job deja în lucru", "job_id": job_id})
            jobs[job_id] = {"job_id": job_id, "file": name, "meeting_date": meeting_date,
                            "state": "queued", "stage": None, "error": None, "created_at": now()}
            save(jobs[job_id])
        work.put(jobs[job_id])
        self.send(202, {"job_id": job_id, "state": "queued", "queue_position": work.qsize()})


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    args = ap.parse_args()
    INBOX.mkdir(parents=True, exist_ok=True)
    threading.Thread(target=worker, daemon=True).start()
    print(f"[service] http://{args.host}:{args.port}  inbox={INBOX}")
    ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
