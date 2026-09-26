"""Extracție LLM: jobs/<id>/llm_input.json -> minutes.json (proces-verbal structurat).

    python -m LLM.extract jobs/<job_id>/llm_input.json --date 2026-09-21 [--out minutes.json]
    python -m LLM.extract <job_id> --date 2026-09-21

O fereastră => un singur apel (rezumat + cazuri). Mai multe => un apel per fereastră
(map), unificare în cod (reduce) și un apel final pentru meeting_summary + ordinea cazurilor.
Rulează doar pe Ollama local. Tot ce s-a trimis și primit: <job>/llm_debug/.
"""
import argparse
import json
import statistics
import sys
import time
from collections import Counter
from datetime import date as Date, datetime, timezone
from pathlib import Path

from LLM import attribution, prompt, quotes, sanitize, schemas
from LLM.config import ROOT, load_config
from LLM.loader import est_tokens, fmt_ts, load, ts_seconds
from LLM.merge import Merger
from LLM.ollama import OllamaClient

DEBUG_PATTERNS = ["window_*.prompt.txt", "window_*.response.json", "final.*", "same_case.json",
                  "supersede.json", "checks.json", "quotes.json", "merge.json", "context_check.json", "run.json"]


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

    # ---------- context ----------
    def budget(self, msgs, stage):
        """(num_ctx de folosit sau None = nu încape, estimare)."""
        est = int(msgs_tokens(msgs) * self.ctx["token_factor"]) + self.cfg["stages"][stage]["num_predict"]
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
        self.calls.append(rec)
        if res["error"]:
            self.errors.append(f"{label}: {res['error']}")
        return res

    # ---------- map ----------
    def window(self, pos, w, merger, summaries):
        single = self.mt.n_windows == 1
        label = f"window_{w.index:03d}"
        if single:
            msgs = prompt.extract_messages(True, self.date, w.lines)
        else:
            prev = [s for _, s in summaries if s][-self.ctx["header_summaries"]:]
            msgs = prompt.extract_messages(
                False, self.date, w.new_lines, context_lines=w.context_lines, window_number=pos + 1,
                n_windows=self.mt.n_windows, known_cases=merger.known_cases(self.ctx["header_max_cases"]),
                previous_summary=" ".join(prev))
        self.dbg.write(f"{label}.prompt.txt", messages_text(msgs))

        t0 = time.perf_counter()
        ec = self.cfg.get("extract", {})
        res = self.call("extract", msgs, schemas.extract_schema(ec.get("max_cases"), ec.get("max_decisions")), label)
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

        events = attribution.fix_attribution(cases, self.mt.turns, [c["case_key"] for c in merger.cases])
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
        print(f"[llm] fereastra {pos + 1}/{self.mt.n_windows}: {len(cases)} cazuri, {n_dec} decizii "
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
        summary = sanitize.s(data.get("meeting_summary"))
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

    # ---------- tot ----------
    def run(self):
        self.context_check()
        merger = Merger(self.cfg, same_case=self.same_case)
        summaries = []
        for pos, w in enumerate(self.mt.windows):
            self.window(pos, w, merger, summaries)
        cases = merger.result(self.supersede)
        if self.mt.n_windows <= 1:
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
        return {"meeting_summary": meeting_summary, "cases": cases}


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
    minutes = ex.run()

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
    """Pentru run_pipeline.py: jobs/<job_id>/llm_input.json -> minutes.json, cu etapa „llm” în status.json."""
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
    raise SystemExit(f"Nu găsesc {arg} (nici ca fișier, nici ca jobs/{arg}/llm_input.json)")


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
