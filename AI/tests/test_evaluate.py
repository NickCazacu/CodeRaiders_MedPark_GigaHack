"""Evaluarea (evaluation/) și post-corecția (pipeline/correct.py) pe date sintetice.
    python -m tests.test_evaluate
"""
import json
import shutil

from evaluation.evaluate import evaluate
from evaluation.gold import GOLD_DIR, build, from_reference, write_labels
from evaluation.metrics import best_order, edit_counts
from pipeline.common import jobs_dir, load_config
from pipeline.correct import Corrector
from pipeline.glossary import find_terms, load_terms, norm_text

GOLD, JOB = "_test_synthetic", "_test_eval"

UTTS = [  # (start, end, "VORBITOR|limbă|text")
    (0.0, 5.0, "S1|ro|Bună dimineața. Pacientul din patul 8 are infarct miocardic."),
    (5.5, 9.0, "S2|ro|Tensiunea arterială 80 pe 40."),
    (8.0, 11.0, "S1|ro|Facem coronarografie mâine."),        # se suprapune cu S2 în 8.0–9.0
    (12.0, 16.0, "S3|ru|Давайте повторим креатинин вечером [neclar]."),
]
EVENTS = [(0.0, 20.0, "REGION"), (8.0, 11.0, "DECISION: coronarografie"), (10.0, 11.0, "ETA: mâine")]


def seg(i, start, end, lang, text, dropped=None):
    toks = text.split()
    step = (end - start) / len(toks)
    words = [{"w": " " + w, "start": start + k * step, "end": start + (k + 1) * step, "p": 0.9}
             for k, w in enumerate(toks)]
    return {"id": i, "setup": "test/float16/auto", "speaker": "UNK", "start": start, "end": end, "lang": lang,
            "text": text, "words": words, "flags": [], "low_confidence": False, "dropped": dropped}


TRANSCRIPT = [
    seg(0, 0.0, 5.0, "ro", "Bună dimineața. Pacientul din patul 8 are infarct miocardic."),
    seg(1, 5.5, 11.0, "ro", "Tensiunea arterială 80 pe facem coronografie mâine."),  # lipsește „40” (în suprapunere)
    seg(2, 12.0, 16.0, "ru", "Давайте повторим креатенин вечером."),
    seg(3, 18.5, 19.0, "ro", "da"),                                   # în liniște => inserție
    seg(4, 20.0, 21.0, "ru", "Продолжение следует", dropped="hallucination_phrase"),
]


def setup_files():
    d = GOLD_DIR / GOLD
    d.mkdir(parents=True, exist_ok=True)
    write_labels(d / "utterances.txt", UTTS)
    write_labels(d / "events.txt", EVENTS)
    (d / "overlap.txt").write_text("", encoding="utf-8")
    job = jobs_dir(load_config()) / JOB
    job.mkdir(parents=True, exist_ok=True)
    (job / "transcript.json").write_text(json.dumps(TRANSCRIPT, ensure_ascii=False), encoding="utf-8")
    return d, job


def test_edit_counts():
    e = edit_counts("a b c d".split(), "a x c".split())
    assert (e.S, e.D, e.I, e.N) == (1, 1, 0, 4)
    # în suprapuneri ordinea replicilor nu e penalizată
    ref, e = best_order([["x", "y"], ["a", "b"]], ["a", "b", "x", "y"])
    assert e.S + e.D + e.I == 0 and ref == ["a", "b", "x", "y"]


def test_norm():
    assert norm_text("Ş-a făcut tromboaspiraţie, 38–40%!") == ["ș", "a", "făcut", "tromboaspirație", "38", "40"]
    assert norm_text("Отёк лёгких") == ["отек", "легких"]


def test_terms():
    terms = load_terms()
    found = {t.concept for t, _, _ in find_terms("TA 80 pe 40, tomografia computerizată, mama ta", terms)}
    assert found == {"ta", "ct"}, found  # „ta” (pronume) nu e abrevierea TA


def test_gold_build_and_evaluate():
    d, job = setup_files()
    try:
        gold = build(GOLD)
        assert gold["overlaps"] == [[8.0, 9.0]]                 # calculat din replicile S1/S2
        assert gold["regions"] == [[0.0, 20.0]]
        assert len(gold["events"]) == 2

        r = evaluate(JOB, GOLD)
        w = r["wer"]
        # replica 0 e perfectă, restul are erori cunoscute
        u0 = next(u for u in r["units"] if u["unit"] == "utt:0")
        assert u0["errors"] == 0, u0
        assert w["all"]["N"] == 9 + 5 + 3 + 4, w["all"]         # [neclar] nu intră în referință
        assert w["clean"]["I"] == 1 and any(u["unit"] == "ins:None" for u in r["units"])  # „da” din liniște
        assert (w["all"]["S"], w["all"]["D"]) == (2, 1)          # coronografie, креатенин; lipsește „40”
        assert (w["overlap"]["N"], w["overlap"]["D"]) == (4, 1), w["overlap"]
        assert w["by_lang"]["ru"]["S"] == 1                      # креатенин
        assert r["events"] == {"decisions": 1, "decisions_in_overlap": 1, "etas": 1, "etas_in_overlap": 0}
        missed = {m["concept"] for m in r["terms"]["missed"]}
        assert missed == {"coronarografie", "creatinina"}, missed
        assert r["language"]["acc"] == 1.0

        # post-corecția repară cei doi termeni, iar evaluarea o vede
        c = Corrector({"min_len": 6, "min_score": 88})
        fixed = [dict(s, words=[dict(x) for x in s["words"]]) for s in TRANSCRIPT]
        n = sum(c.apply(s) for s in fixed)
        assert n == 2, [s.get("corrections") for s in fixed]
        (job / "transcript.json").write_text(json.dumps(fixed, ensure_ascii=False), encoding="utf-8")
        r2 = evaluate(JOB, GOLD)
        assert r2["terms"]["recall"] == 1.0, r2["terms"]["missed"]
        assert r2["wer"]["all"]["rate"] < w["all"]["rate"]
    finally:
        shutil.rmtree(d, ignore_errors=True)
        shutil.rmtree(job, ignore_errors=True)


def test_from_reference(tmp="_test_ref"):
    """Formatul tests/reference/*.txt (tests/evaluate.py) -> gold, cu un interval evaluat per fragment."""
    src = GOLD_DIR / f"{tmp}.txt"
    GOLD_DIR.mkdir(parents=True, exist_ok=True)
    src.write_text("# fragment 0:00-0:20\n"
                   "[Speaker1] 0:00 - 0:05: Bună dimineața [aici nu se intelege].\n"
                   "[Speaker 2] 0:04 - 0:09: Tensiunea 80 pe 40.\n"
                   "# fragment 1:00-1:10\n"
                   "[Speaker3] 1:00 - 1:04: Давайте повторим креатинин.\n", encoding="utf-8")
    try:
        g = from_reference(src, tmp)
        assert g["regions"] == [[0.0, 20.0], [60.0, 70.0]]
        assert [u["speaker"] for u in g["utterances"]] == ["Speaker1", "Speaker2", "Speaker3"]
        assert [u["lang"] for u in g["utterances"]] == ["ro", "ro", "ru"]
        assert g["overlaps"] == [[4.0, 5.0]]
    finally:
        src.unlink()
        shutil.rmtree(GOLD_DIR / tmp, ignore_errors=True)


def test_corrector_is_conservative():
    c = Corrector({"min_len": 6, "min_score": 88})
    for w, lang in [(" intubat", "ro"), (" internat", "ro"), (" operatorul", "ro"), (" pensiunea", "ro"),
                    (" pacientul", "ro"), (" noradrenalina", "ro"), (" отделения", "ru"), (" давайте", "ru")]:
        assert c.correct_word(w, lang)[1] is None, w
    assert c.correct_word(" trombospirație,", "ro")[0] == " tromboaspirație,"
    assert c.correct_word(" Saturatia", "ro")[0] == " Saturația"


if __name__ == "__main__":
    for name, fn in [(n, f) for n, f in globals().items() if n.startswith("test_")]:
        fn()
        print(f"OK  {name}")
