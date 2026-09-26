"""Împachetare pentru LLM: segmentele consecutive ale aceluiași vorbitor devin
replici „[mm:ss] SPEAKER: text”, apoi ferestre de ~window_tokens tokeni cu
suprapunere de overlap_turns replici.

    python -m pipeline.pack_for_llm JOB_ID

Citește transcript.json, scrie llm_input.json.
"""
import argparse
import json
from collections import Counter

from pipeline.common import jobs_dir, load_config, load_status, run_stage


def ts(t):
    m, s = divmod(int(t), 60)
    return f"[{m:02d}:{s:02d}]"  # minutele pot trece de 59 la ședințe lungi


def est_tokens(text):
    # fără tokenizer-ul LLM-ului țintă: octeți UTF-8 / 4. Pentru engleză e ~exact,
    # pentru română/rusă supraestimează ușor, deci ferestrele nu depășesc limita.
    return (len(text.encode("utf-8")) + 3) // 4


def make_turns(segs, max_turn_tokens, mark_low):
    groups = []
    for s in segs:
        g = groups[-1] if groups else None
        # "UNK" (fără diarizare) nu e un vorbitor real: fiecare segment rămâne replică separată
        if g and g[-1]["speaker"] == s["speaker"] != "UNK" \
                and est_tokens(" ".join(x["text"] for x in g) + " " + s["text"]) <= max_turn_tokens:
            g.append(s)
        else:
            groups.append([s])

    def unsure(s):
        return s["low_confidence"] or "uncertain_language" in s["flags"]

    turns = []
    for g in groups:
        dur = sum(s["end"] - s["start"] for s in g)
        low = sum(s["end"] - s["start"] for s in g if unsure(s))
        text = " ".join(s["text"] for s in g)
        low_conf = low / dur >= 0.5 if dur else True
        line = f"{ts(g[0]['start'])} {g[0]['speaker']}: {text}" + (" [?]" if low_conf and mark_low else "")
        turns.append({
            "id": len(turns),
            "speaker": g[0]["speaker"],
            "start": g[0]["start"],
            "end": g[-1]["end"],
            "line": line,
            "low_confidence": low_conf,
            "low_confidence_ratio": round(low / dur, 2) if dur else 1.0,
            "langs": list(dict.fromkeys(s["lang"] for s in g)),
            "segment_ids": [s["id"] for s in g],
            "tokens_est": est_tokens(line) + 1,  # +1: newline
        })
    return turns


def make_windows(turns, max_tokens, overlap):
    windows, i, prev_end = [], 0, 0
    while i < len(turns):
        j, tok = i, 0
        while j < len(turns) and (j == i or tok + turns[j]["tokens_est"] <= max_tokens):
            tok += turns[j]["tokens_est"]
            j += 1
        windows.append({
            "index": len(windows),
            "turn_ids": [i, j - 1],
            "overlap_turns": max(prev_end - i, 0),  # primele N replici repetă finalul ferestrei anterioare
            "start": turns[i]["start"],
            "end": turns[j - 1]["end"],
            "tokens_est": tok,
            "text": "\n".join(t["line"] for t in turns[i:j]),
        })
        if j >= len(turns):
            break
        prev_end, i = j, max(j - overlap, i + 1)
    return windows


def pack(job_id, cfg=None):
    cfg = cfg or load_config()
    p = cfg["pack"]
    job_dir = jobs_dir(cfg) / job_id
    out = job_dir / "llm_input.json"

    def work():
        segs = json.loads((job_dir / "transcript.json").read_text(encoding="utf-8"))
        segs = [s for s in segs if not s["dropped"] and s["text"]]
        turns = make_turns(segs, p["max_turn_tokens"], p["mark_low_confidence"])
        windows = make_windows(turns, p["window_tokens"], p["overlap_turns"])
        data = {
            "job_id": job_id,
            "audio_duration_s": load_status(job_dir).get("duration_s"),
            "speakers": sorted({t["speaker"] for t in turns}),
            "languages": dict(Counter(s["lang"] for s in segs)),
            "format": "[mm:ss] SPEAKER: text" + ("  (\" [?]\" = încredere scăzută)" if p["mark_low_confidence"] else ""),
            "token_estimate": "utf8_bytes/4",
            "n_turns": len(turns),
            "n_windows": len(windows),
            "turns": turns,
            "windows": windows,
        }
        tmp = out.with_name(out.name + ".part")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(out)
        return {"n_turns": len(turns), "n_windows": len(windows),
                "low_confidence_turns": sum(t["low_confidence"] for t in turns),
                "max_window_tokens": max((w["tokens_est"] for w in windows), default=0)}

    run_stage(job_dir, "pack", out, work)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("job_id")
    ap.add_argument("--config")
    args = ap.parse_args()
    print(pack(args.job_id, load_config(args.config)))


if __name__ == "__main__":
    main()
