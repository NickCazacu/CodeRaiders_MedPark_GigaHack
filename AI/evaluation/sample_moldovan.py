"""Eșantion din FraPiz/moldovan-dialectal-romanian-speech-corpus pentru evaluarea ASR
(româna moldovenească: WER și cât de des alegerea limbii dă greșit `ru`).

    python -m evaluation.sample_moldovan [--row-groups 30] [--per-group 20]

Singurul pas cu rețea. Nu descarcă tot corpusul (16.8 GB): fiecare shard parquet are
10 row group-uri de câte 100 de segmente (~65 MB), citite la distanță prin HfFileSystem.
Alegem row group-uri de la profesori diferiți, prin rotație pe discipline, și din
fiecare păstrăm `per_group` segmente aleatoare, convertite ca în pipeline.normalize
(mono 16 kHz, highpass + loudnorm).

Necesită data/external/moldovan_dialect/raw/metadata.jsonl (în aceeași ordine ca
shard-urile: rândul i e în shard i // 1000, row group (i % 1000) // 100).
Scrie în data/external/moldovan_dialect/sample/: wav/*.wav, manifest.jsonl, selection.json.
Reluabil: row group-urile deja convertite sunt sărite.

Licență: „source-content-rights-reserved”, conține numele profesorilor. Doar pentru
evaluare locală; nimic din data/ nu intră în git.
"""
import os

os.environ["HF_HUB_OFFLINE"] = "0"  # înainte de pipeline.common (care pune 1) și de huggingface_hub

import argparse
import json
import random
import re
import subprocess
from collections import defaultdict

from pipeline.common import ROOT, load_config

REPO = "datasets/FraPiz/moldovan-dialectal-romanian-speech-corpus"
BASE = ROOT / "data" / "external" / "moldovan_dialect"
SHARD_ROWS, GROUP_ROWS, N_SHARDS = 1000, 100, 36


def parse_name(f):
    """class-clasa-10__discipline-chimie__teacher-x-y__title-...__id-abc_093.wav -> dict"""
    m = dict(re.findall(r"(class|discipline|teacher|title|id)-(.+?)(?=__|_\d+\.wav$)", f))
    return {"grade": m.get("class"), "discipline": m.get("discipline"),
            "teacher": m.get("teacher"), "source_id": m.get("id")}


def choose_groups(rows, n_groups, rng):
    """Row group-uri „pure” (>= 80% un singur profesor), câte unul per profesor,
    luate prin rotație pe discipline ca eșantionul să nu fie dominat de o materie."""
    groups = defaultdict(list)
    for i, r in enumerate(rows):
        groups[(i // SHARD_ROWS, i % SHARD_ROWS // GROUP_ROWS)].append(parse_name(r["audio_filename"]))
    by_teacher = defaultdict(list)
    for key, metas in groups.items():
        t = max({m["teacher"] for m in metas}, key=lambda x: sum(m["teacher"] == x for m in metas))
        if sum(m["teacher"] == t for m in metas) >= 0.8 * len(metas):
            by_teacher[t].append((key, metas[0]["discipline"]))

    by_disc = defaultdict(list)
    for t, keys in sorted(by_teacher.items()):
        by_disc[keys[0][1]].append(t)
    for ts in by_disc.values():
        rng.shuffle(ts)

    chosen = []
    while len(chosen) < n_groups and any(by_disc.values()):
        for d in sorted(by_disc):
            if by_disc[d] and len(chosen) < n_groups:
                t = by_disc[d].pop()
                key, _ = rng.choice(by_teacher[t])
                chosen.append({"shard": key[0], "group": key[1], "teacher": t, "discipline": d})
    return sorted(chosen, key=lambda g: (g["shard"], g["group"]))


def to_wav16k(data, dst, n):
    """Bytes WAV (orice rată/canale) -> mono 16 kHz PCM, cu aceleași filtre ca pipeline.normalize."""
    tmp = dst.with_name(dst.stem + ".part.wav")
    r = subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", "pipe:0",
        "-ac", "1", "-af", f"highpass=f={n['highpass_hz']},loudnorm={n['loudnorm']}",
        "-ar", str(n["sample_rate"]), "-c:a", "pcm_s16le", str(tmp),
    ], input=data, capture_output=True)
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg: {r.stderr.decode('utf-8', 'replace')[-500:]}")
    tmp.replace(dst)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--row-groups", type=int, default=30, help="câte row group-uri (~65 MB fiecare)")
    ap.add_argument("--per-group", type=int, default=20, help="segmente păstrate din fiecare")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--config")
    args = ap.parse_args()

    import pyarrow.parquet as pq
    from huggingface_hub import HfFileSystem
    from tqdm import tqdm

    n = load_config(args.config)["normalize"]
    rows = [json.loads(l) for l in open(BASE / "raw" / "metadata.jsonl", encoding="utf-8")]
    out = BASE / "sample"
    wav_dir = out / "wav"
    wav_dir.mkdir(parents=True, exist_ok=True)

    rng = random.Random(args.seed)
    sel_path = out / "selection.json"
    if sel_path.exists():  # reluare: aceeași selecție, chiar dacă metadatele/seed-ul s-ar schimba
        chosen = json.loads(sel_path.read_text(encoding="utf-8"))
    else:
        chosen = choose_groups(rows, args.row_groups, rng)
        for g in chosen:
            base = g["shard"] * SHARD_ROWS + g["group"] * GROUP_ROWS
            size = len(rows[base:base + GROUP_ROWS])
            g["rows"] = sorted(rng.sample(range(size), min(args.per_group, size)))
        sel_path.write_text(json.dumps(chosen, indent=1), encoding="utf-8")
    print(f"{len(chosen)} row group-uri, {len({g['teacher'] for g in chosen})} profesori, "
          f"{len({g['discipline'] for g in chosen})} discipline")

    fs = HfFileSystem()
    manifest = []
    for g in tqdm(chosen, unit="rg"):
        base = g["shard"] * SHARD_ROWS + g["group"] * GROUP_ROWS
        items = [(k, rows[base + k], wav_dir / f"md_{g['shard']:02d}_{g['group']}_{k:02d}.wav") for k in g["rows"]]
        if not all(p.exists() for _, _, p in items):
            path = f"{REPO}/data/train-{g['shard']:05d}-of-{N_SHARDS:05d}.parquet"
            with fs.open(path, "rb") as f:
                t = pq.ParquetFile(f).read_row_group(g["group"], columns=["audio", "audio_filename"])
            names, audio = t.column("audio_filename").to_pylist(), t.column("audio").to_pylist()
            for k, r, p in items:
                if names[k] != r["audio_filename"]:
                    raise SystemExit(f"metadata.jsonl nu corespunde shard-ului: {names[k]} != {r['audio_filename']}")
                if not p.exists():
                    to_wav16k(audio[k]["bytes"], p, n)
        for k, r, p in items:
            manifest.append({"id": p.stem, "wav": p.relative_to(out).as_posix(), "text": r["text"],
                             "duration": r["duration"], **parse_name(r["audio_filename"]),
                             "audio_filename": r["audio_filename"]})

    with open(out / "manifest.jsonl", "w", encoding="utf-8") as f:
        f.writelines(json.dumps(m, ensure_ascii=False) + "\n" for m in manifest)
    total = sum(m["duration"] for m in manifest)
    print(f"{len(manifest)} segmente, {total / 60:.1f} min -> {out / 'manifest.jsonl'}")


if __name__ == "__main__":
    main()
