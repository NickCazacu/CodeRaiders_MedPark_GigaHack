"""Normalize: ffmpeg -> mono 16 kHz PCM WAV (highpass 80 Hz + loudnorm),
apoi denoise opțional cu noisereduce.

    python -m pipeline.normalize JOB_ID [--denoise | --no-denoise]
"""
import argparse
import subprocess

from pipeline.common import jobs_dir, load_config, load_status, run_stage, save_status

CHUNK_S = 60  # denoise pe bucăți, ca un fișier de 2 ore să nu umple RAM-ul


def ffmpeg_normalize(src, dst, cfg, duration=None):
    from tqdm import tqdm

    n = cfg["normalize"]
    tmp = dst.with_name(dst.stem + ".part.wav")
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostats", "-y",
        "-progress", "pipe:1",  # key=value pe stdout, pentru bara de progres
        "-i", str(src),
        "-vn", "-ac", "1",
        "-af", f"highpass=f={n['highpass_hz']},loudnorm={n['loudnorm']}",
        "-ar", str(n["sample_rate"]),
        "-c:a", "pcm_s16le",
        str(tmp),
    ]
    with subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                          encoding="utf-8", errors="replace") as p, \
            tqdm(total=round(duration) if duration else None, unit="s", desc="normalize") as bar:
        for line in p.stdout:
            if line.startswith("out_time_us=") and line[12:].strip().isdigit():
                bar.update(max(0, int(line[12:]) // 1_000_000 - bar.n))
        err = p.stderr.read()
    if p.returncode != 0:
        raise RuntimeError(f"ffmpeg a eșuat ({p.returncode}): {err.strip()[-2000:]}")
    tmp.replace(dst)


def denoise(src, dst, prop_decrease):
    import noisereduce as nr
    import soundfile as sf
    from tqdm import tqdm

    tmp = dst.with_name(dst.stem + ".part.wav")
    info = sf.info(str(src))
    block = CHUNK_S * info.samplerate
    with sf.SoundFile(str(tmp), "w", info.samplerate, 1, "PCM_16") as out:
        for chunk in tqdm(sf.blocks(str(src), blocksize=block, dtype="float32"),
                          total=-(-info.frames // block), unit="min", desc="denoise"):
            out.write(nr.reduce_noise(y=chunk, sr=info.samplerate,
                                      stationary=False, prop_decrease=prop_decrease))
    tmp.replace(dst)


def normalize(job_id, use_denoise=None, cfg=None):
    cfg = cfg or load_config()
    job_dir = jobs_dir(cfg) / job_id
    status = load_status(job_dir)
    if "source" not in status:
        raise SystemExit(f"Jobul {job_id} nu există sau nu a trecut prin ingest")
    if use_denoise is None:
        use_denoise = cfg["normalize"]["denoise"]

    src = job_dir / status["source"]
    wav = job_dir / "audio_16k.wav"
    run_stage(job_dir, "normalize", wav, lambda: ffmpeg_normalize(src, wav, cfg, status.get("duration_s")))

    final = wav
    if use_denoise:
        final = job_dir / "audio_16k_denoised.wav"
        prop = cfg["normalize"]["denoise_prop_decrease"]
        run_stage(job_dir, "denoise", final, lambda: denoise(wav, final, prop))

    status = load_status(job_dir)
    status["audio"] = final.name  # intrarea pentru etapele următoare (VAD/ASR)
    save_status(job_dir, status)
    return final


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("job_id")
    ap.add_argument("--denoise", action=argparse.BooleanOptionalAction, default=None,
                    help="suprascrie normalize.denoise din config")
    ap.add_argument("--config", help="implicit: config.yaml din rădăcina proiectului")
    args = ap.parse_args()

    print(normalize(args.job_id, args.denoise, load_config(args.config)))


if __name__ == "__main__":
    main()
