"""Acoperirea procesului-verbal față de o listă de fapte confirmate (per pacient).

    python -m evaluation.mom_checklist tests/reference/medpark_facts.json JOB [JOB ...]

Pentru fiecare pacient din listă: cazul din minutes.json al cărui case_key se potrivește cu `key`, apoi câte
dintre `items` (regex) apar în textul lui (constatări, rezumat, subiect, decizii, întrebări). Un fapt găsit doar
la alt pacient e numărat separat („la alt pacient”): e o eroare de atribuire, nu o omisiune.
"""
import argparse
import json
import re
from pathlib import Path

from pipeline.common import jobs_dir, load_config


def case_text(c):
    parts = [c["case_key"], c.get("topic", ""), c.get("discussion_summary", "")]
    parts += [f["fact"] for f in c.get("facts", [])]
    parts += [d["decision"] for d in c.get("decisions", [])]
    parts += list(c.get("open_questions", []))
    return " \n".join(parts)


def score(checklist, minutes):
    cases = minutes["cases"]
    rows, tot, found, wrong = [], 0, 0, 0
    for p in checklist["patients"]:
        mine = [c for c in cases if re.search(p["key"], c["case_key"], re.I)]
        text = " ".join(case_text(c) for c in mine)
        other = " ".join(case_text(c) for c in cases if c not in mine)
        ok, elsewhere, missing = [], [], []
        for name, rx in p["items"].items():
            if mine and re.search(rx, text, re.I):
                ok.append(name)
            elif re.search(rx, other, re.I):
                elsewhere.append(name)
            else:
                missing.append(name)
        rows.append((p["id"], [c["case_key"] for c in mine], ok, elsewhere, missing))
        tot += len(p["items"])
        found += len(ok)
        wrong += len(elsewhere)
    return rows, tot, found, wrong


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("checklist")
    ap.add_argument("jobs", nargs="+")
    ap.add_argument("-v", "--verbose", action="store_true", help="listează faptele lipsă / la alt pacient")
    args = ap.parse_args()
    checklist = json.loads(Path(args.checklist).read_text(encoding="utf-8"))
    for job in args.jobs:
        p = Path(job)  # job din AI/jobs sau direct un minutes.json (ex. LLM/runs/<nume>/minutes.json)
        p = p if p.suffix == ".json" else jobs_dir(load_config()) / job / "minutes.json"
        m = json.loads(p.read_text(encoding="utf-8"))
        rows, tot, found, wrong = score(checklist, m)
        exp = checklist.get("expected_cases")
        print(f"== {job}: {found}/{tot} fapte ({100 * found / tot:.0f}%), {wrong} la alt pacient, "
              f"{len(m['cases'])} cazuri" + (f" (așteptate {exp})" if exp else ""))
        for pid, keys, ok, elsewhere, missing in rows:
            print(f"   {pid:9s} {len(ok):2d}/{len(ok) + len(elsewhere) + len(missing):2d}  caz: {keys or '— negăsit'}")
            if args.verbose:
                if elsewhere:
                    print(f"             la alt pacient: {', '.join(elsewhere)}")
                if missing:
                    print(f"             lipsă: {', '.join(missing)}")


if __name__ == "__main__":
    main()
