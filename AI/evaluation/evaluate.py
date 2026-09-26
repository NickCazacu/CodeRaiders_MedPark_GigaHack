"""Evaluarea ASR a unui job față de gold: WER/CER (total, fără suprapuneri, în suprapuneri,
per limbă), acuratețea termenilor medicali, limba aleasă, decizii/ETA în suprapuneri.

    python -m evaluation.evaluate JOB_ID --gold NAME [--note "ce s-a schimbat"] [--no-save]

Citește jobs/JOB_ID/transcript.json și evaluation/gold/NAME/gold.json. Scrie raportul detaliat
în jobs/JOB_ID/eval_NAME.json și adaugă un rând în evaluation/results.csv (+ RESULTS.md).

Cum se aliniază ipoteza la gold (gold-ul are timpi doar per replică):
- referința = replicile gold concatenate în ordinea timpului; într-un grup de replici care se
  suprapun, în ordinea care dă cele mai puține erori (ASR-ul pe un canal nu are o ordine „corectă”);
- referința și ipoteza se aliniază GLOBAL (Levenshtein), deci numărul total de erori nu depinde
  de timpi. Timpii decid doar CĂREI unități îi aparține fiecare eroare:
  - cuvintele gold au timp interpolat liniar în replică (după poziția în text), iar cuvintele ASR
    timpii lor (word_timestamps);
  - unitatea = regiunea de suprapunere în care cade cuvântul, altfel replica lui gold (pentru
    inserții: replica cea mai apropiată, max MAX_GAP_S; mai departe = inserție „în liniște”).
"""
import argparse
import json
from bisect import bisect_right
from collections import Counter

from evaluation.gold import GOLD_DIR, in_any, ref_text
from evaluation.metrics import Errors, align, append_result, best_order, rnd
from pipeline.common import jobs_dir, load_config, load_status
from pipeline.glossary import find_in_tokens, glossary_hash, load_terms, norm_text, raw_tokens, script_of

MAX_GAP_S = 1.5      # inserție ASR la mai mult de atât de orice replică gold => „în liniște”
TERM_WINDOW_S = 2.0  # toleranța de timp la potrivirea termenilor gold <-> ASR


class Tok:
    __slots__ = ("norm", "raw", "t", "utt")

    def __init__(self, norm, raw, t, utt=None):
        self.norm, self.raw, self.t, self.utt = norm, raw, t, utt


def gold_tokens(u):
    """Tokenii unei replici gold, cu timp interpolat după poziția caracterelor în text."""
    text = ref_text(u["text"])
    norm, raw = norm_text(text), raw_tokens(text)
    total = sum(len(x) for x in norm) or 1
    out, pos = [], 0
    for n, r in zip(norm, raw):
        mid = (pos + len(n) / 2) / total
        out.append(Tok(n, r, u["start"] + mid * (u["end"] - u["start"]), u["id"]))
        pos += len(n)
    return out


def hyp_tokens(segs):
    """Tokenii ASR (segmentele păstrate), cu mijlocul fiecărui cuvânt Whisper ca timp."""
    out = []
    for s in segs:
        words = s.get("words") or []
        if not words and s["text"]:  # fără word timestamps: distribuim uniform
            n = norm_text(s["text"])
            step = (s["end"] - s["start"]) / max(len(n), 1)
            words = [{"w": w, "start": s["start"] + k * step, "end": s["start"] + (k + 1) * step}
                     for k, w in enumerate(n)]
        for w in words:
            t = (w["start"] + w["end"]) / 2
            out.extend(Tok(n, r, t) for n, r in zip(norm_text(w["w"]), raw_tokens(w["w"])))
    return out


def ordered_gold(gold, htoks):
    """Tokenii gold în ordinea referinței. Replicile care se suprapun formează un grup, ordonat
    după ipoteza din fereastra grupului (best_order)."""
    clusters, cur_end = [], None
    for u in gold["utterances"]:
        if clusters and u["start"] < cur_end:
            clusters[-1].append(u)
            cur_end = max(cur_end, u["end"])
        else:
            clusters.append([u])
            cur_end = u["end"]
    out = []
    for c in clusters:
        groups = [gold_tokens(u) for u in c]
        if len(groups) > 1:
            lo, hi = c[0]["start"] - 0.5, max(u["end"] for u in c) + 0.5
            hyp = [t.norm for t in htoks if lo <= t.t <= hi]
            order = best_order([[t.norm for t in g] for g in groups], hyp, return_order=True)
            groups = [groups[k] for k in order]
        out += [t for g in groups for t in g]
    return out


class Buckets:
    """Unitatea fiecărui token: ("ov", k) | ("utt", id) | ("ins", None)."""

    def __init__(self, gold):
        self.ov = gold["overlaps"]
        self.starts = [a for a, _ in self.ov]
        self.utts = gold["utterances"]

    def overlap(self, t):
        k = bisect_right(self.starts, t) - 1
        return k if k >= 0 and t < self.ov[k][1] else None

    def of(self, tok, is_gold):
        k = self.overlap(tok.t)
        if k is not None:
            return ("ov", k)
        if is_gold:
            return ("utt", tok.utt)
        best, dist = None, None
        for u in self.utts:
            d = max(u["start"] - tok.t, tok.t - u["end"], 0.0)
            if dist is None or d < dist:
                best, dist = u["id"], d
        return ("utt", best) if dist is not None and dist <= MAX_GAP_S else ("ins", None)


def word_ops(gtoks, htoks, bk):
    """Alinierea pe cuvinte: [(tag, tok_gold|None, tok_asr|None, unitate, timp)]."""
    res = []
    for tag, i, j in align([t.norm for t in gtoks], [t.norm for t in htoks]):
        g = gtoks[i] if i is not None else None
        h = htoks[j] if j is not None else None
        anchor = g or h
        res.append((tag, g, h, bk.of(anchor, g is not None), anchor.t))
    return res


def char_ops(gtoks, htoks, bk):
    """Alinierea pe caractere; fiecare caracter e atribuit tokenului său: [(tag, unitate, timp)]."""
    def chars(toks):
        text, owner = [], []
        for k, t in enumerate(toks):
            if k:
                text.append(" ")
                owner.append(t)
            text.extend(t.norm)
            owner.extend([t] * len(t.norm))
        return "".join(text), owner

    (ref, rown), (hyp, hown) = chars(gtoks), chars(htoks)
    res = []
    for tag, i, j in align(ref, hyp):
        anchor, is_gold = (rown[i], True) if i is not None else (hown[j], False)
        res.append((tag, bk.of(anchor, is_gold), anchor.t))
    return res


def count(ops, pred=lambda unit, t: True):
    """Errors din operațiile care satisfac pred(unitate, timp)."""
    e = Errors()
    for op in ops:
        tag, unit, t = op[0], op[-2], op[-1]
        if not pred(unit, t):
            continue
        if tag != "ins":
            e.N += 1
        if tag == "sub":
            e.S += 1
        elif tag == "del":
            e.D += 1
        elif tag == "ins":
            e.I += 1
    return e


def unit_details(wops):
    units = {}
    for tag, g, h, unit, _ in wops:
        d = units.setdefault(unit, {"ref": [], "hyp": [], "errors": 0, "N": 0})
        if g:
            d["ref"].append(g.norm)
            d["N"] += 1
        if h:
            d["hyp"].append(h.norm)
        d["errors"] += tag != "eq"
    out = [{"unit": f"{u[0]}:{u[1]}", "wer": rnd(d["errors"] / d["N"]) if d["N"] else None,
            "errors": d["errors"], "ref": " ".join(d["ref"]), "hyp": " ".join(d["hyp"])}
           for u, d in units.items()]
    return sorted(out, key=lambda x: -x["errors"])


def in_regions(t, regions):
    return any(a <= t <= b for a, b in regions)


def term_scores(gold, htoks_by_seg, terms, regions):
    """Recall/precizie pe termeni: același concept și alfabet, la ±TERM_WINDOW_S."""
    ov = gold["overlaps"]

    def occ(toks):
        norm, raw = [x.norm for x in toks], [x.raw for x in toks]
        for t, i, j in find_in_tokens(norm, raw, terms):
            yield {"concept": t.concept, "category": t.category, "script": script_of(norm[i]),
                   "text": " ".join(norm[i:j]), "t": round(sum(x.t for x in toks[i:j]) / (j - i), 2)}

    g_occ = [o for u in gold["utterances"] for o in occ(gold_tokens(u))]
    h_occ = [o for toks in htoks_by_seg for o in occ(toks)
             if in_regions(o["t"], regions)]

    used = set()
    for g in g_occ:
        g["in_overlap"] = in_any(g["t"], g["t"] + 1e-6, ov)
        g["hit"] = None
        for k, h in enumerate(h_occ):
            if k not in used and h["concept"] == g["concept"] and h["script"] == g["script"] \
                    and abs(h["t"] - g["t"]) <= TERM_WINDOW_S:
                used.add(k)
                g["hit"] = h["text"]
                break
    false_terms = [h for k, h in enumerate(h_occ) if k not in used]

    def recall(pred):
        sel = [g for g in g_occ if pred(g)]
        return rnd(sum(g["hit"] is not None for g in sel) / len(sel)) if sel else None

    return {
        "n_gold": len(g_occ), "n_hyp": len(h_occ),
        "recall": recall(lambda g: True),
        "recall_clean": recall(lambda g: not g["in_overlap"]),
        "recall_overlap": recall(lambda g: g["in_overlap"]),
        "precision": rnd((len(h_occ) - len(false_terms)) / len(h_occ)) if h_occ else None,
        "by_category": {c: recall(lambda g, c=c: g["category"] == c) for c in sorted({g["category"] for g in g_occ})},
        "missed": [g for g in g_occ if g["hit"] is None],
        "false": false_terms,
    }


def lang_scores(gold, segs):
    """Limba aleasă de ASR per segment vs. limba replicii gold cu cea mai mare suprapunere în timp."""
    ok, n, conf = 0, 0, Counter()
    for s in segs:
        best, best_ov = None, 0.0
        for u in gold["utterances"]:
            o = min(s["end"], u["end"]) - max(s["start"], u["start"])
            if o > best_ov and u["lang"] != "mix":
                best, best_ov = u["lang"], o
        if best is None:
            continue
        n += 1
        if s["lang"] == best:
            ok += 1
        else:
            conf[f"{best}->{s['lang']}"] += 1
    return {"acc": rnd(ok / n) if n else None, "n": n, "confusions": dict(conf)}


def evaluate(job_id, gold_name, cfg=None):
    cfg = cfg or load_config()
    job = jobs_dir(cfg) / job_id
    gold = json.loads((GOLD_DIR / gold_name / "gold.json").read_text(encoding="utf-8"))
    regions = gold["regions"]
    transcript = json.loads((job / "transcript.json").read_text(encoding="utf-8"))
    segs = [s for s in transcript if not s["dropped"] and in_any(s["start"], s["end"], regions)]

    htoks_by_seg = [hyp_tokens([s]) for s in segs]
    # cuvintele ASR din intervalele evaluate (după mijlocul cuvântului)
    htoks = sorted((t for ts in htoks_by_seg for t in ts if in_regions(t.t, regions)), key=lambda t: t.t)
    gtoks = ordered_gold(gold, htoks)

    bk = Buckets(gold)
    wops, cops = word_ops(gtoks, htoks, bk), char_ops(gtoks, htoks, bk)
    lang_of = {u["id"]: u["lang"] for u in gold["utterances"]}

    def clean(u, t):
        return u[0] != "ov"

    def overl(u, t):
        return u[0] == "ov"

    per_lang = {l: count(wops, lambda u, t, l=l: u[0] == "utt" and lang_of[u[1]] == l)
                for l in sorted(set(lang_of.values()))}
    ev = gold["events"]
    ev_iv = [(e["start"], e["end"]) for e in ev]
    w_ev = count(wops, lambda u, t: in_any(t, t + 1e-6, ev_iv)) if ev else Errors()
    w_ov = count(wops, overl)

    status = load_status(job)
    st = status.get("stages", {})
    ov = gold["overlaps"]
    report = {
        "job": job_id, "gold": gold_name,
        "setup": "; ".join(sorted({s.get("setup") for s in transcript if s.get("setup")})),
        "asr_prompt": st.get("asr", {}).get("initial_prompt"), "asr_hotwords": st.get("asr", {}).get("hotwords"),
        "correction": st.get("postprocess", {}).get("glossary_correction", False),
        "glossary_hash": glossary_hash(),
        "wer": {"all": count(wops).as_dict(), "clean": count(wops, clean).as_dict(), "overlap": w_ov.as_dict(),
                "events": w_ev.as_dict(), "by_lang": {l: e.as_dict() for l, e in per_lang.items()}},
        "cer": {"all": count(cops).as_dict(), "clean": count(cops, clean).as_dict(),
                "overlap": count(cops, overl).as_dict()},
        "terms": term_scores(gold, htoks_by_seg, load_terms(), regions),
        "language": lang_scores(gold, segs),
        "overlap": {"regions": len(ov), "seconds": round(sum(e - s for s, e in ov), 1), "ref_words": w_ov.N},
        "events": {
            "decisions": sum(e["type"] == "decision" for e in ev),
            "decisions_in_overlap": sum(e["type"] == "decision" and in_any(e["start"], e["end"], ov) for e in ev),
            "etas": sum(e["type"] == "eta" for e in ev),
            "etas_in_overlap": sum(e["type"] == "eta" and in_any(e["start"], e["end"], ov) for e in ev),
        },
        "units": unit_details(wops),
    }
    (job / f"eval_{gold_name}.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    return report


def summary_row(r, note=None):
    hw = r["asr_hotwords"] or {}
    pr = r["asr_prompt"] or {}
    w, c, t = r["wer"], r["cer"], r["terms"]
    return {
        "gold": r["gold"], "job": r["job"], "setup": r["setup"],
        "prompt": ",".join(f"{k}:{v}" for k, v in pr.items() if v) or "none",
        "hotwords": ",".join(f"{k}:{v}" for k, v in hw.items() if v) or "none",
        "correction": f"on:{r['glossary_hash']}" if r["correction"] else "off", "note": note,
        "n_ref_words": w["all"]["N"],
        "wer": w["all"]["rate"], "wer_clean": w["clean"]["rate"], "wer_overlap": w["overlap"]["rate"],
        "cer": c["all"]["rate"], "cer_clean": c["clean"]["rate"], "cer_overlap": c["overlap"]["rate"],
        "wer_ro": w["by_lang"].get("ro", {}).get("rate"), "wer_ru": w["by_lang"].get("ru", {}).get("rate"),
        "term_recall": t["recall"], "term_recall_clean": t["recall_clean"],
        "term_recall_overlap": t["recall_overlap"], "term_precision": t["precision"],
        "lang_acc": r["language"]["acc"], "overlap_s": r["overlap"]["seconds"], "overlap_words": r["overlap"]["ref_words"],
        **r["events"], "wer_events": w["events"]["rate"],
    }


def pct(x):
    return "–" if x is None else f"{100 * x:.1f}%"


def print_report(r, top=5):
    w, c = r["wer"], r["cer"]
    print(f"job {r['job']}  gold {r['gold']}  setup {r['setup']}  corecție {'on' if r['correction'] else 'off'}")
    print(f"{'':12s} {'WER':>7s} {'CER':>7s} {'cuvinte ref':>12s}")
    for k in ("all", "clean", "overlap"):
        print(f"{k:12s} {pct(w[k]['rate']):>7s} {pct(c[k]['rate']):>7s} {w[k]['N']:>12d}   "
              f"S={w[k]['S']} D={w[k]['D']} I={w[k]['I']}")
    for l, e in w["by_lang"].items():
        print(f"{'  ' + l:12s} {pct(e['rate']):>7s} {'':>7s} {e['N']:>12d}")
    if r["events"]["decisions"] or r["events"]["etas"]:
        print(f"{'decizii/ETA':12s} {pct(w['events']['rate']):>7s} {'':>7s} {w['events']['N']:>12d}")
    t = r["terms"]
    print(f"termeni: recall {pct(t['recall'])} (clean {pct(t['recall_clean'])}, overlap {pct(t['recall_overlap'])}), "
          f"precizie {pct(t['precision'])}; gold {t['n_gold']}, ASR {t['n_hyp']}")
    if t["missed"]:
        print("  ratați: " + ", ".join(f"{m['text']}@{m['t']:.0f}s" for m in t["missed"][:12]))
    if t["false"]:
        print("  în plus: " + ", ".join(f"{m['text']}@{m['t']:.0f}s" for m in t["false"][:12]))
    L = r["language"]
    print(f"limbă: {pct(L['acc'])} corectă din {L['n']} segmente {L['confusions'] or ''}")
    o, e = r["overlap"], r["events"]
    print(f"suprapuneri: {o['regions']} regiuni, {o['seconds']} s, {o['ref_words']} cuvinte; "
          f"decizii {e['decisions_in_overlap']}/{e['decisions']} și ETA {e['etas_in_overlap']}/{e['etas']} în suprapuneri")
    print(f"cele mai multe erori (detalii în jobs/{r['job']}/eval_{r['gold']}.json):")
    for u in r["units"][:top]:
        print(f"  {u['unit']:8s} erori={u['errors']:<3d} REF: {u['ref'][:110]}\n{'':24s}ASR: {u['hyp'][:110]}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("job_id")
    ap.add_argument("--gold", required=True)
    ap.add_argument("--note", help="ce s-a schimbat față de rularea anterioară (apare în RESULTS.md)")
    ap.add_argument("--no-save", action="store_true", help="nu adăuga rândul în results.csv")
    ap.add_argument("--config")
    args = ap.parse_args()

    r = evaluate(args.job_id, args.gold, load_config(args.config))
    print_report(r)
    if not args.no_save:
        append_result(summary_row(r, args.note))
        print("-> evaluation/results.csv, evaluation/RESULTS.md")


if __name__ == "__main__":
    main()
