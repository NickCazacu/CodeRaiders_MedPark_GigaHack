"""Teste manuale: dai un JSON cu ședința, vezi ce a extras modelul (punctele principale, termenele).

    python -m LLM.manual all                                  # toate din LLM/test_meetings/ -> LLM/test_moms/*.md
    python -m LLM.manual NUME                                 # doar LLM/test_meetings/NUME.json
    python -m LLM.manual sedinta.json [--date YYYY-MM-DD]     # orice JSON; rulează modelul și afișează rezultatul
    python -m LLM.manual review sedinta.json|JOB_ID           # doar afișarea, din rezultatele existente
    python -m LLM.manual new NUME                             # alternativ: ședință scrisă ca text

JSON-ul poate fi llm_input.json (ieșirea pipeline-ului), transcript.json (segmente, se împachetează
cu pipeline.pack_for_llm) sau o listă de replici cu "line". Data: --date, altfel câmpul
"meeting_date" din JSON, altfel azi. Rezultatele: lângă AI/jobs/<id>/llm_input.json pentru un job real,
altfel în LLM/runs/<nume_fișier>/ (minutes.json, llm_debug/, review.md).

În terminal: punctele principale, termenele (când, ce, tip, condiție), deciziile pe cazuri.
review.md: în plus, fiecare decizie lângă replica citată, corecțiile din cod, transcrierea
marcată și o listă de verificare.

Formatul meeting.txt (pentru `new`): o replică pe linie, ca în ferestrele ASR:
    [00:32] SPEAKER_02: Давайте повторим креатинин вечером. [?]
[mm:ss] și vorbitorul sunt opționale (implicit: timp din lungimea textului, vorbitor UNK),
[?] = încredere scăzută, linie goală = pauză (schimbare de subiect), # = comentariu,
`# date: 2026-09-21`, `# window_tokens: 300`, `# overlap: 3` = setări.
"""
import argparse
import json
import re
import sys
from collections import Counter
from datetime import date as Date
from pathlib import Path

from LLM import mom
from LLM.config import LLM_DIR, ROOT
from LLM.loader import load, ts_seconds

MANUAL = LLM_DIR / "manual_tests"
MEETINGS = LLM_DIR / "test_meetings"   # ședințele de test (.json), câte un fișier per ședință
MOMS = LLM_DIR / "test_moms"           # MoM-ul generat pentru fiecare: <nume>.md
JOBS = ROOT / "jobs"                        # joburile pipeline-ului: AI/jobs/<job_id>/
RUNS = LLM_DIR / "runs"                     # rulările manuale (în LLM/.gitignore)
LINE = re.compile(r"^(?:\[(?P<ts>\d+:\d{2}(?::\d{2})?)\]\s*)?(?:(?P<spk>UNK|SPEAKER_\d+):\s*)?(?P<text>.*?)"
                  r"\s*(?P<low>\[\?\])?\s*$")
SETTING = re.compile(r"^#\s*(date|window_tokens|overlap)\s*:\s*(\S+)")
TOPIC_PAUSE = 15.0

TEMPLATE = """\
# Ședință de test: {name}
# date: {date}
# window_tokens: 3500
# overlap: 3
#
# O replică pe linie, ca în ferestrele ASR:  [mm:ss] SPEAKER_01: text [?]
#   [mm:ss] opțional (altfel se calculează din lungimea textului)
#   SPEAKER_NN: / UNK: opțional (implicit UNK)
#   [?] la sfârșit = transcriere nesigură (low_confidence)
#   linie goală = pauză de 15 s (schimbare de subiect)
#   window_tokens mic (ex. 300) => mai multe ferestre, ca la o ședință lungă
#
# Înlocuiește exemplul de mai jos cu ședința ta.

Începem. Pacientul 12 din cardiologie, fibrilație atrială, frecvența 140.
Давайте начнём амиодарон сегодня и ЭКГ завтра утром.
De acord, amiodaronă azi, ECG mâine dimineață.

Pacienta din salonul 4, după colecistectomie, febrilă.
Facem ecografie abdominală azi și, dacă e colecție, drenaj. [?]
"""


# ---------- meeting.txt -> llm_input.json ----------
def lang_of(text):
    cyr = sum("Ѐ" <= c <= "ӿ" for c in text)
    lat = sum(c.isascii() and c.isalpha() for c in text)
    return "ru" if cyr > lat else "ro"


def build_llm_input(job_id, utterances, window_tokens=3500, overlap=3, start=1.0, tail=3.0):
    """utterances: dict-uri {speaker, text, at?, dur?, pause?, low?, lang?} -> dict în formatul llm_input.json,
    construit cu make_turns / make_windows din pipeline/pack_for_llm.py (exact formatul real)."""
    from pipeline.pack_for_llm import make_turns, make_windows

    segs, t = [], start
    for i, u in enumerate(utterances):
        t = u.get("at", t + u.get("pause", 0.0))
        dur = u.get("dur") or round(max(2.0, len(u["text"]) / 13), 1)
        segs.append({"id": i, "speaker": u.get("speaker", "UNK"), "start": round(t, 1), "end": round(t + dur, 1),
                     "text": u["text"], "lang": u.get("lang") or lang_of(u["text"]),
                     "low_confidence": bool(u.get("low")), "flags": [], "dropped": None})
        t += dur + 0.8
    for a, b in zip(segs, segs[1:]):  # timpii expliciți pot fi mai apropiați decât durata estimată
        if a["end"] > b["start"] - 0.2:
            a["end"] = round(max(a["start"] + 0.5, b["start"] - 0.2), 1)
    turns = make_turns(segs, 800, True)
    windows = make_windows(turns, window_tokens, overlap)
    return {
        "job_id": job_id,
        "audio_duration_s": round(t + tail, 1),
        "speakers": sorted({x["speaker"] for x in turns}),
        "languages": dict(Counter(s["lang"] for s in segs)),
        "format": "[mm:ss] SPEAKER: text  (\" [?]\" = încredere scăzută)",
        "token_estimate": "utf8_bytes/4",
        "n_turns": len(turns),
        "n_windows": len(windows),
        "turns": turns,
        "windows": windows,
    }


def parse_meeting(path):
    """meeting.txt -> (setări, replici)."""
    settings, utts, pause = {}, [], 0.0
    for raw in Path(path).read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line.startswith("#"):
            m = SETTING.match(line)
            if m:
                settings[m.group(1)] = m.group(2)
            continue
        if not line:
            pause = TOPIC_PAUSE if utts else 0.0
            continue
        m = LINE.match(line)
        u = {"speaker": m["spk"] or "UNK", "text": m["text"], "low": bool(m["low"]), "pause": pause}
        if m["ts"]:
            u["at"] = float(ts_seconds(m["ts"]))
        if u["text"]:
            utts.append(u)
        pause = 0.0
    return settings, utts


# ---------- review.md ----------
def read_json(path, default=None):
    p = Path(path)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else default


ETA_LABEL = {"absolute": "dată fixă", "relative": "relativ", "duration": "durată", "conditional": "condiționat",
             "vague": "vag", "recurring": "repetat", "none": "fără termen"}


def active(c):
    """Ultima decizie neînlocuită a cazului (cea la care se referă ETA-ul)."""
    a = [d for d in c["decisions"] if not d["superseded"]]
    return a[-1] if a else None


def q(raw):
    """Expresia din transcriere între ghilimele; lipsa ei (eta.raw null) nu arată ca un citat."""
    return f"„{raw}”" if raw else "(expresia lipsește din transcriere)"


def eta_text(e):
    if e["type"] == "none":
        return "—"
    return f"{e['type']} {q(e['raw'])}" + (f", condiție: {e['condition']}" if e["condition"] else "")


def deadlines(cases):
    """Termenele: (C#, caz, decizia, când, tip, condiție, timp) pentru cazurile cu ETA."""
    rows = []
    for i, c in enumerate(cases, 1):
        e, a = c["eta"], active(c)
        if e["type"] != "none":
            rows.append((f"C{i}", c["case_key"], a["decision"] if a else "—", q(e["raw"]),
                         ETA_LABEL[e["type"]], e["condition"] or "", a["timestamp"] if a else ""))
    return rows


def load_results(job_dir):
    job_dir = Path(job_dir)
    minutes = read_json(job_dir / "minutes.json")
    if minutes is None:
        raise SystemExit(f"Lipsește {job_dir / 'minutes.json'}: rulează întâi extracția")
    dbg = job_dir / "llm_debug"
    return (load(job_dir / "llm_input.json"), minutes, read_json(dbg / "run.json", {}),
            read_json(dbg / "checks.json", []), read_json(dbg / "quotes.json", []))


def overview(job_dir):
    """Textul pentru terminal: punctele principale, termenele, deciziile, ce e de verificat."""
    mt, minutes, run, checks, quotes = load_results(job_dir)
    cases = minutes["cases"]
    bar = "═" * 78
    out = [bar, f" {mt.job_id} · {run.get('date', '?')} · {len(mt.turns)} replici, {mt.n_windows} ferestre · "
                f"{run.get('model', '?')} · {run.get('seconds', '?')} s", bar, "",
           "PUNCTELE PRINCIPALE", minutes["meeting_summary"] or "(gol)", ""]

    out.append("TERMENE")
    rows = deadlines(cases)
    if rows:
        for num, key, what, when, typ, cond, ts in rows:
            out.append(f"  {num} {key}")
            out.append(f"     când: {when} ({typ}{', dacă ' + cond if cond else ''})  [{ts}]")
            out.append(f"     ce:   {what}")
    else:
        out.append("  (niciun termen)")
    without = [f"C{i} {c['case_key']}" for i, c in enumerate(cases, 1) if c["eta"]["type"] == "none" and c["decisions"]]
    if without:
        out.append(f"  fără termen: {'; '.join(without)}")
    out.append("")

    out.append("CAZURI ȘI DECIZII")
    for i, c in enumerate(cases, 1):
        out.append(f"  C{i} {c['case_key']}: {c['topic']}")
        for d in c["decisions"]:
            t = mt.turns.get(d["turn_id"]) if d["turn_id"] is not None else None
            mark = "✗ înlocuită " if d["superseded"] else "✓ "
            warn = " ⛔ citat negăsit" if t is None else (" ⚠ replică nesigură" if t.uncertain else "")
            out.append(f"     {mark}{d['status']}: {d['decision']}  [{d['timestamp']}]{warn}")
        if not c["decisions"]:
            out.append("     (fără decizii)")
        for q in c["open_questions"]:
            out.append(f"     ? {q}")
    if not cases:
        out.append("  (niciun caz)")
    out.append("")

    n_unres = sum(q["turn_id"] is None for q in quotes)
    out.append(f"DE VERIFICAT: {len(checks)} corecții în cod, {n_unres} citate negăsite, "
               f"{len(run.get('warnings', []))} avertismente, {len(run.get('errors', []))} erori")
    out.append(f"  proces-verbal (MoM): {MOMS / (Path(job_dir).name.removeprefix('manual-') + '.md')}")
    out.append(f"                       {Path(job_dir) / 'mom.html'} (aceeași, pentru browser)")
    out.append(f"  raport tehnic (citate lângă replici, transcriere, listă de verificare): {Path(job_dir) / 'review.md'}")
    return "\n".join(out)


def review(job_dir, title=None):
    """minutes.json + llm_input.json + llm_debug/ -> review.md (fără apeluri la model)."""
    job_dir = Path(job_dir)
    mt, minutes, run, checks, quotes = load_results(job_dir)
    cases = minutes["cases"]

    cited = {}  # turn_id -> ["C1", ...]
    for i, c in enumerate(cases, 1):
        for d in c["decisions"]:
            if d["turn_id"] is not None:
                cited.setdefault(d["turn_id"], []).append(f"C{i}")

    out = [f"# Revizuire: {title or mt.job_id}", ""]
    out += [f"- Data ședinței: {run.get('date', '?')} · model: {run.get('model', '?')} · "
            f"rulare: {run.get('seconds', '?')} s · cale: {run.get('path', '?')}",
            f"- {len(mt.turns)} replici, {mt.n_windows} ferestre, limbi: {mt.languages}",
            f"- {len(cases)} cazuri, {sum(len(c['decisions']) for c in cases)} decizii, "
            f"{sum(q['turn_id'] is None for q in quotes)} citate fără replică, {len(run.get('errors', []))} erori",
            "", "## Rezumatul ședinței", "", minutes["meeting_summary"] or "_(gol)_", "",
            "## Subiecte principale", "",
            "| # | Caz | Subiect | Ultima decizie | ETA |", "|---|---|---|---|---|"]
    for i, c in enumerate(cases, 1):
        a = active(c)
        last = f"{a['status']}: {a['decision']} [{a['timestamp']}]" if a else "—"
        out.append(f"| C{i} | {c['case_key']} | {c['topic']} | {last} | {eta_text(c['eta'])} |".replace("\n", " "))
    if not cases:
        out.append("| — | _niciun caz_ | | | |")

    out += ["", "## Termene", "",
            "`când` e expresia exactă din transcriere; data concretă o calculează validarea.", "",
            "| # | Caz | Ce | Când | Tip | Condiție | Timp |", "|---|---|---|---|---|---|---|"]
    rows = deadlines(cases)
    out += [("| " + " | ".join(r) + " |").replace("\n", " ") for r in rows] or ["| — | _niciun termen_ | | | | | |"]

    out += ["", "## Cazuri", ""]
    for i, c in enumerate(cases, 1):
        out += [f"### C{i}. {c['case_key']}: {c['topic']}", "", c["discussion_summary"] or "_(fără rezumat)_", ""]
        if c["decisions"]:
            out.append("**Decizii** (bifează dacă e corectă: există, e la pacientul corect, statusul e corect):")
            out.append("")
            for d in c["decisions"]:
                flags = []
                if d["superseded"]:
                    flags.append("înlocuită")
                t = mt.turns.get(d["turn_id"]) if d["turn_id"] is not None else None
                if t is None:
                    flags.append("⛔ citat negăsit în transcriere")
                elif t.uncertain:
                    flags.append("⚠ replică nesigură")
                text = f"~~{d['decision']}~~" if d["superseded"] else d["decision"]
                out.append(f"- [ ] **{d['status']}**: {text} · [{d['timestamp']}]"
                           + (f" · replica {d['turn_id']}" if d["turn_id"] is not None else "")
                           + (f" · {', '.join(flags)}" if flags else ""))
                out.append(f"  - citat: „{d['quote']}”")
                if t is not None:
                    out.append(f"  - replica: `{t.line}`")
            out.append("")
        else:
            out += ["_Nicio decizie._", ""]
        out.append(f"**ETA:** {eta_text(c['eta'])}")
        if c["open_questions"]:
            out += ["", "**Întrebări deschise:**", *[f"- {q}" for q in c["open_questions"]]]
        out.append("")

    signals = [f"- {e['event']}: " + ", ".join(f"{k}={v}" for k, v in e.items() if k != "event") for e in checks]
    signals += [f"- avertisment: {w}" for w in run.get("warnings", [])]
    signals += [f"- EROARE: {e}" for e in run.get("errors", [])]
    out += ["## Corecții făcute în cod și semnale", "",
            "Verifică dacă fiecare corecție e corectă (de ex. o decizie mutată la alt pacient).", ""]
    out += signals or ["_Niciuna._"]

    out += ["", "## Transcriere", "",
            "`▶ C1` = replică citată de o decizie din cazul C1 · `⚠` = nesigură · `┄` = context repetat din fereastra anterioară",
            "", "```"]
    for w in mt.windows:
        out.append(f"── fereastra {w.index + 1}/{mt.n_windows} ──")
        for k, (tid, line) in enumerate(zip(w.turn_ids, w.lines)):
            mark = "┄ " if k < w.overlap_turns else "  "
            tags = " ".join(f"▶ {x}" for x in cited.get(tid, [])) if k >= w.overlap_turns else ""
            warn = "⚠ " if mt.turns[tid].uncertain else "  "
            out.append(f"{mark}{warn}{line}" + (f"   {tags}" if tags else ""))
    out += ["```", "", "## Listă de verificare", "",
            "- [ ] Toate cazurile discutate apar, fiecare o singură dată, cu un case_key recognoscibil",
            "- [ ] Nicio decizie inventată; constatările (analize, diagnostice) nu apar ca decizii",
            "- [ ] Fiecare citat e verbatim și e la pacientul corect",
            "- [ ] Propozițiile întrerupte și replicile retrase nu apar ca decizii",
            "- [ ] Deciziile schimbate sunt marcate înlocuite; ultima decizie e activă",
            "- [ ] ETA: tip corect, raw copiat exact, condiția completată la conditional",
            "- [ ] Rezumatul acoperă subiectele principale, cu timpi [mm:ss] corecți",
            "- [ ] Nu apar vorbitori (UNK, SPEAKER_NN) în rezumate", "",
            "## Note", "", "_Observațiile tale aici._", ""]
    path = job_dir / "review.md"
    path.write_text("\n".join(out), encoding="utf-8")
    return path


# ---------- intrare JSON ----------
def normalize_input(data, job_id, window_tokens=None, overlap=3):
    """Acceptă llm_input.json (obiect cu turns/windows), o listă de replici (cu "line") sau
    transcript.json (listă de segmente cu "text", ieșirea postprocess) -> obiect llm_input.
    window_tokens dat pentru un llm_input => ferestrele se refac (experimente cu ferestre mai mici)."""
    if isinstance(data, dict) and "turns" in data:
        if window_tokens:
            from pipeline.pack_for_llm import make_windows

            windows = make_windows(data["turns"], window_tokens, overlap)
            data = dict(data, windows=windows, n_windows=len(windows))
        return data
    if isinstance(data, list) and data and "line" in data[0]:
        return {"job_id": job_id, "turns": data}  # loader-ul face o singură fereastră
    if isinstance(data, list) and data and "text" in data[0]:
        from pipeline.pack_for_llm import make_turns, make_windows

        segs = [dict(s, flags=s.get("flags", []), low_confidence=s.get("low_confidence", False),
                     lang=s.get("lang", "ro"), speaker=s.get("speaker", "UNK"))
                for s in data if not s.get("dropped") and s.get("text")]
        turns = make_turns(segs, 800, True)
        windows = make_windows(turns, window_tokens or 3500, overlap)
        return {"job_id": job_id, "audio_duration_s": segs[-1]["end"] if segs else None,
                "speakers": sorted({t["speaker"] for t in turns}),
                "languages": dict(Counter(s["lang"] for s in segs)), "n_turns": len(turns),
                "n_windows": len(windows), "turns": turns, "windows": windows}
    raise SystemExit("JSON nerecunoscut: aștept llm_input.json (obiect cu 'turns' și 'windows'), "
                     "o listă de replici cu 'line' sau transcript.json (listă de segmente cu 'text').")


# ---------- comenzi ----------
def resolve(target):
    """NUME din test_meetings / manual_tests, job_id din AI/jobs, sau cale spre un .json
    -> (job_dir, meeting.txt, json sursă)."""
    p = Path(target)
    if p.suffix != ".json" and (MEETINGS / f"{target}.json").is_file():
        p = MEETINGS / f"{target}.json"
    if p.suffix == ".json":
        if not p.is_file():
            raise SystemExit(f"Nu există: {p}")
        p = p.resolve()
        if p.name == "llm_input.json" and p.parent.parent == JOBS.resolve():
            return p.parent, None, None  # job real: rezultatele lângă el
        return RUNS / re.sub(r'[^A-Za-z0-9_-]+', '_', p.stem), None, p
    if (MANUAL / target / "meeting.txt").is_file():
        return RUNS / target, MANUAL / target / "meeting.txt", None
    if (JOBS / target / "llm_input.json").is_file():
        return JOBS / target, None, None
    raise SystemExit(f"Nu găsesc {target}: nici LLM/test_meetings/{target}.json, nici un fișier .json, "
                     f"nici AI/jobs/{target}/llm_input.json, nici LLM/manual_tests/{target}/meeting.txt.")


def export_mom(job_dir, moms_dir=None):
    """mom.md din job -> LLM/test_moms/<nume>.md (numele ședinței, fără prefixul manual-)."""
    moms_dir = Path(moms_dir or MOMS)
    moms_dir.mkdir(parents=True, exist_ok=True)
    name = Path(job_dir).name.removeprefix("manual-")
    dst = moms_dir / f"{name}.md"
    dst.write_text((Path(job_dir) / "mom.md").read_text(encoding="utf-8"), encoding="utf-8")
    return dst


def cmd_new(args):
    d = MANUAL / args.name
    if (d / "meeting.txt").exists():
        raise SystemExit(f"Există deja: {d / 'meeting.txt'}")
    d.mkdir(parents=True, exist_ok=True)
    (d / "meeting.txt").write_text(TEMPLATE.format(name=args.name, date=Date.today().isoformat()), encoding="utf-8")
    print(f"Creat {d / 'meeting.txt'}\nCompletează-l, apoi: python -m LLM.manual run {args.name}")


def cmd_run(args):
    job_dir, report = run_one(args.target, args.date, args.window_tokens, args.tag)
    print("\n" + overview(job_dir))


def cmd_all(args):
    """Toate ședințele din LLM/test_meetings/*.json -> câte un MoM în LLM/test_moms/<nume>.md."""
    files = sorted(MEETINGS.glob("*.json"))
    if not files:
        raise SystemExit(f"Nicio ședință în {MEETINGS}: pune acolo fișierele .json")
    rows = []
    for i, f in enumerate(files, 1):
        print(f"\n━━ [{i}/{len(files)}] {f.name} ━━")
        try:
            job_dir, r = run_one(str(f), args.date, None, args.tag)
            rows.append((f.stem, r["n_cases"], r["n_decisions"], r["seconds"], len(r["errors"]),
                         export_mom_path(job_dir)))
        except SystemExit as e:  # un fișier stricat nu oprește restul
            rows.append((f.stem, "-", "-", "-", f"eșuat: {e}", ""))
    print(f"\n{'ședința':32s} {'cazuri':>6s} {'decizii':>7s} {'sec':>7s}  erori  MoM")
    for name, n_c, n_d, sec, err, path in rows:
        print(f"{name:32s} {n_c!s:>6s} {n_d!s:>7s} {sec!s:>7s}  {err!s:5s}  {path}")


def export_mom_path(job_dir):
    return MOMS / f"{Path(job_dir).name.removeprefix('manual-')}.md"


def run_one(target, date=None, window_tokens=None, tag=None):
    """Un JSON / meeting.txt / job -> model -> minutes.json, review.md, mom.html/.md + LLM/test_moms/<nume>.md."""
    from LLM.extract import extract

    job_dir, meeting, source = resolve(target)
    tag = tag or (f"w{window_tokens}" if window_tokens and not meeting else None)
    if tag:
        # variantă de experiment: folder separat, ca rulările să poată fi comparate; un job real nu se atinge
        if not source and not meeting:
            source = job_dir / "llm_input.json"
            job_dir = RUNS / job_dir.name
        job_dir = job_dir.with_name(f"{job_dir.name}-{re.sub(r'[^A-Za-z0-9_-]+', '_', tag)}")
    if source:
        raw = json.loads(source.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            date = date or raw.get("meeting_date") or raw.get("date")
        data = normalize_input(raw, job_dir.name, window_tokens)
        job_dir.mkdir(parents=True, exist_ok=True)
        (job_dir / "llm_input.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"[manual] {source} -> {job_dir / 'llm_input.json'} ({data.get('n_windows', '?')} ferestre)")
    elif meeting:
        settings, utts = parse_meeting(meeting)
        if not utts:
            raise SystemExit(f"{meeting} nu are nicio replică")
        date = date or settings.get("date")
        data = build_llm_input(job_dir.name, utts, int(window_tokens or settings.get("window_tokens", 3500)),
                               int(settings.get("overlap", 3)))
        job_dir.mkdir(parents=True, exist_ok=True)
        (job_dir / "llm_input.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"[manual] {meeting} -> {job_dir / 'llm_input.json'}: {data['n_turns']} replici, "
              f"{data['n_windows']} ferestre {[w['turn_ids'] for w in data['windows']]}")
    if not date:
        date = Date.today().isoformat()
        print(f"[manual] fără dată (--date, 'meeting_date' în JSON sau '# date:' în meeting.txt): folosesc {date}")
    report = extract(job_dir / "llm_input.json", date, job_dir / "minutes.json", job_dir / "llm_debug")
    review(job_dir, job_dir.name)
    mom.write(job_dir)
    export_mom(job_dir)
    return job_dir, report


def cmd_review(args):
    job_dir, _, _ = resolve(args.target)
    review(job_dir, job_dir.name)
    mom.write(job_dir)
    export_mom(job_dir)
    print(overview(job_dir))


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] not in ("new", "run", "review", "all", "-h", "--help"):
        argv.insert(0, "run")  # python -m LLM.manual sedinta.json  ==  ... run sedinta.json
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # ⚠ ✓ „” și în console vechi / redirect
    except (AttributeError, ValueError):
        pass
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("run", help="JSON / meeting.txt -> model -> punctele principale și termenele + review.md")
    p.add_argument("target", help="NUME din LLM/test_meetings (fără .json), fișier .json (llm_input.json sau "
                                  "transcript.json), job_id din AI/jobs/, sau NUME din LLM/manual_tests")
    p.add_argument("--date", help="YYYY-MM-DD (altfel 'meeting_date' din JSON / '# date:' din meeting.txt, apoi azi)")
    p.add_argument("--window-tokens", type=int, help="refă ferestrele cu această mărime (experiment; "
                                                     "rezultatele în LLM/runs/<nume>-w<N>/)")
    p.add_argument("--tag", help="sufix pentru folderul rezultatelor, ca variantele să nu se suprascrie "
                                 "(ex. --tag think, cu LLM_THINK_EXTRACT=1)")
    p.set_defaults(fn=cmd_run)
    p = sub.add_parser("all", help="toate ședințele din LLM/test_meetings/ -> MoM-uri în LLM/test_moms/")
    p.add_argument("--date", help="YYYY-MM-DD pentru ședințele fără 'meeting_date' (altfel azi)")
    p.add_argument("--tag", help="sufix pentru folderele din LLM/runs/ și numele MoM-urilor")
    p.set_defaults(fn=cmd_all)
    p = sub.add_parser("review", help="doar afișarea și review.md, din rezultatele existente (fără model)")
    p.add_argument("target")
    p.set_defaults(fn=cmd_review)
    p = sub.add_parser("new", help="creează LLM/manual_tests/NUME/meeting.txt (ședință scrisă de mână)")
    p.add_argument("name")
    p.set_defaults(fn=cmd_new)
    args = ap.parse_args(argv)
    if getattr(args, "date", None):
        try:
            Date.fromisoformat(args.date)
        except ValueError:
            sys.exit(f"--date trebuie să fie YYYY-MM-DD, nu {args.date!r}")
    args.fn(args)


if __name__ == "__main__":
    main()
