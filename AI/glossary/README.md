# Glosar (opțional)

Fișierele de aici se trimit la Whisper la transcriere, per limbă:

- `prompt.<lang>.txt` sau `prompt.txt`: `initial_prompt`, un text care descrie stilul și vocabularul;
- `hotwords.<lang>.txt` sau `hotwords.txt`: termeni, câte unul pe linie. Se trimit **doar cu `whisper.use_hotwords: true`**.

Liniile care încep cu `#` sunt ignorate. **Fără date reale de pacienți.**

`terms.tsv` e glosarul medical (un rând = un concept: forme RO/RU/EN, abrevieri, categorie, hotword). Din el:
- `python -m pipeline.glossary hotwords` generează `hotwords.<lang>.txt` (termenii cu `hotword=1`, ~25/limbă);
- `pipeline/correct.py` face post-corecția (`postprocess.glossary_correction.enabled`);
- `evaluation/evaluate.py` măsoară acuratețea termenilor. Detalii: [evaluation/README.md](../evaluation/README.md).

**Implicit: hotwords DA, prompt NU.** Măsurat pe referința Medpark (`tests/reference/medpark.txt`, corectată: se spune
„patul 8”, nu „pacientul 48”):

| Variantă | WER | CER |
|---|---|---|
| fără nimic | 59.9% | 40.4% |
| **hotwords** (`hotwords.<lang>.txt`) | **55.6%** | **33.1%** |
| prompt de domeniu | 71.1% | 51.2% |
| prompt + hotwords | 71.9% | 55.0% |

Nici hotwords, nici promptul nu repară „patul 8” → „Pacientul 48”: e o confuzie acustică de cifre. Termenii de terapie
intensivă din ședință (meropenem, dobutamină, ecocardiografie) nu sunt încă printre cei ~25 marcați `hotword=1`. Orice
schimbare de prompt sau de listă se păstrează doar dacă scade WER-ul măsurat:

```
python -m tests.evaluate tests/reference/medpark.txt JOB_FARA JOB_CU
```

Schimbarea glosarului schimbă amprenta `setup` a ASR-ului, deci rulați într-un job nou sau ștergeți `asr.jsonl`.
