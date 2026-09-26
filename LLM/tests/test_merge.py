"""Reduce: suprapuneri, gruparea cazurilor, superseded, ETA.
    python -m LLM.tests.test_merge
"""
import sys

from LLM.merge import Merger, key_score
from LLM.tests.helpers import cfg, run

ETA_NONE = {"type": "none", "raw": None, "date": None, "condition": None, "needs_review": False}


def eta(t, raw):
    return {"type": t, "raw": raw, "date": None, "condition": None, "needs_review": False}


def d(turn_id, t, window, text=None, quote=None, replaces=False, status="aprobat", order=[0]):
    order[0] += 1
    return {"decision": text or f"{['ecografie', 'dializa', 'cateter', 'operatie', 'transfer', 'externare', 'analize'][(turn_id or 0) % 7]} {turn_id}", "status": status, "quote": quote or f"citat {turn_id}", "timestamp": "00:00",
            "replaces_previous": replaces, "turn_id": turn_id, "_t": t, "_window": window, "_order": order[0]}


def c(key, decisions=(), e=None, summary="", topic="t", questions=()):
    return {"case_key": key, "topic": topic, "discussion_summary": summary, "decisions": list(decisions),
            "eta": e or dict(ETA_NONE), "open_questions": list(questions)}


def test_key_score():
    assert key_score("Pacient 48, cardiologie", "pacient 48 cardiologie") == 100
    assert key_score("Pacient 48", "Pacient 84") == 0          # numere diferite => alt pacient
    assert key_score("Pacientă salon 5, nefrologie", "Pacienta salon 5") >= 90
    assert key_score("Pacient 17, chirurgie", "Pacienta 22, neurologie") == 0
    # același pat, alte numere (vârsta): același pacient; paturi diferite: pacienți diferiți
    assert key_score("Pacienta 45, patul 8, chirurgie", "Pacienta din patul 8, chirurgie") == 100
    assert key_score("Pacient patul 8", "Pacient patul 9") == 0


def test_overlap_duplicate_dropped_once():
    m = Merger(cfg())
    m.add_window(2, [11, 12, 13], 300, [c("Pacient 17, chirurgie", [d(19, 400, 2)])])
    # fereastra 3 începe cu replicile 18-20 ca context; modelul re-extrage decizia din replica 19
    m.add_window(3, [18, 19, 20], 420, [c("Pacient 17, chirurgie", [d(19, 400, 3), d(21, 440, 3)])])
    out = m.result()
    assert len(out) == 1 and [x["turn_id"] for x in out[0]["decisions"]] == [19, 21], out
    assert any(e["event"] == "drop_overlap_duplicate" and e["turn_id"] == 19 for e in m.events), m.events


def test_overlap_item_not_seen_before_is_kept():
    m = Merger(cfg())
    m.add_window(0, [], -1, [c("Pacient 17", [])])
    m.add_window(1, [7, 8, 9], 100, [c("Pacient 17", [d(8, 90, 1)])])  # fereastra 0 a ratat-o
    assert [x["turn_id"] for x in m.result()[0]["decisions"]] == [8]


def test_overlap_duplicate_without_turn_id_by_quote():
    m = Merger(cfg())
    m.add_window(0, [], -1, [c("Pacient 17", [d(19, 400, 0, quote="Operăm pacientul 17 astăzi")])])
    m.add_window(1, [18, 19], 410, [c("Pacient 17", [d(None, 400, 1, quote="operăm pacientul 17 astăzi.")])])
    assert len(m.result()[0]["decisions"]) == 1


def test_case_only_in_context_lines_dropped():
    m = Merger(cfg())
    m.add_window(0, [], -1, [c("Pacient 48, cardiologie", [d(3, 32, 0)])])
    # modelul a „văzut” cazul doar în liniile de context (timpi <= 00:26) și l-a numit altfel
    m.add_window(1, [1, 2], 26, [c("Pacientul din salonul 12", [], summary="Diureza scăzută [00:21] [00:26].")])
    assert [x["case_key"] for x in m.result()] == ["Pacient 48, cardiologie"]
    assert any(e["event"] == "drop_context_case" for e in m.events)


def test_supersede_flag_mode():
    m = Merger(cfg())
    m.add_window(1, [], -1, [c("Pacientă salon 5", [d(10, 100, 1, "Hemodializă mâine")],
                               eta("relative", "mâine dimineață"))])
    m.add_window(3, [], -1, [c("Pacientă salon 5", [d(25, 300, 3, "Anulăm hemodializa", replaces=True),
                                                     d(26, 310, 3, "Cateterul rămâne")], dict(ETA_NONE))])
    decs = m.result()[0]["decisions"]
    assert [x["superseded"] for x in decs] == [True, False, False], decs
    # ultimul ETA cu tip != none câștigă
    assert m.result()[0]["eta"]["raw"] == "mâine dimineață"


def test_supersede_all_mode():
    m = Merger(cfg(**{"merge.supersede": "all"}))
    m.add_window(0, [], -1, [c("P 5", [d(1, 10, 0), d(2, 20, 0), d(3, 30, 0)])])
    assert [x["superseded"] for x in m.result()[0]["decisions"]] == [True, True, False]


def test_latest_eta_wins():
    m = Merger(cfg())
    m.add_window(0, [], -1, [c("Pacient 48", [], eta("conditional", "după ECO"))])
    m.add_window(5, [], -1, [c("Pacient 48", [d(41, 900, 5)], eta("relative", "joi"))])
    assert m.result()[0]["eta"]["raw"] == "joi"


def test_decisions_ordered_by_time():
    m = Merger(cfg())
    m.add_window(0, [], -1, [c("P", [d(5, 50, 0, "b"), d(2, 20, 0, "a")])])
    assert [x["decision"] for x in m.result()[0]["decisions"]] == ["a", "b"]


def test_restated_decision_kept_once():
    m = Merger(cfg())
    # formulări reale din testul de 2 h (aceeași decizie, reluată)
    m.add_window(0, [], -1, [c("Patul 12", [d(3, 10, 0, "Continuarea tratamentului cu ceftriaxonă pentru încă trei "
                                                        "zile; repetarea radiografiei toracice vineri")])])
    m.add_window(4, [], -1, [c("Patul 12", [
        d(80, 600, 4, "Tratamentul cu ceftriaxonă se continuă pentru încă 3 zile și se repetă radiografia toracică "
                      "vineri"),                                                    # recapitulare: aceeași decizie
        d(90, 610, 4, "Tratamentul cu ceftriaxonă se continuă pentru încă 5 zile"),  # alt număr: altă decizie
        d(99, 620, 4, "Continuarea tratamentului cu ceftriaxonă pentru încă trei zile; repetarea radiografiei "
                      "toracice vineri", status="amânat")])])                       # alt status
    decs = m.result()[0]["decisions"]
    assert [x["turn_id"] for x in decs] == [80, 90, 99], decs   # rămâne ultima apariție
    assert any(e["event"] == "drop_restated_decision" for e in m.events)


def test_ambiguous_asks_llm():
    asked = []

    def same(a, b):
        asked.append((a["case_key"], b["case_key"]))
        return True

    m = Merger(cfg(), same_case=same)
    m.add_window(0, [], -1, [c("Pacienta cu pneumonie, terapie", [d(1, 1, 0)])])
    m.add_window(1, [], -1, [c("Pneumonie severă, pacienta de la terapie intensivă", [d(9, 90, 1)])])
    assert asked and len(m.result()) == 1, (asked, m.events)


def test_ambiguous_llm_failure_keeps_separate():
    m = Merger(cfg(), same_case=lambda a, b: None)
    m.add_window(0, [], -1, [c("Pacienta cu pneumonie, terapie", [])])
    m.add_window(1, [], -1, [c("Pneumonie severă, pacienta de la terapie intensivă", [])])
    assert len(m.result()) == 2


def test_known_cases_header():
    m = Merger(cfg())
    m.add_window(0, [], -1, [c("Pacient 48", []), c("Pacient 17", [d(19, 400, 0, "Apendicectomie azi")])])
    assert m.known_cases(40) == ["Pacient 48 — discutat, fără decizie",
                                 "Pacient 17 — aprobat: Apendicectomie azi"]


def test_open_questions_deduped():
    m = Merger(cfg())
    m.add_window(0, [], -1, [c("P", questions=["Ecografia: nu s-a stabilit [00:41]"])])
    m.add_window(1, [], -1, [c("P", questions=["Ecografia: nu s-a stabilit [00:41].", "Alta"])])
    assert m.result()[0]["open_questions"] == ["Ecografia: nu s-a stabilit [00:41]", "Alta"]


if __name__ == "__main__":
    sys.exit(run(globals()))
