"""Detectarea transcrierilor care sunt copii ale initial_prompt (cazuri reale din Medpark).
    python -m tests.test_prompt_leak
"""
from pipeline.asr import prompt_leak

RU = ("Утренняя конференция в отделении реанимации. Доклад по пациентам: пациентка с третьей койки, "
      "пациент с двенадцатой койки, сатурация, артериальное давление, инфаркт миокарда.")
RO = "Ședință de dimineață în secția de reanimare. Raportul pe pacienți: pacienta din patul 3, pacientul din patul 12."


def test_leaks():
    assert prompt_leak("Доклад по пациентке с третьей койки, пациент с двенадцатой койки, сатурация.", RU)
    assert prompt_leak("Доклад по пациентам. Доклад по пациентам.", RU)


def test_real_speech_is_not_leak():
    # vorbire reală care folosește câteva cuvinte din prompt
    assert not prompt_leak("Pacientul din patul 8, el a fost pe dată de uscări și a ajuns la noi cu infarct miocardic.", RO)
    assert not prompt_leak("Клиника, когда мы скинули фрагменты на ЕКС, на 80, мы держали тему на следующую дозу.", RU)
    assert not prompt_leak("Da.", RO)  # prea scurt ca să decidem


if __name__ == "__main__":
    for name, fn in [(n, f) for n, f in globals().items() if n.startswith("test_")]:
        fn()
        print(f"OK  {name}")
