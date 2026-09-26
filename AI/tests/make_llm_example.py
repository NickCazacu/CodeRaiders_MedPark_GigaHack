"""Generează docs/llm_input.example.json cu ACELAȘI cod ca pipeline-ul (pack_for_llm.build),
din segmente sintetice (fără date reale). Fereastra e micșorată la 120 de tokeni ca exemplul
să aibă 2 ferestre cu suprapunere; în producție e ~3500.
    python -m tests.make_llm_example
"""
import json

from pipeline.common import ROOT, load_config
from pipeline.pack_for_llm import build


def seg(i, spk, start, end, lang, text, low=False, flags=()):
    return {"id": i, "speaker": spk, "start": start, "end": end, "lang": lang, "text": text,
            "low_confidence": low, "flags": list(flags), "dropped": None}


SEGMENTS = [
    seg(0, "SPEAKER_00", 3.1, 12.4, "ro", "Bună dimineața. Începem cu reanimarea. Pacientul din patul 8, 67 de ani, internat cu infarct miocardic."),
    seg(1, "SPEAKER_00", 12.6, 21.0, "ro", "S-a făcut tromboaspirație, fracția de ejecție 38–40%. Tensiunea 80 pe 40, pe noradrenalină."),
    seg(2, "SPEAKER_01", 21.5, 26.2, "ro", "Diureza cum este?"),
    seg(3, "SPEAKER_00", 26.5, 31.8, "ro", "Diureza e scăzută, 400 ml pe noapte, creatinina 240."),
    seg(4, "SPEAKER_02", 32.4, 40.9, "ru", "Давайте повторим креатинин вечером и решим по гемодиализу."),
    seg(5, "SPEAKER_01", 41.3, 44.0, "ro", "De acord. Și ecografia?", low=True),
    seg(6, "SPEAKER_00", 44.5, 52.2, "ro", "Ecografia e programată azi la ora 12.", flags=["uncertain_language"]),
    seg(7, "SPEAKER_02", 52.8, 58.1, "ru", "Хорошо. Следующий пациент, седьмая палата."),
    {**seg(8, "SPEAKER_01", 58.5, 60.0, "ru", "Продолжение следует..."), "dropped": "hallucination_phrase"},
]


def main():
    p = dict(load_config()["pack"], window_tokens=120)
    data = build(SEGMENTS, p, "exemplu_sedinta", 61.0)
    out = ROOT / "docs" / "llm_input.example.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    print(out)
    for w in data["windows"]:
        print(f"--- fereastra {w['index']} (overlap {w['overlap_turns']}, ~{w['tokens_est']} tokeni)\n{w['text']}")


if __name__ == "__main__":
    main()
