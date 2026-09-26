"""Generează fixture-urile llm_input.json din scenariile de mai jos, cu exact codul echipei ASR
(pipeline.pack_for_llm.make_turns / make_windows), ca formatul să fie identic cu cel real.

    python -m LLM.tests.fixtures.build

Fiecare replică: (vorbitor, text) sau (vorbitor, text, {opțiuni}); opțiuni: low (încredere mică),
lang (implicit: ru dacă textul e mai mult chirilic, altfel ro), pause (secunde de liniște înainte),
at / dur (început și durată explicite, în secunde).
"""
import json
from collections import Counter
from pathlib import Path

from pipeline.pack_for_llm import make_turns, make_windows

HERE = Path(__file__).resolve().parent
S0, S1, S2, U = "SPEAKER_00", "SPEAKER_01", "SPEAKER_02", "UNK"


def lang_of(text):
    cyr = sum("Ѐ" <= c <= "ӿ" for c in text)
    lat = sum(c.isascii() and c.isalpha() for c in text)
    return "ru" if cyr > lat else "ro"


def build(job_id, script, window_tokens=3500, overlap=3, start=1.0, tail=3.0):
    segs, t = [], start
    for i, item in enumerate(script):
        spk, text, opt = (*item, {}) if len(item) == 2 else item
        t = opt.get("at", t + opt.get("pause", 0.0))
        dur = opt.get("dur") or round(max(2.0, len(text) / 13), 1)
        segs.append({"id": i, "speaker": spk, "start": round(t, 1), "end": round(t + dur, 1), "text": text,
                     "lang": opt.get("lang") or lang_of(text), "low_confidence": bool(opt.get("low")),
                     "flags": [], "dropped": None})
        t += dur + 0.8
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


# ---------------------------------------------------------------------------------------------
# exemplul din documentația ASR: 7 replici, 2 ferestre, a doua cu 2 replici de suprapunere
EXAMPLE = [  # timpii din documentație: [00:21], [00:26], [00:32], [00:41], sfârșit 58.1 s, durată 61 s
    (S0, "Pacientul 48 din salonul 12, internat cu insuficiență renală acută după coronarografie cu contrast.",
     {"at": 0.8, "dur": 9.4}),
    (S0, "S-a pornit parcă diureza ieri, dar azi creatinina a crescut din nou, potasiul 5,6.", {"at": 10.6, "dur": 10.3}),
    (S1, "Diureza cum este?", {"at": 21.5, "dur": 3.5}),
    (S0, "Diureza e scăzută, 400 ml pe noapte, creatinina 240.", {"at": 26.1, "dur": 5.3}),
    (S2, "Давайте повторим креатинин вечером и решим по гемодиализу.", {"at": 32.0, "dur": 8.2}),
    (S1, "De acord. Și ecografia?", {"at": 41.3, "dur": 2.7, "low": True}),
    (S2, "Эхо почек пока не знаю, посмотрим.", {"at": 44.8, "dur": 6.2}),
    (S0, "Bine, atunci trecem la următorul pacient.", {"at": 51.9, "dur": 6.2}),
]

SHORT_TWO_CASES = [
    (S0, "Bună dimineața, începem. Primul caz: pacienta din salonul 3, neurologie, 67 de ani, AVC ischemic de ieri seară."),
    (S1, "A venit la șase ore de la debut, deci fereastra pentru tromboliză a fost depășită."),
    (S0, "CT-ul la internare fără hemoragie. Hemipareză pe dreapta, vorbirea puțin afectată."),
    (S2, "Надо сделать МРТ, чтобы увидеть объём поражения."),
    (S0, "Da. Facem RMN cerebral mâine dimineață și începem aspirina de azi."),
    (S1, "De acord, și consult la kinetoterapeut."),
    (S0, "Bine, kinetoterapia din ziua a doua. Al doilea caz: pacientul 21 din chirurgie, hernie inghinală dreaptă.", {"pause": 20}),
    (S2, "Hernia e reductibilă, fără semne de strangulare, analizele sunt normale."),
    (S1, "Anestezistul l-a văzut ieri, risc ASA doi, no contraindications."),
    (S2, "Atunci programăm operația pe 2 octombrie, hernioplastie cu plasă."),
    (S0, "Da, aprobat, pe 2 octombrie."),
    (S1, "Asta a fost tot pentru azi, mulțumesc."),
]

NO_DECISIONS = [
    (U, "Raportul de gardă. Noaptea a fost liniștită, fără internări noi."),
    (U, "Pacientul 5 din salonul 2 afebril, tensiunea 130 pe 80, a dormit bine."),
    (U, "У пациентки из восьмой палаты боли уменьшились, она уже ходит по коридору."),
    (U, "Pacientul 9 fără plângeri, drenul a fost scos ieri conform planului."),
    (U, "Analizele de dimineață au venit?"),
    (U, "Încă nu, laboratorul a avut o defecțiune la analizor, ne sună ei."),
    (U, "Pacienta din salonul 11, glicemia pe noapte între 7 și 9."),
    (U, "Everything else is stable, no events overnight."),
    (U, "Bine, mulțumesc, asta e tot de la gardă."),
]

ETA_TYPES = [
    (U, "Pacientul 3 din salonul 2, după colecistectomie, evoluție bună."),
    (U, "Controlul CT îl facem pe 5 octombrie, e programat deja."),
    (U, "Pacienta 9, pneumonie, afebrilă a treia zi, saturația 97."),
    (U, "Externarea mâine, cu tratament per os acasă."),
    (U, "Pacientul 11, pielonefrită, urocultura a crescut E. coli sensibil la ceftriaxonă."),
    (U, "Продолжаем цефтриаксон, курс 7 дней, потом контроль мочи."),
    (U, "Pacientul 14, durere toracică atipică, prima troponină negativă."),
    (U, "Dacă troponina a doua crește, facem coronarografie, dacă nu, îl externăm."),
    (U, "Pacientul 20 din neurologie, cefalee persistentă, CT normal."),
    (U, "RMN-ul îl facem cât de curând, when possible, aparatul e ocupat."),
    (U, "Pacientul 25, diabet decompensat, glicemia 18 la internare."),
    (U, "Контроль гликемии каждые 6 часов и корректируем инсулин."),
    (U, "Pacientul 30, după fractura de radius, ghips, fără probleme."),
    (U, "Continuăm același tratament, nimic nou."),
]

UNCERTAIN = [
    (U, "Pacientul 33, pneumonie bilaterală, saturația 88 pe oxigen cinci litri."),
    (U, "Radiografia de azi arată progresie, infiltrat bilateral extins."),
    (U, "Трансфер в реанимацию сегодня, da, îl mutăm acum.", {"low": True}),
    (U, "Bine, anunțăm reanimarea. Pacienta 40 e stabilă, rămâne pe secție."),
]

# ședință lungă: ferestre mici (window_tokens=220) ca să iasă 6+ ferestre
LONG = [
    # pacientul 48: discutat devreme, decis târziu
    (U, "Începem consiliul. Primul, pacientul 48 din cardiologie, a venit cu infarct miocardic inferior."),
    (U, "Pacientul 48, el a fost pe dată de uscări și a ajuns la noi cu infarct miocardic, a fost trombospirație făcută."),
    (U, "Mitrală 3, compracție de 38-40. Da, el e pacient cardiac și trivascular, nu?"),
    (U, "Trivascular, da. Și acolo suspecția și la ruptura de cordaj a fost."),
    (U, "Ruptura de cordaj trebuie confirmată, pe transtoracic nu se vede clar."),
    (U, "Нужно чреспищеводное эхо, без него не решим."),
    (U, "Deci deocamdată nu hotărâm nimic pentru 48, așteptăm ECO transesofagian."),
    (U, "Hemodinamic e stabil, pe diuretice, tensiunea 110 pe 70."),
    # pacienta din salonul 5: decizie care se schimbă
    (U, "Trecem la pacienta din salonul 5, nefrologie, insuficiență renală acută pe fond cronic.", {"pause": 15}),
    (U, "Creatinina 420, potasiul 6,1, diureza 300 ml pe zi."),
    (U, "Cu potasiul ăsta nu mai așteptăm. Hemodializă mâine dimineață."),
    (U, "Согласен, диализ завтра утром, катетер ставим сегодня."),
    (U, "Ok, am notat, cateter azi și dializă mâine."),
    # organizatoric
    (U, "O paranteză organizatorică: graficul de gărzi pe octombrie e afișat.", {"pause": 10}),
    (U, "Cine are schimburi, să vorbească direct cu șefa de secție."),
    (U, "Și sala doi de operații e în renovare până la sfârșitul lunii."),
    # pacientul 17: decizia cade la granița ferestrelor (în suprapunere)
    (U, "Pacientul 17 din chirurgie, apendicită acută, a venit azi-noapte.", {"pause": 15}),
    (U, "Leucocite 16, ecografia arată apendice de 11 milimetri, lichid liber puțin."),
    (U, "Не будем ждать, это классическая картина."),
    (U, "Operăm pacientul 17 astăzi, apendicectomie laparoscopică."),
    (U, "Da, sala unu e liberă după prânz."),
    (U, "Anestezistul confirmă, a mâncat ultima dată aseară."),
    (U, "Bun. Mergem mai departe."),
    # înapoi la salonul 5: decizia se schimbă
    (U, "Am revenit la pacienta din salonul 5, au venit analizele de control chiar acum.", {"pause": 20}),
    (U, "Creatinina a scăzut la 250, potasiul 5,2, diureza a crescut la 900 ml."),
    (U, "Atunci anulăm hemodializa, continuăm conservator și repetăm analizele mâine."),
    (U, "Согласна, диализ отменяем, катетер пока оставим."),
    # pacientul 22: decizie pe o replică nesigură [?]
    (U, "Pacientul 22 din neurologie, cădere acasă, confuz.", {"pause": 15}),
    (U, "Pe anticoagulante, INR 3,4."),
    (U, "КТ с контрастом завтра, и сегодня... нет, КТ сегодня без контраста.", {"low": True}),
    (U, "Da, CT nativ azi, urgent, din cauza anticoagulantelor."),
    (U, "Și oprim anticoagulantul până la rezultat."),
    # filler cu propoziții întrerupte
    (U, "Mai era ceva despre... nu, am uitat, revin.", {"pause": 10}),
    (U, "Farmacia a anunțat că meropenemul e în stoc din nou."),
    (U, "Bine de știut. Pentru salonul 9 poate... lăsăm, nu e cazul azi."),
    (U, "Asistentele cer să completăm fișele de transfer la timp."),
    (U, "Da, vă rog, mai ales în weekend."),
    # înapoi la 48: decizia
    (U, "Revenim la pacientul 48, a venit rezultatul ECO transesofagian.", {"pause": 25}),
    (U, "Se confirmă ruptura de cordaj pe valva mitrală posterioară, regurgitare severă."),
    (U, "Fracția de ejecție rămâne 38-40 la sută."),
    (U, "Atunci e indicație chirurgicală clară, plastie de valvă mitrală plus bypass."),
    (U, "Operăm pacientul 48 joi, plastie mitrală și bypass aortocoronarian."),
    (U, "Согласен, в четверг, я скажу кардиохирургам."),
    (U, "Până joi îl ținem pe terapie intensivă, monitorizare."),
    (U, "Ok, am încheiat, mulțumesc tuturor.", {"pause": 5}),
]

FIXTURES = {
    "llm_input.example.json": dict(job_id="exemplu_sedinta", script=EXAMPLE, window_tokens=116, overlap=2, tail=2.1),
    "short_two_cases.json": dict(job_id="scurt_doua_cazuri", script=SHORT_TWO_CASES),
    "no_decisions.json": dict(job_id="fara_decizii", script=NO_DECISIONS),
    "eta_types.json": dict(job_id="tipuri_eta", script=ETA_TYPES),
    "uncertain_decision.json": dict(job_id="decizie_nesigura", script=UNCERTAIN),
    "long_meeting.json": dict(job_id="sedinta_lunga", script=LONG, window_tokens=220, overlap=3),
}


def main():
    for name, kw in FIXTURES.items():
        data = build(**kw)
        (HERE / name).write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"{name:26s} {data['n_turns']:3d} replici, {data['n_windows']} ferestre "
              f"{[w['turn_ids'] for w in data['windows']]}")


if __name__ == "__main__":
    main()
