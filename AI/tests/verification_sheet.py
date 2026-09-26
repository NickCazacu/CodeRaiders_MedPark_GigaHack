"""Fișă de verificare manuală a procesului-verbal: fiecare afirmație cu momentul din înregistrare.

    python -m tests.verification_sheet JOB_ID

Scrie jobs/<id>/verificare.md (date medicale: rămâne local, în jobs/). Pentru fiecare decizie, termen și
întrebare deschisă: momentul [mm:ss], replica transcrisă de ASR acolo și căsuțe de bifat după ascultare.
"""
import argparse
import json
import re

from pipeline.common import jobs_dir, load_config

TS = re.compile(r"\[(\d+):(\d{2})\]")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("job_id")
    args = ap.parse_args()

    d = jobs_dir(load_config()) / args.job_id
    m = json.loads((d / "minutes.json").read_text(encoding="utf-8"))
    turns = json.loads((d / "llm_input.json").read_text(encoding="utf-8"))["turns"]

    by_id = {t["id"]: t for t in turns}

    def line_at(ts, turn_id=None):
        """Replica citată: după turn_id (pus de modulul LLM), altfel replica care începe cel mai aproape de mm:ss
        (timpii [mm:ss] din procesul-verbal sunt începuturile replicilor)."""
        if turn_id in by_id:
            return by_id[turn_id]["line"]
        mm, ss = (int(x) for x in ts.split(":"))
        return min(turns, key=lambda x: abs(x["start"] - (mm * 60 + ss)))["line"]

    out = [f"# Fișă de verificare: {args.job_id}", "",
           "Pentru fiecare punct: ascultați înregistrarea la momentul indicat și bifați.",
           "`[?]` la finalul replicii = ASR-ul a fost nesigur acolo.", "",
           "## Rezumatul ședinței", "", m.get("meeting_summary", "—"), ""]
    for ts in sorted(set(TS.findall(m.get("meeting_summary", "")))):
        t = f"{ts[0]}:{ts[1]}"
        out.append(f"- [ ] corect  [ ] greșit  **[{t}]** → `{line_at(t)}`")
    n = 0
    for i, c in enumerate(m["cases"], 1):
        out += ["", f"## {i}. {c['case_key']}", "", f"**Subiect:** {c['topic']}", "",
                "- [ ] pacientul/cazul e identificat corect  [ ] greșit: ______", ""]
        for dcs in c["decisions"]:
            n += 1
            out += [f"### Decizia {n}: {dcs['decision']}",
                    f"- status în MoM: **{dcs['status']}**{' (înlocuită ulterior)' if dcs.get('superseded') else ''}",
                    f"- citat: „{dcs['quote']}” **[{dcs['timestamp']}]**",
                    f"- replica ASR: `{line_at(dcs['timestamp'], dcs.get('turn_id'))}`",
                    "- [ ] decizia chiar s-a luat  [ ] statusul e corect  [ ] pacientul e cel corect  [ ] inventată",
                    ""]
        eta = c.get("eta") or {}
        if eta.get("type") not in (None, "none"):
            out += [f"- termen: {eta['type']} „{eta.get('raw') or '—'}” — [ ] corect  [ ] greșit", ""]
        for q in c.get("open_questions", []):
            ts = TS.search(q)
            ref = f" → `{line_at(f'{ts[1]}:{ts[2]}')}`" if ts else ""
            out.append(f"- întrebare deschisă: {q}{ref} — [ ] corect  [ ] greșit")
    out += ["", "## Ce lipsește", "",
            "Ascultați toată ședința și notați deciziile sau pacienții discutați care NU apar mai sus:", "",
            "- ______", "- ______", "",
            "## Scor", "", f"- decizii corecte: ___ / {n}",
            "- decizii inventate: ___", "- decizii lipsă (din secțiunea de mai sus): ___", ""]
    path = d / "verificare.md"
    path.write_text("\n".join(out), encoding="utf-8")
    print(path)


if __name__ == "__main__":
    main()
