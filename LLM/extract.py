"""Extracție LLM: AI/jobs/<id>/llm_input.json -> minutes.json (proces-verbal structurat).

    python -m LLM.extract AI/jobs/<job_id>/llm_input.json --date 2026-09-21 [--out minutes.json]
    python -m LLM.extract <job_id> --date 2026-09-21

O fereastră => un singur apel (rezumat + cazuri). Mai multe => un apel per fereastră
(map), unificare în cod (reduce) și un apel final pentru meeting_summary + ordinea cazurilor.
Rulează doar pe Ollama local. Tot ce s-a trimis și primit: <job>/llm_debug/.
"""
import argparse
import ctypes
import json
import statistics
import sys
import time
from collections import Counter
from contextlib import contextmanager
from datetime import date as Date, datetime, timezone
from pathlib import Path

from LLM import attribution, prompt, quotes, sanitize, schemas
from LLM.config import ROOT, load_config
from LLM.loader import Window, est_tokens, fmt_ts, load, ts_seconds
from LLM.merge import Merger
from LLM.ollama import OllamaClient

DEBUG_PATTERNS = ["window_*.prompt.txt", "window_*.response.json", "final.*", "same_case.json", "translate.json",
                  "supersede.json", "checks.json", "quotes.json", "merge.json", "context_check.json", "run.json",
                  "segment.*"]


@contextmanager
def keep_awake():
    """Windows: fără somn automat cât rulează extracția (o ședință lungă durează minute; un laptop
    adormit la mijloc a întins o rulare de 5 min la 47). Doar pentru acest proces; închiderea
    capacului adoarme oricum laptopul."""
    k32 = getattr(getattr(ctypes, "windll", None), "kernel32", None)
    ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001
    if k32:
        k32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)
    try:
        yield
    finally:
        if k32:
            k32.SetThreadExecutionState(ES_CONTINUOUS)


class Debug:
    def __init__(self, path):
        self.dir = Path(path)
        self.dir.mkdir(parents=True, exist_ok=True)
        for pat in DEBUG_PATTERNS:  # doar fișierele noastre, din rularea anterioară
            for f in self.dir.glob(pat):
                f.unlink()

    def write(self, name, obj):
        text = obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False, indent=1)
        (self.dir / name).write_text(text, encoding="utf-8")


def messages_text(msgs):
    return "\n\n".join(f"##### {m['role']}\n{m['content']}" for m in msgs)


def msgs_tokens(msgs):
    return sum(est_tokens(m["content"]) + 4 for m in msgs)  # +4: marcajele de rol din chat template


class Extractor:
    def __init__(self, meeting, date, cfg, client, debug):
        self.mt, self.date, self.cfg, self.client, self.dbg = meeting, date, cfg, client, debug
        self.ctx = cfg["context"]
        self.calls, self.errors, self.warnings = [], [], list(meeting.warnings)
        self.quotes, self.same_case_log, self.supersede_log, self.checks = [], [], [], []
        self._order = 0
        self.observed_factor = None  # tokeni Qwen reali / estimare, măsurat pe apelurile acestei ședințe
        self.mode = "single" if meeting.n_windows == 1 else "map-reduce"  # „patients” dacă segmentarea reușește

    # ---------- context ----------
    def factor(self):
        # config.token_factor e prudent (ferestre doar de transcriere ~1.56, prompt întreg ~1.4); după primul
        # apel folosim raportul real, altfel num_ctx crește degeaba și modelul nu mai încape tot în VRAM
        return self.observed_factor * 1.03 if self.observed_factor else self.ctx["token_factor"]

    def budget(self, msgs, stage):
        """(num_ctx de folosit sau None = nu încape, estimare)."""
        est = int(msgs_tokens(msgs) * self.factor()) + self.cfg["stages"][stage]["num_predict"]
        base = self.cfg["ollama"]["num_ctx"]
        if est <= base:
            return base, est
        if self.ctx["on_overflow"] == "expand" and est <= self.ctx["max_num_ctx"]:
            return -(-est // 1024) * 1024, est
        return None, est

    def context_check(self):
        """Înainte de orice apel: fereastra + promptul fix + antet estimat + ieșire vs num_ctx."""
        single = self.mt.n_windows == 1
        fixed = msgs_tokens(prompt.extract_messages(single, self.date, [], context_lines=[]))
        f, reserve = self.ctx["token_factor"], (0 if single else self.ctx["header_reserve_tokens"])
        out_tok = self.cfg["stages"]["extract"]["num_predict"]
        rows = []
        for w in self.mt.windows:
            est = int((fixed + w.tokens) * f) + reserve + out_tok
            ok = est <= self.cfg["ollama"]["num_ctx"]
            rows.append({"window": w.index, "tokens_est": w.tokens, "prompt_fixed": fixed,
                         "header_reserve": reserve, "num_predict": out_tok, "total_est": est,
                         "num_ctx": self.cfg["ollama"]["num_ctx"], "ok": ok})
            if not ok:
                self.warn(f"fereastra {w.index}: ~{est} tokeni > num_ctx {self.cfg['ollama']['num_ctx']} "
                          f"(on_overflow={self.ctx['on_overflow']}, max_num_ctx={self.ctx['max_num_ctx']})")
        self.dbg.write("context_check.json", rows)
        return rows

    def warn(self, msg):
        self.warnings.append(msg)
        print(f"[llm] ATENȚIE: {msg}")

    def call(self, stage, msgs, schema, label):
        num_ctx, est = self.budget(msgs, stage)
        rec = {"stage": stage, "label": label, "est_tokens": est, "num_ctx": num_ctx}
        if num_ctx is None:
            err = f"{label}: ~{est} tokeni nu încap nici în max_num_ctx={self.ctx['max_num_ctx']}; sărit"
            self.errors.append(err)
            print(f"[llm] EROARE: {err}")
            self.calls.append({**rec, "error": err})
            return {"data": None, "raw": None, "error": err, "errors": [err], "attempts": 0, "meta": {}}
        if num_ctx != self.cfg["ollama"]["num_ctx"]:
            self.warn(f"{label}: ~{est} tokeni, num_ctx mărit la {num_ctx}")
        res = self.client.chat(stage, msgs, schema, num_ctx=num_ctx)
        rec.update(attempts=res["attempts"], error=res["error"],
                   est_prompt_raw=msgs_tokens(msgs), **res.get("meta", {}))
        real = (res.get("meta") or {}).get("prompt_eval_count")
        if real and stage == "extract":  # promptul mare e reprezentativ; cele scurte au alt raport
            self.observed_factor = max(self.observed_factor or 0, real / rec["est_prompt_raw"])
        self.calls.append(rec)
        if res["error"]:
            self.errors.append(f"{label}: {res['error']}")
        return res

    # ---------- împărțirea pe pacienți ----------
    def patient_windows(self):
        """Modul „segment”: un apel scurt împarte ședința pe pacienți (ordine + momentul de început), apoi
        fiecare pacient devine o fereastră separată. Un model mic amestecă pacienții când extrage totul
        dintr-o fereastră mare; o sarcină simplă de delimitare o face mult mai bine.
        -> [(Window, {"label", "start", "cue"})] sau None (dezactivat / eșuat / nu încape: ferestrele ASR)."""
        sc = self.cfg.get("segment") or {}
        ids = sorted(self.mt.turns)
        if not sc.get("enabled") or len(ids) < 2:
            return None
        T = self.mt.turns
        msgs = prompt.segment_messages([T[i].line for i in ids])
        self.dbg.write("segment.prompt.txt", messages_text(msgs))
        if self.budget(msgs, "segment")[0] is None:
            self.warn("împărțirea pe pacienți sărită: transcrierea nu încape într-un apel; folosesc ferestrele ASR")
            return None
        res = self.call("segment", msgs, schemas.SEGMENTS, "segment")
        if res["error"]:  # nu e fatal: continuăm pe ferestrele ASR
            self.errors.pop()
            self.warn(f"împărțirea pe pacienți a eșuat ({res['error']}); folosesc ferestrele ASR")
            return None

        found = []
        for p in (res["data"] or {}).get("patients") or []:
            sec = ts_seconds(sanitize.s(p.get("start")))
            label = sanitize.fix_bed_numbers(sanitize.s(p.get("label")), [], "segment")
            if sec is not None and label:
                found.append((sec, label, sanitize.s(p.get("cue"))))
        starts = []  # (poziția primei replici în ids, label, cue)
        merge_s = sc.get("min_patient_s", 15)
        for sec, label, cue in sorted(found, key=lambda x: x[0]):
            k = next((j for j, i in enumerate(ids) if T[i].start >= sec - 1), None)
            if k is None:
                continue
            if starts and T[ids[k]].start - T[ids[starts[-1][0]]].start < merge_s:
                continue  # două începuturi la câteva secunde (ex. 02:58 și 03:00): același pacient
            starts.append((k, label, cue))
        self.dbg.write("segment.response.json", {"raw": res["raw"], "parsed": res["data"],
                                                 "starts": [{"turn_id": ids[k], "ts": T[ids[k]].ts, "label": lb,
                                                             "cue": cue} for k, lb, cue in starts]})
        if starts and starts[0][0] > 0:
            if T[ids[starts[0][0]]].start - T[ids[0]].start < merge_s:
                starts[0] = (0, *starts[0][1:])  # câteva replici de deschidere („S-a pornit”): ale primului pacient
            else:  # discuție substanțială înaintea primului pacient găsit: e un pacient separat, nenumit
                starts.insert(0, (0, "pacientul discutat la începutul ședinței (identifică-l din fragment)", ""))
        if len(starts) < 2:
            self.warn("împărțirea pe pacienți a găsit un singur pacient; folosesc ferestrele ASR")
            return None

        max_tok, ctx_n = sc.get("max_segment_tokens", 1200), sc.get("context_turns", 2)
        out = []
        for n, (k, label, cue) in enumerate(starts):
            seg = ids[k:starts[n + 1][0] if n + 1 < len(starts) else len(ids)]
            chunks, cur, tok = [], [], 0  # un pacient lung se împarte în bucăți, cu aceeași etichetă
            for i in seg:
                if cur and tok + T[i].tokens > max_tok:
                    chunks.append(cur)
                    cur, tok = [], 0
                cur.append(i)
                tok += T[i].tokens
            chunks.append(cur)
            for ch in chunks:
                pos0 = ids.index(ch[0])
                tids = ids[max(0, pos0 - ctx_n):pos0] + ch
                out.append((Window(index=len(out), turn_ids=tids, overlap_turns=len(tids) - len(ch),
                                   start=T[ch[0]].start, end=T[ch[-1]].end, tokens=sum(T[i].tokens for i in tids),
                                   text="\n".join(T[i].line for i in tids), lines=[T[i].line for i in tids]),
                            {"label": label, "start": T[ch[0]].ts, "cue": cue}))
        self.mode = "patients"
        print(f"[llm] împărțire pe pacienți: {len(starts)} pacienți, {len(out)} fragmente")
        return out

    # ---------- map ----------
    def window(self, pos, w, merger, summaries, n_windows=None, patient=None):
        n_windows = n_windows or self.mt.n_windows
        single = n_windows == 1 and not patient
        label = f"window_{w.index:03d}"
        if single:
            msgs = prompt.extract_messages(True, self.date, w.lines)
        else:
            prev = [s for _, s in summaries if s][-self.ctx["header_summaries"]:]
            msgs = prompt.extract_messages(
                False, self.date, w.new_lines, context_lines=w.context_lines, window_number=pos + 1,
                n_windows=n_windows, known_cases=merger.known_cases(self.ctx["header_max_cases"]),
                previous_summary=" ".join(prev), patient=patient)
        self.dbg.write(f"{label}.prompt.txt", messages_text(msgs))

        t0 = time.perf_counter()
        ec = self.cfg.get("extract", {})
        res = self.call("extract", msgs, schemas.extract_schema(ec.get("max_cases"), ec.get("max_decisions"),
                                                                ec.get("max_facts")), label)
        cases, summary, fixes = sanitize.extract(res["data"]) if res["data"] is not None else ([], "", [])

        keys = Counter(c["case_key"] for c in cases)
        for key, n in keys.items():
            if n >= 3:
                self.warn(f"{label}: modelul a repetat cazul {key!r} de {n} ori (buclă)")

        turns = [self.mt.turns[i] for i in w.turn_ids]
        for case in cases:
            for d in case["decisions"]:
                r = quotes.resolve(d["quote"], d["timestamp"], turns, self.cfg["quotes"])
                r.update(window=w.index, case_key=case["case_key"],
                         in_context=r["turn_id"] in set(w.context_ids) if r["turn_id"] is not None else None)
                self.quotes.append(r)
                d["turn_id"] = r["turn_id"]
                if r["turn_id"] is not None:
                    t = self.mt.turns[r["turn_id"]]
                    d["timestamp"], d["_t"] = t.ts, t.start
                    r["turn_uncertain"] = t.uncertain
                else:
                    sec = ts_seconds(d["timestamp"])
                    d["_t"] = sec if sec is not None else w.start
                d["_window"], d["_order"] = w.index, self._order
                self._order += 1

        # în modul pe pacienți fragmentul stabilește deja pacientul; mutarea după „ultimul număr pomenit”
        # greșește când pacientul curent e numit fără număr (ex. „patul nou”) și îl ia pe cel anterior
        events = [] if patient else attribution.fix_attribution(cases, self.mt.turns, [c["case_key"] for c in merger.cases])
        events += attribution.check_eta_raw(cases, turns)
        self.checks += [{"window": w.index, **e} for e in events]

        self.dbg.write(f"{label}.response.json", {
            "window": w.index, "turn_ids": [w.turn_ids[0], w.turn_ids[-1]] if w.turn_ids else [],
            "overlap_turns": w.overlap_turns, "error": res["error"], "errors": res["errors"],
            "meta": res["meta"], "raw": res["raw"], "parsed": res["data"], "sanitize_fixes": fixes,
            "checks": events, "cases_after_checks": cases,
        })

        ctx_end = ts_seconds(self.mt.turns[w.context_ids[-1]].ts) if w.context_ids else -1
        merger.add_window(w.index, w.context_ids, ctx_end, cases)
        summaries.append((w, summary))
        n_dec = sum(len(c["decisions"]) for c in cases)
        print(f"[llm] fereastra {pos + 1}/{n_windows}: {len(cases)} cazuri, {n_dec} decizii "
              f"({time.perf_counter() - t0:.1f} s){' EROARE' if res['error'] else ''}")

    def same_case(self, a, b):
        res = self.call("same_case", prompt.same_case_messages(a, b), schemas.SAME_CASE,
                        f"same_case {a['case_key']!r} ~ {b['case_key']!r}")
        ans = None if res["data"] is None else bool(res["data"].get("same"))
        self.same_case_log.append({"a": a["case_key"], "b": b["case_key"], "answer": ans,
                                   "raw": res["raw"], "error": res["error"]})
        return ans

    def supersede(self, case_key, decisions):
        res = self.call("supersede", prompt.supersede_messages(case_key, decisions), schemas.SUPERSEDE,
                        f"supersede {case_key!r}")
        idx = None
        if res["data"] is not None:
            idx = {i for i in res["data"].get("superseded") or [] if isinstance(i, int)}
        self.supersede_log.append({"case_key": case_key, "n_decisions": len(decisions),
                                   "answer": None if idx is None else sorted(idx), "raw": res["raw"],
                                   "error": res["error"]})
        return idx

    # ---------- reduce ----------
    def final(self, cases, summaries):
        lines = []
        for i, c in enumerate(cases):
            active = [d for d in c["decisions"] if not d["superseded"]]
            last = f"{active[-1]['status']}: {active[-1]['decision']}" if active else "fără decizie"
            lines.append(f"{i}: {c['case_key']} — {c['topic']} — {last}")
        wsum = [f"Fragmentul {k + 1} ({fmt_ts(w.start)}–{fmt_ts(w.end)}): {s}"
                for k, (w, s) in enumerate(summaries) if s]
        msgs = prompt.final_messages(self.date, wsum, lines)
        self.dbg.write("final.prompt.txt", messages_text(msgs))
        res = self.call("final", msgs, schemas.FINAL, "final")
        self.dbg.write("final.response.json", {k: res.get(k) for k in ("error", "errors", "meta", "raw", "thinking")}
                       | {"parsed": res["data"]})

        data = res["data"] or {}
        summary = sanitize.fix_ranges(sanitize.s(data.get("meeting_summary")))
        if not summary:
            summary = " ".join(s for _, s in summaries if s)
            self.warn("meeting_summary lipsă din apelul final: folosesc rezumatele ferestrelor")
        order, seen = [], set()
        for i in data.get("case_order") or []:
            if isinstance(i, int) and 0 <= i < len(cases) and i not in seen:
                order.append(i)
                seen.add(i)
        order += [i for i in range(len(cases)) if i not in seen]
        return summary, [cases[i] for i in order]

    def translate(self, minutes):
        """Rusa rămasă în câmpurile care trebuie să fie în română -> un apel scurt de traducere.
        Se acceptă doar traduceri complete și fără chirilică; altfel textul rămâne (și e semnalat)."""
        refs = sanitize.non_romanian_fields(minutes)
        if not refs:
            return
        texts = [obj[key] for obj, key in refs]
        res = self.call("translate", prompt.translate_messages(texts), schemas.TRANSLATE, "translate")
        out = (res["data"] or {}).get("texts") if res["data"] else None
        log = {"texts": texts, "raw": res["raw"], "error": res["error"], "applied": []}
        if isinstance(out, list) and len(out) == len(texts):
            for (obj, key), old, new in zip(refs, texts, out):
                new = sanitize.s(new)
                if new and not sanitize.CYRILLIC.search(new):
                    obj[key] = sanitize.fix_ranges(new)
                    log["applied"].append({"from": old, "to": obj[key]})
                    self.checks.append({"window": None, "event": "translated_to_romanian", "from": old, "to": obj[key]})
        self.dbg.write("translate.json", log)

    # ---------- tot ----------
    def run(self):
        self.context_check()
        merger = Merger(self.cfg, same_case=self.same_case)
        summaries = []
        pw = self.patient_windows()
        wins = [w for w, _ in pw] if pw else self.mt.windows
        pats = [p for _, p in pw] if pw else [None] * len(wins)
        for pos, (w, p) in enumerate(zip(wins, pats)):
            self.window(pos, w, merger, summaries, len(wins), p)
        cases = merger.result(self.supersede)
        if len(wins) <= 1:
            meeting_summary = summaries[0][1] if summaries else ""
        elif not cases and not any(s for _, s in summaries):
            meeting_summary = ""  # nimic extras (ferestre eșuate sau goale): nu are ce rezuma
        else:
            meeting_summary, cases = self.final(cases, summaries)
        self.dbg.write("quotes.json", self.quotes)
        self.dbg.write("same_case.json", self.same_case_log)
        self.dbg.write("supersede.json", self.supersede_log)
        self.dbg.write("checks.json", self.checks)
        self.dbg.write("merge.json", {"events": merger.events, "cases": cases})
        minutes = {"meeting_summary": meeting_summary, "cases": cases}
        self.translate(minutes)
        # deocamdată ieșirea e doar în română; alte limbi vor veni mai târziu
        for p in sanitize.romanian_problems(minutes):
            self.warn(f"nu e în română: {p}")
        return minutes


def extract(input_path, date, out=None, debug_dir=None, cfg=None, client=None):
    """Funcția apelată de pipeline după ce scrie llm_input.json. Returnează raportul rulării."""
    cfg = cfg or load_config()
    Date.fromisoformat(date)  # YYYY-MM-DD, altfel ValueError
    t0 = time.perf_counter()
    meeting = load(input_path, cfg["input"]["uncertain_ratio"])
    out = Path(out) if out else meeting.path.parent / "minutes.json"
    dbg = Debug(debug_dir or meeting.path.parent / "llm_debug")
    path = "single" if meeting.n_windows == 1 else "map-reduce"
    print(f"[llm] {meeting.job_id}: {len(meeting.turns)} replici, {meeting.n_windows} ferestre, "
          f"cale {path}, model {cfg['ollama']['model']}")
    for w in meeting.warnings:
        print(f"[llm] ATENȚIE: {w}")

    ex = Extractor(meeting, date, cfg, client or OllamaClient(cfg), dbg)
    with keep_awake():
        minutes = ex.run()
    path = ex.mode  # „patients” dacă împărțirea pe pacienți a reușit

    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.name + ".part")
    tmp.write_text(json.dumps(minutes, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(out)

    ratios = [c["prompt_eval_count"] / c["est_prompt_raw"] for c in ex.calls
              if c.get("prompt_eval_count") and c.get("est_prompt_raw")]
    report = {
        "job_id": meeting.job_id, "input": str(meeting.path), "out": str(out), "date": date,
        "finished_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "seconds": round(time.perf_counter() - t0, 2), "path": path, "model": cfg["ollama"]["model"],
        "n_turns": len(meeting.turns), "n_windows": meeting.n_windows,
        "audio_duration_s": meeting.audio_duration_s, "languages": meeting.languages,
        "n_cases": len(minutes["cases"]), "n_decisions": sum(len(c["decisions"]) for c in minutes["cases"]),
        "quotes_unresolved": sum(q["turn_id"] is None for q in ex.quotes),
        "checks": dict(Counter(e["event"] for e in ex.checks)),  # corecții în cod, detalii în checks.json
        # tokeni Qwen reali / estimarea utf8/4: dacă diferă mult de 1, ajustează context.token_factor
        "token_factor_observed": round(statistics.median(ratios), 3) if ratios else None,
        "errors": ex.errors, "warnings": ex.warnings, "calls": ex.calls,
        "config": {k: cfg[k] for k in ("ollama", "stages", "context", "merge")},
    }
    dbg.write("run.json", report)
    print(f"[llm] gata în {report['seconds']} s: {report['n_cases']} cazuri, {report['n_decisions']} decizii, "
          f"{report['quotes_unresolved']} citate fără replică, {len(ex.errors)} erori -> {out}")
    return report


def extract_job(job_id, date, cfg=None, force=False):
    """Pentru AI/run_pipeline.py: AI/jobs/<job_id>/llm_input.json -> minutes.json, cu etapa „llm” în status.json."""
    from pipeline.common import jobs_dir, load_config as load_pipeline_config, run_stage

    job_dir = jobs_dir(load_pipeline_config()) / job_id
    out = job_dir / "minutes.json"
    if force and out.exists():
        out.unlink()

    def work():
        r = extract(job_dir / "llm_input.json", date, out, cfg=cfg)
        return {k: r[k] for k in ("path", "n_windows", "n_cases", "n_decisions", "quotes_unresolved",
                                  "token_factor_observed")} | {"llm_errors": len(r["errors"])}

    run_stage(job_dir, "llm", out, work)
    return out


def resolve_input(arg):
    p = Path(arg)
    if p.is_file():
        return p
    job = ROOT / "jobs" / arg / "llm_input.json"
    if job.is_file():
        return job
    raise SystemExit(f"Nu găsesc {arg} (nici ca fișier, nici ca AI/jobs/{arg}/llm_input.json)")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input", help="llm_input.json sau job_id")
    ap.add_argument("--date", required=True, help="data ședinței, YYYY-MM-DD")
    ap.add_argument("--out", help="implicit: minutes.json lângă llm_input.json")
    ap.add_argument("--debug-dir", help="implicit: llm_debug/ lângă llm_input.json")
    ap.add_argument("--config", help="implicit: LLM/config.yaml")
    ap.add_argument("--think-final", action="store_true", help="think: true pentru apelul final")
    args = ap.parse_args()

    try:
        Date.fromisoformat(args.date)
    except ValueError:
        sys.exit(f"--date trebuie să fie YYYY-MM-DD, nu {args.date!r}")
    cfg = load_config(args.config)
    if args.think_final:
        cfg["stages"]["final"]["think"] = True
    report = extract(resolve_input(args.input), args.date, args.out, args.debug_dir, cfg)
    sys.exit(1 if report["errors"] and report["n_cases"] == 0 and report["n_windows"] else 0)


if __name__ == "__main__":
    main()
