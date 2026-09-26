# Glosar (opțional)

Fișierele de aici se trimit la Whisper la transcriere, per limbă:

- `prompt.<lang>.txt` sau `prompt.txt`: `initial_prompt`, un text care descrie stilul și vocabularul;
- `hotwords.<lang>.txt` sau `hotwords.txt`: termeni, câte unul pe linie. Se trimit **doar cu `whisper.use_hotwords: true`**.

Liniile care încep cu `#` sunt ignorate. **Fără date reale de pacienți.**

`terms.tsv` e glosarul medical (un rând = un concept: forme RO/RU/EN, abrevieri, categorie, hotword). Din el:
- `python -m pipeline.glossary hotwords` generează `hotwords.<lang>.txt` (termenii cu `hotword=1`, ~25/limbă);
- `pipeline/correct.py` face post-corecția (`postprocess.glossary_correction.enabled`);
- `evaluation/evaluate.py` măsoară acuratețea termenilor. Detalii: [evaluation/README.md](../evaluation/README.md).

**Implicit nu folosim prompt.** Pe referința Medpark (`tests/reference/medpark.txt`), un prompt de domeniu a crescut WER-ul
de la 59.5% la 63.0%. De exemplu, a transformat „pacientul 48” în „pacientul din patul 8”. Orice prompt sau listă de termeni
se adaugă doar dacă scade WER-ul măsurat:

```
python -m tests.evaluate tests/reference/medpark.txt JOB_FARA JOB_CU
```

Schimbarea glosarului schimbă amprenta `setup` a ASR-ului, deci rulați într-un job nou sau ștergeți `asr.jsonl`.
