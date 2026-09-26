"""Procesul-verbal (MoM) pentru revizuire: minutes.json -> mom.html + mom.md, fără model.

    python -m LLM.mom AI/jobs/<job_id>         # sau cale spre minutes.json / llm_input.json

Doar redă ce a extras modelul (nimic nou): rezumat, ordinea de zi, discuții și decizii pe
puncte, sinteza deciziilor și termenelor, întrebări deschise și notele pentru revizuire
(replici nesigure, citate negăsite, termene fără expresie exactă). Fiecare afirmație păstrează
timpul [mm:ss] din înregistrare. Termenele sunt expresiile din transcriere; datele concrete le
calculează validarea.
"""
import argparse
import html
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from LLM.config import ROOT
from LLM.loader import load

ETA_LABEL = {"absolute": "dată fixă", "relative": "relativ", "duration": "durată", "conditional": "condiționat",
             "vague": "vag", "recurring": "periodic", "none": ""}
STATUS_CLASS = {"aprobat": "ok", "respins": "no", "amânat": "wait", "necesită investigații suplimentare": "wait",
                "în discuție": "open"}
# ordinea și etichetele constatărilor clinice (schemas.FACT_CATEGORIES)
FACT_LABEL = {"diagnostic": "Diagnostic", "istoric": "Istoric", "analize": "Analize", "imagistică": "Imagistică",
              "microbiologie": "Microbiologie", "tratament": "Tratament", "procedură": "Proceduri",
              "monitorizare": "Monitorizare", "evoluție": "Evoluție"}


def facts_by_category(case):
    """[(etichetă, [constatări])] în ordinea clinică; minutes.json mai vechi nu au `facts`."""
    facts = case.get("facts") or []
    return [(label, [f for f in facts if f["category"] == cat]) for cat, label in FACT_LABEL.items()
            if any(f["category"] == cat for f in facts)]


def read_json(path, default=None):
    p = Path(path)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else default


def fmt_duration(seconds):
    if not seconds:
        return "—"
    m = int(round(seconds / 60))
    return f"{m // 60} h {m % 60:02d} min" if m >= 60 else f"{m} min"


def eta_phrase(e):
    """Termenul în română (tip + condiție). Expresia verbatim (poate fi în rusă/engleză) nu intră în
    textul procesului-verbal, ci în adnotarea de revizuire (eta_source)."""
    if e["type"] == "none":
        return ""
    return ETA_LABEL[e["type"]] + (f", condiție: {e['condition']}" if e["condition"] else "")


def eta_source(e):
    if e["type"] == "none":
        return ""
    return f"„{e['raw']}”" if e["raw"] else "(fără expresie exactă în transcriere)"


def collect(job_dir):
    """Datele pentru MoM, cu semnalele de revizuire pe fiecare decizie."""
    job_dir = Path(job_dir)
    minutes = read_json(job_dir / "minutes.json")
    if minutes is None:
        raise SystemExit(f"Lipsește {job_dir / 'minutes.json'}")
    mt = load(job_dir / "llm_input.json") if (job_dir / "llm_input.json").exists() else None
    run = read_json(job_dir / "llm_debug" / "run.json", {})
    checks = read_json(job_dir / "llm_debug" / "checks.json", [])
    notes = []
    for i, c in enumerate(minutes["cases"], 1):
        active = [d for d in c["decisions"] if not d["superseded"]]
        for d in c["decisions"]:
            d["_flags"] = []
            t = mt.turns.get(d["turn_id"]) if mt and d["turn_id"] is not None else None
            if d["turn_id"] is None:
                d["_flags"].append("citatul nu a fost găsit în transcriere")
            elif t is not None and t.uncertain:
                d["_flags"].append("bazată pe o replică transcrisă nesigur")
            last = bool(active) and d is active[-1]
            d["_eta"] = eta_phrase(c["eta"]) if last else ""
            d["_eta_src"] = eta_source(c["eta"]) if last else ""
            for f in d["_flags"]:
                notes.append(f"Punctul {i} ({c['case_key']}), decizia [{d['timestamp']}]: {f}.")
        if c["eta"]["type"] != "none" and not c["eta"]["raw"]:
            notes.append(f"Punctul {i} ({c['case_key']}): termenul ({ETA_LABEL[c['eta']['type']]}) nu are "
                         "expresia exactă din transcriere.")
    moved = sum(e["event"] == "decision_moved" for e in checks)
    if moved:
        noun = "decizie a fost mutată" if moved == 1 else "decizii au fost mutate"
        notes.append(f"{moved} {noun} automat la pacientul discutat în acel moment (vezi llm_debug/checks.json).")
    for e in run.get("errors", []):
        notes.append(f"Eroare la generare: {e}")
    return {
        "title": "Proces-verbal al ședinței",
        "job_id": mt.job_id if mt else job_dir.name,
        "date": run.get("date", "—"),
        "duration": fmt_duration(mt.audio_duration_s if mt else None),
        "generated": datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M"),
        "model": run.get("model", "—"),
        "summary": minutes["meeting_summary"],
        "cases": minutes["cases"],
        "notes": notes,
    }


# ---------- Markdown ----------
def render_md(m):
    out = [f"# {m['title']}", "",
           f"**Data ședinței:** {m['date']} · **Durata înregistrării:** {m['duration']} · "
           f"**Generat:** {m['generated']} ({m['model']}, automat, de verificat)", "",
           "## 1. Rezumat", "", m["summary"] or "_—_", "", "## 2. Ordinea de zi", ""]
    out += [f"{i}. {c['case_key']}: {c['topic']}" for i, c in enumerate(m["cases"], 1)] or ["_Niciun punct._"]
    out += ["", "## 3. Discuții și decizii", ""]
    for i, c in enumerate(m["cases"], 1):
        out += [f"### 3.{i}. {c['case_key']}", "", f"**Subiect:** {c['topic']}", "",
                f"**Discuție:** {c['discussion_summary'] or '—'}", ""]
        groups = facts_by_category(c)
        if groups:
            out.append("**Constatări clinice:**")
            for label, facts in groups:
                out.append(f"- _{label}:_ " + "; ".join(f"{f['fact']} [{f['timestamp']}]" for f in facts))
            out.append("")
        if c["decisions"]:
            out.append("**Decizii:**")
            for d in c["decisions"]:
                text = f"~~{d['decision']}~~ (modificată ulterior)" if d["superseded"] else d["decision"]
                eta = f" — termen: {d['_eta']}" if d["_eta"] else ""
                flag = " ⚠" if d["_flags"] else ""
                out.append(f"- **{d['status']}**: {text}{eta} [{d['timestamp']}]{flag}")
                src = f" · termen spus: {d['_eta_src']}" if d["_eta_src"] else ""
                out.append(f"  - _spus în înregistrare: „{d['quote']}”{src}_")
        else:
            out.append("**Decizii:** nicio decizie.")
        if c["open_questions"]:
            out += ["", "**Rămâne de clarificat:**", *[f"- {q}" for q in c["open_questions"]]]
        out.append("")
    out += ["## 4. Sinteza deciziilor și termenelor", "",
            "| Punct | Decizie | Status | Termen | Sursa |", "|---|---|---|---|---|"]
    rows = [(i, c, d) for i, c in enumerate(m["cases"], 1) for d in c["decisions"] if not d["superseded"]]
    out += [f"| {i}. {c['case_key']} | {d['decision']} | {d['status']} | {d['_eta'] or '—'} | [{d['timestamp']}] |"
            for i, c, d in rows] or ["| — | _nicio decizie_ | | | |"]
    questions = [(i, c, q) for i, c in enumerate(m["cases"], 1) for q in c["open_questions"]]
    out += ["", "## 5. Întrebări deschise", ""]
    out += [f"- {i}. {c['case_key']}: {q}" for i, c, q in questions] or ["_Niciuna._"]
    out += ["", "## 6. Note pentru revizuire", "",
            "- Termenele sunt expresiile din transcriere; datele concrete se calculează la validare.",
            "- Timpii [mm:ss] trimit la momentul din înregistrare.",
            "- Textele „spus în înregistrare” sunt citate exacte, în limba vorbită; restul e în română.",
            *[f"- ⚠ {n}" for n in m["notes"]], ""]
    return "\n".join(out)


# ---------- HTML ----------
CSS = """
:root{--bg:#fbfaf7;--card:#fff;--ink:#1d1d1b;--muted:#6b6a66;--line:#e4e1da;--accent:#1f5f8b;
--ok:#1e7a45;--no:#a33a2c;--wait:#9a6400;--open:#5a5a8a;--warn-bg:#fff6e0;--warn:#8a5a00}
@media (prefers-color-scheme:dark){:root{--bg:#161614;--card:#1f1f1c;--ink:#ecebe6;--muted:#a3a19a;
--line:#34332e;--accent:#7fb6dd;--ok:#6fcf97;--no:#f08a7a;--wait:#e8b45a;--open:#aaaaee;--warn-bg:#3a2f14;--warn:#f0c46a}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);
font:15px/1.55 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
main{max-width:920px;margin:0 auto;padding:32px 16px 64px}
h1{font-size:26px;margin:0 0 6px}h2{font-size:18px;margin:36px 0 12px;padding-bottom:6px;border-bottom:1px solid var(--line)}
h3{font-size:16px;margin:0 0 8px}.meta{color:var(--muted);font-size:13px}
.draft{display:inline-block;margin-top:10px;padding:4px 10px;border-radius:4px;background:var(--warn-bg);color:var(--warn);font-size:13px}
.card{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:16px 18px;margin:12px 0}
.ts{font-variant-numeric:tabular-nums;color:var(--accent);font-size:13px;white-space:nowrap}
.quote{color:var(--muted);font-style:italic;font-size:13px;margin:2px 0 0}
.status{font-size:12px;font-weight:600;padding:1px 7px;border-radius:10px;border:1px solid currentColor;white-space:nowrap}
.ok{color:var(--ok)}.no{color:var(--no)}.wait{color:var(--wait)}.open{color:var(--open)}
.sup{text-decoration:line-through;color:var(--muted)}.flag{color:var(--warn);font-size:13px}
ul.dec{list-style:none;padding:0;margin:8px 0 0}ul.dec li{padding:8px 0;border-top:1px dashed var(--line)}
.eta{font-size:13px;margin-top:2px}.label{color:var(--muted);font-size:13px}
table{width:100%;border-collapse:collapse;font-size:14px}th,td{text-align:left;vertical-align:top;padding:8px;border-bottom:1px solid var(--line)}
th{color:var(--muted);font-weight:600;font-size:12px;text-transform:uppercase;letter-spacing:.03em}
.table-wrap{overflow-x:auto}ol.agenda li{margin:4px 0}.notes li{margin:4px 0}
table.facts{margin:4px 0 10px}table.facts th{width:130px;text-transform:none;font-size:13px;letter-spacing:0}
@media print{body{background:#fff;color:#000}.card{border-color:#ccc;break-inside:avoid}.draft{border:1px solid #c90}}
"""


def render_html(m):
    e = html.escape

    def status(s):
        return f'<span class="status {STATUS_CLASS.get(s, "open")}">{e(s)}</span>'

    agenda = "".join(f"<li>{e(c['case_key'])}: {e(c['topic'])}</li>" for c in m["cases"]) or "<li>Niciun punct.</li>"
    cards = []
    for i, c in enumerate(m["cases"], 1):
        decs = []
        for d in c["decisions"]:
            text = (f'<span class="sup">{e(d["decision"])}</span> <span class="label">(modificată ulterior)</span>'
                    if d["superseded"] else e(d["decision"]))
            eta = f'<div class="eta"><span class="label">Termen:</span> {e(d["_eta"])}</div>' if d["_eta"] else ""
            flags = "".join(f'<div class="flag">⚠ {e(f)}</div>' for f in d["_flags"])
            src = f' · termen spus: {e(d["_eta_src"])}' if d["_eta_src"] else ""
            decs.append(f'<li>{status(d["status"])} {text} <span class="ts">[{e(d["timestamp"])}]</span>{eta}'
                        f'<div class="quote">spus în înregistrare: „{e(d["quote"])}”{src}</div>{flags}</li>')
        decisions = f'<ul class="dec">{"".join(decs)}</ul>' if decs else '<p class="label">Nicio decizie.</p>'
        qs = "".join(f"<li>{e(q)}</li>" for q in c["open_questions"])
        questions = f'<p class="label">Rămâne de clarificat:</p><ul>{qs}</ul>' if qs else ""
        rows_f = "".join(f'<tr><th>{e(label)}</th><td>'
                         + "<br>".join(f'{e(f["fact"])} <span class="ts">[{e(f["timestamp"])}]</span>' for f in facts)
                         + "</td></tr>" for label, facts in facts_by_category(c))
        facts_html = (f'<p class="label">Constatări clinice:</p><table class="facts">{rows_f}</table>'
                      if rows_f else "")
        cards.append(f'<section class="card"><h3>{i}. {e(c["case_key"])}</h3>'
                     f'<p><span class="label">Subiect:</span> {e(c["topic"])}</p>'
                     f'<p><span class="label">Discuție:</span> {e(c["discussion_summary"] or "—")}</p>'
                     f'{facts_html}{decisions}{questions}</section>')
    rows = "".join(f'<tr><td>{i}. {e(c["case_key"])}</td><td>{e(d["decision"])}</td><td>{status(d["status"])}</td>'
                   f'<td>{e(d["_eta"]) or "—"}</td><td class="ts">[{e(d["timestamp"])}]</td></tr>'
                   for i, c in enumerate(m["cases"], 1) for d in c["decisions"] if not d["superseded"])
    table = (f'<div class="table-wrap"><table><thead><tr><th>Punct</th><th>Decizie</th><th>Status</th><th>Termen</th>'
             f'<th>Sursa</th></tr></thead><tbody>{rows}</tbody></table></div>') if rows else "<p>Nicio decizie.</p>"
    qs = "".join(f"<li>{i}. {e(c['case_key'])}: {e(q)}</li>"
                 for i, c in enumerate(m["cases"], 1) for q in c["open_questions"]) or "<li>Niciuna.</li>"
    notes = "".join(f"<li>⚠ {e(n)}</li>" for n in m["notes"])
    return f"""<!doctype html>
<html lang="ro"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Proces-verbal {e(m['date'])}</title><style>{CSS}</style></head>
<body><main>
<h1>{e(m['title'])}</h1>
<div class="meta">Data ședinței: <b>{e(m['date'])}</b> · Durata înregistrării: {e(m['duration'])} ·
Generat: {e(m['generated'])} · {e(m['model'])}</div>
<div class="draft">Generat automat din înregistrare — de verificat înainte de trimitere</div>
<h2>1. Rezumat</h2><p>{e(m['summary'] or '—')}</p>
<h2>2. Ordinea de zi</h2><ol class="agenda">{agenda}</ol>
<h2>3. Discuții și decizii</h2>{''.join(cards) or '<p>Niciun punct.</p>'}
<h2>4. Sinteza deciziilor și termenelor</h2>{table}
<h2>5. Întrebări deschise</h2><ul>{qs}</ul>
<h2>6. Note pentru revizuire</h2><ul class="notes">
<li>Termenele sunt expresiile din transcriere; datele concrete se calculează la validare.</li>
<li>Timpii [mm:ss] trimit la momentul din înregistrare.</li>
<li>Textele „spus în înregistrare” sunt citate exacte, în limba vorbită; restul e în română.</li>{notes}</ul>
<p class="meta">Job: {e(m['job_id'])}</p>
</main></body></html>
"""


def write(job_dir):
    """Scrie mom.html și mom.md în job_dir; întoarce calea spre mom.html."""
    job_dir = Path(job_dir)
    m = collect(job_dir)
    (job_dir / "mom.md").write_text(render_md(m), encoding="utf-8")
    path = job_dir / "mom.html"
    path.write_text(render_html(m), encoding="utf-8")
    return path


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("target", help="AI/jobs/<job_id>, sau cale spre minutes.json / llm_input.json din acel job")
    p = Path(ap.parse_args().target)
    job_dir = p.parent if p.suffix == ".json" else p
    if not (job_dir / "minutes.json").exists() and (ROOT / "jobs" / p.name / "minutes.json").exists():
        job_dir = ROOT / "jobs" / p.name
    print(write(job_dir))


if __name__ == "__main__":
    sys.exit(main())
