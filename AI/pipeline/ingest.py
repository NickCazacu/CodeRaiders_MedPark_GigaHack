"""Ingest: creează jobs/{id}/, copiază sursa, ffprobe (durată, stream-uri).

    python -m pipeline.ingest sedinta.mp3 [--job-id ID]
"""
import argparse
import hashlib
import json
import re
import shutil
from pathlib import Path

from pipeline.common import jobs_dir, load_config, load_status, now, run, run_stage, save_status


def file_hash(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def default_job_id(path):
    # același fișier => același id, deci reluarea funcționează automat
    stem = re.sub(r"[^A-Za-z0-9_-]+", "_", Path(path).stem)[:40].strip("_") or "job"
    return f"{stem}-{file_hash(path)[:8]}"


def ffprobe(path):
    out = run([
        "ffprobe", "-v", "error", "-print_format", "json",
        "-show_entries", "format=duration,format_name,bit_rate:stream=index,codec_type,codec_name,sample_rate,channels",
        str(path),
    ])
    return json.loads(out)


def ingest(src, job_id=None, cfg=None):
    cfg = cfg or load_config()
    src = Path(src).resolve()
    if not src.is_file():
        raise SystemExit(f"Fișierul nu există: {src}")

    job_id = job_id or default_job_id(src)
    job_dir = jobs_dir(cfg) / job_id
    job_dir.mkdir(parents=True, exist_ok=True)

    status = load_status(job_dir)
    status.setdefault("job_id", job_id)
    status.setdefault("created_at", now())
    status["input_path"] = str(src)
    status["source"] = "source" + src.suffix.lower()
    save_status(job_dir, status)

    dst = job_dir / status["source"]

    def copy():
        tmp = dst.with_name(dst.name + ".part")
        shutil.copyfile(src, tmp)
        tmp.replace(dst)
        return {"bytes": dst.stat().st_size}

    run_stage(job_dir, "ingest", dst, copy)

    probe_path = job_dir / "probe.json"

    def probe():
        info = ffprobe(dst)
        if not any(s.get("codec_type") == "audio" for s in info.get("streams", [])):
            raise RuntimeError("Fișierul nu conține niciun stream audio")
        probe_path.write_text(json.dumps(info, indent=2), encoding="utf-8")
        return {"duration_s": round(float(info["format"]["duration"]), 2)}

    stage = run_stage(job_dir, "probe", probe_path, probe)

    status = load_status(job_dir)
    status["duration_s"] = stage.get("duration_s")
    save_status(job_dir, status)
    return job_id, job_dir


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input", help="fișier audio/video (mp3, m4a, wav, mp4, ...)")
    ap.add_argument("--job-id", help="implicit: <nume>-<sha256[:8]>")
    ap.add_argument("--config", help="implicit: config.yaml din rădăcina proiectului")
    args = ap.parse_args()

    job_id, job_dir = ingest(args.input, args.job_id, load_config(args.config))
    print(f"job_id={job_id}\njob_dir={job_dir}")


if __name__ == "__main__":
    main()
