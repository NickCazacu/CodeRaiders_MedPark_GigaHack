"""Citat -> turn_id și curățarea răspunsului modelului.
    python -m LLM.tests.test_quotes
"""
import sys

from LLM import sanitize
from LLM.loader import load
from LLM.quotes import resolve
from LLM.tests.helpers import cfg, fixture, run

M = load(fixture("llm_input.example.json"))
W1 = [M.turns[i] for i in M.windows[1].turn_ids]
Q = cfg()["quotes"]


def test_exact_quote_and_timestamp():
    r = resolve("Давайте повторим креатинин вечером и решим по гемодиализу.", "00:32", W1, Q)
    assert r["turn_id"] == 3 and r["ts_match"] and r["exact"], r


def test_partial_quote():
    r = resolve("повторим креатинин вечером", "00:32", W1, Q)
    assert r["turn_id"] == 3 and r["exact"], r


def test_wrong_timestamp_text_still_wins():
    r = resolve("Diureza e scăzută, 400 ml pe noapte, creatinina 240.", "00:40", W1, Q)
    assert r["turn_id"] == 2 and not r["ts_match"] and r["method"] == "text", r


def test_small_differences():
    # sedilă în loc de virgulă, punctuație și majuscule diferite
    r = resolve("diureza e scazută 400 ml pe noapte, creatinina 240", "00:26", W1, Q)
    assert r["turn_id"] == 2, r


def test_invented_quote_unresolved():
    r = resolve("Facem hemodializă azi la ora 14.", "00:32", W1, Q)
    assert r["turn_id"] is None and r["method"] == "no_match", r


def test_short_quote_needs_timestamp():
    assert resolve("De acord.", "00:41", W1, Q)["turn_id"] == 4
    assert resolve("De acord.", "", W1, Q)["turn_id"] is None


def test_sanitize_cleans_quote_and_enums():
    cases, summary, fixes = sanitize.extract({"summary": " s ", "cases": [{
        "case_key": "Pacient 48", "topic": "t", "discussion_summary": "x [00:32]",
        "decisions": [{"quote": "[00:32] SPEAKER_02: Давайте повторим креатинин вечером [?]", "timestamp": "0:32",
                       "decision": "Repetarea creatininei", "status": "Aprobat", "replaces_previous": False},
                      {"quote": "", "timestamp": "", "decision": "", "status": "aprobat"}],
        "eta": {"type": "relativ", "raw": "вечером", "condition": "ceva"},
        "open_questions": ["", "Ecografia [00:41]"]}]})
    c = cases[0]
    d = c["decisions"]
    assert len(d) == 1 and d[0]["quote"] == "Давайте повторим креатинин вечером" and d[0]["timestamp"] == "00:32", d
    assert d[0]["status"] == "aprobat", d
    assert c["eta"] == {"type": "relative", "raw": "вечером", "date": None, "condition": None,
                        "needs_review": False}, c["eta"]
    assert c["open_questions"] == ["Ecografia [00:41]"] and summary == "s" and fixes


def test_sanitize_eta_none_and_bad_status():
    cases, _, _ = sanitize.extract({"cases": [{
        "case_key": "", "topic": "Caz fără cheie", "discussion_summary": "", "open_questions": [],
        "decisions": [{"quote": "q", "timestamp": "01:02:03", "decision": "d", "status": "poate"}],
        "eta": {"type": "none", "raw": "mâine", "condition": ""}}]})
    c = cases[0]
    assert c["case_key"] == "Caz fără cheie" and c["decisions"][0]["status"] == "în discuție"
    assert c["decisions"][0]["timestamp"] == "62:03"  # h:mm:ss -> mm:ss ca în transcriere
    assert c["eta"]["raw"] is None and c["eta"]["type"] == "none"


if __name__ == "__main__":
    sys.exit(run(globals()))
