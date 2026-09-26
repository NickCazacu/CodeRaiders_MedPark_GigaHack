# Rezultatele experimentelor ASR

Fiecare rând: ce s-a schimbat, pe ce date s-a măsurat, rezultatul și decizia. Setările adoptate sunt în `config.yaml`.

**Limita importantă:** există o singură înregistrare medicală reală (Medpark, 12 min), cu 2 fragmente transcrise manual
(`tests/reference/medpark.txt`, 349 de cuvinte). Toate reglajele de mai jos sunt făcute pe ea, deci nu mai e un test
independent. Diferențe de ~1–3 puncte WER (~5–10 cuvinte) sunt în zona de zgomot.

## Medpark, WER/CER pe referință (`python -m tests.evaluate tests/reference/medpark.txt JOB`)

Referința a fost corectată pe 26.09: se spune „patul 8”, nu „pacientul 48”. Rândurile marcate „vechi” sunt pe
referința inițială (346 de cuvinte).

| Configurație | WER | CER | Decizie |
|---|---|---|---|
| medium, detecție automată de limbă, fără diarizare (vechi) | 81.2% | 67.4% | — |
| large-v3, detecție automată (vechi) | 89.6% | 79.3% | detectorul confundă româna moldovenească cu rusa |
| large-v3, `--language ro` (vechi) | 62.7% | 43.4% | bun pe română, dar pierde rusa |
| large-v3 + diarizare, comparare ro/ru fără bonus (vechi) | 65.9% | 48.6% | — |
| … + bonus ro 0.3 (vechi) | 60.1% | 41.2% | — |
| **… + bonus ro 0.5, fără prompt** (vechi) | **59.5%** | **40.1%** | **adoptat**: egal cu româna forțată, rusa rămâne posibilă |
| … + prompt de domeniu (vechi) | 63.0% | 41.8% | respins: inventa „patul 8” din exemplele promptului |
| … + `--denoise` (noisereduce) (vechi) | 63.0% | 43.4% | respins |
| … + `--beam-size 10` (vechi) | 57.8% | 40.7% | neconcludent (CER mai slab), nu s-a adoptat |
| … + VAD prag 0.35 (vechi) | 59.8% | 38.0% | neconcludent |
| setarea adoptată, pe referința corectată | 59.9% | 40.4% | — |
| **+ hotwords (`glossary/hotwords.*.txt`)** | **55.6%** | **33.1%** | **adoptat** (cost ~+40% timp de ASR) |
| + prompt de domeniu | 71.1% | 51.2% | respins |
| + prompt + hotwords | 71.9% | 55.0% | respins |

Descompunerea erorilor la setarea adoptată (fără hotwords): 44.8% cuvinte corecte, 37.9% înlocuite, 17.3% lipsă
(mai ales intervenții scurte suprapuse peste alt vorbitor), 4.3% în plus. Cuvintele lipsă nu vin din VAD (pragul
mai sensibil nu le-a recuperat), ci din vorbirea suprapusă.

## Amestec de limbi (`python -m evaluation.lang_mix`)

„Ședință” sintetică de 7.9 min din fragmente reale cu transcriere cunoscută: 24 în română moldovenească (Kaggle,
doar videoclipurile păstrate pentru test), 12 în rusă și 6 în engleză (FLEURS dev), în blocuri de 1–4 fragmente.
Pipeline complet, cu diarizare.

| Configurație | ro: limbă ok / WER | ru: limbă ok / WER | en: limbă ok / WER |
|---|---|---|---|
| bonus ro 0.5 față de **orice** limbă | 100% / 59.3% | 92% / 25.6% | **50%** / 31.4% (engleza tradusă în română) |
| **preferință ro peste ru 0.5 (doar duelul ro–ru)** | 100% / 59.3% | 92% / 25.6% | **100% / 1.3%** |

Medpark cu regula nouă: identic (55.6% / 33.1%), deci fără regresie. Regula nouă e adoptată (`language_preference`).

**WER-ul pe română e supraestimat:** fragmentele Kaggle conțin adesea mai multă vorbire decât transcrierea lor
(ex. audio „…nu a fost posibil, cancerul deja avansase”, transcriere până la „posibil”). Transcrierea large-v3 pe
aceste fragmente e în mare corectă. Consecințe: (1) accentul moldovenesc pe vorbire clară nu e problema principală;
(2) etichetele Kaggle nu pot fi folosite la fine-tuning fără filtrare, altfel modelul ar învăța să omită vorbire.

## Nevalidat încă

- Bonusul ro 0.5 pe **rusă reală**: dacă rusa vorbită iese transcrisă ca română, bonusul trebuie scăzut.
- Orice setare pe alte înregistrări medicale (nu există).
