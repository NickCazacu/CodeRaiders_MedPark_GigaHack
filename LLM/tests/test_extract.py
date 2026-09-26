"""Fluxul complet cu un client Ollama fals (fără rețea, fără GPU).
    python -m LLM.tests.test_extract
"""
import json
import sys

from LLM import extract as ex
from LLM.tests.helpers import FakeClient, case, cfg, check_minutes, dec, fact, fixture, run, tmpdir


def go(name, script, **cfg_over):
    d = tmpdir()
    client = FakeClient(script)
    report = ex.extract(fixture(name), "2026-09-21", d / "minutes.json", d / "llm_debug", cfg(**cfg_over), client)
    minutes = json.loads((d / "minutes.json").read_text(encoding="utf-8"))
    return minutes, report, client, d


def test_single_window_path():
    resp = {"summary": "Două cazuri: AVC [00:01] și hernie [01:13].", "cases": [
        case("Pacientă salon 3, neurologie", [dec("Facem RMN cerebral mâine dimineață și începem aspirina de azi.",
                                                  "00:42")], {"type": "relative", "raw": "mâine dimineață", "condition": ""}),
        case("Pacient 21, chirurgie", [dec("programăm operația pe 2 octombrie", "01:44")],
             {"type": "absolute", "raw": "pe 2 octombrie", "condition": ""}),
    ]}
    m, report, client, _ = go("short_two_cases.json", {"extract": [resp]})
    assert [c["stage"] for c in client.calls] == ["extract"], client.calls   # fără apel final
    assert report["path"] == "single" and m["meeting_summary"].startswith("Două cazuri"), m
    assert not check_minutes(m), check_minutes(m)
    assert all(d["turn_id"] is not None for c in m["cases"] for d in c["decisions"]), m
    # promptul „single” nu are secțiune de context
    assert "CONTEXT" not in client.calls[0]["messages"][-1]["content"]


def test_example_two_windows_with_context_marking():
    w0 = {"summary": "Pacientul 48: insuficiență renală după contrast [00:00].",
          "cases": [case("Pacient 48, salon 12", summary="Creatinina crește [00:00].")]}
    w1 = {"summary": "Se repetă creatinina seara [00:32].", "cases": [case(
        "Pacient 48, salon 12", [dec("Давайте повторим креатинин вечером и решим по гемодиализу.", "00:32",
                                     "Repetarea creatininei seara și decizia privind hemodializa")],
        {"type": "relative", "raw": "вечером", "condition": ""}, summary="Se repetă creatinina seara [00:32].",
        questions=["Ecografia: nu s-a stabilit [00:41]"])]}
    final = {"meeting_summary": "Pentru pacientul 48 se repetă creatinina seara [00:32].", "case_order": [0]}
    m, report, client, d = go("llm_input.example.json", {"extract": [w0, w1], "final": [final]})
    assert [c["stage"] for c in client.calls] == ["extract", "extract", "final"]
    user = client.calls[1]["messages"][-1]["content"]
    ctx, cur = user.split("=== FEREASTRA CURENTĂ")
    assert "[00:21]" in ctx and "[00:26]" in ctx and "[00:32]" not in ctx, user   # 2 replici de context
    assert "[00:32]" in cur and "Pacient 48, salon 12 — discutat, fără decizie" in user, user
    dec0 = m["cases"][0]["decisions"][0]
    assert dec0["turn_id"] == 3 and dec0["timestamp"] == "00:32" and not dec0["superseded"], dec0
    assert m["cases"][0]["eta"]["raw"] == "вечером" and not check_minutes(m)
    for f in ("window_000.prompt.txt", "window_001.response.json", "quotes.json", "merge.json",
              "final.prompt.txt", "context_check.json", "run.json"):
        assert (d / "llm_debug" / f).exists(), f


def test_long_meeting_overlap_and_late_decision():
    # model „ideal”: pacientul 48 discutat în ferestrele 0-1, decis în 5; decizia pt. 17 (replica 19)
    # e extrasă în fereastra 2 și din nou din contextul ferestrei 3
    p48, p17 = "Pacient 48, cardiologie", "Pacient 17, chirurgie"
    s5 = "Pacientă salon 5, nefrologie"
    script = {"extract": [
        {"summary": "Pacientul 48 [00:01].", "cases": [case(p48, [dec("Deci deocamdată nu hotărâm nimic pentru 48, "
                                                                       "așteptăm ECO transesofagian.", "01:01",
                                                                       status="necesită investigații suplimentare")])]},
        {"summary": "Salon 5 [01:34].", "cases": [case(s5, [dec("Cu potasiul ăsta nu mai așteptăm. Hemodializă mâine "
                                                                 "dimineață.", "01:52")],
                                                        {"type": "relative", "raw": "mâine dimineață", "condition": ""})]},
        {"summary": "Pacientul 17 [03:11].", "cases": [case(p17, [dec("Operăm pacientul 17 astăzi, apendicectomie "
                                                                       "laparoscopică.", "03:26")])]},
        {"summary": "Salon 5 revizuit [04:14].", "cases": [
            case(p17, [dec("Operăm pacientul 17 astăzi, apendicectomie laparoscopică.", "03:26")]),
            case(s5, [dec("Atunci anulăm hemodializa, continuăm conservator și repetăm analizele mâine.", "04:34",
                          replaces=True)], {"type": "relative", "raw": "mâine", "condition": ""})]},
        {"summary": "Pacientul 22 [05:10].", "cases": [case("Pacient 22, neurologie", [
            dec("КТ с контрастом завтра, и сегодня... нет, КТ сегодня без контраста.", "05:24")])]},
        {"summary": "Pacientul 48 operat joi [07:11].", "cases": [case(p48, [
            dec("Operăm pacientul 48 joi, plastie mitrală și bypass aortocoronarian.", "07:39")],
            {"type": "relative", "raw": "joi", "condition": ""})]},
    ],
        # un apel per caz cu >= 2 decizii, în ordinea primei apariții: 48 (nimic înlocuit), salon 5 (prima)
        "supersede": [{"superseded": []}, {"superseded": [0]}],
        "final": [{"meeting_summary": "Rezumat [00:01].", "case_order": [0, 3, 1, 2]}]}
    m, report, client, d = go("long_meeting.json", script)
    assert report["n_windows"] == 6 and not report["errors"], report["errors"]
    assert not check_minutes(m), check_minutes(m)
    by = {c["case_key"]: c for c in m["cases"]}
    assert [c["case_key"] for c in m["cases"]] == [p48, "Pacient 22, neurologie", s5, p17], list(by)
    # 17: o singură decizie, deși a fost extrasă de două ori
    assert len(by[p17]["decisions"]) == 1 and by[p17]["decisions"][0]["turn_id"] == 19, by[p17]
    # 48: decizia târzie e ultima, activă; ETA „joi”
    assert by[p48]["decisions"][-1]["turn_id"] == 41 and by[p48]["eta"]["raw"] == "joi", by[p48]
    # salon 5: decizia schimbată e superseded
    assert [x["superseded"] for x in by[s5]["decisions"]] == [True, False], by[s5]
    # 22: decizie pe replica [?] => turn_id pe replica nesigură (validarea pune needs_review)
    tid = by["Pacient 22, neurologie"]["decisions"][0]["turn_id"]
    quotes = json.loads((d / "llm_debug" / "quotes.json").read_text(encoding="utf-8"))
    assert tid == 29 and any(q["turn_id"] == 29 and q["turn_uncertain"] for q in quotes), quotes
    # antetul ferestrei 5 conține cazurile cunoscute
    assert "Pacient 48, cardiologie — necesită investigații suplimentare" in client.calls[5]["messages"][-1]["content"]


def test_failed_window_continues():
    script = {"extract": [None, {"summary": "s [00:32]", "cases": [
        case("Pacient 48", [dec("Давайте повторим креатинин вечером и решим по гемодиализу.", "00:32")])]}],
        "final": [None]}
    m, report, client, _ = go("llm_input.example.json", script)
    assert len(report["errors"]) == 2 and len(m["cases"]) == 1, report["errors"]
    assert m["meeting_summary"] == "s [00:32]"   # fallback: rezumatele ferestrelor
    assert not check_minutes(m)


def test_non_romanian_summary_warned():
    m, report, _, _ = go("uncertain_decision.json", {"extract": [{"summary": "Перевод в реанимацию [00:20].", "cases": [
        case("Pacient 33", [dec("Трансфер в реанимацию сегодня, da, îl mutăm acum.", "00:20", "Transfer în ATI azi")])]}]})
    assert any("nu e în română: meeting_summary" in w for w in report["warnings"]), report["warnings"]
    assert not any("decision" in w for w in report["warnings"])   # citatul rusesc e permis (verbatim)


def test_leftover_russian_translated():
    resp = {"summary": "Transfer în ATI azi [00:20].", "cases": [case(
        "Pacient 33", [dec("Трансфер в реанимацию сегодня, da, îl mutăm acum.", "00:20", "Перевод в реанимацию")],
        questions=["Родственникам позвонить [00:20]", "Rămâne de văzut [00:20]"])]}
    # al doilea text tradus păstrează chirilică => nu se aplică, rămâne semnalat
    m, report, client, d = go("uncertain_decision.json", {
        "extract": [resp], "translate": [{"texts": ["Transfer în reanimare", "Позвонить rudelor [00:20]"]}]})
    c = m["cases"][0]
    assert c["decisions"][0]["decision"] == "Transfer în reanimare", c
    assert c["decisions"][0]["quote"].startswith("Трансфер")          # citatul rămâne verbatim
    assert c["open_questions"] == ["Родственникам позвонить [00:20]", "Rămâne de văzut [00:20]"], c
    assert any("open_questions[1]" in w for w in report["warnings"]), report["warnings"]
    sent = client.calls[-1]["messages"][0]["content"]
    assert "0. Перевод в реанимацию" in sent and "Rămâne de văzut" not in sent, sent


def test_empty_result_valid():
    m, report, _, _ = go("no_decisions.json", {"extract": [{"summary": "Raport de gardă fără evenimente [00:01].",
                                                              "cases": []}]})
    assert m == {"meeting_summary": "Raport de gardă fără evenimente [00:01].", "cases": []}, m
    assert not report["errors"]


def test_hallucinated_quote_kept_with_null_turn():
    m, report, _, _ = go("uncertain_decision.json", {"extract": [{"summary": "s", "cases": [
        case("Pacient 33", [dec("Îl intubăm imediat.", "00:20")])]}]})
    assert m["cases"][0]["decisions"][0]["turn_id"] is None and report["quotes_unresolved"] == 1


def test_window_too_large_is_reported_not_truncated():
    # num_ctx mic: toate ferestrele depășesc; cu on_overflow=skip nu se trimite nimic
    m, report, client, d = go("llm_input.example.json", {}, **{"ollama.num_ctx": 1024, "context.on_overflow": "skip"})
    assert client.calls == [], client.calls
    assert report["warnings"] and len(report["errors"]) == 2 and m == {"meeting_summary": "", "cases": []}, report
    check = json.loads((d / "llm_debug" / "context_check.json").read_text(encoding="utf-8"))
    assert not any(r["ok"] for r in check), check


def test_facts_kept_deduped_and_rendered():
    # aceeași constatare în ambele ferestre (suprapunere) -> o singură dată; categorie invalidă -> corectată
    w0 = {"summary": "", "cases": [case("Pacient 48, salon 12", summary="Creatinina crește [00:00].",
                                        facts=[fact("Creatinina a crescut după contrast", "00:00"),
                                               fact("Insuficiență renală acută", "00:00", "diagnostic")])]}
    w1 = {"summary": "", "cases": [case("Pacient 48, salon 12", summary="Se repetă creatinina seara [00:32].",
                                        facts=[fact("Creatinina a crescut dupa contrast.", "00:00"),
                                               fact("Hemodializa se discută", "00:32", "altceva")])]}
    final = {"meeting_summary": "Pacientul 48 [00:32].", "case_order": [0]}
    m, _, _, d = go("llm_input.example.json", {"extract": [w0, w1], "final": [final]})
    facts = m["cases"][0]["facts"]
    assert [f["fact"] for f in facts] == ["Creatinina a crescut după contrast", "Insuficiență renală acută",
                                           "Hemodializa se discută"], facts
    assert facts[2]["category"] == "evoluție" and not check_minutes(m), (facts, check_minutes(m))
    from LLM import mom
    mom.write(d)
    md = (d / "mom.md").read_text(encoding="utf-8")
    assert "**Constatări clinice:**" in md and "_Analize:_ Creatinina a crescut după contrast [00:00]" in md, md
    assert "Creatinina a crescut după contrast" in (d / "mom.html").read_text(encoding="utf-8")


def test_patient_mode_one_window_per_patient():
    # segmentarea: pacientul din patul 8 de la început, „patul nou” (= 9) de la [00:52]
    seg = {"patients": [{"start": "00:03", "label": "Pacient patul 8, reanimare", "cue": "patul 8"},
                        {"start": "00:52", "label": "Pacient patul nou", "cue": "седьмая палата"}]}
    p8 = {"summary": "Pacientul din patul 8 [00:03].", "cases": [case(
        "Pacient patul 8", [dec("Давайте повторим креатинин вечером и решим по гемодиализу.", "00:32",
                                "Repetarea creatininei seara")],
        facts=[fact("Creatinina 240", "00:26"), fact("Diureza 400 ml pe noapte", "00:26", "evoluție")])]}
    p9 = {"summary": "Următorul pacient [00:52].", "cases": [case("Pacient patul 9", summary="Salonul 7 [00:52].")]}
    final = {"meeting_summary": "Două cazuri [00:03] [00:52].", "case_order": [0, 1]}
    m, report, client, d = go("llm_input.example.json", {"segment": [seg], "extract": [p8, p9], "final": [final]},
                              **{"segment.enabled": True})
    assert [c["stage"] for c in client.calls] == ["segment", "extract", "extract", "final"], client.calls
    assert report["path"] == "patients", report["path"]
    second = client.calls[2]["messages"][-1]["content"]
    ctx, frag = second.split("=== FRAGMENT")
    assert "„Pacient patul 9”" in second and "Pacient patul 8" in second, second   # eticheta corectată + cunoscuții
    assert "[00:44]" in ctx and "[00:52]" in frag and "[00:26]" not in frag, second  # context = 2 replici
    first = client.calls[1]["messages"][-1]["content"]
    assert "[00:44]" in first.split("=== FRAGMENT")[1] and "[00:52]" not in first, first
    keys = [c["case_key"] for c in m["cases"]]
    assert keys == ["Pacient patul 8", "Pacient patul 9"] and not check_minutes(m), (keys, check_minutes(m))
    assert m["cases"][0]["decisions"][0]["turn_id"] == 3 and len(m["cases"][0]["facts"]) == 2, m
    assert (d / "llm_debug" / "segment.response.json").exists()


def test_patient_mode_long_meeting_in_parts():
    # transcriere prea lungă pentru un apel: 3 bucăți (replicile 0-1, 2-3, 4-6), fiecare cu pacientul care continuă
    segs = [{"patients": [{"start": "00:03", "label": "Pacient patul 8, reanimare", "cue": "patul 8"}]},
            # modelul repetă pacientul care continuă, cu alt diagnostic în etichetă: același pat => unit
            {"patients": [{"start": "00:26", "label": "Patul 8 – insuficiență renală", "cue": ""}]},
            # un început dinaintea bucății (inventat) se ignoră
            {"patients": [{"start": "00:03", "label": "Pacient 48", "cue": ""},
                          {"start": "00:52", "label": "Pacient patul nou", "cue": "седьмая палата"}]}]
    p8 = {"summary": "", "cases": [case("Pacient patul 8", [dec("Давайте повторим креатинин вечером", "00:32",
                                                                 "Repetarea creatininei seara")])]}
    p9 = {"summary": "", "cases": [case("Pacient patul 9")]}
    final = {"meeting_summary": "Două cazuri [00:03] [00:52].", "case_order": [0, 1]}
    m, report, client, d = go("llm_input.example.json",
                              {"segment": segs, "extract": [p8, p9], "final": [final]},
                              **{"segment.enabled": True, "segment.window_tokens": 70})
    assert [c["stage"] for c in client.calls] == ["segment"] * 3 + ["extract", "extract", "final"], client.calls
    assert "Pacient patul 8, reanimare" in client.calls[1]["messages"][-1]["content"]   # știe ce continuă
    assert "CONTINUARE" not in client.calls[0]["messages"][-1]["content"]
    assert report["path"] == "patients" and [c["case_key"] for c in m["cases"]] == ["Pacient patul 8", "Pacient patul 9"]
    starts = json.loads((d / "llm_debug" / "segment.response.json").read_text(encoding="utf-8"))["starts"]
    assert [s["ts"] for s in starts] == ["00:03", "00:52"], starts


def test_failed_segment_part_is_retried_in_halves():
    # bucata 2 (replicile 4-6) eșuează: se reîncearcă pe jumătăți (4 | 5-6), a doua găsește pacientul nou
    segs = [{"patients": [{"start": "00:03", "label": "Pacient patul 8", "cue": ""}]},
            {"patients": []},
            None,
            {"patients": []},
            {"patients": [{"start": "00:52", "label": "Pacient patul 9", "cue": ""}]}]
    w = {"summary": "", "cases": []}
    m, report, client, d = go("llm_input.example.json", {"segment": segs, "extract": [w] * 4, "final": [None]},
                              **{"segment.enabled": True, "segment.window_tokens": 70, "segment.min_split_turns": 2})
    assert [c["stage"] for c in client.calls][:5] == ["segment"] * 5, client.calls
    assert any("reîncerc pe jumătăți" in x for x in report["warnings"]), report["warnings"]
    starts = json.loads((d / "llm_debug" / "segment.response.json").read_text(encoding="utf-8"))["starts"]
    assert [s["ts"] for s in starts] == ["00:03", "00:52"], starts


def test_same_patient():
    assert ex.same_patient("Pacient patul 9, pneumonie", "Patul 9 – insuficiență respiratorie")
    assert ex.same_patient("Pacientul 48", "Pacient 48, cardiologie")
    assert not ex.same_patient("Pacient patul 8", "Pacient 48")
    assert not ex.same_patient("Boxa 3", "Patul 3")
    assert ex.same_patient("Alți pacienți", "alți  pacienți") and not ex.same_patient("Primul pacient", "Alți pacienți")


def test_patient_mode_falls_back_to_asr_windows():
    # segmentare eșuată, apoi o segmentare cu un singur pacient: ambele -> ferestrele ASR, fără eroare
    for seg in (None, {"patients": [{"start": "00:03", "label": "Pacient patul 8", "cue": ""}]}):
        w = {"summary": "", "cases": []}
        _, report, client, _ = go("llm_input.example.json", {"segment": [seg], "extract": [w, w]},
                                  **{"segment.enabled": True})
        assert [c["stage"] for c in client.calls] == ["segment", "extract", "extract"], client.calls
        assert report["path"] == "map-reduce" and not report["errors"] and report["warnings"], report


def test_window_too_large_expands_ctx():
    _, report, client, _ = go("llm_input.example.json", {"extract": [{"summary": "", "cases": []}] * 2},
                              **{"ollama.num_ctx": 2048, "context.max_num_ctx": 12288,
                                 "stages.extract.num_predict": 2048})
    assert all(c["num_ctx"] > 2048 for c in client.calls if c["stage"] == "extract"), client.calls
    assert any("num_ctx mărit" in w for w in report["warnings"])


def test_budget_uses_observed_token_ratio():
    # primul apel: factorul din config (1.55) cere num_ctx mai mare; Ollama raportează prompt real mai mic
    # => apelul următor încape în num_ctx de bază (modelul rămâne tot în VRAM)
    class Measured(FakeClient):
        def chat(self, stage, messages, schema, num_ctx=None):
            res = super().chat(stage, messages, schema, num_ctx)
            res["meta"] = {"prompt_eval_count": int(ex.msgs_tokens(messages) * 1.2)}
            return res

    client = Measured({"extract": [{"summary": "", "cases": []}] * 2})
    ex.extract(fixture("llm_input.example.json"), "2026-09-21", tmpdir() / "m.json", tmpdir(),
               cfg(**{"ollama.num_ctx": 8192, "context.token_factor": 1.9, "stages.extract.num_predict": 2048}),
               client)
    first, second = [c["num_ctx"] for c in client.calls if c["stage"] == "extract"]
    assert first > 8192 and second == 8192, (first, second)


if __name__ == "__main__":
    sys.exit(run(globals()))
