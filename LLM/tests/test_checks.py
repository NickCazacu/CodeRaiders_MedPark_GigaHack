"""Verificările din cod: atribuirea deciziilor, eta.raw verbatim, supersede, client Ollama, schema.
    python -m LLM.tests.test_checks
"""
import sys

from LLM.attribution import check_eta_raw, fix_attribution, ids, relation
from LLM.config import load_config
from LLM.loader import load
from LLM.merge import Merger
from LLM.ollama import OllamaClient
from LLM.schemas import extract_schema
from LLM.tests.helpers import cfg, fixture, run

SHORT = load(fixture("short_two_cases.json"))
LONG = load(fixture("long_meeting.json"))


def eta(t="none", raw=None):
    return {"type": t, "raw": raw, "date": None, "condition": None, "needs_review": False}


def d(turn_id, text="d"):
    return {"decision": text, "status": "aprobat", "quote": "q", "timestamp": "00:00", "replaces_previous": False,
            "turn_id": turn_id, "_t": 0, "_window": 0, "_order": turn_id}


def c(key, decisions, e=None):
    return {"case_key": key, "topic": "t", "discussion_summary": "", "decisions": decisions, "eta": e or eta(),
            "open_questions": []}


def test_ids_and_relation():
    assert ids("Pacientul 48, cardiologie") == ({"48"}, set())
    assert ids("Pacienta din salonul 5, nefrologie") == (set(), {"5"})
    assert ids("Пациент 22 из палаты 3") == ({"22"}, {"3"})
    assert relation(ids("Pacient 48"), ids("pacientul 48 din salonul 2")) == "match"
    assert relation(ids("Pacient 48"), ids("Pacientul 22")) == "mismatch"
    assert relation(ids("Pacienta salon 3"), ids("pacientul 21")) == "mismatch"   # tipuri diferite, numere diferite
    assert relation(ids("Pacient, cardiologie"), ids("pacientul 21")) == "unknown"


def test_decision_moved_to_patient_being_discussed():
    # turn 10 („Da, aprobat, pe 2 octombrie.”) vine după „Al doilea caz: pacientul 21”
    cases = [c("Pacienta salon 3, neurologie", [d(4), d(10)], eta("absolute", "pe 2 octombrie")),
             c("Pacient 21, chirurgie", [d(9)])]
    ev = fix_attribution(cases, SHORT.turns)
    assert [x["turn_id"] for x in cases[0]["decisions"]] == [4], cases
    assert [x["turn_id"] for x in cases[1]["decisions"]] == [9, 10], cases
    assert cases[0]["eta"]["type"] == "none" and cases[1]["eta"]["raw"] == "pe 2 octombrie", cases   # ETA mutat
    assert {e["event"] for e in ev} == {"decision_moved", "eta_moved"}, ev


def test_same_turn_in_two_cases_kept_once():
    cases = [c("Pacienta salon 3, neurologie", [d(10)]), c("Pacient 21, chirurgie", [d(10)])]
    fix_attribution(cases, SHORT.turns)
    assert [len(x["decisions"]) for x in cases] == [0, 1], cases


def test_moved_to_known_case_from_earlier_window():
    # fereastra 5 a lungii: deciziile pacientului 22 (replicile 29, 31) puse la pacientul 48
    cases = [c("Pacient 48, cardiologie", [d(29), d(31)])]
    ev = fix_attribution(cases, LONG.turns, known_keys=["Pacient 48, cardiologie", "Pacient 22, neurologie"])
    # cazul 22 nu era în răspunsul ferestrei: e creat cu cheia cunoscută, merge-ul îl unește cu cel vechi
    assert [x["case_key"] for x in cases] == ["Pacient 48, cardiologie", "Pacient 22, neurologie"], cases
    assert cases[0]["decisions"] == [] and [x["turn_id"] for x in cases[1]["decisions"]] == [29, 31]
    assert sum(e["event"] == "decision_moved" for e in ev) == 2


def test_mismatch_without_target_is_kept_and_logged():
    cases = [c("Pacient 48, cardiologie", [d(29)])]
    ev = fix_attribution(cases, LONG.turns)
    assert cases[0]["decisions"] and ev[0]["event"] == "attribution_mismatch_kept", ev


def test_correct_attribution_untouched():
    cases = [c("Pacient 17, chirurgie", [d(19)]), c("Pacienta salon 5", [d(10), d(25)])]
    assert fix_attribution(cases, LONG.turns) == [] and len(cases[1]["decisions"]) == 2


def test_eta_raw_must_be_in_transcript():
    turns = [SHORT.turns[i] for i in SHORT.windows[0].turn_ids]
    cases = [c("A", [], eta("absolute", "pe 2 octombrie")), c("B", [], eta("vague", "когда possible"))]
    ev = check_eta_raw(cases, turns)
    assert cases[0]["eta"]["raw"] == "pe 2 octombrie" and cases[1]["eta"]["raw"] is None, cases
    assert cases[1]["eta"]["type"] == "vague" and len(ev) == 1


def test_eta_raw_paraphrase_realigned_translation_dropped():
    eta_fx = load(fixture("eta_types.json"))
    turns = [eta_fx.turns[i] for i in eta_fx.windows[0].turn_ids]
    cases = [c("P 14", [d(7)], eta("conditional", "dacă crește troponina a doua")),
             c("P 20", [d(9)], eta("vague", "когда возможно"))]
    check_eta_raw(cases, turns)
    raw14 = cases[0]["eta"]["raw"]
    assert raw14 and raw14 in eta_fx.turns[7].text and "troponina a doua" in raw14.casefold(), raw14
    assert cases[1]["eta"]["raw"] is None   # tradus, nu există în transcriere


def test_eta_type_rule():
    from LLM.attribution import eta_type_rule
    assert eta_type_rule("duration", "каждые 6 часов") == "recurring"
    assert eta_type_rule("relative", "zilnic") == "recurring"
    assert eta_type_rule("duration", "de două ori pe zi") == "recurring"
    assert eta_type_rule("absolute", "joi") == "relative"
    assert eta_type_rule("absolute", "в четверг") == "relative"
    assert eta_type_rule("absolute", "сегодня") == "relative"
    # fără echivoc doar: o dată calendaristică rămâne absolute, o durată rămâne duration
    assert eta_type_rule("absolute", "joi, 2 octombrie") is None
    assert eta_type_rule("absolute", "pe 15.10") is None
    assert eta_type_rule("duration", "курс 7 дней") is None
    assert eta_type_rule("conditional", "dacă crește, zilnic") is None


def test_check_eta_fixes_type_after_verbatim_check():
    turns = [LONG.turns[i] for i in range(37, 45)]
    cases = [c("P 48", [], eta("absolute", "joi"))]
    ev = check_eta_raw(cases, turns)
    assert cases[0]["eta"]["type"] == "relative" and ev[0]["event"] == "eta_type_fixed", ev


def test_supersede_llm_mode():
    def merger(answer):
        m = Merger(cfg())
        acc = c("Pacientă salon 5", [dict(d(10), _t=10), dict(d(11), _t=11), dict(d(25), _t=25)])
        m.add_window(0, [], -1, [acc])
        return m.result(lambda key, decs: answer)[0]["decisions"]

    assert [x["superseded"] for x in merger({0, 1})] == [True, True, False]
    assert [x["superseded"] for x in merger({2})] == [False, False, False]   # ultima nu poate fi înlocuită
    assert [x["superseded"] for x in merger(set())] == [False, False, False]
    # apel eșuat => replaces_previous (aici niciunul)
    assert [x["superseded"] for x in merger(None)] == [False, False, False]


def test_supersede_not_called_for_single_decision():
    calls = []
    m = Merger(cfg())
    m.add_window(0, [], -1, [c("P 1", [d(1)])])
    m.result(lambda k, decs: calls.append(k))
    assert calls == []


def test_extract_schema_caps():
    s = extract_schema(12, 8)
    assert s["properties"]["cases"]["maxItems"] == 12
    assert s["properties"]["cases"]["items"]["properties"]["decisions"]["maxItems"] == 8
    assert "maxItems" not in extract_schema()["properties"]["cases"]


def test_retry_uses_new_seed_and_penalty():
    client = OllamaClient(load_config(env={}))
    sent = []

    def post(body):
        sent.append(dict(body["options"]))
        if len(sent) == 1:
            return {"message": {"content": "{\"cases\": ["}, "done_reason": "length"}   # buclă tăiată
        return {"message": {"content": "{\"cases\": [], \"summary\": \"\"}"}, "done_reason": "stop"}

    client._post = post
    res = client.chat("extract", [{"role": "user", "content": "x"}], {})
    assert res["data"] == {"cases": [], "summary": ""} and res["attempts"] == 2, res
    assert sent[0]["seed"] != sent[1]["seed"] and sent[0]["repeat_penalty"] == 1.1, sent


def test_only_local_ollama():
    try:
        load_config(env={"LLM_OLLAMA_URL": "http://10.0.0.5:11434"})
    except SystemExit:
        return
    raise AssertionError("URL non-local acceptat")


if __name__ == "__main__":
    sys.exit(run(globals()))
