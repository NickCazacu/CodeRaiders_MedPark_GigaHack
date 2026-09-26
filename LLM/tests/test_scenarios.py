"""Scenariile cerute, rulate cu modelul real (Ollama local, qwen3:8b). Sărite dacă serverul nu răspunde.
    python -m LLM.tests.test_scenarios [nume_test ...]

Verifică strict ce contează: fiecare decizie la pacientul corect (replicile fiecărui pacient sunt
cunoscute din fixture), tipul exact de ETA, eta.raw copiat din transcriere, suprapuneri, superseded, [?].
Ieșirile rămân în directorul temporar afișat, pentru inspecție.
"""
import json
import sys
import urllib.request

from LLM import extract as ex
from LLM.attribution import ids
from LLM.config import load_config
from LLM.loader import load
from LLM.quotes import norm
from LLM.tests.helpers import check_minutes, fixture, run, tmpdir

OUT = tmpdir()
_cache = {}

# pacient ("P", număr) / salon ("W", număr) -> replicile în care se discută (din tests/fixtures/build.py)
PATIENTS = {
    "short_two_cases.json": {("W", "3"): range(0, 7), ("P", "21"): range(6, 12)},
    "eta_types.json": {("P", "3"): range(0, 2), ("P", "9"): range(2, 4), ("P", "11"): range(4, 6),
                       ("P", "14"): range(6, 8), ("P", "20"): range(8, 10), ("P", "25"): range(10, 12),
                       ("P", "30"): range(12, 14)},
    "long_meeting.json": {("P", "48"): [*range(0, 8), *range(37, 45)], ("W", "5"): [*range(8, 13), *range(23, 27)],
                          ("P", "17"): range(16, 23), ("P", "22"): range(27, 32)},
    "uncertain_decision.json": {("P", "33"): range(0, 3), ("P", "40"): range(3, 4)},
    "llm_input.example.json": {("P", "48"): range(0, 7)},
}
ETA = {("P", "3"): "absolute", ("P", "9"): "relative", ("P", "11"): "duration", ("P", "14"): "conditional",
       ("P", "20"): "vague", ("P", "25"): "recurring", ("P", "30"): "none"}


def ollama_up():
    cfg = load_config()
    try:
        op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with op.open(cfg["ollama"]["url"].rstrip("/") + "/api/tags", timeout=5) as r:
            names = {m["name"] for m in json.loads(r.read())["models"]}
        return cfg["ollama"]["model"] in names
    except OSError:
        return False


def minutes(name):
    if name not in _cache:
        d = OUT / name.removesuffix(".json")
        report = ex.extract(fixture(name), "2026-09-21", d / "minutes.json", d / "llm_debug")
        m = json.loads((d / "minutes.json").read_text(encoding="utf-8"))
        problems = check_minutes(m)
        assert not problems, problems
        assert not report["errors"], report["errors"]
        _cache[name] = (m, report)
    return _cache[name]


def labels(case):
    p, w = ids(case["case_key"])
    return {("P", x) for x in p} | {("W", x) for x in w}


def case_of(m, label):
    hits = [c for c in m["cases"] if label in labels(c)]
    assert len(hits) == 1, f"{label}: {len(hits)} cazuri: {[c['case_key'] for c in m['cases']]}"
    return hits[0]


def decisions(m):
    return [d for c in m["cases"] for d in c["decisions"]]


def attribution_problems(name):
    m, _ = minutes(name)
    spec, out = PATIENTS[name], []
    for c in m["cases"]:
        mine = [lab for lab in labels(c) if lab in spec]
        allowed = {t for lab in mine for t in spec[lab]}
        for d in c["decisions"]:
            if mine and d["turn_id"] is not None and d["turn_id"] not in allowed:
                out.append(f"{c['case_key']!r}: replica {d['turn_id']} ({d['decision']}) e a altui pacient")
    return out


def eta_raw_problems(name):
    m, _ = minutes(name)
    text = norm(" ".join(t.text for t in load(fixture(name)).turns.values()))
    return [f"{c['case_key']!r}: eta.raw {c['eta']['raw']!r} nu e în transcriere"
            for c in m["cases"] if c["eta"]["raw"] and norm(c["eta"]["raw"]) not in text]


def test_1_short_two_cases():
    m, report = minutes("short_two_cases.json")
    assert report["path"] == "single"
    w3, p21 = case_of(m, ("W", "3")), case_of(m, ("P", "21"))
    assert w3["decisions"] and p21["decisions"], m["cases"]
    assert p21["eta"]["type"] == "absolute" and "2 octombrie" in (p21["eta"]["raw"] or ""), p21["eta"]
    assert report["quotes_unresolved"] == 0, report["quotes_unresolved"]


def test_2_no_decisions():
    m, _ = minutes("no_decisions.json")
    assert decisions(m) == [], decisions(m)


def test_3_long_case_discussed_early_decided_late():
    m, report = minutes("long_meeting.json")
    assert report["n_windows"] >= 6 and report["path"] == "map-reduce"
    p48 = case_of(m, ("P", "48"))
    active = [d for d in p48["decisions"] if not d["superseded"]]
    assert active and active[-1]["turn_id"] is not None and active[-1]["turn_id"] >= 37, p48["decisions"]
    assert p48["eta"]["type"] == "relative" and "joi" in (p48["eta"]["raw"] or ""), p48["eta"]


def test_4_overlap_decision_extracted_once():
    m, _ = minutes("long_meeting.json")
    ids_ = [d["turn_id"] for d in decisions(m) if d["turn_id"] is not None]
    assert len(ids_) == len(set(ids_)), sorted(ids_)
    assert case_of(m, ("P", "17"))["decisions"]


def test_5_changed_decision_superseded():
    m, _ = minutes("long_meeting.json")
    s5 = case_of(m, ("W", "5"))
    decs = s5["decisions"]
    early = [d for d in decs if d["turn_id"] in (10, 11)]
    late = [d for d in decs if d["turn_id"] in range(23, 27)]
    assert early and late, decs
    assert all(d["superseded"] for d in early) and not decs[-1]["superseded"], decs


def test_6_eta_types():
    m, _ = minutes("eta_types.json")
    got = {lab: case_of(m, lab)["eta"] for lab in ETA}
    wrong = {f"pacient {lab[1]}": f"{e['type']} ({e['raw']!r}), așteptat {ETA[lab]}"
             for lab, e in got.items() if e["type"] != ETA[lab]}
    assert not wrong, wrong
    assert all(e["raw"] for lab, e in got.items() if ETA[lab] != "none"), got
    cond = got[("P", "14")]
    assert cond["condition"] and "troponin" in norm(cond["raw"]), cond


def test_7_decision_on_uncertain_turn():
    m, _ = minutes("uncertain_decision.json")
    assert 2 in {d["turn_id"] for d in case_of(m, ("P", "33"))["decisions"]}, m["cases"]


def test_8_unk_speakers():
    for name in ("long_meeting.json", "uncertain_decision.json", "no_decisions.json"):
        m, _ = minutes(name)
        assert not check_minutes(m)   # fără UNK/SPEAKER_NN/[mm:ss] în citate sau rezumate


def test_9_example_from_docs():
    m, _ = minutes("llm_input.example.json")
    p48 = case_of(m, ("P", "48"))
    assert [d["turn_id"] for d in p48["decisions"]] == [3] and p48["decisions"][0]["timestamp"] == "00:32", p48
    assert p48["eta"]["type"] == "relative" and p48["eta"]["raw"] == "вечером", p48["eta"]


def test_10_decisions_on_the_right_patient():
    problems = [p for name in PATIENTS for p in attribution_problems(name)]
    assert not problems, problems


def test_11_eta_raw_verbatim():
    problems = [p for name in PATIENTS for p in eta_raw_problems(name)]
    assert not problems, problems


if __name__ == "__main__":
    if not ollama_up():
        print("SKIP: Ollama local sau modelul nu sunt disponibile")
        sys.exit(0)
    only = sys.argv[1:]
    ns = {k: v for k, v in globals().items() if not only or not k.startswith("test_") or any(o in k for o in only)}
    print(f"ieșiri: {OUT}")
    failed = run(ns)
    sys.exit(1 if failed else 0)
