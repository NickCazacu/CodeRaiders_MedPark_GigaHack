"""Helpers comune: config, status.json, rulare etapă cu skip + cronometrare."""
import json
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

import yaml

# Fără rețea la inferență: pyannote 4 are telemetrie pornită implicit (otel.pyannote.ai),
# iar huggingface_hub ar verifica online modelele. Descărcarea unică: vezi SETUP.md.
os.environ["PYANNOTE_METRICS_ENABLED"] = "false"
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

ROOT = Path(__file__).resolve().parent.parent


def load_config(path=None):
    path = Path(path) if path else ROOT / "config.yaml"
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def jobs_dir(cfg):
    return ROOT / cfg["paths"]["jobs_dir"]


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_status(job_dir):
    p = Path(job_dir) / "status.json"
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return {"stages": {}}


def save_status(job_dir, status):
    # scriere atomică: un crash la mijloc nu lasă status.json corupt
    p = Path(job_dir) / "status.json"
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(status, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, p)


def run_stage(job_dir, name, output, fn, done=None):
    """Rulează fn() doar dacă `output` nu există (sau, dacă e dat, `done()` e fals).
    fn poate returna un dict cu informații extra, salvate în status.json."""
    status = load_status(job_dir)
    if done() if done else Path(output).exists():
        print(f"[{name}] sărit (există {Path(output).name})")
        return status["stages"].get(name, {})

    stage = {"state": "running", "started_at": now()}
    status["stages"][name] = stage
    save_status(job_dir, status)
    print(f"[{name}] start")

    t0 = time.perf_counter()
    try:
        info = fn() or {}
    except Exception as e:
        stage.update(state="failed", error=str(e), seconds=round(time.perf_counter() - t0, 2))
        save_status(job_dir, status)
        raise
    stage.update(state="done", finished_at=now(), seconds=round(time.perf_counter() - t0, 2), **info)
    save_status(job_dir, status)
    print(f"[{name}] gata în {stage['seconds']} s")
    return stage


def run(cmd):
    """Rulează o comandă externă; la eroare aruncă excepție cu stderr-ul."""
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise RuntimeError(f"{cmd[0]} a eșuat ({r.returncode}): {r.stderr.strip()[-2000:]}")
    return r.stdout
